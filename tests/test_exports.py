"""Test d'intégration : chaîne complète, puis validité des exports.

Le moteur de routage est remplacé par une session HTTP contrôlée et la
population par un raster fabriqué à valeurs connues : la chaîne est donc
vérifiée de bout en bout sans réseau, avec des résultats prévisibles.
"""

from __future__ import annotations

import json
import zipfile

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from src.config import EQUAL_AREA_CRS, METHODOLOGICAL_WARNINGS, WGS84
from src.exports import (
    filename,
    html_report,
    isochrones_geojson,
    isochrones_geopackage,
    long_csv,
    matrix_csv,
    metadata_json,
)
from src.isochrones import compute_all, to_geodataframe
from src.maps import catchment_map, territorial_map_html
from src.models import AnalysisMetadata, Facility
from src.population import inspect_raster
from src.routing import ORSClient, self_hosted_capabilities
from src.spatial_analysis import attach_population, combined_coverage, long_table

from tests.test_routing import FakeResponse, FakeSession, isochrone_payload

PIXEL = 0.01
THRESHOLDS = [600, 1200, 1800]


@pytest.fixture
def raster(tmp_path):
    path = tmp_path / "pop.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=40, width=40, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(-17.7, 14.9, PIXEL, PIXEL), nodata=-99999.0,
    ) as dataset:
        dataset.write(np.full((40, 40), 5.0, dtype="float32"), 1)
    return str(path)


@pytest.fixture
def pipeline(raster):
    """Deux structures voisines, zones volontairement chevauchantes."""
    facilities = [
        Facility(name="Hôpital régional", latitude=14.7, longitude=-17.5, identifier="a"),
        Facility(name="Centre de santé", latitude=14.71, longitude=-17.49, identifier="b"),
    ]
    session = FakeSession([
        FakeResponse(200, isochrone_payload(THRESHOLDS)),
        FakeResponse(200, isochrone_payload(THRESHOLDS)),
    ])
    client = ORSClient(
        base_url="https://ors.example.org", api_key="k", session=session,
        throttle_seconds=0.0, use_cache=False,
        capabilities=self_hosted_capabilities("https://ors.example.org"),
    )

    results = compute_all(facilities, client, "driving-car", THRESHOLDS)
    attach_population(results, raster, reference_population=8000.0)
    frame = long_table(results)
    coverage = combined_coverage(results, THRESHOLDS, raster, reference_population=8000.0)

    metadata = AnalysisMetadata(
        mode="Mode 2 — zone de desserte par structure",
        profile="driving-car",
        thresholds_seconds=THRESHOLDS,
        routing_engine="openrouteservice",
        routing_engine_version="9.0.0",
        routing_base_url="https://ors.example.org",
        population_source="WorldPop — test",
        population_year=2020,
        population_raster=inspect_raster(raster, year=2020).to_dict(),
        crs=f"{WGS84} (superficies en {EQUAL_AREA_CRS})",
        warnings=list(METHODOLOGICAL_WARNINGS),
        country_iso3="SEN",
        category="hospitals",
    )
    return results, frame, coverage, metadata


# --------------------------------------------------------------------------- #
# Chaîne complète                                                              #
# --------------------------------------------------------------------------- #


def test_chaine_complete_produit_des_valeurs_coherentes(pipeline):
    results, frame, coverage, _ = pipeline

    assert len(results) == 2
    assert len(frame) == 6  # 2 structures x 3 seuils
    assert frame["population_cumulee"].notna().all()
    assert (frame["population_intervalle"] >= 0).all()
    assert (frame["superficie_km2"] > 0).all()

    # L'union est strictement inférieure à la somme brute : zones chevauchantes.
    last = coverage.iloc[-1]
    assert last["population_union"] < last["population_somme_brute"]
    assert last["population_chevauchement"] > 0


def test_les_parts_de_population_sont_coherentes(pipeline):
    _, frame, _, _ = pipeline
    shares = frame["part_population_pct"].dropna()
    assert (shares > 0).all()
    assert (shares <= 100).all()


# --------------------------------------------------------------------------- #
# Exports tabulaires                                                           #
# --------------------------------------------------------------------------- #


def test_csv_long_contient_la_tracabilite(pipeline):
    _, frame, _, metadata = pipeline
    text = long_csv(frame, metadata).decode("utf-8-sig")
    header = text.splitlines()[0]

    for column in (
        "structure", "latitude", "longitude", "mode_deplacement", "seuil_min",
        "population_cumulee", "population_intervalle", "superficie_km2",
        "source_population", "annee_population", "moteur_routage",
        "version_moteur_routage", "date_calcul", "systeme_coordonnees", "avertissements",
    ):
        assert column in header

    assert "openrouteservice" in text
    assert "WorldPop" in text


def test_csv_matrice_une_colonne_par_seuil(pipeline):
    _, frame, _, metadata = pipeline
    lines = matrix_csv(frame, metadata, "population_cumulee").decode("utf-8-sig").splitlines()

    commented = [line for line in lines if line.startswith("#")]
    header = next(line for line in lines if not line.startswith("#"))

    assert any("metrique" in line for line in commented)
    assert any("date_calcul" in line for line in commented)
    assert header.split(",")[1:] == ["10 min", "20 min", "30 min"]
    assert len([line for line in lines if not line.startswith("#")]) == 3  # entête + 2 structures


