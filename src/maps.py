"""Cartographie.

Deux rendus, correspondant aux deux modes, sans mélange des géométries :

* **mode 1** — composant MapLibre GL affichant directement les **PMTiles
  officiels HeiGIT** (aucune géométrie recalculée côté application) ;
* **mode 2** — carte Folium affichant les **isochrones renvoyées par le moteur
  de routage** (aucun cercle, aucun tampon).
"""

from __future__ import annotations

import base64
import json
from typing import Any, Sequence

import folium
from folium.plugins import Draw, Fullscreen, MarkerCluster

from .config import (
    BASEMAP_STYLE,
    HEALTH_CATEGORIES,
    THRESHOLDS_SECONDS,
    color_for_facility,
    color_for_threshold,
)
from .data_catalog import pmtiles_url
from .models import Facility, FacilityIsochrones


# --------------------------------------------------------------------------- #
# Mode 1 — PMTiles HeiGIT                                                      #
# --------------------------------------------------------------------------- #


def render_html_component(html: str, *, height: int = 640) -> None:
    """Affiche un document HTML autonome dans Streamlit.

    ``st.components.v1.html`` est déprécié dans les versions récentes ; un repli
    par ``st.iframe`` sur une URL ``data:`` garantit que la carte MapLibre
    continue de fonctionner après son retrait.
    """
    import streamlit as st

    component = getattr(getattr(st, "components", None), "v1", None)
    if component is not None and hasattr(component, "html"):
        try:
            component.html(html, height=height)
            return
        except Exception:  # pragma: no cover - dépend de la version
            pass

    encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
    st.iframe(f"data:text/html;base64,{encoded}", height=height)


