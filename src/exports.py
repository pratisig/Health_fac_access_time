"""Exports traçables : CSV, GeoJSON, GeoPackage, rapport HTML, métadonnées.

Chaque export embarque la traçabilité complète du calcul (structure,
coordonnées, mode de déplacement, seuil, populations, superficie, source
démographique, année, moteur et version de routage, date, CRS, avertissements
méthodologiques), afin qu'un fichier puisse être relu et critiqué hors de
l'application.
"""

from __future__ import annotations

import io
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import geopandas as gpd
import pandas as pd

from .config import WGS84
from .isochrones import to_geodataframe
from .models import AnalysisMetadata, FacilityIsochrones
from .spatial_analysis import matrix_table


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _decorate(frame: pd.DataFrame, metadata: AnalysisMetadata) -> pd.DataFrame:
    """Ajoute les colonnes de traçabilité communes à tous les exports tabulaires."""
    result = frame.copy()
    result["source_population"] = metadata.population_source
    result["annee_population"] = metadata.population_year
    result["moteur_routage"] = metadata.routing_engine
    result["version_moteur_routage"] = metadata.routing_engine_version
    result["url_moteur_routage"] = metadata.routing_base_url
    result["date_calcul"] = metadata.computed_at
    result["systeme_coordonnees"] = metadata.crs
    result["mode_analyse"] = metadata.mode
    result["avertissements"] = " | ".join(metadata.warnings)
    return result


# --------------------------------------------------------------------------- #
# CSV                                                                          #
# --------------------------------------------------------------------------- #


def long_csv(long_frame: pd.DataFrame, metadata: AnalysisMetadata) -> bytes:
    """CSV au format long : une ligne par structure et par seuil."""
    return _decorate(long_frame, metadata).to_csv(index=False).encode("utf-8-sig")


def matrix_csv(
    long_frame: pd.DataFrame,
    metadata: AnalysisMetadata,
    metric: str = "population_cumulee",
) -> bytes:
    """CSV au format matrice : une ligne par structure, une colonne par seuil."""
    matrix = matrix_table(long_frame, metric)
    if matrix.empty:
        return b""

    buffer = io.StringIO()
    buffer.write(f"# metrique,{metric}\n")
    buffer.write(f"# mode_analyse,{metadata.mode}\n")
    buffer.write(f"# mode_deplacement,{metadata.profile}\n")
    buffer.write(f"# source_population,{metadata.population_source}\n")
    buffer.write(f"# annee_population,{metadata.population_year}\n")
    buffer.write(f"# moteur_routage,{metadata.routing_engine} {metadata.routing_engine_version}\n")
    buffer.write(f"# date_calcul,{metadata.computed_at}\n")
    buffer.write(f"# systeme_coordonnees,{metadata.crs}\n")
    matrix.to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8-sig")


# --------------------------------------------------------------------------- #
# Géométries                                                                   #
# --------------------------------------------------------------------------- #


def isochrones_geojson(
    results: Sequence[FacilityIsochrones],
    metadata: AnalysisMetadata,
    *,
    geometry: str = "cumulative",
) -> bytes:
    """GeoJSON des isochrones, enrichi des métadonnées du calcul."""
    frame = to_geodataframe(results, geometry=geometry)
    if frame.empty:
        return json.dumps(
            {"type": "FeatureCollection", "features": [], "metadata": metadata.to_dict()},
            ensure_ascii=False,
        ).encode("utf-8")

    document = json.loads(frame.to_json(na="null"))
    document["metadata"] = metadata.to_dict()
    document["crs"] = {
        "type": "name",
        "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
    }
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def isochrones_geopackage(
    results: Sequence[FacilityIsochrones],
    metadata: AnalysisMetadata,
    *,
    long_frame: pd.DataFrame | None = None,
) -> bytes:
    """GeoPackage multi-couches : zones cumulées, couronnes, structures, métadonnées."""
    cumulative = to_geodataframe(results, geometry="cumulative")
    rings = to_geodataframe(results, geometry="ring")

    facilities = gpd.GeoDataFrame(
        pd.DataFrame([result.facility.to_dict() for result in results]),
        geometry=[result.facility.geometry for result in results],
        crs=WGS84,
    ) if results else None

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / f"isochrones_{_stamp()}.gpkg"

        if not cumulative.empty:
            cumulative.to_file(path, layer="isochrones_cumulees", driver="GPKG")
        if not rings.empty:
            rings.to_file(path, layer="couronnes", driver="GPKG")
        if facilities is not None and not facilities.empty:
            facilities.to_file(path, layer="structures", driver="GPKG")
        if long_frame is not None and not long_frame.empty:
            # Table attributaire non spatiale, portée par une couche vide de géométrie.
            gpd.GeoDataFrame(
                long_frame.copy(), geometry=[None] * len(long_frame), crs=WGS84
            ).to_file(path, layer="tableau_long", driver="GPKG")

        metadata_frame = pd.DataFrame(
            [{"cle": key, "valeur": json.dumps(value, ensure_ascii=False, default=str)}
             for key, value in metadata.to_dict().items()]
        )
        gpd.GeoDataFrame(
            metadata_frame, geometry=[None] * len(metadata_frame), crs=WGS84
        ).to_file(path, layer="metadonnees", driver="GPKG")

        if not path.exists():
            return b""
        return path.read_bytes()


