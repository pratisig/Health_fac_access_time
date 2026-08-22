"""Application Streamlit — population accessible aux structures de santé.

Deux analyses strictement séparées :

**Mode 1 — Accessibilité territoriale (HeiGIT / OpenAccessLens).**
Affiche les isochrones nationales officielles et les statistiques
démographiques déjà publiées. Répond à : « quel est le niveau actuel
d'accessibilité territoriale aux services de santé ? »

**Mode 2 — Zone de desserte propre à chaque structure.**
Calcule de nouvelles isochrones autour de chaque structure importée ou
dessinée, puis somme WorldPop dans chaque zone. Répond à : « combien de
personnes peuvent atteindre cette structure précise en 10, 20 … 120 minutes ? »

Aucune géométrie n'est fabriquée : les surfaces du mode 1 viennent des PMTiles
HeiGIT, celles du mode 2 du moteur de routage. Un seuil non calculable reste
absent et son motif est affiché.
"""

from __future__ import annotations

import traceback
from typing import Sequence

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from src import charts, exports, maps
from src.config import (
    EQUAL_AREA_CRS,
    HEALTH_CATEGORIES,
    MAX_FACILITIES,
    METHODOLOGICAL_WARNINGS,
    THRESHOLDS_MINUTES,
    THRESHOLDS_SECONDS,
    TRAVEL_PROFILES,
    WGS84,
    ors_api_key,
)
from src.data_catalog import (
    CatalogError,
    add_interval_population,
    available_population_types,
    fetch_countries,
    fetch_stats,
    national_stats,
    pmtiles_url,
    stats_url,
)
from src.facility_io import (
    FacilityImportError,
    facilities_to_geodataframe,
    read_csv_facilities,
    read_geofile_facilities,
    read_shapefile_zip,
)
from src.isochrones import compute_all
from src.models import AnalysisMetadata, Facility
from src.population import (
    WORLDPOP_PRODUCTS,
    PopulationDataError,
    download_raster,
    inspect_raster,
    raster_total,
    resolve_worldpop_url,
)
from src.routing import (
    RoutingCapabilities,
    RoutingError,
    available_routing_engines,
    create_routing_client,
)
from src.spatial_analysis import (
    MATRIX_METRICS,
    attach_population,
    check_consistency,
    combined_coverage,
    long_table,
    matrix_table,
    summary_indicators,
    union_geodataframe,
)

st.set_page_config(
    page_title="Population accessible aux structures de santé",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------------------- #
# État de session                                                              #
# --------------------------------------------------------------------------- #

DEFAULTS = {
    "facilities": [],
    "results": None,
    "metadata": None,
    "long_frame": None,
    "coverage": None,
    "raster_path": None,
    "raster_metadata": None,
    "reference_population": None,
    "cancel": False,
    "consistency": [],
    "import_messages": [],
}

for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)


def reset_results() -> None:
    """Invalide les résultats après un changement de paramètre structurant."""
    for key in ("results", "metadata", "long_frame", "coverage", "consistency"):
        st.session_state[key] = DEFAULTS[key]


# --------------------------------------------------------------------------- #
# Fonctions mises en cache                                                     #
# --------------------------------------------------------------------------- #


@st.cache_data(show_spinner="Chargement du catalogue OpenAccessLens…", ttl=86400)
def load_countries():
    return fetch_countries()


@st.cache_data(show_spinner="Chargement des statistiques HeiGIT…", ttl=86400)
def load_stats(iso3: str, category: str) -> pd.DataFrame:
    return fetch_stats(iso3, category)


@st.cache_data(show_spinner="Recherche du raster WorldPop…", ttl=86400)
def resolve_raster_url(product_key: str, iso3: str, year: int) -> str:
    return resolve_worldpop_url(WORLDPOP_PRODUCTS[product_key], iso3, year)


@st.cache_resource(show_spinner=False)
def load_raster(url: str) -> str:
    """Télécharge le raster une seule fois par session et par URL."""
    return str(download_raster(url))


@st.cache_data(show_spinner="Calcul de la population totale de référence…", ttl=86400)
def compute_reference_population(path: str, is_density: bool) -> float:
    return raster_total(path, is_density=is_density)


# --------------------------------------------------------------------------- #
# Barre latérale                                                               #
# --------------------------------------------------------------------------- #

st.sidebar.title("🏥 Accessibilité santé")

