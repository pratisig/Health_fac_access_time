"""Tests du moteur de routage : échecs, quotas, capacités, absence de substitut."""

from __future__ import annotations

import json

import pytest
import requests

from src.config import THRESHOLDS_SECONDS
from src.isochrones import compute_facility_isochrones
from src.models import Facility
from src.routing import (
    ORSClient,
    RoutingError,
    RoutingQuotaError,
    public_capabilities,
    self_hosted_capabilities,
)


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("réponse non JSON")
        return self._payload


class FakeSession:
    """Session HTTP contrôlée, pour tester sans réseau."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        self.calls.append({"url": url, "payload": json})
        if not self.responses:
            raise AssertionError("Aucune réponse simulée restante")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def isochrone_payload(ranges):
    """Réponse GeoJSON minimale d'openrouteservice, un carré par seuil."""
    features = []
    for index, seconds in enumerate(sorted(ranges), start=1):
        size = 0.01 * index
        features.append(
            {
                "type": "Feature",
                "properties": {"value": float(seconds), "group_index": 0},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-17.5 - size, 14.7 - size], [-17.5 + size, 14.7 - size],
                        [-17.5 + size, 14.7 + size], [-17.5 - size, 14.7 + size],
                        [-17.5 - size, 14.7 - size],
                    ]],
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {"engine": {"version": "9.0.0", "build_date": "2025-01-01"}},
    }


@pytest.fixture
def facility():
    return Facility(name="Hôpital test", latitude=14.7, longitude=-17.5)


def client_with(responses, **kwargs):
    return ORSClient(
        base_url="https://ors.example.org",
        api_key="clé-de-test",
        session=FakeSession(responses),
        throttle_seconds=0.0,
        use_cache=False,
        capabilities=kwargs.pop("capabilities", self_hosted_capabilities("https://ors.example.org")),
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Cas nominal                                                                  #
# --------------------------------------------------------------------------- #


def test_calcul_nominal_produit_des_zones_emboitees(facility):
    thresholds = [600, 1200, 1800]
    client = client_with([FakeResponse(200, isochrone_payload(thresholds))])

    result = compute_facility_isochrones(facility, client, "driving-car", thresholds)

    assert len(result.bands) == 3
    assert result.failed_thresholds == {}
    assert result.engine_version == "9.0.0"

    bands = sorted(result.bands, key=lambda band: band.threshold_seconds)
    for previous, current in zip(bands, bands[1:]):
        assert current.geometry.contains(previous.geometry)
        assert current.ring_geometry.area > 0


def test_le_sens_du_calcul_est_vers_la_structure(facility):
    """« Qui peut atteindre la structure » impose location_type=destination."""
    session = FakeSession([FakeResponse(200, isochrone_payload([600]))])
    client = ORSClient(
        base_url="https://ors.example.org", api_key="k", session=session,
        throttle_seconds=0.0, use_cache=False,
    )
    client.isochrones([-17.5, 14.7], "driving-car", [600])

    assert session.calls[0]["payload"]["location_type"] == "destination"
    assert session.calls[0]["payload"]["range_type"] == "time"
    assert session.calls[0]["payload"]["locations"] == [[-17.5, 14.7]]


def test_les_douze_seuils_sont_repartis_sur_deux_requetes(facility):
    session = FakeSession([
        FakeResponse(200, isochrone_payload(THRESHOLDS_SECONDS[:10])),
        FakeResponse(200, isochrone_payload(THRESHOLDS_SECONDS[10:])),
    ])
    client = ORSClient(
        base_url="https://ors.example.org", api_key="k", session=session,
        throttle_seconds=0.0, use_cache=False,
        capabilities=self_hosted_capabilities("https://ors.example.org"),
    )
    features, failures = client.isochrones([-17.5, 14.7], "driving-car", THRESHOLDS_SECONDS)

    assert len(session.calls) == 2
    assert len(features) == 12
    assert failures == {}


# --------------------------------------------------------------------------- #
# Échecs du moteur                                                             #
# --------------------------------------------------------------------------- #


def test_echec_reseau_est_consigne_sans_geometrie_de_substitution(facility):
    client = client_with([requests.ConnectionError("réseau coupé")])

    result = compute_facility_isochrones(facility, client, "driving-car", [600, 1200])

    assert result.bands == []
    assert result.succeeded is False
    assert set(result.failed_thresholds) == {600, 1200}
    assert "injoignable" in result.failed_thresholds[600]


def test_quota_atteint(facility):
    client = client_with([FakeResponse(429, {"error": "quota"})])

    with pytest.raises(RoutingQuotaError, match="Quota"):
        client.session.responses = [FakeResponse(429, {"error": "quota"})]
        client._request("driving-car", {"locations": [[0, 0]], "range": [600]})


def test_quota_est_consigne_par_seuil(facility):
    client = client_with([FakeResponse(429, {"error": "quota"})])
    result = compute_facility_isochrones(facility, client, "driving-car", [600])

    assert result.bands == []
    assert "Quota" in result.failed_thresholds[600]


def test_cle_invalide(facility):
    client = client_with([FakeResponse(401, {"error": "clé invalide"})])
    result = compute_facility_isochrones(facility, client, "driving-car", [600])

    assert "ORS_API_KEY" in result.failed_thresholds[600]


def test_erreur_3004_est_expliquee(facility):
    payload = {"error": {"code": 3004,
                         "message": "Parameter 'range=7200.0' is out of range. Maximum possible value is 3600."}}
    client = client_with([FakeResponse(400, payload)])
    result = compute_facility_isochrones(facility, client, "driving-car", [7200])

    assert "auto-hébergée" in result.failed_thresholds[7200]


def test_absence_de_cle_sur_instance_publique():
    client = ORSClient(
        base_url="https://api.openrouteservice.org", api_key=None,
        session=FakeSession([]), throttle_seconds=0.0, use_cache=False,
    )
    with pytest.raises(RoutingError, match="ORS_API_KEY"):
        client._request("driving-car", {"locations": [[0, 0]], "range": [600]})


def test_seuil_hors_capacite_publique_nest_pas_requete(facility):
    """Un seuil > 60 min en voiture n'est pas envoyé : il est refusé en amont."""
    session = FakeSession([FakeResponse(200, isochrone_payload([600, 3600]))])
    client = ORSClient(
        base_url="https://api.openrouteservice.org", api_key="k", session=session,
        throttle_seconds=0.0, use_cache=False, capabilities=public_capabilities(),
    )
    features, failures = client.isochrones([-17.5, 14.7], "driving-car", [600, 3600, 7200])

    assert set(features) == {600, 3600}
    assert 7200 in failures
    assert "plafonne" in failures[7200]
    assert 7200 not in session.calls[0]["payload"]["range"]


def test_reponse_partielle_est_signalee(facility):
    """Un seuil absent de la réponse est consigné, pas comblé."""
    client = client_with([FakeResponse(200, isochrone_payload([600]))])
    result = compute_facility_isochrones(facility, client, "driving-car", [600, 1200])

    assert [band.threshold_seconds for band in result.bands] == [600]
    assert 1200 in result.failed_thresholds


def test_reponse_non_json(facility):
    client = client_with([FakeResponse(200, None, text="<html>maintenance</html>")])
    result = compute_facility_isochrones(facility, client, "driving-car", [600])

    assert result.bands == []
    assert "illisible" in result.failed_thresholds[600]


def test_geometrie_vide_est_rejetee(facility):
    payload = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"value": 600.0}, "geometry": None}],
        "metadata": {"engine": {"version": "9.0.0"}},
    }
    client = client_with([FakeResponse(200, payload)])
    result = compute_facility_isochrones(facility, client, "driving-car", [600])

    assert result.bands == []
    assert 600 in result.failed_thresholds


