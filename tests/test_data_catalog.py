"""Tests du catalogue OpenAccessLens : URL, parsing YAML, statistiques cumulées."""

from __future__ import annotations

import pandas as pd
import pytest

from src.config import HEALTH_CATEGORIES, THRESHOLDS_SECONDS
from src.data_catalog import (
    CatalogError,
    add_interval_population,
    available_population_types,
    national_stats,
    parse_countries_yaml,
    pmtiles_url,
    stats_url,
)


# --------------------------------------------------------------------------- #
# URL                                                                          #
# --------------------------------------------------------------------------- #


def test_url_pmtiles_conforme_a_openaccesslens():
    assert pmtiles_url("SEN", "hospitals") == (
        "https://hot.storage.heigit.org/heigit-hdx-public/access/aux/tiles/"
        "sen/sen_hospitals_isochrones.pmtiles"
    )


def test_url_stats_conforme_a_openaccesslens():
    assert stats_url("sen", "primary_healthcare") == (
        "https://hot.storage.heigit.org/heigit-hdx-public/access/aux/stats/"
        "sen/category=primary_healthcare/data.parquet"
    )


def test_categories_de_sante():
    assert set(HEALTH_CATEGORIES) == {"hospitals", "primary_healthcare"}


# --------------------------------------------------------------------------- #
# Catalogue                                                                    #
# --------------------------------------------------------------------------- #


def test_parsing_yaml_indexe_par_iso3():
    text = """
sen:
  name: Senegal
ken:
  name: Kenya
"""
    countries = parse_countries_yaml(text)

    assert [country.iso3 for country in countries] == ["KEN", "SEN"]
    assert countries[1].code == "sen"
    assert countries[1].name == "Senegal"


def test_parsing_yaml_valeurs_scalaires():
    countries = parse_countries_yaml("sen: Senegal\ntcd: Chad\n")
    assert {country.name for country in countries} == {"Senegal", "Chad"}


def test_parsing_yaml_liste():
    text = """
- iso3: SEN
  name: Senegal
- iso3: KEN
  name: Kenya
"""
    countries = parse_countries_yaml(text)
    assert len(countries) == 2


def test_parsing_yaml_ignore_les_cles_non_iso3():
    countries = parse_countries_yaml("sen:\n  name: Senegal\nmetadata:\n  version: 1\n")
    assert len(countries) == 1


def test_parsing_yaml_vide_leve_une_erreur():
    with pytest.raises(CatalogError):
        parse_countries_yaml("metadata: {}\n")


def test_parsing_yaml_invalide_leve_une_erreur():
    with pytest.raises(CatalogError):
        parse_countries_yaml("sen: [non fermé\n")


# --------------------------------------------------------------------------- #
# Statistiques                                                                 #
# --------------------------------------------------------------------------- #


@pytest.fixture
def stats():
    return pd.DataFrame(
        {
            "admin_level": ["ADM0"] * 4 + ["ADM1"] * 2,
            "range": [600, 1200, 1800, 2400, 600, 1200],
            "population_type": ["total"] * 4 + ["total"] * 2,
            "population": [1000.0, 2500.0, 4000.0, 4200.0, 300.0, 700.0],
            "population_share": [10.0, 25.0, 40.0, 42.0, 3.0, 7.0],
        }
    )


def test_filtre_national(stats):
    national = national_stats(stats)
    assert len(national) == 4
    assert list(national["range"]) == [600, 1200, 1800, 2400]


def test_population_par_intervalle_depuis_le_cumul(stats):
    result = add_interval_population(national_stats(stats))

    assert list(result["population_interval"]) == [1000.0, 1500.0, 1500.0, 200.0]
    # La somme des couronnes reconstitue exactement le dernier cumul.
    assert result["population_interval"].sum() == pytest.approx(4200.0)


def test_population_par_intervalle_jamais_negative():
    frame = pd.DataFrame({"range": [600, 1200], "population": [1000.0, 950.0]})
    result = add_interval_population(frame)

    assert (result["population_interval"] >= 0).all()


def test_population_par_intervalle_sur_donnees_vides():
    result = add_interval_population(pd.DataFrame({"range": [], "population": []}))
    assert "population_interval" in result.columns


def test_groupes_demographiques_total_en_premier():
    frame = pd.DataFrame({"population_type": ["female", "total", "male", "total"]})
    assert available_population_types(frame)[0] == "total"


def test_seuils_correspondent_aux_valeurs_publiees():
    """Les 12 seuils santé de l'application sont ceux du jeu OpenAccessLens."""
    assert list(THRESHOLDS_SECONDS) == [
        600, 1200, 1800, 2400, 3000, 3600, 4200, 4800, 5400, 6000, 6600, 7200
    ]