mode = st.sidebar.radio(
    "Analyse",
    options=[
        "Mode 1 — Accessibilité territoriale (HeiGIT)",
        "Mode 2 — Zone de desserte par structure",
    ],
    help=(
        "Le mode 1 lit les données nationales déjà publiées par HeiGIT. "
        "Le mode 2 calcule de nouvelles isochrones autour de vos structures."
    ),
    key="analysis_mode",
)
IS_MODE_2 = mode.startswith("Mode 2")

st.sidebar.divider()

# -- Pays et catégorie ------------------------------------------------------ #

countries = []
catalog_error: str | None = None
try:
    countries = load_countries()
except CatalogError as error:
    catalog_error = str(error)

if countries:
    labels = [f"{country.name} ({country.iso3})" for country in countries]
    default_index = next(
        (index for index, country in enumerate(countries) if country.iso3 == "SEN"), 0
    )
    selected_label = st.sidebar.selectbox("Pays", labels, index=default_index)
    country = countries[labels.index(selected_label)]
    iso3 = country.code
    country_name = country.name
else:
    st.sidebar.error(
        "Catalogue OpenAccessLens indisponible. Saisissez un code ISO3 pour "
        "continuer : les statistiques territoriales resteront inaccessibles, "
        "mais WorldPop et le routage fonctionnent."
    )
    iso3 = st.sidebar.text_input("Code ISO3", value="sen", max_chars=3).strip().lower()
    country_name = iso3.upper()

category = st.sidebar.selectbox(
    "Catégorie de service",
    options=list(HEALTH_CATEGORIES),
    format_func=lambda key: HEALTH_CATEGORIES[key],
)

# -- Seuils ----------------------------------------------------------------- #

st.sidebar.divider()
st.sidebar.subheader("Seuils de temps")

if "selected_minutes" not in st.session_state:
    st.session_state.selected_minutes = list(THRESHOLDS_MINUTES)

button_all, button_none = st.sidebar.columns(2)
if button_all.button("Tout sélectionner", width="stretch"):
    st.session_state.selected_minutes = list(THRESHOLDS_MINUTES)
if button_none.button("Tout désélectionner", width="stretch"):
    st.session_state.selected_minutes = []

selected_minutes = st.sidebar.multiselect(
    "Classes affichées (minutes)",
    options=list(THRESHOLDS_MINUTES),
    default=None,
    key="selected_minutes",
    format_func=lambda value: f"≤ {value} min",
)
selected_seconds = sorted(int(value) * 60 for value in selected_minutes)

if not selected_seconds:
    st.sidebar.warning("Aucun seuil sélectionné : cartes, tableaux et exports seront vides.")

# -- Population ------------------------------------------------------------- #

st.sidebar.divider()
st.sidebar.subheader("Population — WorldPop")

product_key = st.sidebar.selectbox(
    "Produit",
    options=list(WORLDPOP_PRODUCTS),
    index=list(WORLDPOP_PRODUCTS).index("unconstrained_1km"),
    format_func=lambda key: WORLDPOP_PRODUCTS[key].label,
    help=(
        "Le produit 1 km est nettement plus léger : il convient aux plateformes "
        "à mémoire limitée. Le 100 m est plus précis mais pèse plusieurs centaines "
        "de mégaoctets pour un grand pays."
    ),
)
product = WORLDPOP_PRODUCTS[product_key]
year = st.sidebar.selectbox(
    "Année",
    options=list(reversed(product.years)),
    index=0,
)

load_population = st.sidebar.button("Charger le raster WorldPop", width="stretch")

if load_population:
    try:
        url = resolve_raster_url(product_key, iso3, int(year))
        progress = st.sidebar.progress(0.0, text="Téléchargement WorldPop…")

        def _report(downloaded: int, total: int | None) -> None:
            if total:
                progress.progress(
                    min(downloaded / total, 1.0),
                    text=f"Téléchargement WorldPop… {downloaded / 1e6:.0f} / {total / 1e6:.0f} Mo",
                )

        path = str(download_raster(url, progress=_report))
        progress.empty()

        metadata = inspect_raster(
            path, source="WorldPop", method=product.method, year=int(year)
        )
        st.session_state.raster_path = path
        st.session_state.raster_metadata = metadata
        st.session_state.reference_population = compute_reference_population(
            path, metadata.is_density
        )
        reset_results()
        st.sidebar.success(f"Raster chargé : {metadata.path.split('/')[-1]}")
    except (PopulationDataError, Exception) as error:  # noqa: BLE001
        st.sidebar.error(f"WorldPop indisponible : {error}")

