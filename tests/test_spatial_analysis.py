"""Tests de l'analyse spatiale : emboîtement, couronnes, unions, non-double-comptage."""

from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from src.config import THRESHOLDS_MINUTES, THRESHOLDS_SECONDS
from src.isochrones import area_km2, build_rings, enforce_nesting
from src.models import Facility, FacilityIsochrones, IsochroneBand
from src.routing import chunk_thresholds, public_capabilities
from src.spatial_analysis import (
    attach_population,
    check_consistency,
    combined_coverage,
    long_table,
    matrix_table,
    summary_indicators,
    union_geodataframe,
    union_geometry,
)

PIXEL = 0.01


@pytest.fixture
def raster(tmp_path):
    """Grille 20x20 uniforme : 1 personne par pixel, sur [0, 0.2] x [0.8, 1.0]."""
    path = tmp_path / "pop.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=20, width=20, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(0.0, 1.0, PIXEL, PIXEL), nodata=-99999.0,
    ) as dataset:
        dataset.write(np.ones((20, 20), dtype="float32"), 1)
    return str(path)


def make_result(name: str, boxes: dict[int, tuple], *, identifier: str | None = None):
    """Construit un résultat de routage à partir de rectangles emboîtés."""
    facility = Facility(name=name, latitude=0.9, longitude=0.1,
                        identifier=identifier or name.lower())
    geometries = {seconds: box(*bounds) for seconds, bounds in boxes.items()}
    nested = enforce_nesting(geometries)
    rings = build_rings(nested)

    result = FacilityIsochrones(facility=facility, profile="driving-car",
                                engine="openrouteservice", engine_version="test")
    ordered = sorted(nested)
    for position, seconds in enumerate(ordered):
        band = IsochroneBand(
            facility_id=facility.identifier,
            facility_name=facility.name,
            threshold_seconds=seconds,
            geometry=nested[seconds],
            ring_geometry=rings[seconds],
            previous_threshold_seconds=ordered[position - 1] if position else None,
        )
        band.area_km2_cumulative = area_km2(band.geometry)
        band.area_km2_interval = area_km2(band.ring_geometry)
        result.bands.append(band)
    return result


# --------------------------------------------------------------------------- #
# Tri et emboîtement des seuils                                                #
# --------------------------------------------------------------------------- #


def test_seuils_sont_tries_et_complets():
    assert list(THRESHOLDS_MINUTES) == [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]
    assert list(THRESHOLDS_SECONDS) == [value * 60 for value in THRESHOLDS_MINUTES]
    assert THRESHOLDS_SECONDS == tuple(sorted(THRESHOLDS_SECONDS))


def test_emboitement_est_garanti_meme_avec_des_entrees_desordonnees():
    """zone_10 ⊂ zone_20 ⊂ zone_30, même si le moteur renvoie mal ordonné."""
    geometries = {
        1800: box(0, 0, 3, 3),
        600: box(0, 0, 1, 1),
        1200: box(0, 0, 2, 2),
    }
    nested = enforce_nesting(geometries)

    assert list(nested) == [600, 1200, 1800]
    assert nested[1200].contains(nested[600])
    assert nested[1800].contains(nested[1200])


def test_emboitement_repare_une_zone_non_incluse():
    """Un artefact de lissage ne doit pas casser l'inclusion."""
    geometries = {
        600: box(0, 0, 2, 2),
        1200: box(1, 1, 3, 3),  # ne contient pas la zone 10 min
    }
    nested = enforce_nesting(geometries)

    assert nested[1200].contains(nested[600])
    assert nested[1200].area > box(1, 1, 3, 3).area


def test_couronnes_sont_disjointes_et_recomposent_le_cumul():
    nested = enforce_nesting({600: box(0, 0, 1, 1), 1200: box(0, 0, 2, 2), 1800: box(0, 0, 3, 3)})
    rings = build_rings(nested)

    assert rings[600].area == pytest.approx(1.0)
    assert rings[1200].area == pytest.approx(3.0)
    assert rings[1800].area == pytest.approx(5.0)
    assert sum(ring.area for ring in rings.values()) == pytest.approx(nested[1800].area)
    assert rings[1200].intersection(rings[1800]).area == pytest.approx(0.0)