def territorial_map_html(
    iso3: str,
    category: str,
    selected_seconds: Sequence[int],
    facilities: Sequence[Facility] = (),
    *,
    height: int = 620,
    centre: tuple[float, float] | None = None,
    zoom: float = 5.0,
) -> str:
    """Composant MapLibre affichant les PMTiles OpenAccessLens.

    Le nom de la couche vectorielle est **lu dans les métadonnées du PMTiles**
    plutôt que supposé, comme le fait l'application officielle.
    """
    url = pmtiles_url(iso3, category)
    selected = sorted(int(value) for value in selected_seconds)

    colours = {
        str(seconds): color_for_threshold(seconds) for seconds in THRESHOLDS_SECONDS
    }
    markers = [
        {
            "name": facility.name,
            "lon": facility.longitude,
            "lat": facility.latitude,
        }
        for facility in facilities
    ]

    if centre is None:
        if markers:
            centre = (
                sum(item["lon"] for item in markers) / len(markers),
                sum(item["lat"] for item in markers) / len(markers),
            )
        else:
            centre = (15.0, 2.0)
            zoom = 2.3

    legend_rows = "".join(
        f'<div class="row"><span class="swatch" style="background:{color_for_threshold(seconds)}"></span>'
        f"≤ {seconds // 60} min</div>"
        for seconds in selected
    )

    payload = json.dumps(
        {
            "pmtiles": url,
            "style": BASEMAP_STYLE,
            "selected": selected,
            "colours": colours,
            "markers": markers,
            "centre": list(centre),
            "zoom": zoom,
        }
    )

    return f"""
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8" />
<link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet" />
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script src="https://unpkg.com/pmtiles@3.2.0/dist/pmtiles.js"></script>
<style>
  html, body {{ margin:0; padding:0; height:100%; font-family: system-ui, sans-serif; }}
  #map {{ position:absolute; inset:0; }}
  .panel {{ position:absolute; bottom:18px; left:12px; z-index:2; background:rgba(255,255,255,.94);
           border-radius:8px; padding:10px 12px; font-size:12px; box-shadow:0 1px 6px rgba(0,0,0,.25);
           max-height:52%; overflow:auto; }}
  .panel h4 {{ margin:0 0 6px; font-size:12px; }}
  .row {{ display:flex; align-items:center; gap:7px; line-height:1.7; }}
  .swatch {{ width:15px; height:11px; border-radius:2px; display:inline-block; }}
  .status {{ position:absolute; top:12px; left:12px; z-index:3; background:#fff; padding:8px 12px;
             border-radius:6px; font-size:12px; box-shadow:0 1px 6px rgba(0,0,0,.2); max-width:70%; }}
  .status.error {{ background:#fdecec; color:#8a1c1c; }}
</style>
</head>
<body>
<div id="map"></div>
<div class="panel"><h4>Temps d'accès — {HEALTH_CATEGORIES.get(category, category)}</h4>{legend_rows}</div>
<div class="status" id="status">Chargement des PMTiles HeiGIT…</div>
<script>
const CONFIG = {payload};
const statusBox = document.getElementById('status');

const protocol = new pmtiles.Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile);

const map = new maplibregl.Map({{
  container: 'map',
  style: CONFIG.style,
  center: CONFIG.centre,
  zoom: CONFIG.zoom,
  attributionControl: {{ compact: true }}
}});
map.addControl(new maplibregl.NavigationControl({{ showCompass: false }}), 'top-right');

function fillColourExpression() {{
  // Une couleur par valeur exacte de `range` (secondes). Les seuils non
  // sélectionnés sont rendus transparents plutôt que supprimés, afin de
  // conserver l'ordre d'empilement des surfaces cumulatives.
  const expression = ['match', ['to-number', ['get', 'range']]];
  Object.keys(CONFIG.colours).forEach(function (key) {{
    const seconds = parseInt(key, 10);
    expression.push(seconds);
    expression.push(CONFIG.selected.indexOf(seconds) >= 0 ? CONFIG.colours[key] : 'rgba(0,0,0,0)');
  }});
  expression.push('rgba(0,0,0,0)');
  return expression;
}}

map.on('load', async function () {{
  try {{
    const archive = new pmtiles.PMTiles(CONFIG.pmtiles);
    protocol.add(archive);
    const metadata = await archive.getMetadata();
    const layers = metadata.vector_layers || [];
    if (!layers.length) {{ throw new Error('Aucune couche vectorielle dans le PMTiles'); }}
    const sourceLayer = layers[0].id;
    const header = await archive.getHeader();

    map.addSource('access', {{ type: 'vector', url: 'pmtiles://' + CONFIG.pmtiles }});

    // Les grands `range` sont dessinés en premier : les zones rapides restent visibles.
    const ordered = Object.keys(CONFIG.colours).map(Number).sort(function (a, b) {{ return b - a; }});
    ordered.forEach(function (seconds) {{
      map.addLayer({{
        id: 'iso-' + seconds,
        type: 'fill',
        source: 'access',
        'source-layer': sourceLayer,
        filter: ['==', ['to-number', ['get', 'range']], seconds],
        paint: {{
          'fill-color': CONFIG.colours[String(seconds)],
          'fill-opacity': CONFIG.selected.indexOf(seconds) >= 0 ? 0.62 : 0,
          'fill-outline-color': 'rgba(255,255,255,0.35)'
        }}
      }});
    }});

    if (header && header.minLon !== undefined) {{
      map.fitBounds([[header.minLon, header.minLat], [header.maxLon, header.maxLat]], {{
        padding: 30, duration: 0
      }});
    }}

    map.on('click', function (event) {{
      const hits = map.queryRenderedFeatures(event.point).filter(function (feature) {{
        return feature.layer.id.indexOf('iso-') === 0 && feature.properties.range !== undefined;
      }});
      if (!hits.length) {{ return; }}
      const value = Math.min.apply(null, hits.map(function (h) {{ return Number(h.properties.range); }}));
      new maplibregl.Popup()
        .setLngLat(event.lngLat)
        .setHTML('<strong>Temps d\\'accès HeiGIT</strong><br/>≤ ' + (value / 60) +
                 ' min<br/><small>range = ' + value + ' s</small>')
        .addTo(map);
    }});

    statusBox.remove();
  }} catch (error) {{
    statusBox.className = 'status error';
    statusBox.textContent = 'Données HeiGIT indisponibles : ' + error.message +
      '. Aucune géométrie de substitution n\\'est affichée.';
  }}
}});

CONFIG.markers.forEach(function (marker) {{
  new maplibregl.Marker({{ color: '#c1121f' }})
    .setLngLat([marker.lon, marker.lat])
    .setPopup(new maplibregl.Popup().setHTML(
      '<strong>' + marker.name + '</strong><br/><small>' +
      marker.lat.toFixed(5) + ', ' + marker.lon.toFixed(5) + '</small>'))
    .addTo(map);
}});
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Mode 2 — isochrones calculées                                                #
# --------------------------------------------------------------------------- #


def _format_number(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "non calculé"
    return f"{value:,.0f}{suffix}".replace(",", " ")


def catchment_map(
    results: Sequence[FacilityIsochrones],
    selected_seconds: Sequence[int],
    *,
    facilities: Sequence[Facility] = (),
    colour_by: str = "threshold",
    show_rings: bool = False,
    enable_draw: bool = False,
    union_layer: Any | None = None,
) -> folium.Map:
    """Carte Folium des zones de desserte calculées par le moteur de routage.

    ``colour_by`` vaut ``"threshold"`` (une couleur par seuil, palette
    OpenAccessLens) ou ``"facility"`` (une couleur par structure, pour comparer
    visuellement plusieurs structures).
    """
    selected = sorted(int(value) for value in selected_seconds)

    points = [
        (result.facility.latitude, result.facility.longitude) for result in results
    ] + [(facility.latitude, facility.longitude) for facility in facilities]

    if points:
        centre = (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
        zoom = 9
    else:
        centre, zoom = (14.7167, -17.4677), 6

    fmap = folium.Map(
        location=centre,
        zoom_start=zoom,
        tiles="OpenStreetMap",
        control_scale=True,
    )
    Fullscreen().add_to(fmap)

    # Les grands seuils sont ajoutés d'abord : les zones rapides restent au-dessus.
    for facility_index, result in enumerate(results):
        facility_colour = color_for_facility(facility_index)
        group = folium.FeatureGroup(name=f"Zones — {result.facility.name}", show=True)

        for band in sorted(result.bands, key=lambda item: -item.threshold_seconds):
            if band.threshold_seconds not in selected:
                continue
            geometry = band.ring_geometry if show_rings else band.geometry
            if geometry is None or geometry.is_empty:
                continue

            colour = (
                color_for_threshold(band.threshold_seconds)
                if colour_by == "threshold"
                else facility_colour
            )
            opacity = 0.45 if colour_by == "threshold" else 0.18 + 0.03 * (
                band.threshold_seconds // 600
            )

            popup = folium.Popup(
                folium.Html(
                    f"<div style='font-family:system-ui;font-size:13px;min-width:230px'>"
                    f"<strong>{result.facility.name}</strong><br/>"
                    f"<span style='color:#555'>Zone ≤ {band.threshold_minutes} min "
                    f"({band.threshold_seconds} s)</span><hr style='margin:6px 0'/>"
                    f"Population cumulée : <strong>{_format_number(band.population_cumulative)}</strong><br/>"
                    f"Population de la couronne : <strong>{_format_number(band.population_interval)}</strong><br/>"
                    f"Superficie cumulée : {_format_number(band.area_km2_cumulative, ' km²')}<br/>"
                    f"Part de la population : "
                    f"{'non calculée' if band.population_share is None else f'{band.population_share:.2f} %'}<br/>"
                    f"<small style='color:#777'>Moteur : {result.engine} {result.engine_version} — "
                    f"profil {result.profile}</small></div>",
                    script=True,
                ),
                max_width=320,
            )

            folium.GeoJson(
                geometry.__geo_interface__,
                name=f"{result.facility.name} ≤ {band.threshold_minutes} min",
                style_function=(
                    lambda _feature, colour=colour, opacity=opacity: {
                        "fillColor": colour,
                        "color": colour,
                        "weight": 1,
                        "fillOpacity": opacity,
                    }
                ),
                highlight_function=lambda _feature: {"weight": 3, "fillOpacity": 0.7},
                tooltip=f"{result.facility.name} — ≤ {band.threshold_minutes} min",
                popup=popup,
            ).add_to(group)

        group.add_to(fmap)

    if union_layer is not None and not union_layer.empty:
        union_group = folium.FeatureGroup(name="Couverture combinée (union)", show=False)
        for _, row in union_layer.iterrows():
            folium.GeoJson(
                row.geometry.__geo_interface__,
                style_function=lambda _feature: {
                    "fillColor": "#000000",
                    "color": "#000000",
                    "weight": 2,
                    "fillOpacity": 0.05,
                    "dashArray": "4,4",
                },
                tooltip=f"Union ≤ {int(row['seuil_minutes'])} min",
            ).add_to(union_group)
        union_group.add_to(fmap)

    marker_group = folium.FeatureGroup(name="Structures de santé", show=True)
    cluster_target: Any = marker_group
    all_facilities = list(facilities) or [result.facility for result in results]
    if len(all_facilities) > 20:
        cluster_target = MarkerCluster().add_to(marker_group)

    for index, facility in enumerate(all_facilities):
        folium.Marker(
            location=(facility.latitude, facility.longitude),
            tooltip=facility.name,
            popup=folium.Popup(
                f"<strong>{facility.name}</strong><br/>"
                f"{facility.latitude:.5f}, {facility.longitude:.5f}<br/>"
                f"<small>Source : {facility.source}</small>",
                max_width=280,
            ),
            icon=folium.Icon(
                color="red" if colour_by == "threshold" else "blue",
                icon="plus-sign",
            ),
        ).add_to(cluster_target)
    marker_group.add_to(fmap)

    if enable_draw:
        Draw(
            export=False,
            draw_options={
                "polyline": False,
                "polygon": False,
                "rectangle": False,
                "circle": False,      # aucun cercle : ce serait une fausse isochrone
                "circlemarker": False,
                "marker": True,
            },
            edit_options={"edit": False, "remove": True},
        ).add_to(fmap)

    folium.LayerControl(collapsed=False).add_to(fmap)
    _add_legend(fmap, selected, colour_by, results)
    return fmap


def _add_legend(
    fmap: folium.Map,
    selected: Sequence[int],
    colour_by: str,
    results: Sequence[FacilityIsochrones],
) -> None:
    """Légende HTML rétractable, cohérente avec le mode de coloration."""
    if colour_by == "threshold":
        title = "Temps de trajet"
        rows = "".join(
            f'<div style="display:flex;align-items:center;gap:7px;line-height:1.65">'
            f'<span style="width:15px;height:11px;border-radius:2px;background:{color_for_threshold(seconds)}"></span>'
            f"≤ {seconds // 60} min</div>"
            for seconds in selected
        )
    else:
        title = "Structures"
        rows = "".join(
            f'<div style="display:flex;align-items:center;gap:7px;line-height:1.65">'
            f'<span style="width:15px;height:11px;border-radius:2px;background:{color_for_facility(index)}"></span>'
            f"{result.facility.name}</div>"
            for index, result in enumerate(results)
        )

    html = f"""
    <div style="position:fixed;bottom:24px;left:12px;z-index:9999;background:rgba(255,255,255,.95);
                border-radius:8px;padding:10px 12px;font-family:system-ui,sans-serif;font-size:12px;
                box-shadow:0 1px 6px rgba(0,0,0,.25);max-height:45vh;overflow:auto">
      <details open>
        <summary style="cursor:pointer;font-weight:600;margin-bottom:5px">{title}</summary>
        {rows}
      </details>
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(html))


def picker_map(facilities: Sequence[Facility]) -> folium.Map:
    """Carte minimale servant à dessiner de nouvelles structures."""
    if facilities:
        centre = (
            sum(facility.latitude for facility in facilities) / len(facilities),
            sum(facility.longitude for facility in facilities) / len(facilities),
        )
        zoom = 10
    else:
        centre, zoom = (14.7167, -17.4677), 6

    fmap = folium.Map(location=centre, zoom_start=zoom, tiles="OpenStreetMap", control_scale=True)
    Fullscreen().add_to(fmap)
    Draw(
        export=False,
        draw_options={
            "polyline": False,
            "polygon": False,
            "rectangle": False,
            "circle": False,
            "circlemarker": False,
            "marker": True,
        },
        edit_options={"edit": False, "remove": True},
    ).add_to(fmap)

    for facility in facilities:
        folium.Marker(
            location=(facility.latitude, facility.longitude),
            tooltip=facility.name,
            icon=folium.Icon(color="red", icon="plus-sign"),
        ).add_to(fmap)
    return fmap