if st.session_state.raster_metadata is not None:
    meta = st.session_state.raster_metadata
    with st.sidebar.expander("Métadonnées du raster", expanded=False):
        st.write(
            {
                "CRS": meta.crs,
                "Résolution (deg)": f"{meta.pixel_size_x:.6f}",
                "Résolution (~m)": (
                    f"{meta.approximate_resolution_m:.0f}"
                    if meta.approximate_resolution_m
                    else "—"
                ),
                "NoData": meta.nodata,
                "Type": meta.dtype,
                "Unité du pixel": meta.unit,
                "Densité": "oui" if meta.is_density else "non (comptage)",
                "Année": meta.year,
                "Méthode": meta.method,
            }
        )
        if st.session_state.reference_population:
            st.caption(
                f"Population totale du raster : "
                f"{st.session_state.reference_population:,.0f}".replace(",", " ")
            )
else:
    st.sidebar.info("Aucun raster chargé : les populations resteront non calculées.")

# -- Routage ---------------------------------------------------------------- #

routing_engine_key: str | None = None
capabilities: RoutingCapabilities | None = None
routing_engines: dict[str, RoutingCapabilities] = {}

if IS_MODE_2:
    st.sidebar.divider()
    st.sidebar.subheader("Moteur de routage")

    profile = st.sidebar.selectbox(
        "Mode de déplacement",
        options=list(TRAVEL_PROFILES),
        format_func=lambda key: TRAVEL_PROFILES[key],
    )

    routing_engines = available_routing_engines()

    def _engine_label(key: str) -> str:
        item = routing_engines[key]
        limit = item.max_range_for(profile)
        maximum = f"{limit // 60} min max" if limit is not None else "seuil configurable"
        key_status = "clé requise" if item.api_key_required else "sans clé"
        return (
            f"{'Valhalla FOSSGIS' if key == 'valhalla' else 'openrouteservice'} — "
            f"{key_status} · {maximum} · {item.max_contours} contours/requête"
        )

    if routing_engines:
        routing_engine_key = st.sidebar.selectbox(
            "Backend d'isochrones",
            options=list(routing_engines),
            format_func=_engine_label,
            help=(
                "Valhalla est activé par défaut sans secret. openrouteservice "
                "n'apparaît que si ORS_API_KEY est configurée."
            ),
        )
        capabilities = routing_engines[routing_engine_key]
        st.sidebar.caption(
            f"Endpoint : {capabilities.base_url}  \n"
            f"Temps transmis en {capabilities.time_unit} · sens : vers la structure "
            f"({capabilities.direction_parameter}={capabilities.direction_value})."
        )
        limit = capabilities.max_range_for(profile)
        if limit is not None and any(value > limit for value in selected_seconds):
            st.sidebar.warning(
                f"Portée maximale pour « {TRAVEL_PROFILES[profile]} » : {limit // 60} min. "
                "Les seuils supérieurs resteront vides avec leur motif."
            )
        if routing_engine_key == "valhalla":
            st.sidebar.warning(
                "**Serveur de démonstration FOSSGIS mutualisé.** Lancez de petits "
                "lots, n'enchaînez pas les recalculs et laissez le cache réutiliser "
                "les réponses. Pour un usage intensif, déployez Valhalla vous-même."
            )
    else:
        st.sidebar.error(
            "Aucun moteur de routage utilisable : Valhalla est désactivé et aucune "
            "clé ORS_API_KEY n'est configurée. Le lancement de l'analyse est bloqué."
        )

    if not ors_api_key():
        st.sidebar.caption(
            "openrouteservice indisponible dans le sélecteur : ORS_API_KEY absente."
        )
else:
    profile = "driving-car"


# --------------------------------------------------------------------------- #
# Mode 1 — Accessibilité territoriale                                          #
# --------------------------------------------------------------------------- #


