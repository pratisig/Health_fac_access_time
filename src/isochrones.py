"""Construction des zones de desserte cumulatives (mode 2).

Garanties apportées par ce module :

* **emboîtement strict** — la zone ≤ 20 min contient la zone ≤ 10 min, etc.
  Le moteur renvoie déjà des zones cumulées, mais l'union successive rend la
  propriété vraie même en cas d'artefact de lissage ;
* **couronnes par différence géométrique** — la population d'un intervalle est
  mesurée dans ``zone_k \\ zone_{k-1}``, et non déduite d'une soustraction de
  totaux : elle ne peut donc pas être négative ;
* **aucun substitut géométrique** — un seuil non calculé reste absent, avec son
  motif. Jamais de cercle, jamais de tampon.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .config import EQUAL_AREA_CRS, WGS84
from .models import Facility, FacilityIsochrones, IsochroneBand
from .routing import RoutingClient, RoutingError


def _clean(geometry: BaseGeometry | None) -> BaseGeometry | None:
    """Répare une géométrie invalide sans en altérer l'emprise."""
    if geometry is None or geometry.is_empty:
        return None
    if not geometry.is_valid:
        geometry = geometry.buffer(0)
    if geometry.is_empty:
        return None
    return geometry


def enforce_nesting(geometries: dict[int, BaseGeometry]) -> dict[int, BaseGeometry]:
    """Rend la série de zones strictement emboîtée par seuil croissant.

    ``zone_k`` devient l'union de toutes les zones de seuil ≤ k. La propriété
    ``zone_10 ⊆ zone_20 ⊆ ... ⊆ zone_120`` est ainsi garantie.
    """
    nested: dict[int, BaseGeometry] = {}
    accumulated: BaseGeometry | None = None

    for seconds in sorted(geometries):
        geometry = _clean(geometries[seconds])
        if geometry is None:
            continue
        accumulated = geometry if accumulated is None else unary_union([accumulated, geometry])
        cleaned = _clean(accumulated)
        if cleaned is not None:
            nested[seconds] = cleaned
            accumulated = cleaned
    return nested


def build_rings(nested: dict[int, BaseGeometry]) -> dict[int, BaseGeometry | None]:
    """Calcule les couronnes entre seuils consécutifs.

    La couronne du plus petit seuil est la zone elle-même. Une couronne vide
    (zone identique au seuil précédent, réseau saturé) vaut ``None``.
    """
    rings: dict[int, BaseGeometry | None] = {}
    previous: BaseGeometry | None = None

    for seconds in sorted(nested):
        current = nested[seconds]
        if previous is None:
            rings[seconds] = current
        else:
            difference = _clean(current.difference(previous))
            rings[seconds] = difference
        previous = current
    return rings


def area_km2(geometry: BaseGeometry | None) -> float | None:
    """Superficie en km², calculée en projection équivalente (jamais en degrés)."""
    if geometry is None or geometry.is_empty:
        return None
    series = gpd.GeoSeries([geometry], crs=WGS84).to_crs(EQUAL_AREA_CRS)
    return float(series.area.iloc[0]) / 1_000_000.0


# --------------------------------------------------------------------------- #
# Orchestration                                                                #
# --------------------------------------------------------------------------- #


