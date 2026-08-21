"""Analyse spatiale : populations cumulées, couronnes, unions et non-double-comptage.

Deux notions strictement distinctes sont maintenues partout :

``population_cumulee``
    population de la zone **≤ seuil** (contient tous les seuils inférieurs) ;
``population_intervalle``
    population de la **couronne** entre le seuil précédent et le seuil courant.

La population d'intervalle est mesurée directement dans la géométrie de la
couronne (``zone_k \\ zone_{k-1}``), ce qui la rend structurellement positive.
La relation ``somme des couronnes == population cumulée`` est vérifiable par
``check_consistency``.

Les populations de plusieurs structures ne sont **jamais** additionnées pour
produire une couverture : l'union géométrique est calculée puis sommée une seule
fois, et l'écart avec la somme brute donne la population des chevauchements.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

import geopandas as gpd
import pandas as pd
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .isochrones import area_km2
from .models import FacilityIsochrones
from .population import zonal_sum


# --------------------------------------------------------------------------- #
# Rattachement de la population                                                #
# --------------------------------------------------------------------------- #


def attach_population(
    results: Sequence[FacilityIsochrones],
    raster_path: str | None,
    *,
    is_density: bool = False,
    reference_population: float | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Sequence[FacilityIsochrones]:
    """Renseigne les populations cumulée et d'intervalle de chaque bande.

    Sans raster, toutes les valeurs restent ``None`` : l'analyse géométrique
    reste valide, la démographie est simplement indisponible.
    """
    bands = [band for result in results for band in result.bands]
    total = len(bands)

    if raster_path is None:
        return results

    for index, band in enumerate(bands, start=1):
        band.population_cumulative = zonal_sum(
            raster_path, band.geometry, is_density=is_density
        )
        band.population_interval = (
            zonal_sum(raster_path, band.ring_geometry, is_density=is_density)
            if band.ring_geometry is not None and not band.ring_geometry.is_empty
            else 0.0
        )
        if (
            reference_population
            and reference_population > 0
            and band.population_cumulative is not None
        ):
            band.population_share = 100.0 * band.population_cumulative / reference_population
        if progress is not None:
            progress(index, total)

    return results


def check_consistency(
    results: Sequence[FacilityIsochrones],
    *,
    tolerance_ratio: float = 0.02,
) -> list[str]:
    """Contrôles de cohérence, renvoyés sous forme d'avertissements lisibles.

    Sont vérifiés : la croissance de la population cumulée avec le seuil, la
    positivité des populations d'intervalle, et l'égalité entre la somme des
    couronnes et la population cumulée du dernier seuil.
    """
    messages: list[str] = []

    for result in results:
        name = result.facility.name
        previous_cumulative: float | None = None
        running = 0.0

        for band in sorted(result.bands, key=lambda item: item.threshold_seconds):
            current = band.population_cumulative
            if current is None:
                continue

            if previous_cumulative is not None and current < previous_cumulative - 1e-6:
                messages.append(
                    f"{name} : population cumulée décroissante entre "
                    f"{band.previous_threshold_seconds // 60 if band.previous_threshold_seconds else 0} "
                    f"et {band.threshold_minutes} min "
                    f"({previous_cumulative:,.0f} → {current:,.0f})."
                )
            previous_cumulative = current

            if band.population_interval is not None:
                if band.population_interval < -1e-6:
                    messages.append(
                        f"{name} : population d'intervalle négative à "
                        f"{band.threshold_minutes} min."
                    )
                running += max(band.population_interval, 0.0)

        if previous_cumulative and previous_cumulative > 0:
            gap = abs(running - previous_cumulative) / previous_cumulative
            if gap > tolerance_ratio:
                messages.append(
                    f"{name} : la somme des couronnes ({running:,.0f}) s'écarte de "
                    f"{gap:.1%} de la population cumulée finale ({previous_cumulative:,.0f}). "
                    "Écart attendu si des seuils intermédiaires n'ont pas été calculés."
                )

    return messages


# --------------------------------------------------------------------------- #
# Tableaux                                                                     #
# --------------------------------------------------------------------------- #

LONG_COLUMNS: tuple[str, ...] = (
    "structure",
    "identifiant",
    "latitude",
    "longitude",
    "mode_deplacement",
    "seuil_min",
    "seuil_secondes",
    "population_cumulee",
    "population_intervalle",
    "superficie_km2",
    "superficie_intervalle_km2",
    "part_population_pct",
    "moteur_routage",
    "version_moteur",
)


def long_table(results: Iterable[FacilityIsochrones]) -> pd.DataFrame:
    """Format long : une ligne par structure et par seuil."""
    records: list[dict[str, Any]] = []

    for result in results:
        for band in sorted(result.bands, key=lambda item: item.threshold_seconds):
            record = {
                "structure": band.facility_name,
                "identifiant": band.facility_id,
                "latitude": result.facility.latitude,
                "longitude": result.facility.longitude,
                "mode_deplacement": result.profile,
                "seuil_min": band.threshold_minutes,
                "seuil_secondes": band.threshold_seconds,
                "population_cumulee": band.population_cumulative,
                "population_intervalle": band.population_interval,
                "superficie_km2": band.area_km2_cumulative,
                "superficie_intervalle_km2": band.area_km2_interval,
                "part_population_pct": band.population_share,
                "moteur_routage": result.engine,
                "version_moteur": result.engine_version,
            }
            record.update(
                {f"demo_{key}": value for key, value in band.demographics.items()}
            )
            records.append(record)

    if not records:
        return pd.DataFrame(columns=list(LONG_COLUMNS))

    frame = pd.DataFrame(records)
    ordered = [column for column in LONG_COLUMNS if column in frame.columns]
    remaining = [column for column in frame.columns if column not in ordered]
    return frame[ordered + remaining].sort_values(["structure", "seuil_min"]).reset_index(drop=True)


#: Métriques disponibles dans le tableau matriciel.
MATRIX_METRICS: dict[str, str] = {
    "population_cumulee": "Population cumulée",
    "population_intervalle": "Population par intervalle",
    "part_population_pct": "Part de la population (%)",
    "superficie_km2": "Superficie cumulée (km²)",
    "superficie_intervalle_km2": "Superficie de la couronne (km²)",
}


def matrix_table(long_frame: pd.DataFrame, metric: str = "population_cumulee") -> pd.DataFrame:
    """Format matrice : une ligne par structure, une colonne par seuil."""
    if long_frame.empty or metric not in long_frame.columns:
        return pd.DataFrame()

    pivot = long_frame.pivot_table(
        index="structure", columns="seuil_min", values=metric, aggfunc="first"
    )
    pivot = pivot.reindex(sorted(pivot.columns), axis=1)
    pivot.columns = [f"{int(column)} min" for column in pivot.columns]
    return pivot.reset_index()


# --------------------------------------------------------------------------- #
# Couverture combinée                                                          #
# --------------------------------------------------------------------------- #


def union_geometry(
    results: Sequence[FacilityIsochrones], threshold_seconds: int
) -> BaseGeometry | None:
    """Union géométrique des zones ≤ seuil de toutes les structures."""
    geometries = [
        band.geometry
        for result in results
        for band in result.bands
        if band.threshold_seconds == threshold_seconds
        and band.geometry is not None
        and not band.geometry.is_empty
    ]
    if not geometries:
        return None
    if len(geometries) == 1:
        return geometries[0]
    merged = unary_union(geometries)
    return merged if not merged.is_empty else None


def combined_coverage(
    results: Sequence[FacilityIsochrones],
    thresholds_seconds: Sequence[int],
    raster_path: str | None,
    *,
    is_density: bool = False,
    reference_population: float | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> pd.DataFrame:
    """Couverture réelle de plusieurs structures, sans double comptage.

    Pour chaque seuil :

    * ``population_somme_brute`` — somme des populations par structure, qui
      **surestime** la couverture dès que les zones se recouvrent ;
    * ``population_union`` — somme WorldPop dans l'union géométrique, soit la
      population réellement couverte, comptée une seule fois ;
    * ``population_chevauchement`` — différence entre les deux, c'est-à-dire la
      population desservie par au moins deux structures.
    """
    records: list[dict[str, Any]] = []
    thresholds = sorted(set(int(value) for value in thresholds_seconds))
    total = len(thresholds)

    for index, seconds in enumerate(thresholds, start=1):
        bands = [
            band
            for result in results
            for band in result.bands
            if band.threshold_seconds == seconds
        ]
        if not bands:
            continue

        geometry = union_geometry(results, seconds)
        raw_sum = sum(
            band.population_cumulative
            for band in bands
            if band.population_cumulative is not None
        )
        has_population = any(band.population_cumulative is not None for band in bands)

        union_population: float | None = None
        if raster_path is not None and geometry is not None:
            union_population = zonal_sum(raster_path, geometry, is_density=is_density)

        overlap: float | None = None
        if union_population is not None and has_population:
            overlap = max(raw_sum - union_population, 0.0)

        records.append(
            {
                "seuil_min": seconds // 60,
                "seuil_secondes": seconds,
                "nombre_structures": len(bands),
                "population_somme_brute": raw_sum if has_population else None,
                "population_union": union_population,
                "population_chevauchement": overlap,
                "superficie_union_km2": area_km2(geometry),
                "part_population_union_pct": (
                    100.0 * union_population / reference_population
                    if union_population is not None
                    and reference_population
                    and reference_population > 0
                    else None
                ),
            }
        )

        if progress is not None:
            progress(index, total)

    return pd.DataFrame(records)


def union_geodataframe(
    results: Sequence[FacilityIsochrones],
    thresholds_seconds: Sequence[int],
    crs: str = "EPSG:4326",
) -> gpd.GeoDataFrame:
    """Unions géométriques par seuil, prêtes pour l'affichage ou l'export."""
    records: list[dict[str, Any]] = []
    geometries: list[BaseGeometry] = []

    for seconds in sorted(set(int(value) for value in thresholds_seconds)):
        geometry = union_geometry(results, seconds)
        if geometry is None:
            continue
        records.append(
            {
                "seuil_secondes": seconds,
                "seuil_minutes": seconds // 60,
                "superficie_km2": area_km2(geometry),
            }
        )
        geometries.append(geometry)

    if not records:
        return gpd.GeoDataFrame(pd.DataFrame(columns=["seuil_minutes"]), geometry=[], crs=crs)
    return gpd.GeoDataFrame(pd.DataFrame(records), geometry=geometries, crs=crs)


# --------------------------------------------------------------------------- #
# Indicateurs de synthèse                                                      #
# --------------------------------------------------------------------------- #


def summary_indicators(
    results: Sequence[FacilityIsochrones],
    coverage: pd.DataFrame,
) -> dict[str, Any]:
    """Indicateurs agrégés : couverture maximale, seuil médian, échecs."""
    indicators: dict[str, Any] = {
        "structures_analysees": len(results),
        "structures_en_echec": sum(1 for result in results if not result.succeeded),
        "seuils_en_echec": sum(len(result.failed_thresholds) for result in results),
    }

    if not coverage.empty and coverage["population_union"].notna().any():
        valid = coverage.dropna(subset=["population_union"])
        largest = valid.iloc[valid["seuil_min"].idxmax()] if len(valid) else None
        if largest is not None:
            indicators["population_couverte_max"] = float(largest["population_union"])
            indicators["seuil_max_min"] = int(largest["seuil_min"])
            indicators["superficie_couverte_max_km2"] = float(largest["superficie_union_km2"])
            if largest.get("population_chevauchement") is not None:
                indicators["population_chevauchement"] = float(
                    largest["population_chevauchement"]
                )

        # Seuil médian : premier seuil atteignant la moitié de la population
        # finalement couverte. Indicateur de rapidité d'accès.
        final = float(valid["population_union"].max())
        if final > 0:
            half = final / 2.0
            reached = valid[valid["population_union"] >= half]
            if not reached.empty:
                indicators["seuil_median_min"] = int(reached["seuil_min"].min())

    return indicators