def render_territorial_mode() -> None:
    st.title("Mode 1 — Accessibilité territoriale HeiGIT / OpenAccessLens")
    st.caption(
        "Isochrones nationales officielles et statistiques déjà publiées par HeiGIT. "
        "Aucune géométrie n'est recalculée ici : la carte affiche directement les PMTiles."
    )

    if catalog_error:
        st.error(catalog_error)

    st.info(
        "**Lecture correcte.** Ces surfaces représentent le temps de trajet vers le "
        "service de santé *le plus proche déjà existant*, à l'échelle du pays. Elles "
        "ne décrivent pas la zone de desserte d'une structure particulière — c'est "
        "l'objet du mode 2.",
        icon="ℹ️",
    )

    map_tab, stats_tab, source_tab = st.tabs(
        ["Carte PMTiles", "Statistiques publiées", "Sources"]
    )

    with map_tab:
        if not selected_seconds:
            st.warning("Sélectionnez au moins une classe de temps dans la barre latérale.")
        else:
            maps.render_html_component(
                maps.territorial_map_html(
                    iso3, category, selected_seconds, st.session_state.facilities
                ),
                height=640,
            )
            if st.session_state.facilities:
                st.caption(
                    f"{len(st.session_state.facilities)} structure(s) importée(s) "
                    "localisée(s) sur la carte territoriale."
                )

    with stats_tab:
        try:
            raw_stats = load_stats(iso3, category)
        except CatalogError as error:
            st.error(str(error))
            st.stop()

        types = available_population_types(raw_stats)
        population_type = st.selectbox(
            "Groupe démographique", options=types or ["total"]
        ) if types else "total"

        national = national_stats(
            raw_stats, population_type=population_type if types else None
        )
        if selected_seconds:
            national = national[national["range"].isin(selected_seconds)]
        national = add_interval_population(national)

        if national.empty:
            st.warning("Aucune statistique pour cette sélection.")
        else:
            columns = [
                column
                for column in (
                    "range", "range_minutes", "population_type",
                    "population", "population_interval", "population_share",
                )
                if column in national.columns
            ]
            display = national[columns].rename(
                columns={
                    "range": "seuil (s)",
                    "range_minutes": "seuil (min)",
                    "population_type": "groupe",
                    "population": "population cumulée",
                    "population_interval": "population de la couronne",
                    "population_share": "part (%)",
                }
            )
            st.dataframe(display, width="stretch", hide_index=True)
            st.plotly_chart(
                charts.territorial_stats_chart(national, population_type),
                width="stretch",
            )
            st.download_button(
                "⬇️ Statistiques territoriales (CSV)",
                data=display.to_csv(index=False).encode("utf-8-sig"),
                file_name=exports.filename(f"heigit_{iso3}_{category}", "csv"),
                mime="text/csv",
            )

    with source_tab:
        st.markdown(
            f"""
Les trois ressources utilisées, telles que publiées par HeiGIT :

| Ressource | URL |
|---|---|
| Catalogue | `.../access/aux/countries.yaml` |
| Isochrones | `{pmtiles_url(iso3, category)}` |
| Statistiques | `{stats_url(iso3, category)}` |

* attribut de temps : **`range`**, en **secondes** ;
* seuils santé : 600 à 7200 s, soit 10 à 120 minutes ;
* population : WorldPop 100 m, croisée par HeiGIT avec ses propres isochrones ;
* moteur : openrouteservice sur OpenStreetMap, profil motorisé.

Les statistiques affichées sont lues telles quelles. Elles ne sont ni
recalculées, ni corrigées, ni complétées.
"""
        )


# --------------------------------------------------------------------------- #
# Mode 2 — Gestion des structures                                              #
# --------------------------------------------------------------------------- #


