"""Régressions des libellés de couronnes dans les graphiques Plotly."""

from __future__ import annotations

import pandas as pd

from src.charts import cumulative_versus_interval, interval_histogram


def irregular_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "structure": ["Hôpital test"] * 3,
            "seuil_precedent_min": [0, 10, 30],
            "seuil_min": [10, 30, 60],
            "population_cumulee": [100.0, 250.0, 400.0],
            "population_intervalle": [100.0, 150.0, 150.0],
        }
    )


def test_histogramme_utilise_les_bornes_reelles():
    figure = interval_histogram(irregular_frame())

    assert list(figure.data[0].x) == ["0–10 min", "10–30 min", "30–60 min"]
    assert "Couronne de temps de trajet" in figure.layout.xaxis.title.text


def test_infobulle_cumul_intervalle_utilise_les_bornes_reelles():
    figure = cumulative_versus_interval(irregular_frame(), "Hôpital test")
    bars = figure.data[0]

    assert list(bars.customdata) == ["0–10 min", "10–30 min", "30–60 min"]
    assert "Couronne %{customdata}" in bars.hovertemplate


def test_compatibilite_table_externe_derive_le_seuil_precedent_reel():
    legacy = irregular_frame().drop(columns="seuil_precedent_min")
    figure = interval_histogram(legacy)

    assert list(figure.data[0].x) == ["0–10 min", "10–30 min", "30–60 min"]
