"""Configuration centrale, constantes vérifiées et accès aux secrets.

Aucune clé d'API n'est écrite en dur. Les secrets sont lus, dans l'ordre :

1. ``st.secrets`` (Streamlit Community Cloud, Hugging Face Spaces) ;
2. variables d'environnement ;
3. valeur par défaut ``None`` (la fonctionnalité concernée est alors désactivée
   explicitement, jamais remplacée par une valeur simulée).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

# --------------------------------------------------------------------------- #
# Seuils temporels                                                             #
# --------------------------------------------------------------------------- #

#: Seuils d'accessibilité, en secondes : 10, 20, ..., 120 minutes.
#:
#: Ces douze valeurs ne sont pas arbitraires : elles correspondent exactement aux
#: valeurs de l'attribut ``range`` présentes dans les données OpenAccessLens pour
#: les catégories santé (cf. ``RANGE_OPTIONS.health`` dans
#: https://github.com/GIScience/open-access-lens/blob/main/src/config.ts).
THRESHOLDS_SECONDS: Final[tuple[int, ...]] = tuple(minutes * 60 for minutes in range(10, 130, 10))

#: Les mêmes seuils exprimés en minutes.
THRESHOLDS_MINUTES: Final[tuple[int, ...]] = tuple(seconds // 60 for seconds in THRESHOLDS_SECONDS)

# --------------------------------------------------------------------------- #
# OpenAccessLens / HeiGIT                                                      #
# --------------------------------------------------------------------------- #

HEIGIT_STORAGE_BASE: Final[str] = "https://hot.storage.heigit.org/heigit-hdx-public"
HEIGIT_COUNTRIES_URL: Final[str] = f"{HEIGIT_STORAGE_BASE}/access/aux/countries.yaml"
HEIGIT_TILES_BASE: Final[str] = f"{HEIGIT_STORAGE_BASE}/access/aux/tiles"
HEIGIT_STATS_BASE: Final[str] = f"{HEIGIT_STORAGE_BASE}/access/aux/stats"

#: Catégories de santé publiées par OpenAccessLens.
HEALTH_CATEGORIES: Final[dict[str, str]] = {
    "hospitals": "Hôpitaux",
    "primary_healthcare": "Soins de santé primaires",
}

#: Palette officielle OpenAccessLens pour la santé (12 classes, viridis inversé).
ISOCHRONE_COLORS_HEALTH: Final[tuple[str, ...]] = (
    "#fde725",
    "#c2df23",
    "#86d549",
    "#52c569",
    "#2ab07f",
    "#1e9b8a",
    "#25858e",
    "#2d708e",
    "#38588c",
    "#433e85",
    "#482173",
    "#440154",
)

#: Palette de distinction entre structures (mode 2), lisible pour les daltoniens.
FACILITY_COLORS: Final[tuple[str, ...]] = (
    "#e6194b",
    "#3cb44b",
    "#4363d8",
    "#f58231",
    "#911eb4",
    "#46f0f0",
    "#f032e6",
    "#bcf60c",
    "#008080",
    "#9a6324",
)

def color_for_threshold(seconds: int) -> str:
    """Couleur officielle associée à un seuil, en secondes."""
    try:
        index = THRESHOLDS_SECONDS.index(int(seconds))
    except ValueError:
        return "#808080"
    return ISOCHRONE_COLORS_HEALTH[index]


def color_for_facility(index: int) -> str:
    """Couleur de distinction d'une structure, par position."""
    return FACILITY_COLORS[index % len(FACILITY_COLORS)]


# --------------------------------------------------------------------------- #
# openrouteservice / HeiGIT                                                    #
# --------------------------------------------------------------------------- #

#: Point d'entrée public de l'API openrouteservice opérée par HeiGIT.
ORS_PUBLIC_BASE_URL: Final[str] = "https://api.openrouteservice.org"

#: Limites documentées de l'API publique (https://openrouteservice.org/restrictions/).
#:
#: Ces plafonds sont **durs** : au-delà, l'API renvoie l'erreur 3004
#: ``Parameter 'range=...' is out of range``. L'application les respecte et
#: signale les seuils inatteignables au lieu de les inventer.
ORS_PUBLIC_MAX_RANGE_SECONDS: Final[dict[str, int]] = {
    "driving-car": 3600,        # 1 h
    "driving-hgv": 3600,        # 1 h
    "foot-walking": 72000,      # 20 h
    "foot-hiking": 72000,       # 20 h
    "cycling-regular": 18000,   # 5 h
}

#: Nombre maximal d'intervalles par requête isochrone sur l'API publique.
ORS_PUBLIC_MAX_INTERVALS: Final[int] = 10

#: Nombre maximal de localisations par requête isochrone sur l'API publique.
ORS_PUBLIC_MAX_LOCATIONS: Final[int] = 5

#: Profils de déplacement proposés dans l'interface. Les clés sont les noms ORS ;
#: le client Valhalla les traduit respectivement en ``auto`` et ``pedestrian``.
TRAVEL_PROFILES: Final[dict[str, str]] = {
    "driving-car": "Voiture",
    "foot-walking": "Marche",
}

# --------------------------------------------------------------------------- #
# Valhalla / serveur de démonstration FOSSGIS                                 #
# --------------------------------------------------------------------------- #

#: Endpoint public mondial, sans clé, documenté par le projet Valhalla.
VALHALLA_FOSSGIS_URL: Final[str] = "https://valhalla1.openstreetmap.de/isochrone"