def render_facility_manager() -> None:
    st.subheader("1. Structures de santé")

    import_tab, draw_tab, table_tab = st.tabs(
        ["Importer un fichier", "Dessiner sur la carte", "Gérer la liste"]
    )

    with import_tab:
        upload = st.file_uploader(
            "CSV, GeoJSON, GeoPackage ou Shapefile compressé (.zip)",
            type=["csv", "geojson", "json", "gpkg", "zip"],
        )
        st.caption(
            "Colonnes acceptées : `nom`/`name`/`structure`/`établissement`, "
            "`latitude`/`lat`/`y`, `longitude`/`lon`/`lng`/`x`. "
            "Les fichiers géographiques sont reprojetés en EPSG:4326."
        )

        if upload is not None and st.button("Importer", type="primary"):
            try:
                suffix = upload.name.lower().rsplit(".", 1)[-1]
                if suffix == "csv":
                    imported, rejected = read_csv_facilities(upload.getvalue())
                elif suffix == "zip":
                    imported, rejected = read_shapefile_zip(upload.getvalue())
                else:
                    import tempfile
                    from pathlib import Path

                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=f".{suffix}"
                    ) as handle:
                        handle.write(upload.getvalue())
                        temporary = Path(handle.name)
                    imported, rejected = read_geofile_facilities(temporary)

                st.session_state.facilities.extend(imported)
                st.session_state.import_messages = rejected
                reset_results()
                st.success(f"{len(imported)} structure(s) importée(s).")
                if rejected:
                    with st.expander(f"{len(rejected)} ligne(s) rejetée(s)"):
                        for message in rejected:
                            st.write(f"• {message}")
            except FacilityImportError as error:
                st.error(str(error))
            except Exception as error:  # noqa: BLE001
                st.error(f"Import impossible : {error}")

        st.download_button(
            "⬇️ Modèle CSV",
            data=(
                "nom,latitude,longitude\n"
                "Hôpital 1,14.7167,-17.4677\n"
                "Centre de santé 2,14.7150,-17.2730\n"
            ).encode("utf-8-sig"),
            file_name="modele_structures.csv",
            mime="text/csv",
        )

    with draw_tab:
        st.caption(
            "Placez un marqueur avec l'outil de dessin, puis validez. "
            "Aucun outil de cercle n'est proposé : un cercle n'est pas une isochrone."
        )
        drawn = st_folium(
            maps.picker_map(st.session_state.facilities),
            height=460,
            width=None,
            returned_objects=["all_drawings", "last_clicked"],
            key="draw_map",
        )

        candidates: list[tuple[float, float]] = []
        for drawing in (drawn or {}).get("all_drawings") or []:
            geometry = drawing.get("geometry") or {}
            if geometry.get("type") == "Point":
                longitude, latitude = geometry["coordinates"][:2]
                candidates.append((float(latitude), float(longitude)))

        if candidates:
            st.write(f"{len(candidates)} point(s) dessiné(s).")
            default_name = st.text_input("Préfixe des noms", value="Structure dessinée")
            if st.button("Ajouter les points dessinés", type="primary"):
                existing = {
                    (round(f.latitude, 6), round(f.longitude, 6))
                    for f in st.session_state.facilities
                }
                added = 0
                for index, (latitude, longitude) in enumerate(candidates, start=1):
                    if (round(latitude, 6), round(longitude, 6)) in existing:
                        continue
                    st.session_state.facilities.append(
                        Facility(
                            name=f"{default_name} {len(st.session_state.facilities) + 1}",
                            latitude=latitude,
                            longitude=longitude,
                            source="dessin",
                        )
                    )
                    added += 1
                reset_results()
                st.success(f"{added} structure(s) ajoutée(s).")
                st.rerun()

    with table_tab:
        facilities: list[Facility] = st.session_state.facilities
        if not facilities:
            st.info("Aucune structure. Importez un fichier ou dessinez un point.")
            return

        frame = pd.DataFrame(
            [
                {
                    "analyser": True,
                    "nom": facility.name,
                    "latitude": facility.latitude,
                    "longitude": facility.longitude,
                    "source": facility.source,
                    "identifiant": facility.identifier,
                }
                for facility in facilities
            ]
        )
        edited = st.data_editor(
            frame,
            width="stretch",
            hide_index=True,
            num_rows="fixed",
            column_config={
                "analyser": st.column_config.CheckboxColumn("Analyser", default=True),
                "nom": st.column_config.TextColumn("Nom", required=True),
                "latitude": st.column_config.NumberColumn("Latitude", format="%.6f", disabled=True),
                "longitude": st.column_config.NumberColumn("Longitude", format="%.6f", disabled=True),
                "source": st.column_config.TextColumn("Source", disabled=True),
                "identifiant": st.column_config.TextColumn("ID", disabled=True),
            },
            key="facility_editor",
        )

        for facility, (_, row) in zip(facilities, edited.iterrows()):
            if row["nom"] and row["nom"] != facility.name:
                facility.name = str(row["nom"])

        st.session_state.selected_ids = set(
            edited.loc[edited["analyser"], "identifiant"].tolist()
        )

        remove_column, clear_column = st.columns([3, 1])
        to_remove = remove_column.multiselect(
            "Supprimer des structures",
            options=[facility.identifier for facility in facilities],
            format_func=lambda identifier: next(
                f.name for f in facilities if f.identifier == identifier
            ),
        )
        if to_remove and remove_column.button("Confirmer la suppression"):
            st.session_state.facilities = [
                facility for facility in facilities if facility.identifier not in to_remove
            ]
            reset_results()
            st.rerun()

        if clear_column.button("Tout effacer", width="stretch"):
            st.session_state.facilities = []
            reset_results()
            st.rerun()


def selected_facilities() -> list[Facility]:
    """Structures cochées dans le tableau de gestion."""
    identifiers = st.session_state.get("selected_ids")
    facilities: list[Facility] = st.session_state.facilities
    if identifiers is None:
        return facilities
    return [facility for facility in facilities if facility.identifier in identifiers]


# --------------------------------------------------------------------------- #
# Mode 2 — Calcul                                                              #
# --------------------------------------------------------------------------- #


