"""Parcours Streamlit de bout en bout avec moteur de routage contrôlé."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from streamlit.testing.v1 import AppTest

import src.data_catalog as data_catalog
from src.models import Facility
from src.population import inspect_raster, raster_total
from src.routing import valhalla_capabilities

MODE_2 = "Mode 2 — Zone de desserte par structure"


class MockRoutingClient:
    """Moteur déterministe injecté uniquement par l'état privé d'AppTest."""

    capabilities = valhalla_capabilities("https://valhalla.test/isochrone")

    def isochrones(
        self, coordinates, profile, thresholds_seconds, *, smoothing=None
    ):
        del profile, smoothing
        longitude, latitude = coordinates
        features = {}
        for index, seconds in enumerate(sorted(thresholds_seconds), start=1):
            size = 0.015 * index
            features[int(seconds)] = {
                "type": "Feature",
                "properties": {"contour": seconds / 60},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [longitude - size, latitude - size],
                        [longitude + size, latitude - size],
                        [longitude + size, latitude + size],
                        [longitude - size, latitude + size],
                        [longitude - size, latitude - size],
                    ]],
                },
            }
        return features, {}

    def engine_info(self):
        return {
            "engine": "valhalla",
            "version": "test-double",
            "base_url": "https://valhalla.test/isochrone",
            "location_type": "destination (reverse=true)",
            "direction": "towards_location",
        }


def _app(monkeypatch) -> AppTest:
    # Le catalogue n'est pas l'objet de ces tests et ne doit jamais toucher le réseau.
    monkeypatch.setattr(data_catalog, "fetch_countries", lambda: [])
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=60)
    app.session_state["analysis_mode"] = MODE_2
    app.session_state["selected_minutes"] = [10, 30, 60]
    return app


def _raster(tmp_path):
    path = tmp_path / "app_population.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=100,
        width=100,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-18.0, 15.2, 0.01, 0.01),
        nodata=-99999.0,
    ) as dataset:
        dataset.write(np.full((100, 100), 2.0, dtype="float32"), 1)
    return path


def test_analyse_complete_affiche_resultats_graphiques_et_exports(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("ORS_API_KEY", raising=False)
    monkeypatch.delenv("VALHALLA_ENABLED", raising=False)
    app = _app(monkeypatch)

    facility = Facility(
        name="Hôpital AppTest",
        latitude=14.7,
        longitude=-17.5,
        identifier="apptest",
        source="test",
    )
    raster = _raster(tmp_path)
    metadata = inspect_raster(str(raster), source="WorldPop test", year=2020)
    app.session_state["facilities"] = [facility]
    app.session_state["selected_ids"] = {facility.identifier}
    app.session_state["_routing_client"] = MockRoutingClient()
    app.session_state["raster_path"] = str(raster)
    app.session_state["raster_metadata"] = metadata
    app.session_state["reference_population"] = raster_total(str(raster))

    app.run()
    launch = next(button for button in app.button if button.label == "▶️ Lancer l'analyse")
    assert launch.disabled is False
    launch.click().run()

    assert not app.exception
    assert any(title.value.startswith("Mode 2") for title in app.title)
    assert any(metric.label == "Structures analysées" for metric in app.metric)

    frame = app.session_state["long_frame"]
    assert len(frame) == 3
    assert list(frame["seuil_precedent_min"]) == [0, 10, 30]
    assert frame["population_cumulee"].notna().all()
    assert app.session_state["metadata"].routing_engine == "valhalla"
    assert len(app.get("plotly_chart")) >= 5

    labels = {button.label for button in app.download_button}
    assert {
        "⬇️ CSV — format long",
        "⬇️ CSV — format matrice",
        "⬇️ Métadonnées (JSON)",
        "⬇️ GeoJSON — zones cumulées",
        "⬇️ GeoJSON — couronnes",
        "⬇️ Rapport HTML (imprimable en PDF)",
    } <= labels


def test_aucun_moteur_desactive_explicitement_le_lancement(monkeypatch):
    monkeypatch.setenv("VALHALLA_ENABLED", "false")
    monkeypatch.delenv("ORS_API_KEY", raising=False)
    app = _app(monkeypatch)
    facility = Facility(
        name="Hôpital sans moteur",
        latitude=14.7,
        longitude=-17.5,
        identifier="sans-moteur",
    )
    app.session_state["facilities"] = [facility]
    app.session_state["selected_ids"] = {facility.identifier}

    app.run()

    assert not app.exception
    messages = " ".join(element.value for element in app.error)
    assert "Aucun moteur de routage utilisable" in messages
    launch = next(button for button in app.button if button.label == "▶️ Lancer l'analyse")
    assert launch.disabled is True
    assert app.session_state["results"] is None