def test_une_structure_en_echec_ninterrompt_pas_les_autres():
    from src.isochrones import compute_all

    facilities = [
        Facility(name="A", latitude=14.7, longitude=-17.5, identifier="a"),
        Facility(name="B", latitude=14.8, longitude=-17.4, identifier="b"),
    ]
    client = client_with([
        requests.ConnectionError("réseau coupé"),
        FakeResponse(200, isochrone_payload([600])),
    ])

    results = compute_all(facilities, client, "driving-car", [600])

    assert len(results) == 2
    assert results[0].succeeded is False
    assert results[1].succeeded is True


def test_annulation_interrompt_le_lot():
    from src.isochrones import compute_all

    facilities = [
        Facility(name=str(index), latitude=14.7, longitude=-17.5, identifier=str(index))
        for index in range(4)
    ]
    client = client_with([FakeResponse(200, isochrone_payload([600]))])

    results = compute_all(
        facilities, client, "driving-car", [600],
        should_stop=lambda: len(client.session.calls) >= 1,
    )
    assert len(results) < len(facilities)


# --------------------------------------------------------------------------- #
# Capacités                                                                    #
# --------------------------------------------------------------------------- #


def test_instance_auto_hebergee_ne_presume_aucune_limite():
    capabilities = self_hosted_capabilities("https://ors.interne")
    assert capabilities.supports("driving-car", 7200) is True
    assert capabilities.unsupported("driving-car", THRESHOLDS_SECONDS) == []


def test_capacites_publiques_par_profil():
    capabilities = public_capabilities()
    assert capabilities.max_range_for("driving-car") == 3600
    assert capabilities.max_range_for("foot-walking") == 72000
    assert capabilities.max_intervals == 10
    assert capabilities.max_locations == 5