def compute_facility_isochrones(
    facility: Facility,
    client: RoutingClient,
    profile: str,
    thresholds_seconds: Sequence[int],
    *,
    smoothing: float | None = None,
) -> FacilityIsochrones:
    """Calcule les zones de desserte d'une structure.

    Une erreur du moteur n'interrompt pas l'analyse : elle est consignée dans
    ``failed_thresholds`` et les autres structures restent traitées.
    """
    result = FacilityIsochrones(facility=facility, profile=profile)

    try:
        features, failures = client.isochrones(
            facility.ors_coordinates, profile, thresholds_seconds, smoothing=smoothing
        )
    except RoutingError as error:
        result.failed_thresholds = {int(value): str(error) for value in thresholds_seconds}
        return result
    finally:
        # La version du moteur n'est connue qu'après une réponse : elle est donc
        # relue ici, y compris en cas d'échec partiel.
        info = client.engine_info()
        result.engine = info["engine"]
        result.engine_version = info["version"]

    result.failed_thresholds = dict(failures)

    raw: dict[int, BaseGeometry] = {}
    for seconds, feature in features.items():
        geometry = feature.get("geometry")
        if not geometry:
            result.failed_thresholds[seconds] = "Géométrie absente de la réponse du moteur"
            continue
        cleaned = _clean(shape(geometry))
        if cleaned is None:
            result.failed_thresholds[seconds] = "Géométrie vide renvoyée par le moteur"
            continue
        raw[seconds] = cleaned

    nested = enforce_nesting(raw)
    rings = build_rings(nested)

    ordered = sorted(nested)
    for position, seconds in enumerate(ordered):
        previous = ordered[position - 1] if position > 0 else None
        band = IsochroneBand(
            facility_id=facility.identifier,
            facility_name=facility.name,
            threshold_seconds=seconds,
            geometry=nested[seconds],
            ring_geometry=rings.get(seconds),
            previous_threshold_seconds=previous,
        )
        band.area_km2_cumulative = area_km2(band.geometry)
        band.area_km2_interval = area_km2(band.ring_geometry)
        result.bands.append(band)

    # Le moteur peut renvoyer la version fusionnée d'un seuil dont la version
    # brute manquait : on ne consigne un échec que pour ce qui est réellement absent.
    for seconds in sorted(set(int(value) for value in thresholds_seconds)):
        if seconds in nested:
            result.failed_thresholds.pop(seconds, None)

    return result


def compute_all(
    facilities: Sequence[Facility],
    client: RoutingClient,
    profile: str,
    thresholds_seconds: Sequence[int],
    *,
    smoothing: float | None = None,
    progress: Callable[[int, int, Facility], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[FacilityIsochrones]:
    """Traite un lot de structures, avec progression et annulation.

    ``should_stop`` est consulté avant chaque structure : l'analyse peut donc
    être interrompue et reprise, le cache disque évitant de recalculer.
    """
    results: list[FacilityIsochrones] = []
    total = len(facilities)

    for index, facility in enumerate(facilities, start=1):
        if should_stop is not None and should_stop():
            break
        if progress is not None:
            progress(index, total, facility)
        results.append(
            compute_facility_isochrones(
                facility, client, profile, thresholds_seconds, smoothing=smoothing
            )
        )
    return results


# --------------------------------------------------------------------------- #
# Conversion vers GeoDataFrame                                                 #
# --------------------------------------------------------------------------- #


def to_geodataframe(
    results: Iterable[FacilityIsochrones],
    *,
    geometry: str = "cumulative",
) -> gpd.GeoDataFrame:
    """Assemble les bandes en GeoDataFrame EPSG:4326.

    ``geometry`` vaut ``"cumulative"`` (zone ≤ seuil) ou ``"ring"`` (couronne).
    """
    records: list[dict[str, Any]] = []
    geometries: list[BaseGeometry] = []

    for result in results:
        for band in result.bands:
            shape_geometry = band.geometry if geometry == "cumulative" else band.ring_geometry
            if shape_geometry is None or shape_geometry.is_empty:
                continue
            records.append(
                {
                    "identifiant": band.facility_id,
                    "structure": band.facility_name,
                    "latitude": result.facility.latitude,
                    "longitude": result.facility.longitude,
                    "mode_deplacement": result.profile,
                    "seuil_secondes": band.threshold_seconds,
                    "seuil_minutes": band.threshold_minutes,
                    "seuil_precedent_min": (
                        band.previous_threshold_seconds // 60
                        if band.previous_threshold_seconds is not None
                        else 0
                    ),
                    "type_zone": "cumulée" if geometry == "cumulative" else "couronne",
                    "population_cumulee": band.population_cumulative,
                    "population_intervalle": band.population_interval,
                    "superficie_km2": (
                        band.area_km2_cumulative
                        if geometry == "cumulative"
                        else band.area_km2_interval
                    ),
                    "part_population_pct": band.population_share,
                    "moteur_routage": result.engine,
                    "version_moteur": result.engine_version,
                }
            )
            geometries.append(shape_geometry)

    if not records:
        return gpd.GeoDataFrame(
            pd.DataFrame(columns=["structure", "seuil_minutes"]), geometry=[], crs=WGS84
        )
    return gpd.GeoDataFrame(pd.DataFrame(records), geometry=geometries, crs=WGS84)