def test_couronne_vide_quand_la_zone_ne_grandit_plus():
    nested = enforce_nesting({600: box(0, 0, 1, 1), 1200: box(0, 0, 1, 1)})
    rings = build_rings(nested)
    assert rings[1200] is None or rings[1200].area == pytest.approx(0.0)


def test_superficie_calculee_en_projection_equivalente():
    """1° x 1° près de l'équateur ≈ 12 300 km², jamais 1 (degrés carrés)."""
    surface = area_km2(box(0.0, 0.0, 1.0, 1.0))
    assert 11_000 < surface < 13_500


def test_superficie_geometrie_absente():
    assert area_km2(None) is None


# --------------------------------------------------------------------------- #
# Population cumulée et par intervalle                                         #
# --------------------------------------------------------------------------- #


def test_population_cumulee_et_intervalle(raster):
    result = make_result("A", {
        600: (0.0, 0.9, 0.05, 1.0),    # 50 pixels
        1200: (0.0, 0.9, 0.10, 1.0),   # 100 pixels
        1800: (0.0, 0.9, 0.20, 1.0),   # 200 pixels
    })
    attach_population([result], raster)

    bands = {band.threshold_seconds: band for band in result.bands}
    assert bands[600].population_cumulative == pytest.approx(50, rel=0.05)
    assert bands[1200].population_cumulative == pytest.approx(100, rel=0.05)
    assert bands[1800].population_cumulative == pytest.approx(200, rel=0.05)

    # La couronne 10–20 vaut bien cumul_20 - cumul_10.
    assert bands[1200].population_interval == pytest.approx(
        bands[1200].population_cumulative - bands[600].population_cumulative, rel=0.1
    )
    assert bands[1800].population_interval == pytest.approx(100, rel=0.1)


def test_population_du_premier_seuil_egale_sa_couronne(raster):
    result = make_result("A", {600: (0.0, 0.9, 0.05, 1.0), 1200: (0.0, 0.9, 0.1, 1.0)})
    attach_population([result], raster)

    first = result.bands[0]
    assert first.population_interval == pytest.approx(first.population_cumulative, rel=0.01)


def test_aucune_population_negative(raster):
    result = make_result("A", {
        seconds: (0.0, 0.9, 0.01 * index, 1.0)
        for index, seconds in enumerate(THRESHOLDS_SECONDS, start=2)
    })
    attach_population([result], raster)

    for band in result.bands:
        if band.population_interval is not None:
            assert band.population_interval >= 0.0
        if band.population_cumulative is not None:
            assert band.population_cumulative >= 0.0


def test_population_cumulee_est_croissante(raster):
    result = make_result("A", {
        600: (0.0, 0.9, 0.05, 1.0),
        1200: (0.0, 0.9, 0.10, 1.0),
        1800: (0.0, 0.9, 0.20, 1.0),
    })
    attach_population([result], raster)

    values = [band.population_cumulative for band in
              sorted(result.bands, key=lambda item: item.threshold_seconds)]
    assert values == sorted(values)


def test_somme_des_couronnes_egale_le_cumul_final(raster):
    result = make_result("A", {
        600: (0.0, 0.9, 0.05, 1.0),
        1200: (0.0, 0.9, 0.10, 1.0),
        1800: (0.0, 0.9, 0.20, 1.0),
    })
    attach_population([result], raster)

    rings = sum(band.population_interval for band in result.bands)
    final = max(band.population_cumulative for band in result.bands)
    assert rings == pytest.approx(final, rel=0.02)
    assert check_consistency([result]) == []


def test_part_de_population(raster):
    result = make_result("A", {600: (0.0, 0.9, 0.1, 1.0)})
    attach_population([result], raster, reference_population=400.0)

    assert result.bands[0].population_share == pytest.approx(25.0, rel=0.05)


