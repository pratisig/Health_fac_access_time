"""Population WorldPop : résolution d'URL, téléchargement, métadonnées, somme zonale.

Principes
---------
* La source démographique est **WorldPop**, en « nombre de personnes par pixel »
  (produits ``ppp``). Les métadonnées de chaque raster sont **lues dans le
  fichier**, jamais supposées, et exportées avec les résultats.
* Les populations sont obtenues par **somme zonale réelle** des pixels dans les
  polygones d'isochrones (``rasterio.mask``), avec respect du masque NoData.
* Si un raster est une densité (personnes/km²), la somme est pondérée par la
  surface réelle de chaque pixel ; le cas est détecté et signalé.
* Aucune valeur n'est inventée : en l'absence de raster, la population vaut
  ``None`` et l'interface l'indique.

Disponibilité des URL
---------------------
Les motifs d'URL ci-dessous sont ceux du serveur ``data.worldpop.org``. Ils sont
**vérifiés à l'exécution** par une requête HEAD : la première URL réellement
disponible est retenue. Aucun chemin n'est présumé valide.
"""

from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import rasterio
import requests
from rasterio.mask import mask as rio_mask
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from .config import (
    HTTP_TIMEOUT_SECONDS,
    WORLDPOP_CONSTRAINED_YEAR,
    WORLDPOP_DATA_BASE,
    WORLDPOP_UNCONSTRAINED_YEARS,
    cache_dir,
)
from .models import RasterMetadata


class PopulationDataError(RuntimeError):
    """Le raster de population est absent, illisible ou incompatible."""


# --------------------------------------------------------------------------- #
# Catalogue de produits WorldPop                                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WorldPopProduct:
    """Un produit WorldPop sélectionnable dans l'interface."""

    key: str
    label: str
    resolution: str
    method: str
    years: tuple[int, ...]
    url_templates: tuple[str, ...]

    def urls(self, iso3: str, year: int) -> list[str]:
        upper = iso3.upper()
        lower = iso3.lower()
        return [
            template.format(base=WORLDPOP_DATA_BASE, ISO3=upper, iso3=lower, year=year)
            for template in self.url_templates
        ]


WORLDPOP_PRODUCTS: dict[str, WorldPopProduct] = {
    "unconstrained_100m": WorldPopProduct(
        key="unconstrained_100m",
        label="Non contraint, 100 m (2000-2020)",
        resolution="3 arc-secondes (~100 m à l'équateur)",
        method="Désagrégation dasymétrique top-down par forêt aléatoire (non contrainte)",
        years=WORLDPOP_UNCONSTRAINED_YEARS,
        url_templates=(
            "{base}/Global_2000_2020/{year}/{ISO3}/{iso3}_ppp_{year}.tif",
        ),
    ),
    "unconstrained_100m_unadj": WorldPopProduct(
        key="unconstrained_100m_unadj",
        label="Non contraint, 100 m, ajusté ONU (2000-2020)",
        resolution="3 arc-secondes (~100 m à l'équateur)",
        method="Top-down non contraint, recalé sur les estimations nationales des Nations unies",
        years=WORLDPOP_UNCONSTRAINED_YEARS,
        url_templates=(
            "{base}/Global_2000_2020/{year}/{ISO3}/{iso3}_ppp_{year}_UNadj.tif",
        ),
    ),
    "constrained_100m": WorldPopProduct(
        key="constrained_100m",
        label="Contraint par le bâti, 100 m (2020)",
        resolution="3 arc-secondes (~100 m à l'équateur)",
        method="Top-down contraint par les emprises bâties (Maxar/Ecopia ou BSGM)",
        years=(WORLDPOP_CONSTRAINED_YEAR,),
        url_templates=(
            "{base}/Global_2000_2020_Constrained/{year}/maxar_v1/{ISO3}/{iso3}_ppp_{year}_constrained.tif",
            "{base}/Global_2000_2020_Constrained/{year}/BSGM/{ISO3}/{iso3}_ppp_{year}_constrained.tif",
        ),
    ),
    "constrained_100m_unadj": WorldPopProduct(
        key="constrained_100m_unadj",
        label="Contraint par le bâti, 100 m, ajusté ONU (2020)",
        resolution="3 arc-secondes (~100 m à l'équateur)",
        method="Top-down contraint par le bâti, recalé sur les estimations nationales des Nations unies",
        years=(WORLDPOP_CONSTRAINED_YEAR,),
        url_templates=(
            "{base}/Global_2000_2020_Constrained/{year}/maxar_v1/{ISO3}/{iso3}_ppp_{year}_UNadj_constrained.tif",
            "{base}/Global_2000_2020_Constrained/{year}/BSGM/{ISO3}/{iso3}_ppp_{year}_UNadj_constrained.tif",
        ),
    ),
    "unconstrained_1km": WorldPopProduct(
        key="unconstrained_1km",
        label="Non contraint, 1 km agrégé (2000-2020) — léger",
        resolution="30 arc-secondes (~1 km à l'équateur)",
        method="Agrégation 1 km du produit top-down non contraint",
        years=WORLDPOP_UNCONSTRAINED_YEARS,
        url_templates=(
            "{base}/Global_2000_2020_1km/{year}/{ISO3}/{iso3}_ppp_{year}_1km_Aggregated.tif",
        ),
    ),
}