def run_analysis(targets: Sequence[Facility]) -> None:
    """Lance le calcul complet : isochrones, population, agrégations."""
    if routing_engine_key is None:
        st.error("Aucun moteur de routage utilisable : l'analyse ne peut pas démarrer.")
        return
    # Le point d'injection privé permet à AppTest de couvrir le parcours complet
    # sans accès réseau. En production, le client est toujours créé ici.
    client = st.session_state.get("_routing_client") or create_routing_client(
        routing_engine_key
    )

    progress = st.progress(0.0, text="Préparation…")
    log = st.empty()
    st.session_state.cancel = False

    def report(index: int, total: int, facility: Facility) -> None:
        progress.progress(
            (index - 1) / max(total, 1),
            text=f"Isochrones {index}/{total} — {facility.name}",
        )

    try:
        results = compute_all(
            targets,
            client,
            profile,
            selected_seconds,
            progress=report,
            should_stop=lambda: st.session_state.get("cancel", False),
        )
    except RoutingError as error:
        progress.empty()
        st.error(str(error))
        return

    progress.progress(0.6, text="Somme zonale WorldPop…")

    raster_path = st.session_state.raster_path
    raster_metadata = st.session_state.raster_metadata
    is_density = bool(raster_metadata.is_density) if raster_metadata else False

    if raster_path:
        def population_report(index: int, total: int) -> None:
            progress.progress(
                0.6 + 0.3 * index / max(total, 1),
                text=f"Population : zone {index}/{total}",
            )

        attach_population(
            results,
            raster_path,
            is_density=is_density,
            reference_population=st.session_state.reference_population,
            progress=population_report,
        )

    progress.progress(0.92, text="Couverture combinée…")
    coverage = combined_coverage(
        results,
        selected_seconds,
        raster_path,
        is_density=is_density,
        reference_population=st.session_state.reference_population,
    )

    progress.progress(1.0, text="Terminé")
    progress.empty()
    log.empty()

    engine = client.engine_info()
    st.session_state.results = results
    st.session_state.long_frame = long_table(results)
    st.session_state.coverage = coverage
    st.session_state.consistency = check_consistency(results)
    st.session_state.metadata = AnalysisMetadata(
        mode="Mode 2 — zone de desserte par structure",
        profile=profile,
        thresholds_seconds=list(selected_seconds),
        routing_engine=engine["engine"],
        routing_engine_version=engine["version"],
        routing_base_url=engine["base_url"],
        population_source=(
            f"WorldPop — {WORLDPOP_PRODUCTS[product_key].label}"
            if raster_path
            else "aucune (population non calculée)"
        ),
        population_year=int(year) if raster_path else None,
        population_raster=raster_metadata.to_dict() if raster_metadata else None,
        crs=f"{WGS84} (superficies calculées en {EQUAL_AREA_CRS})",
        warnings=list(METHODOLOGICAL_WARNINGS),
        country_iso3=iso3.upper(),
        category=category,
    )