def test_sans_raster_les_populations_restent_nulles():
    result = make_result("A", {600: (0.0, 0.9, 0.1, 1.0)})
    attach_population([result], None)

    assert result.bands[0].population_cumulative is None
    assert result.bands[0].population_interval is None
    assert result.bands[0].area_km2_cumulative is not None  # la géométrie reste valide


def test_controle_de_coherence_detecte_une_decroissance():
    result = make_result("A", {600: (0.0, 0.9, 0.05, 1.0), 1200: (0.0, 0.9, 0.1, 1.0)})
    result.bands[0].population_cumulative = 500.0
    result.bands[0].population_interval = 500.0
    result.bands[1].population_cumulative = 200.0   # incohérent
    result.bands[1].population_interval = 0.0

    messages = check_consistency([result])
    assert any("décroissante" in message for message in messages)


# --------------------------------------------------------------------------- #
# Union et non-double-comptage                                                 #
# --------------------------------------------------------------------------- #


def test_union_de_zones_qui_se_chevauchent(raster):
    a = make_result("A", {600: (0.00, 0.9, 0.10, 1.0)}, identifier="a")
    b = make_result("B", {600: (0.05, 0.9, 0.15, 1.0)}, identifier="b")
    attach_population([a, b], raster)

    merged = union_geometry([a, b], 600)
    assert merged.bounds[0] == pytest.approx(0.0)
    assert merged.bounds[2] == pytest.approx(0.15)

    coverage = combined_coverage([a, b], [600], raster)
    row = coverage.iloc[0]

    # Somme brute = 100 + 100 = 200 ; union réelle = 150 ; chevauchement = 50.
    assert row["population_somme_brute"] == pytest.approx(200, rel=0.05)
    assert row["population_union"] == pytest.approx(150, rel=0.05)
    assert row["population_chevauchement"] == pytest.approx(50, rel=0.1)
    assert row["population_union"] < row["population_somme_brute"]


def test_zones_disjointes_sans_chevauchement(raster):
    a = make_result("A", {600: (0.00, 0.9, 0.05, 1.0)}, identifier="a")
    b = make_result("B", {600: (0.10, 0.9, 0.15, 1.0)}, identifier="b")
    attach_population([a, b], raster)

    row = combined_coverage([a, b], [600], raster).iloc[0]

    assert row["population_chevauchement"] == pytest.approx(0.0, abs=2.0)
    assert row["population_union"] == pytest.approx(row["population_somme_brute"], rel=0.05)


def test_structures_identiques_ne_sont_pas_comptees_deux_fois(raster):
    """Deux structures aux zones identiques couvrent la population une seule fois."""
    a = make_result("A", {600: (0.0, 0.9, 0.1, 1.0)}, identifier="a")
    b = make_result("B", {600: (0.0, 0.9, 0.1, 1.0)}, identifier="b")
    attach_population([a, b], raster)

    row = combined_coverage([a, b], [600], raster).iloc[0]

    assert row["population_union"] == pytest.approx(100, rel=0.05)
    assert row["population_somme_brute"] == pytest.approx(200, rel=0.05)
    assert row["population_chevauchement"] == pytest.approx(100, rel=0.1)


def test_union_croit_avec_le_seuil(raster):
    a = make_result("A", {600: (0.0, 0.9, 0.05, 1.0), 1200: (0.0, 0.9, 0.1, 1.0)})
    attach_population([a], raster)

    coverage = combined_coverage([a], [600, 1200], raster)
    assert coverage["population_union"].is_monotonic_increasing


def test_union_geodataframe(raster):
    a = make_result("A", {600: (0.0, 0.9, 0.05, 1.0), 1200: (0.0, 0.9, 0.1, 1.0)})
    frame = union_geodataframe([a], [600, 1200])

    assert len(frame) == 2
    assert frame.crs.to_string() == "EPSG:4326"
    assert list(frame["seuil_minutes"]) == [10, 20]