# --------------------------------------------------------------------------- #
# Métadonnées                                                                  #
# --------------------------------------------------------------------------- #


def metadata_json(metadata: AnalysisMetadata, extra: dict[str, Any] | None = None) -> bytes:
    """Métadonnées complètes du calcul, au format JSON."""
    document = metadata.to_dict()
    if extra:
        document.update(extra)
    return json.dumps(document, ensure_ascii=False, indent=2, default=str).encode("utf-8")


# --------------------------------------------------------------------------- #
# Rapport HTML                                                                 #
# --------------------------------------------------------------------------- #


def _table_html(frame: pd.DataFrame, limit: int = 400) -> str:
    if frame.empty:
        return "<p><em>Aucune donnée.</em></p>"
    return frame.head(limit).to_html(
        index=False, float_format=lambda value: f"{value:,.1f}".replace(",", " "),
        classes="data", border=0, na_rep="—",
    )


def html_report(
    long_frame: pd.DataFrame,
    coverage: pd.DataFrame,
    metadata: AnalysisMetadata,
    *,
    indicators: dict[str, Any] | None = None,
    failures: dict[str, dict[int, str]] | None = None,
) -> bytes:
    """Rapport HTML autonome, imprimable en PDF depuis le navigateur.

    Le HTML est préféré au PDF généré côté serveur : il évite une dépendance
    lourde (moteur de rendu), reste lisible partout et s'imprime en PDF via la
    fonction d'impression du navigateur.
    """
    matrix_cumulative = matrix_table(long_frame, "population_cumulee")
    matrix_interval = matrix_table(long_frame, "population_intervalle")

    indicator_html = ""
    if indicators:
        indicator_html = "".join(
            f'<div class="card"><span class="value">'
            f'{value:,.0f}</span><span class="label">{key.replace("_", " ")}</span></div>'.replace(",", " ")
            if isinstance(value, (int, float))
            else f'<div class="card"><span class="value">{value}</span>'
                 f'<span class="label">{key.replace("_", " ")}</span></div>'
            for key, value in indicators.items()
        )

    failure_html = ""
    if failures:
        rows = "".join(
            f"<li><strong>{name}</strong> — {seconds // 60} min : {reason}</li>"
            for name, items in failures.items()
            for seconds, reason in sorted(items.items())
        )
        if rows:
            failure_html = (
                "<h2>Seuils non calculés</h2>"
                "<p>Ces seuils sont absents des résultats. Aucune valeur de "
                "substitution n'a été produite.</p>"
                f"<ul class='failures'>{rows}</ul>"
            )

    raster_html = "<p><em>Aucun raster de population utilisé.</em></p>"
    if metadata.population_raster:
        raster_html = _table_html(
            pd.DataFrame(
                [{"propriété": key, "valeur": str(value)}
                 for key, value in metadata.population_raster.items()]
            )
        )

    warnings_html = "".join(f"<li>{item}</li>" for item in metadata.warnings)

    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8" />