#: Groupes d'âge WorldPop (structures par sexe et âge).
AGE_GROUPS: tuple[int, ...] = (0, 1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50,
                               55, 60, 65, 70, 75, 80)

SEX_LABELS: dict[str, str] = {"f": "Femmes", "m": "Hommes"}


def agesex_urls(iso3: str, year: int, sex: str, age: int) -> list[str]:
    """URL candidate d'un raster WorldPop par sexe et groupe d'âge."""
    return [
        f"{WORLDPOP_DATA_BASE}/AgeSex_structures/Global_2000_2020/{year}/"
        f"{iso3.upper()}/{iso3.lower()}_{sex}_{age}_{year}.tif"
    ]


# --------------------------------------------------------------------------- #
# Téléchargement                                                               #
# --------------------------------------------------------------------------- #


def url_exists(url: str, *, timeout: int = 30) -> bool:
    """Teste réellement la disponibilité d'une URL (HEAD, repli GET partiel)."""
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code < 400:
            return True
        if response.status_code in (403, 405):  # serveur refusant HEAD
            partial = requests.get(
                url, timeout=timeout, stream=True, headers={"Range": "bytes=0-0"}
            )
            partial.close()
            return partial.status_code < 400
    except requests.RequestException:
        return False
    return False


def resolve_worldpop_url(product: WorldPopProduct, iso3: str, year: int) -> str:
    """Retourne la première URL réellement disponible pour ce produit."""
    if year not in product.years:
        raise PopulationDataError(
            f"Année {year} indisponible pour « {product.label} ». "
            f"Années publiées : {product.years[0]}-{product.years[-1]}."
        )

    candidates = product.urls(iso3, year)
    for url in candidates:
        if url_exists(url):
            return url

    raise PopulationDataError(
        "Aucun raster WorldPop trouvé pour "
        f"{iso3.upper()} / {year} / {product.label}. URL testées :\n"
        + "\n".join(f"  - {url}" for url in candidates)
        + "\nVérifiez la disponibilité sur https://hub.worldpop.org/."
    )