def test_couverture_sans_raster_reste_geometrique():
    a = make_result("A", {600: (0.0, 0.9, 0.05, 1.0)})
    coverage = combined_coverage([a], [600], None)

    assert coverage.iloc[0]["population_union"] is None
    assert coverage.iloc[0]["superficie_union_km2"] > 0


# --------------------------------------------------------------------------- #
# Tableaux                                                                     #
# --------------------------------------------------------------------------- #


def test_tableau_long(raster):
    a = make_result("A", {600: (0.0, 0.9, 0.05, 1.0), 1200: (0.0, 0.9, 0.1, 1.0)})
    attach_population([a], raster)
    frame = long_table([a])

    assert len(frame) == 2
    assert list(frame["seuil_min"]) == [10, 20]
    for column in ("structure", "population_cumulee", "population_intervalle", "superficie_km2"):
        assert column in frame.columns


def test_tableau_matrice(raster):
    a = make_result("A", {600: (0.0, 0.9, 0.05, 1.0), 1200: (0.0, 0.9, 0.1, 1.0)}, identifier="a")
    b = make_result("B", {600: (0.0, 0.9, 0.05, 1.0)}, identifier="b")
    attach_population([a, b], raster)

    matrix = matrix_table(long_table([a, b]), "population_cumulee")

    assert list(matrix.columns) == ["structure", "10 min", "20 min"]
    assert len(matrix) == 2


def test_matrice_colonnes_triees_numeriquement(raster):
    a = make_result("A", {seconds: (0.0, 0.9, 0.001 * index, 1.0)
                          for index, seconds in enumerate(THRESHOLDS_SECONDS, start=2)})
    matrix = matrix_table(long_table([a]), "superficie_km2")

    minutes = [int(column.split()[0]) for column in matrix.columns[1:]]
    assert minutes == sorted(minutes)
    assert minutes[-1] == 120


def test_matrice_vide_si_metrique_absente():
    assert matrix_table(long_table([]), "population_cumulee").empty


def test_indicateurs_de_synthese(raster):
    a = make_result("A", {600: (0.0, 0.9, 0.05, 1.0), 1200: (0.0, 0.9, 0.1, 1.0)})
    attach_population([a], raster)
    coverage = combined_coverage([a], [600, 1200], raster)

    indicators = summary_indicators([a], coverage)
    assert indicators["structures_analysees"] == 1
    assert indicators["seuil_max_min"] == 20
    assert indicators["population_couverte_max"] > 0


# --------------------------------------------------------------------------- #
# Découpage des requêtes de routage                                            #
# --------------------------------------------------------------------------- #


def test_douze_seuils_sont_decoupes_en_deux_requetes():
    """L'API publique n'accepte que 10 intervalles : 12 seuils = 2 requêtes."""
    batches = chunk_thresholds(THRESHOLDS_SECONDS, 10)

    assert len(batches) == 2
    assert len(batches[0]) == 10 and len(batches[1]) == 2
    assert [value for batch in batches for value in batch] == list(THRESHOLDS_SECONDS)


def test_decoupage_trie_et_dedoublonne():
    batches = chunk_thresholds([1200, 600, 1200, 1800], 2)
    assert batches == [[600, 1200], [1800]]


def test_capacites_publiques_refusent_au_dela_de_60_minutes():
    capabilities = public_capabilities()

    assert capabilities.supports("driving-car", 3600) is True
    assert capabilities.supports("driving-car", 4200) is False
    assert capabilities.unsupported("driving-car", THRESHOLDS_SECONDS) == [
        4200, 4800, 5400, 6000, 6600, 7200
    ]
    assert "auto-hébergée" in capabilities.explain("driving-car", 7200)


def test_capacites_publiques_acceptent_la_marche_jusqua_120_minutes():
    capabilities = public_capabilities()
    assert capabilities.unsupported("foot-walking", THRESHOLDS_SECONDS) == []