<title>Accessibilité aux structures de santé — {metadata.computed_at[:10]}</title>
<style>
 body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 0 auto; max-width: 1080px;
        padding: 32px 24px; color: #1a1a1a; line-height: 1.55; }}
 h1 {{ font-size: 24px; margin-bottom: 4px; }}
 h2 {{ font-size: 17px; margin-top: 34px; border-bottom: 2px solid #eee; padding-bottom: 6px; }}
 .subtitle {{ color: #666; font-size: 13px; margin-top: 0; }}
 .cards {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 18px 0; }}
 .card {{ background: #f6f8fa; border-radius: 8px; padding: 12px 16px; min-width: 150px; }}
 .card .value {{ display: block; font-size: 21px; font-weight: 650; }}
 .card .label {{ display: block; font-size: 11px; color: #666; text-transform: capitalize; }}
 table.data {{ border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 8px; }}
 table.data th, table.data td {{ border-bottom: 1px solid #e5e5e5; padding: 6px 8px; text-align: right; }}
 table.data th:first-child, table.data td:first-child {{ text-align: left; }}
 table.data thead th {{ background: #f0f2f5; }}
 .warn {{ background: #fff8e6; border-left: 4px solid #e0a800; padding: 12px 16px; border-radius: 4px; }}
 .failures li {{ font-size: 12px; color: #8a1c1c; }}
 footer {{ margin-top: 40px; font-size: 11px; color: #888; border-top: 1px solid #eee; padding-top: 12px; }}
 @media print {{ body {{ max-width: none; }} h2 {{ page-break-after: avoid; }} }}
</style></head><body>

<h1>Population accessible aux structures de santé</h1>
<p class="subtitle">Mode : {metadata.mode} — déplacement : {metadata.profile} —
calcul du {metadata.computed_at}</p>

<div class="cards">{indicator_html}</div>

<h2>Paramètres et traçabilité</h2>
{_table_html(pd.DataFrame([
    {"propriété": "Mode d'analyse", "valeur": metadata.mode},
    {"propriété": "Pays (ISO3)", "valeur": metadata.country_iso3 or "—"},
    {"propriété": "Catégorie", "valeur": metadata.category or "—"},
    {"propriété": "Mode de déplacement", "valeur": metadata.profile},
    {"propriété": "Seuils (minutes)",
     "valeur": ", ".join(str(value // 60) for value in metadata.thresholds_seconds)},
    {"propriété": "Moteur de routage",
     "valeur": f"{metadata.routing_engine} {metadata.routing_engine_version}"},
    {"propriété": "URL du moteur", "valeur": metadata.routing_base_url},
    {"propriété": "Source de population", "valeur": metadata.population_source},
    {"propriété": "Année de population", "valeur": metadata.population_year or "—"},
    {"propriété": "Système de coordonnées", "valeur": metadata.crs},
    {"propriété": "Date du calcul", "valeur": metadata.computed_at},
]))}

<h2>Raster de population utilisé</h2>
{raster_html}

<h2>Population cumulée par seuil (personnes atteignant la structure en ≤ seuil)</h2>
{_table_html(matrix_cumulative)}

<h2>Population par couronne (entre deux seuils consécutifs)</h2>
<p style="font-size:12px;color:#666">Ces valeurs sont additionnables entre elles
pour une même structure ; les valeurs cumulées ne le sont pas.</p>
{_table_html(matrix_interval)}

<h2>Couverture combinée sans double comptage</h2>
{_table_html(coverage)}

<h2>Détail par structure et par seuil</h2>
{_table_html(long_frame)}

{failure_html}

<h2>Limites méthodologiques</h2>
<div class="warn"><ul>{warnings_html}</ul></div>

<footer>
Isochrones : openrouteservice sur données OpenStreetMap (ODbL).
Population : WorldPop (CC BY 4.0). Accessibilité territoriale : HeiGIT OpenAccessLens.
Document généré automatiquement — aucune valeur n'y est simulée ni extrapolée.
</footer>
</body></html>
""".encode("utf-8")


# --------------------------------------------------------------------------- #
# Noms de fichiers                                                             #
# --------------------------------------------------------------------------- #


def filename(prefix: str, extension: str) -> str:
    """Nom de fichier horodaté, stable et triable."""
    return f"{prefix}_{_stamp()}.{extension.lstrip('.')}"
