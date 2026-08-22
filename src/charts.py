"""Graphiques Plotly.

Chaque figure distingue explicitement la **population cumulée** (zone ≤ seuil)
de la **population par intervalle** (couronne), afin qu'aucune lecture ne
conduise à additionner des cumuls.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from .config import ISOCHRONE_COLORS_HEALTH, color_for_threshold

EMPTY_MESSAGE = "Aucune donnée à représenter pour la sélection courante."


def _empty_figure(message: str = EMPTY_MESSAGE) -> go.Figure:
    figure = go.Figure()
    figure.add_annotation(
        text=message, showarrow=False, font=dict(size=13, color="#666"),
        xref="paper", yref="paper", x=0.5, y=0.5,
    )
    figure.update_layout(
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        height=320, margin=dict(l=10, r=10, t=30, b=10),
    )
    return figure


def cumulative_curve(long_frame: pd.DataFrame) -> go.Figure:
    """Courbe de population cumulée par seuil, une ligne par structure."""
    if long_frame.empty or long_frame["population_cumulee"].isna().all():
        return _empty_figure("Population non calculée : aucun raster WorldPop chargé.")

    figure = px.line(
        long_frame.dropna(subset=["population_cumulee"]),
        x="seuil_min",
        y="population_cumulee",
        color="structure",
        markers=True,
        labels={
            "seuil_min": "Temps de trajet (minutes)",
            "population_cumulee": "Population cumulée",
            "structure": "Structure",
        },
    )
    figure.update_layout(
        title="Population cumulée pouvant atteindre la structure",
        hovermode="x unified",
        height=420,
        margin=dict(l=10, r=10, t=50, b=10),
    )
    figure.update_xaxes(dtick=10)
    return figure


def _with_interval_labels(frame: pd.DataFrame) -> pd.DataFrame:
    """Ajoute les bornes des couronnes sans supposer un pas de temps fixe."""
    labelled = frame.copy()
    if "seuil_precedent_min" not in labelled.columns:
        # Compatibilité avec un tableau externe ancien : la borne est le seuil
        # précédent réellement présent pour la même structure, jamais ``seuil-10``.
        labelled = labelled.sort_values(["structure", "seuil_min"])
        labelled["seuil_precedent_min"] = (
            labelled.groupby("structure")["seuil_min"].shift(fill_value=0)
        )
    labelled["intervalle"] = labelled.apply(
        lambda row: (
            f"{int(row['seuil_precedent_min'])}–{int(row['seuil_min'])} min"
        ),
        axis=1,
    )
    return labelled


def interval_histogram(long_frame: pd.DataFrame) -> go.Figure:
    """Histogramme de la population propre à chaque couronne réelle."""
    if long_frame.empty or long_frame["population_intervalle"].isna().all():
        return _empty_figure("Population par intervalle non calculée.")

    frame = _with_interval_labels(
        long_frame.dropna(subset=["population_intervalle"])
    )

    figure = px.bar(
        frame,
        x="intervalle",
        y="population_intervalle",
        color="structure",
        barmode="group",
        labels={
            "intervalle": "Couronne de temps de trajet",
            "population_intervalle": "Population de la couronne",
            "structure": "Structure",
        },
    )
    figure.update_layout(
        title="Population située dans chaque couronne (sans double comptage interne)",
        height=420,
        margin=dict(l=10, r=10, t=50, b=10),
    )
    return figure


def cumulative_versus_interval(long_frame: pd.DataFrame, structure: str) -> go.Figure:
    """Comparaison explicite cumul / couronne pour une structure."""
    frame = long_frame[long_frame["structure"] == structure].sort_values("seuil_min")
    if frame.empty or frame["population_cumulee"].isna().all():
        return _empty_figure()
    frame = _with_interval_labels(frame)

    figure = go.Figure()
    figure.add_bar(
        x=frame["seuil_min"],
        y=frame["population_intervalle"],
        name="Population de la couronne",
        customdata=frame["intervalle"],
        hovertemplate="Couronne %{customdata}<br>Population %{y:,.0f}<extra></extra>",
        marker_color=[color_for_threshold(int(value) * 60) for value in frame["seuil_min"]],
    )
    figure.add_scatter(
        x=frame["seuil_min"],
        y=frame["population_cumulee"],
        name="Population cumulée",
        mode="lines+markers",
        line=dict(color="#111111", width=2),
        yaxis="y",
    )
    figure.update_layout(
        title=f"{structure} — cumul et couronnes",
        xaxis_title="Temps de trajet (minutes)",
        yaxis_title="Population",
        height=420,
        hovermode="x unified",
        margin=dict(l=10, r=10, t=50, b=10),
    )
    figure.update_xaxes(dtick=10)
    return figure


def facility_comparison(long_frame: pd.DataFrame, threshold_minutes: int) -> go.Figure:
    """Classement des structures à un seuil donné."""
    frame = long_frame[long_frame["seuil_min"] == threshold_minutes]
    frame = frame.dropna(subset=["population_cumulee"]).sort_values("population_cumulee")
    if frame.empty:
        return _empty_figure()

    figure = px.bar(
        frame,
        x="population_cumulee",
        y="structure",
        orientation="h",
        labels={
            "population_cumulee": f"Population accessible en ≤ {threshold_minutes} min",
            "structure": "",
        },
        color="population_cumulee",
        color_continuous_scale="Viridis",
    )
    figure.update_layout(
        title=f"Comparaison des structures à ≤ {threshold_minutes} minutes",
        height=max(320, 42 * len(frame)),
        coloraxis_showscale=False,
        margin=dict(l=10, r=10, t=50, b=10),
    )
    return figure


def coverage_chart(coverage: pd.DataFrame) -> go.Figure:
    """Somme brute, union réelle et chevauchement, par seuil."""
    if coverage.empty or coverage["population_union"].isna().all():
        return _empty_figure("Couverture combinée non calculée.")

    figure = go.Figure()
    figure.add_bar(
        x=coverage["seuil_min"],
        y=coverage["population_somme_brute"],
        name="Somme brute par structure (surestime)",
        marker_color="#cbd5e1",
    )
    figure.add_bar(
        x=coverage["seuil_min"],
        y=coverage["population_union"],
        name="Population réellement couverte (union)",
        marker_color="#2ab07f",
    )
    figure.add_scatter(
        x=coverage["seuil_min"],
        y=coverage["population_chevauchement"],
        name="Population en chevauchement",
        mode="lines+markers",
        line=dict(color="#c1121f", width=2, dash="dot"),
    )
    figure.update_layout(
        title="Couverture combinée : le double comptage rendu visible",
        xaxis_title="Temps de trajet (minutes)",
        yaxis_title="Population",
        barmode="group",
        height=430,
        hovermode="x unified",
        margin=dict(l=10, r=10, t=50, b=10),
    )
    figure.update_xaxes(dtick=10)
    return figure


def demographic_profile_chart(profile: dict[str, float], title: str) -> go.Figure:
    """Pyramide des âges à partir des rasters WorldPop par sexe et âge."""
    if not profile:
        return _empty_figure("Profil démographique non calculé.")

    records = []
    for label, value in profile.items():
        parts = label.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        sex, age = parts
        records.append({"sexe": sex, "age": int(age), "population": value})

    if not records:
        return _empty_figure("Profil démographique illisible.")

    frame = pd.DataFrame(records).sort_values("age")
    frame["valeur"] = frame.apply(
        lambda row: -row["population"] if row["sexe"].startswith("Homme") else row["population"],
        axis=1,
    )
    frame["tranche"] = frame["age"].astype(str) + " ans"

    figure = px.bar(
        frame,
        x="valeur",
        y="tranche",
        color="sexe",
        orientation="h",
        labels={"valeur": "Population", "tranche": "Groupe d'âge"},
        color_discrete_map={"Hommes": "#2d708e", "Femmes": "#c2df23"},
    )
    figure.update_layout(
        title=f"Profil démographique WorldPop — {title}",
        height=560,
        margin=dict(l=10, r=10, t=50, b=10),
    )
    figure.update_xaxes(tickformat="~s")
    return figure


def territorial_stats_chart(stats: pd.DataFrame, population_type: str) -> go.Figure:
    """Statistiques nationales OpenAccessLens : cumul et couronnes."""
    if stats.empty:
        return _empty_figure("Statistiques OpenAccessLens indisponibles.")

    figure = go.Figure()
    figure.add_bar(
        x=stats["range"] / 60,
        y=stats.get("population_interval"),
        name="Population de la couronne",
        marker_color=[
            color_for_threshold(int(value)) for value in stats["range"].fillna(0)
        ],
    )
    figure.add_scatter(
        x=stats["range"] / 60,
        y=stats["population"],
        name="Population cumulée (publiée)",
        mode="lines+markers",
        line=dict(color="#111111", width=2),
    )
    figure.update_layout(
        title=f"Accessibilité territoriale publiée par HeiGIT — {population_type}",
        xaxis_title="Temps de trajet vers le service (minutes)",
        yaxis_title="Population",
        height=430,
        hovermode="x unified",
        margin=dict(l=10, r=10, t=50, b=10),
    )
    figure.update_xaxes(dtick=10)
    return figure


def area_chart(long_frame: pd.DataFrame) -> go.Figure:
    """Superficie cumulée par seuil."""
    if long_frame.empty or long_frame["superficie_km2"].isna().all():
        return _empty_figure("Superficies non calculées.")

    figure = px.line(
        long_frame.dropna(subset=["superficie_km2"]),
        x="seuil_min",
        y="superficie_km2",
        color="structure",
        markers=True,
        labels={
            "seuil_min": "Temps de trajet (minutes)",
            "superficie_km2": "Superficie cumulée (km²)",
        },
    )
    figure.update_layout(
        title="Extension spatiale de la zone de desserte",
        height=380,
        hovermode="x unified",
        margin=dict(l=10, r=10, t=50, b=10),
    )
    figure.update_xaxes(dtick=10)
    return figure


def threshold_palette_preview(selected: Sequence[int]) -> go.Figure:
    """Aperçu de la palette pour les seuils sélectionnés."""
    ordered = sorted(selected)
    figure = go.Figure(
        go.Bar(
            x=[1] * len(ordered),
            y=[f"≤ {value // 60} min" for value in ordered],
            orientation="h",
            marker_color=[color_for_threshold(value) for value in ordered],
            showlegend=False,
        )
    )
    figure.update_layout(
        height=max(160, 26 * len(ordered)),
        xaxis=dict(visible=False),
        margin=dict(l=10, r=10, t=10, b=10),
        colorway=list(ISOCHRONE_COLORS_HEALTH),
    )
    return figure