def download_raster(
    url: str,
    *,
    progress: Callable[[int, int | None], None] | None = None,
    timeout: int = HTTP_TIMEOUT_SECONDS,
) -> Path:
    """Télécharge un raster dans le cache disque, avec reprise par fichier complet.

    Un fichier déjà présent et non vide n'est pas retéléchargé, ce qui rend le
    cache persistant utile entre deux sessions.
    """
    directory = cache_dir() / "worldpop"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / Path(url).name

    if target.exists() and target.stat().st_size > 0:
        return target

    temporary = target.with_suffix(target.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            total_header = response.headers.get("Content-Length")
            total = int(total_header) if total_header else None
            downloaded = 0
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress is not None:
                        progress(downloaded, total)
    except requests.RequestException as error:
        temporary.unlink(missing_ok=True)
        raise PopulationDataError(f"Téléchargement WorldPop échoué ({url}) : {error}") from error

    shutil.move(str(temporary), str(target))
    return target


# --------------------------------------------------------------------------- #
# Métadonnées                                                                  #
# --------------------------------------------------------------------------- #


def _approximate_resolution_m(dataset: rasterio.DatasetReader) -> float | None:
    """Résolution approximative en mètres, au centre de l'emprise."""
    try:
        size_x = abs(dataset.transform.a)
        if dataset.crs is None:
            return None
        if dataset.crs.is_geographic:
            centre_latitude = (dataset.bounds.bottom + dataset.bounds.top) / 2.0
            return size_x * 111_320.0 * math.cos(math.radians(centre_latitude))
        return size_x
    except Exception:  # pragma: no cover
        return None


def inspect_raster(
    path: str | Path,
    *,
    source: str = "WorldPop",
    method: str = "",
    year: int | None = None,
    declared_unit: str | None = None,
) -> RasterMetadata:
    """Lit et documente les métadonnées réelles d'un raster de population."""
    path = Path(path)
    if not path.exists():
        raise PopulationDataError(f"Raster introuvable : {path}")

    try:
        with rasterio.open(path) as dataset:
            if dataset.count < 1:
                raise PopulationDataError(f"Raster sans bande exploitable : {path}")

            name = path.name.lower()
            is_density = "density" in name or "_dens" in name
            unit = declared_unit or (
                "personnes par km²" if is_density else "personnes par pixel"
            )

            return RasterMetadata(
                path=str(path),
                crs=dataset.crs.to_string() if dataset.crs else None,
                width=dataset.width,
                height=dataset.height,
                pixel_size_x=abs(dataset.transform.a),
                pixel_size_y=abs(dataset.transform.e),
                nodata=dataset.nodata,
                dtype=dataset.dtypes[0],
                unit=unit,
                is_density=is_density,
                year=year,
                source=source,
                method=method,
                bounds=tuple(dataset.bounds),
                approximate_resolution_m=_approximate_resolution_m(dataset),
            )
    except rasterio.errors.RasterioIOError as error:
        raise PopulationDataError(f"Raster illisible ({path}) : {error}") from error


# --------------------------------------------------------------------------- #
# Somme zonale                                                                 #
# --------------------------------------------------------------------------- #


def _pixel_area_km2(dataset: rasterio.DatasetReader, latitudes: np.ndarray) -> np.ndarray:
    """Surface de chaque pixel, en km², pour un raster géographique."""
    size_x = abs(dataset.transform.a)
    size_y = abs(dataset.transform.e)
    if dataset.crs is not None and dataset.crs.is_geographic:
        metres_x = size_x * 111_320.0 * np.cos(np.radians(latitudes))
        metres_y = size_y * 110_540.0
        return (metres_x * metres_y) / 1_000_000.0
    return np.full_like(latitudes, (size_x * size_y) / 1_000_000.0, dtype="float64")


def zonal_sum(
    raster_path: str | Path,
    geometry: BaseGeometry | None,
    *,
    is_density: bool = False,
    all_touched: bool = False,
) -> float | None:
    """Somme réelle des pixels de population dans un polygone.

    Retourne ``None`` si la géométrie est vide ou hors de l'emprise du raster —
    jamais 0, qui signifierait « zone peuplée de personne ».

    Les pixels NoData et les valeurs négatives (sentinelles) sont exclus. Pour un
    raster de densité, chaque pixel est pondéré par sa surface réelle.
    """
    if geometry is None or geometry.is_empty:
        return None

    try:
        with rasterio.open(raster_path) as dataset:
            bounds = dataset.bounds
            minx, miny, maxx, maxy = geometry.bounds
            if maxx < bounds.left or minx > bounds.right or maxy < bounds.bottom or miny > bounds.top:
                return None

            try:
                data, transform = rio_mask(
                    dataset,
                    [mapping(geometry)],
                    crop=True,
                    all_touched=all_touched,
                    filled=True,
                    nodata=dataset.nodata if dataset.nodata is not None else -9999.0,
                )
            except ValueError:
                # Emprises disjointes : rasterio lève ValueError.
                return None

            band = data[0].astype("float64")
            nodata = dataset.nodata if dataset.nodata is not None else -9999.0

            valid = np.isfinite(band)
            if nodata is not None:
                valid &= band != nodata
            # WorldPop encode l'absence de donnée par une valeur négative.
            valid &= band >= 0

            if not valid.any():
                return 0.0

            if not is_density:
                return float(band[valid].sum())

            rows = np.arange(band.shape[0])
            _, latitudes = rasterio.transform.xy(
                transform, rows, np.zeros_like(rows), offset="center"
            )
            latitude_column = np.asarray(latitudes, dtype="float64").reshape(-1, 1)
            areas = _pixel_area_km2(dataset, latitude_column)
            weighted = np.where(valid, band * areas, 0.0)
            return float(weighted.sum())

    except rasterio.errors.RasterioIOError as error:
        raise PopulationDataError(f"Lecture du raster impossible : {error}") from error


def zonal_sums(
    raster_path: str | Path,
    geometries: Sequence[BaseGeometry | None],
    *,
    is_density: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> list[float | None]:
    """Somme zonale sur une série de géométries."""
    results: list[float | None] = []
    total = len(geometries)
    for index, geometry in enumerate(geometries, start=1):
        results.append(zonal_sum(raster_path, geometry, is_density=is_density))
        if progress is not None:
            progress(index, total)
    return results


def raster_total(raster_path: str | Path, *, is_density: bool = False) -> float:
    """Population totale du raster, servant de référence pour les parts.

    Lue par blocs afin de rester dans la mémoire disponible même pour un raster
    national à 100 m.
    """
    with rasterio.open(raster_path) as dataset:
        nodata = dataset.nodata
        total = 0.0
        for _, window in dataset.block_windows(1):
            block = dataset.read(1, window=window).astype("float64")
            valid = np.isfinite(block) & (block >= 0)
            if nodata is not None:
                valid &= block != nodata
            if not valid.any():
                continue
            if not is_density:
                total += float(block[valid].sum())
            else:
                rows = np.arange(window.height) + window.row_off
                _, latitudes = rasterio.transform.xy(
                    dataset.transform, rows, np.zeros_like(rows), offset="center"
                )
                latitude_column = np.asarray(latitudes, dtype="float64").reshape(-1, 1)
                areas = _pixel_area_km2(dataset, latitude_column)
                total += float(np.where(valid, block * areas, 0.0).sum())
        return total


# --------------------------------------------------------------------------- #
# Profil démographique                                                         #
# --------------------------------------------------------------------------- #


def demographic_profile(
    iso3: str,
    year: int,
    geometry: BaseGeometry,
    *,
    sexes: Iterable[str] = ("f", "m"),
    ages: Iterable[int] = AGE_GROUPS,
    progress: Callable[[str], None] | None = None,
) -> dict[str, float]:
    """Somme zonale par sexe et groupe d'âge, à partir des rasters WorldPop.

    Chaque raster manquant est ignoré silencieusement côté valeurs (la clé est
    simplement absente) : aucune estimation n'est substituée. L'opération est
    coûteuse (36 rasters nationaux) et doit rester optionnelle.
    """
    profile: dict[str, float] = {}
    for sex in sexes:
        for age in ages:
            label = f"{SEX_LABELS.get(sex, sex)} {age}"
            if progress is not None:
                progress(label)
            for url in agesex_urls(iso3, year, sex, age):
                if not url_exists(url):
                    continue
                try:
                    path = download_raster(url)
                    value = zonal_sum(path, geometry)
                except PopulationDataError:
                    break
                if value is not None:
                    profile[label] = value
                break
    return profile