def render_catchment_mode() -> None:
    st.title("Mode 2 — Zone de desserte propre à chaque structure")
    st.caption(
        "Chaque structure devient la destination de ses propres isochrones routières "
        "cumulatives. Les couronnes utilisent les seuils réellement sélectionnés."
    )

    render_facility_manager()

    st.divider()
    st.subheader("2. Calcul")

    targets = selected_facilities()
    left, right = st.columns([3, 1])

    with left:
        if not targets:
            st.info("Sélectionnez au moins une structure à analyser.")
        elif len(targets) > MAX_FACILITIES:
            st.error(
                f"{len(targets)} structures sélectionnées : la limite est fixée à "
                f"{MAX_FACILITIES} par lot pour rester dans les quotas d'API et la "
                "mémoire disponible. Réduisez la sélection ou traitez par lots."
            )
        else:
            contours_per_request = capabilities.max_contours if capabilities else 1
            requests_count = len(targets) * max(
                1, -(-len(selected_seconds) // contours_per_request)
            )
            st.write(
                f"**{len(targets)}** structure(s) × **{len(selected_seconds)}** seuil(s) "
                f"≈ **{requests_count}** requête(s) au moteur de routage "
                "(les résultats déjà en cache ne sont pas recalculés)."
            )
            if capabilities is not None:
                unsupported = capabilities.unsupported(profile, selected_seconds)
                if unsupported:
                    st.warning(
                        "Seuils hors capacité du moteur et donc **non calculés** : "
                        + ", ".join(f"{value // 60} min" for value in unsupported)
                        + ". Aucune géométrie de substitution ne sera produite."
                    )
            if routing_engine_key is None:
                st.error(
                    "Aucun moteur de routage utilisable. Activez Valhalla ou "
                    "configurez ORS_API_KEY ; aucune analyse silencieuse ne sera lancée."
                )

    with right:
        can_run = (
            bool(targets)
            and bool(selected_seconds)
            and len(targets) <= MAX_FACILITIES
            and routing_engine_key is not None
        )
        if st.button("▶️ Lancer l'analyse", type="primary", disabled=not can_run,
                     width="stretch"):
            try:
                run_analysis(targets)
            except Exception as error:  # noqa: BLE001
                st.error(f"Échec inattendu : {error}")
                with st.expander("Détail technique"):
                    st.code(traceback.format_exc())
        if st.session_state.results is not None and st.button(
            "Effacer les résultats", width="stretch"
        ):
            reset_results()
            st.rerun()

    if st.session_state.results is None:
        return

    render_results()


# --------------------------------------------------------------------------- #
# Mode 2 — Résultats                                                           #
# --------------------------------------------------------------------------- #


def render_results() -> None:
    results = st.session_state.results
    long_frame: pd.DataFrame = st.session_state.long_frame
    coverage: pd.DataFrame = st.session_state.coverage
    metadata: AnalysisMetadata = st.session_state.metadata

    visible = long_frame[long_frame["seuil_min"].isin(
        [value // 60 for value in selected_seconds]
    )] if not long_frame.empty else long_frame

    st.divider()
    st.subheader("3. Résultats")

    indicators = summary_indicators(results, coverage)
    columns = st.columns(4)
    columns[0].metric("Structures analysées", indicators.get("structures_analysees", 0))
    if "population_couverte_max" in indicators:
        columns[1].metric(
            f"Population couverte ≤ {indicators['seuil_max_min']} min",
            f"{indicators['population_couverte_max']:,.0f}".replace(",", " "),
        )
    if "population_chevauchement" in indicators:
        columns[2].metric(
            "Dont chevauchements",
            f"{indicators['population_chevauchement']:,.0f}".replace(",", " "),
            help="Population desservie par au moins deux structures, comptée une seule fois dans l'union.",
        )
    if "seuil_median_min" in indicators:
        columns[3].metric(
            "Seuil médian",
            f"{indicators['seuil_median_min']} min",
            help="Premier seuil atteignant la moitié de la population finalement couverte.",
        )

    failures = {
        result.facility.name: result.failed_thresholds
        for result in results
        if result.failed_thresholds
    }
    if failures:
        with st.expander(f"⚠️ Seuils non calculés ({sum(len(v) for v in failures.values())})"):
            for name, items in failures.items():
                for seconds, reason in sorted(items.items()):
                    st.write(f"**{name}** — {seconds // 60} min : {reason}")

    if st.session_state.consistency:
        with st.expander("Contrôles de cohérence"):
            for message in st.session_state.consistency:
                st.write(f"• {message}")

    if st.session_state.raster_path is None:
        st.warning(
            "Aucun raster WorldPop chargé : seules les géométries et superficies sont "
            "disponibles. Les populations restent non calculées — elles ne sont pas estimées.",
            icon="⚠️",
        )

    map_tab, table_tab, chart_tab, export_tab = st.tabs(
        ["Carte", "Tableaux", "Graphiques", "Exports"]
    )

    with map_tab:
        controls = st.columns(3)
        colour_by = controls[0].radio(
            "Coloration", ["threshold", "facility"],
            format_func=lambda key: "Par seuil" if key == "threshold" else "Par structure",
            horizontal=True,
        )
        show_rings = controls[1].toggle("Afficher les couronnes plutôt que les cumuls")
        show_union = controls[2].toggle("Ajouter la couverture combinée")

        union_layer = (
            union_geodataframe(results, selected_seconds) if show_union else None
        )
        st_folium(
            maps.catchment_map(
                results,
                selected_seconds,
                facilities=[result.facility for result in results],
                colour_by=colour_by,
                show_rings=show_rings,
                union_layer=union_layer,
            ),
            height=620,
            width=None,
            returned_objects=[],
            key="result_map",
        )

    with table_tab:
        layout = st.radio("Format", ["Long", "Matrice"], horizontal=True)
        if layout == "Long":
            st.dataframe(visible, width="stretch", hide_index=True)
        else:
            metric = st.selectbox(
                "Métrique", options=list(MATRIX_METRICS),
                format_func=lambda key: MATRIX_METRICS[key],
            )
            st.dataframe(
                matrix_table(visible, metric), width="stretch", hide_index=True
            )
            st.caption(
                "Rappel : les colonnes « population cumulée » ne s'additionnent pas "
                "entre elles ; les colonnes « population par intervalle » si."
            )

        st.markdown("**Couverture combinée (sans double comptage)**")
        st.dataframe(coverage, width="stretch", hide_index=True)

    with chart_tab:
        st.plotly_chart(charts.cumulative_curve(visible), width="stretch")
        st.plotly_chart(charts.interval_histogram(visible), width="stretch")
        st.plotly_chart(charts.coverage_chart(coverage), width="stretch")
        st.plotly_chart(charts.area_chart(visible), width="stretch")

        if not visible.empty:
            names = sorted(visible["structure"].unique())
            chosen = st.selectbox("Détail d'une structure", names)
            st.plotly_chart(
                charts.cumulative_versus_interval(visible, chosen),
                width="stretch",
            )
            if len(names) > 1:
                threshold = st.select_slider(
                    "Seuil de comparaison (minutes)",
                    options=sorted(visible["seuil_min"].unique()),
                    value=sorted(visible["seuil_min"].unique())[-1],
                )
                st.plotly_chart(
                    charts.facility_comparison(visible, threshold),
                    width="stretch",
                )

    with export_tab:
        st.caption(
            "Chaque export embarque la traçabilité complète : structure, coordonnées, "
            "mode de déplacement, seuil, populations, superficie, source et année "
            "WorldPop, moteur et version de routage, date, CRS et avertissements."
        )
        first, second = st.columns(2)

        first.download_button(
            "⬇️ CSV — format long",
            data=exports.long_csv(visible, metadata),
            file_name=exports.filename("acces_long", "csv"),
            mime="text/csv",
            width="stretch",
        )
        metric = first.selectbox(
            "Métrique de la matrice", options=list(MATRIX_METRICS),
            format_func=lambda key: MATRIX_METRICS[key], key="export_metric",
        )
        first.download_button(
            "⬇️ CSV — format matrice",
            data=exports.matrix_csv(visible, metadata, metric),
            file_name=exports.filename(f"acces_matrice_{metric}", "csv"),
            mime="text/csv",
            width="stretch",
        )
        first.download_button(
            "⬇️ Métadonnées (JSON)",
            data=exports.metadata_json(metadata, {"indicateurs": indicators}),
            file_name=exports.filename("metadonnees", "json"),
            mime="application/json",
            width="stretch",
        )

        second.download_button(
            "⬇️ GeoJSON — zones cumulées",
            data=exports.isochrones_geojson(results, metadata, geometry="cumulative"),
            file_name=exports.filename("isochrones_cumulees", "geojson"),
            mime="application/geo+json",
            width="stretch",
        )
        second.download_button(
            "⬇️ GeoJSON — couronnes",
            data=exports.isochrones_geojson(results, metadata, geometry="ring"),
            file_name=exports.filename("couronnes", "geojson"),
            mime="application/geo+json",
            width="stretch",
        )
        if second.button("Préparer le GeoPackage", width="stretch"):
            try:
                payload = exports.isochrones_geopackage(
                    results, metadata, long_frame=visible
                )
                second.download_button(
                    "⬇️ GeoPackage (.gpkg)",
                    data=payload,
                    file_name=exports.filename("isochrones", "gpkg"),
                    mime="application/geopackage+sqlite3",
                    width="stretch",
                )
            except Exception as error:  # noqa: BLE001
                second.error(f"GeoPackage indisponible : {error}")

        second.download_button(
            "⬇️ Rapport HTML (imprimable en PDF)",
            data=exports.html_report(
                visible, coverage, metadata, indicators=indicators, failures=failures
            ),
            file_name=exports.filename("rapport", "html"),
            mime="text/html",
            width="stretch",
        )


# --------------------------------------------------------------------------- #
# Routage de l'interface                                                       #
# --------------------------------------------------------------------------- #

if IS_MODE_2:
    render_catchment_mode()
else:
    render_territorial_mode()

with st.sidebar.expander("Limites du modèle", expanded=False):
    for warning in METHODOLOGICAL_WARNINGS:
        st.caption(f"• {warning}")

st.sidebar.caption(
    "Isochrones : Valhalla FOSSGIS ou openrouteservice / OpenStreetMap (ODbL) — "
    "Population : WorldPop (CC BY 4.0) — "
    "Accessibilité territoriale : HeiGIT OpenAccessLens."
)