def test_csv_matrice_par_intervalle(pipeline):
    _, frame, _, metadata = pipeline
    payload = matrix_csv(frame, metadata, "population_intervalle").decode("utf-8-sig")
    assert "population_intervalle" in payload


# --------------------------------------------------------------------------- #
# Exports géographiques                                                        #
# --------------------------------------------------------------------------- #


def test_geojson_valide_et_documente(pipeline):
    results, _, _, metadata = pipeline
    document = json.loads(isochrones_geojson(results, metadata).decode("utf-8"))

    assert document["type"] == "FeatureCollection"
    assert len(document["features"]) == 6
    assert document["metadata"]["moteur_routage"] == "openrouteservice"
    assert document["metadata"]["source_population"].startswith("WorldPop")
    assert document["metadata"]["avertissements_methodologiques"]

    properties = document["features"][0]["properties"]
    for key in ("structure", "seuil_minutes", "population_cumulee", "superficie_km2"):
        assert key in properties


def test_geojson_des_couronnes_est_distinct(pipeline):
    results, _, _, metadata = pipeline
    cumulative = json.loads(isochrones_geojson(results, metadata, geometry="cumulative"))
    rings = json.loads(isochrones_geojson(results, metadata, geometry="ring"))

    assert cumulative["features"][0]["properties"]["type_zone"] == "cumulée"
    assert rings["features"][0]["properties"]["type_zone"] == "couronne"


def test_geopackage_multicouches(pipeline, tmp_path):
    results, frame, _, metadata = pipeline
    payload = isochrones_geopackage(results, metadata, long_frame=frame)
    assert payload[:4] == b"SQLi"  # entête SQLite

    path = tmp_path / "sortie.gpkg"
    path.write_bytes(payload)

    layers = set(gpd.list_layers(path)["name"])
    assert {"isochrones_cumulees", "couronnes", "structures", "metadonnees"} <= layers

    zones = gpd.read_file(path, layer="isochrones_cumulees")
    assert len(zones) == 6
    assert zones.crs.to_string() == "EPSG:4326"


def test_geodataframe_des_isochrones(pipeline):
    results, _, _, _ = pipeline
    frame = to_geodataframe(results)

    assert frame.crs.to_string() == "EPSG:4326"
    assert frame.geometry.is_valid.all()
    assert set(frame["seuil_minutes"]) == {10, 20, 30}


# --------------------------------------------------------------------------- #
# Rapport et métadonnées                                                       #
# --------------------------------------------------------------------------- #


def test_rapport_html(pipeline):
    results, frame, coverage, metadata = pipeline
    failures = {"Structure hors réseau": {7200: "Seuil non calculable"}}
    html = html_report(frame, coverage, metadata, indicators={"structures_analysees": 2},
                       failures=failures).decode("utf-8")

    assert html.startswith("<!DOCTYPE html>")
    assert "Population cumulée par seuil" in html
    assert "Population par couronne" in html
    assert "Couverture combinée sans double comptage" in html
    assert "Limites méthodologiques" in html
    assert "Seuils non calculés" in html
    assert "openrouteservice" in html
    assert METHODOLOGICAL_WARNINGS[0][:40] in html


def test_metadonnees_json(pipeline):
    _, _, _, metadata = pipeline
    document = json.loads(metadata_json(metadata, {"indicateurs": {"a": 1}}))

    assert document["seuils_minutes"] == [10, 20, 30]
    assert document["systeme_coordonnees"].startswith("EPSG:4326")
    assert document["raster_population"]["unite_pixel"] == "personnes par pixel"
    assert document["indicateurs"] == {"a": 1}
    assert len(document["avertissements_methodologiques"]) == len(METHODOLOGICAL_WARNINGS)


def test_nom_de_fichier_horodate():
    name = filename("acces_long", "csv")
    assert name.startswith("acces_long_") and name.endswith(".csv")


# --------------------------------------------------------------------------- #
# Cartographie                                                                 #
# --------------------------------------------------------------------------- #


def test_carte_mode2_ne_contient_aucun_cercle(pipeline):
    results, _, _, _ = pipeline
    html = catchment_map(results, THRESHOLDS,
                         facilities=[result.facility for result in results]).get_root().render()

    assert "L.circle(" not in html
    assert "L.Circle(" not in html
    assert "Hôpital régional" in html
    assert "Population cumulée" in html


def test_carte_mode1_utilise_les_pmtiles_officiels():
    html = territorial_map_html("sen", "hospitals", [600, 1200])

    assert "sen_hospitals_isochrones.pmtiles" in html
    assert "pmtiles://" in html
    assert "get', 'range'" in html or "'range'" in html
    assert "≤ 10 min" in html


def test_carte_mode1_signale_une_source_indisponible():
    html = territorial_map_html("sen", "hospitals", [600])
    assert "Données HeiGIT indisponibles" in html
    assert "substitution" in html