#: Limites renvoyées par le serveur (151 = temps, 152 = nombre de contours).
VALHALLA_FOSSGIS_MAX_RANGE_SECONDS: Final[int] = 120 * 60
VALHALLA_FOSSGIS_MAX_CONTOURS: Final[int] = 4

# --------------------------------------------------------------------------- #
# WorldPop                                                                     #
# --------------------------------------------------------------------------- #

WORLDPOP_DATA_BASE: Final[str] = "https://data.worldpop.org/GIS/Population"

#: Années couvertes par la série « Global 2000-2020 » (non contrainte, 100 m et 1 km).
WORLDPOP_UNCONSTRAINED_YEARS: Final[tuple[int, ...]] = tuple(range(2000, 2021))

#: Année unique de la série « constrained » (2020, 100 m).
WORLDPOP_CONSTRAINED_YEAR: Final[int] = 2020

# --------------------------------------------------------------------------- #
# Projections                                                                  #
# --------------------------------------------------------------------------- #

#: CRS de travail pour le routage, l'affichage et les échanges.
WGS84: Final[str] = "EPSG:4326"

#: CRS équivalent-surface mondial utilisé pour les superficies (Equal Earth).
#: Les superficies ne sont jamais calculées en degrés.
EQUAL_AREA_CRS: Final[str] = "EPSG:8857"

# --------------------------------------------------------------------------- #
# Limites d'exécution                                                          #
# --------------------------------------------------------------------------- #

#: Nombre maximal de structures analysables en une passe (mode 2).
MAX_FACILITIES: Final[int] = 25

#: Taille des lots d'appels au moteur de routage.
ROUTING_BATCH_SIZE: Final[int] = 5

#: Délai (secondes) entre deux requêtes de routage, pour respecter les quotas.
ROUTING_THROTTLE_SECONDS: Final[float] = 1.6

#: Délai maximal d'une requête réseau.
HTTP_TIMEOUT_SECONDS: Final[int] = 120

# --------------------------------------------------------------------------- #
# Cache disque                                                                 #
# --------------------------------------------------------------------------- #


def cache_dir() -> Path:
    """Répertoire de cache disque (rasters, catalogues, isochrones).

    Surchargeable par ``HEALTH_ACCESS_CACHE_DIR`` afin de pointer vers un volume
    persistant (Hugging Face Spaces : ``/data``).
    """
    raw = os.environ.get("HEALTH_ACCESS_CACHE_DIR", "").strip()
    path = Path(raw) if raw else Path.home() / ".cache" / "health_access"
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------- #
# Secrets                                                                      #
# --------------------------------------------------------------------------- #


def get_secret(name: str, default: str | None = None) -> str | None:
    """Lit un secret depuis ``st.secrets`` puis l'environnement.

    L'import de Streamlit est différé pour que ``src`` reste utilisable hors
    application (tests, scripts, API).
    """
    try:
        import streamlit as st

        if name in st.secrets:
            value = str(st.secrets[name]).strip()
            if value:
                return value
    except Exception:  # pragma: no cover - hors contexte Streamlit
        pass

    value = os.environ.get(name, "").strip()
    return value or default


def ors_api_key() -> str | None:
    """Clé d'API openrouteservice, ou ``None`` si elle n'est pas configurée."""
    return get_secret("ORS_API_KEY")


def ors_base_url() -> str:
    """URL de base openrouteservice.

    Renseigner ``ORS_BASE_URL`` pour cibler une instance auto-hébergée, seule
    façon de dépasser 60 minutes en profil motorisé.
    """
    return (get_secret("ORS_BASE_URL") or ORS_PUBLIC_BASE_URL).rstrip("/")


def ors_is_public_instance() -> bool:
    """Vrai si l'application cible l'API publique HeiGIT."""
    return ors_base_url().rstrip("/") == ORS_PUBLIC_BASE_URL


def valhalla_base_url() -> str:
    """Endpoint Valhalla, surchargeable pour une instance dédiée compatible."""
    return (get_secret("VALHALLA_BASE_URL") or VALHALLA_FOSSGIS_URL).rstrip("/")


def valhalla_is_enabled() -> bool:
    """Vrai par défaut ; ``VALHALLA_ENABLED=false`` désactive le serveur public."""
    value = (get_secret("VALHALLA_ENABLED", "true") or "true").strip().lower()
    return value not in {"0", "false", "no", "non", "off"}


# --------------------------------------------------------------------------- #
# Avertissements méthodologiques (exportés avec chaque résultat)               #
# --------------------------------------------------------------------------- #

METHODOLOGICAL_WARNINGS: Final[tuple[str, ...]] = (
    "Les isochrones sont calculées sur le réseau routier OpenStreetMap : leur qualité "
    "dépend de la complétude et de l'exactitude de la cartographie locale.",
    "Le modèle n'intègre aucun trafic en temps réel, aucune congestion, aucune "
    "fermeture temporaire, aucune saisonnalité (pluies, pistes impraticables).",
    "Les vitesses sont celles du profil de routage, pas des vitesses observées.",
    "WorldPop est une estimation modélisée dasymétrique : les valeurs par pixel "
    "comportent une incertitude, surtout en zone peu dense ou peu cartographiée.",
    "La population accessible ne présume ni de la capacité, ni de la qualité, ni de "
    "la disponibilité effective des services de la structure.",
    "Le transport public et les barrières financières ou culturelles d'accès ne sont "
    "pas modélisés.",
    "Aucune barrière administrative ou frontalière n'est appliquée : une isochrone "
    "peut franchir une frontière nationale.",
)
