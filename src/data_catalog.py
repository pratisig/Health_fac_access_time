"""Accès au catalogue et aux statistiques OpenAccessLens / HeiGIT (mode 1).

Sources vérifiées dans le dépôt amont
https://github.com/GIScience/open-access-lens/blob/main/src/config.ts :

* catalogue    ``/access/aux/countries.yaml`` ;
* isochrones   ``/access/aux/tiles/{iso3}/{iso3}_{category}_isochrones.pmtiles`` ;
* statistiques ``/access/aux/stats/{iso3}/category={category}/data.parquet``.

L'attribut de temps est ``range``, en secondes. Aucune valeur de repli n'est
fabriquée : si la source est indisponible, l'erreur est propagée telle quelle.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
import yaml

from .config import (
    HEIGIT_COUNTRIES_URL,
    HEIGIT_STATS_BASE,
    HEIGIT_TILES_BASE,
    HTTP_TIMEOUT_SECONDS,
)


class CatalogError(RuntimeError):
    """Le catalogue ou les statistiques OpenAccessLens sont indisponibles."""


@dataclass(frozen=True)
class Country:
    """Une entrée du catalogue OpenAccessLens."""

    iso3: str
    name: str
    raw: dict[str, Any]

    @property
    def code(self) -> str:
        """Code ISO3 en minuscules, tel qu'utilisé dans les chemins de stockage."""
        return self.iso3.lower()


# --------------------------------------------------------------------------- #
# Catalogue                                                                    #
# --------------------------------------------------------------------------- #


def _extract_name(iso3: str, payload: Any) -> str:
    """Extrait un libellé lisible d'une entrée du YAML, quelle que soit sa forme."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("name", "country", "label", "title", "country_name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return iso3.upper()


def parse_countries_yaml(text: str) -> list[Country]:
    """Analyse ``countries.yaml``.

    Le fichier est indexé par code ISO alpha-3. Deux formes sont tolérées : un
    dictionnaire ``{iso3: {...}}`` et une liste d'objets portant une clé ISO3.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise CatalogError(f"countries.yaml illisible : {error}") from error

    countries: list[Country] = []

    if isinstance(document, dict):
        for key, payload in document.items():
            iso3 = str(key).strip()
            if len(iso3) != 3:
                continue
            raw = payload if isinstance(payload, dict) else {"name": payload}
            countries.append(Country(iso3=iso3.upper(), name=_extract_name(iso3, payload), raw=raw))
    elif isinstance(document, list):
        for payload in document:
            if not isinstance(payload, dict):
                continue
            iso3 = ""
            for key in ("iso3", "iso_3", "code", "id"):
                value = payload.get(key)
                if isinstance(value, str) and len(value.strip()) == 3:
                    iso3 = value.strip()
                    break
            if not iso3:
                continue
            countries.append(
                Country(iso3=iso3.upper(), name=_extract_name(iso3, payload), raw=payload)
            )
    else:
        raise CatalogError("countries.yaml : structure inattendue")

    if not countries:
        raise CatalogError("countries.yaml ne contient aucun pays exploitable")

    return sorted(countries, key=lambda country: country.name.casefold())


def fetch_countries(*, timeout: int = HTTP_TIMEOUT_SECONDS) -> list[Country]:
    """Télécharge et analyse le catalogue dynamique des pays."""
    try:
        response = requests.get(HEIGIT_COUNTRIES_URL, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as error:
        raise CatalogError(
            "Catalogue OpenAccessLens injoignable "
            f"({HEIGIT_COUNTRIES_URL}) : {error}"
        ) from error
    return parse_countries_yaml(response.text)


# --------------------------------------------------------------------------- #
# URLs de ressources                                                           #
# --------------------------------------------------------------------------- #


def pmtiles_url(iso3: str, category: str) -> str:
    """URL du PMTiles officiel des isochrones nationales."""
    code = iso3.strip().lower()
    return f"{HEIGIT_TILES_BASE}/{code}/{code}_{category}_isochrones.pmtiles"


def stats_url(iso3: str, category: str) -> str:
    """URL du Parquet officiel des statistiques démographiques agrégées."""
    code = iso3.strip().lower()
    return f"{HEIGIT_STATS_BASE}/{code}/category={category}/data.parquet"


# --------------------------------------------------------------------------- #
# Statistiques                                                                 #
# --------------------------------------------------------------------------- #

#: Colonnes attendues dans le Parquet OpenAccessLens.
STATS_COLUMNS: tuple[str, ...] = (
    "admin_level",
    "range",
    "population_type",
    "population",
    "population_share",
)


def fetch_stats(
    iso3: str,
    category: str,
    *,
    timeout: int = HTTP_TIMEOUT_SECONDS,
) -> pd.DataFrame:
    """Télécharge les statistiques agrégées publiées par OpenAccessLens.

    Les valeurs renvoyées sont celles calculées par HeiGIT en croisant ses
    isochrones avec WorldPop 100 m. Elles ne sont ni recalculées ni corrigées.
    """
    url = stats_url(iso3, category)
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as error:
        raise CatalogError(f"Statistiques OpenAccessLens indisponibles ({url}) : {error}") from error

    try:
        frame = pd.read_parquet(io.BytesIO(response.content))
    except Exception as error:
        raise CatalogError(f"Parquet OpenAccessLens illisible ({url}) : {error}") from error

    if "range" in frame.columns:
        frame["range"] = pd.to_numeric(frame["range"], errors="coerce")
        frame["range_minutes"] = frame["range"] / 60.0
    return frame


def national_stats(
    frame: pd.DataFrame,
    *,
    population_type: str | None = None,
    admin_level: str = "ADM0",
) -> pd.DataFrame:
    """Filtre les statistiques au niveau national et à un groupe démographique."""
    result = frame
    if "admin_level" in result.columns:
        levels = result["admin_level"].astype(str).str.upper()
        if (levels == admin_level.upper()).any():
            result = result[levels == admin_level.upper()]
    if population_type is not None and "population_type" in result.columns:
        result = result[result["population_type"].astype(str) == population_type]
    if "range" in result.columns:
        result = result.sort_values("range")
    return result.reset_index(drop=True)


def available_population_types(frame: pd.DataFrame) -> list[str]:
    """Groupes démographiques présents dans les statistiques publiées."""
    if "population_type" not in frame.columns:
        return []
    values = frame["population_type"].dropna().astype(str).unique().tolist()
    return sorted(values, key=lambda item: (item != "total", item))


def add_interval_population(frame: pd.DataFrame) -> pd.DataFrame:
    """Ajoute la population par couronne à une série cumulée par ``range``.

    Les statistiques OpenAccessLens sont cumulatives : la population du seuil de
    20 min inclut celle du seuil de 10 min. La différence successive donne la
    population propre à chaque intervalle. Les valeurs sont bornées à 0 : un
    écart négatif ne peut provenir que d'un arrondi de la source.
    """
    if frame.empty or "population" not in frame.columns:
        return frame.assign(population_interval=pd.Series(dtype="float64"))

    result = frame.sort_values("range").copy()
    cumulative = pd.to_numeric(result["population"], errors="coerce")
    result["population_interval"] = cumulative.diff().fillna(cumulative).clip(lower=0.0)
    return result
