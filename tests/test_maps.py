"""Tests du composant cartographique du mode 1 (PMTiles HeiGIT).

Le script JavaScript du composant est exécuté dans un DOM et un réseau simulés
par ``tests/map_component_harness.js``. Objectif : garantir qu'aucune cause
d'échec ne laisse le bandeau « Chargement des PMTiles HeiGIT… » figé — c'était
le symptôme observé, et il masquait la cause réelle.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.config import THRESHOLDS_SECONDS
from src.maps import territorial_map_html

HARNESS = Path(__file__).parent / "map_component_harness.js"


@pytest.fixture(scope="module")
def html() -> str:
    return territorial_map_html("sen", "hospitals", [600, 1200, 1800])


# --------------------------------------------------------------------------- #
# Contenu du composant                                                         #
# --------------------------------------------------------------------------- #


def test_utilise_les_pmtiles_officiels(html):
    assert "sen_hospitals_isochrones.pmtiles" in html
    assert "pmtiles://" in html
    assert "hot.storage.heigit.org" in html


def test_les_douze_seuils_sont_transmis(html):
    config = json.loads(re.search(r"const CONFIG = (\{.*?\});", html, re.S).group(1))
    assert config["thresholds"] == list(THRESHOLDS_SECONDS)
    assert config["selected"] == [600, 1200, 1800]
    assert len(config["colours"]) == 12


def test_cdn_de_secours_configures(html):
    config = json.loads(re.search(r"const CONFIG = (\{.*?\});", html, re.S).group(1))
    for key in ("maplibreJs", "pmtilesJs", "maplibreCss"):
        assert len(config["cdn"][key]) >= 2, f"{key} doit avoir un CDN de secours"
        assert any("jsdelivr" in url for url in config["cdn"][key])


def test_style_maplibre_est_construit_inline(html):
    assert "function buildStyle()" in html
    assert "style: buildStyle()" in html
    assert "tiles.openfreemap.org" not in html


def test_chien_de_garde_present(html):
    """Sans chien de garde, un blocage réseau fige le message de chargement."""
    assert "Délai de 25 s dépassé" in html
    assert "unhandledrejection" in html
    assert "addEventListener('error'" in html


def test_message_derreur_sans_substitution(html):
    assert "Données HeiGIT indisponibles" in html
    assert "substitution" in html


def test_pas_de_cercle_de_substitution(html):
    assert "circle" not in html.lower().replace("navigationcontrol", "")


def test_legende_et_attribut_range(html):
    assert "≤ 10 min" in html
    assert "'range'" in html


# --------------------------------------------------------------------------- #
# Exécution du script dans un navigateur simulé                                #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js absent")
def test_aucun_scenario_ne_reste_bloque_sur_chargement(html, tmp_path):
    """Neuf scénarios couvrent succès, style local et erreurs explicites.

    Scénarios couverts : succès, style inline autonome, CDN injoignable, archive
    404, CORS refusé, chargement de carte bloqué, archive sans couche vectorielle,
    aucune entité au zoom courant et valeurs de ``range`` inattendues.
    """
    page = tmp_path / "map.html"
    page.write_text(html, encoding="utf-8")

    result = subprocess.run(
        ["node", str(HARNESS), str(page)],
        capture_output=True, text=True, timeout=180,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ÉCHEC" not in result.stdout
    assert result.stdout.count("OK ") == 9
