"""Import, validation et normalisation des structures de santé.

Formats acceptés : CSV, GeoJSON, GeoPackage, Shapefile (ZIP), plus les points
dessinés dans l'application. Toute géométrie est reprojetée en EPSG:4326 avant
le routage, et toute coordonnée est validée.
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable, Sequence

import geopandas as gpd
import pandas as pd

from .config import WGS84
from .models import Facility

# --------------------------------------------------------------------------- #
# Alias de colonnes                                                            #
# --------------------------------------------------------------------------- #

NAME_ALIASES: tuple[str, ...] = (
    "nom", "name", "structure", "etablissement", "établissement", "facility",
    "facility_name", "nom_structure", "libelle", "libellé", "denomination",
    "dénomination", "designation", "désignation", "label", "title", "nom_fs",
)

LATITUDE_ALIASES: tuple[str, ...] = (
    "latitude", "lat", "y", "ycoord", "y_coord", "coord_y", "lattitude",
    "gps_latitude", "point_y", "_y",
)

LONGITUDE_ALIASES: tuple[str, ...] = (
    "longitude", "lon", "lng", "long", "x", "xcoord", "x_coord", "coord_x",
    "gps_longitude", "point_x", "_x",
)


class FacilityImportError(ValueError):
    """Erreur d'import bloquante et explicite (jamais silencieuse)."""


def _normalise(label: Any) -> str:
    """Normalise un nom de colonne : minuscules, sans espaces ni séparateurs."""
    text = str(label).strip().lower()
    for char in (" ", "-", ".", "/", "\\", "'", "’"):
        text = text.replace(char, "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def find_column(columns: Iterable[Any], aliases: Sequence[str]) -> str | None:
    """Retrouve la colonne correspondant à l'un des alias fournis.

    La correspondance exacte est prioritaire sur la correspondance partielle,
    pour éviter qu'une colonne ``latitude_source`` ne prime sur ``latitude``.
    """
    mapping = {_normalise(column): column for column in columns}

    for alias in aliases:
        if alias in mapping:
            return mapping[alias]

    for alias in aliases:
        for normalised, original in mapping.items():
            if normalised.startswith(alias) or normalised.endswith(alias):
                return original
    return None


def _to_float(value: Any) -> float | None:
    """Convertit en flottant en tolérant la virgule décimale et les espaces."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return None if pd.isna(result) else result

    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        result = float(text)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(result) else result


def validate_coordinates(latitude: Any, longitude: Any) -> tuple[float, float]:
    """Valide un couple de coordonnées géographiques.

    Lève ``FacilityImportError`` si les valeurs sont absentes, non numériques ou
    hors des bornes WGS84. Le point (0, 0) est refusé : dans les jeux de données
    de terrain il traduit presque toujours une coordonnée manquante.
    """
    lat = _to_float(latitude)
    lon = _to_float(longitude)

    if lat is None or lon is None:
        raise FacilityImportError(f"Coordonnées non numériques : ({latitude!r}, {longitude!r})")
    if not -90.0 <= lat <= 90.0:
        raise FacilityImportError(f"Latitude hors bornes [-90, 90] : {lat}")
    if not -180.0 <= lon <= 180.0:
        raise FacilityImportError(f"Longitude hors bornes [-180, 180] : {lon}")
    if lat == 0.0 and lon == 0.0:
        raise FacilityImportError("Coordonnées (0, 0) refusées : valeur manquante probable")
    return lat, lon


# --------------------------------------------------------------------------- #
# CSV                                                                          #
# --------------------------------------------------------------------------- #


def read_csv_facilities(
    source: Any,
    *,
    encoding: str = "utf-8",
) -> tuple[list[Facility], list[str]]:
    """Lit un CSV de structures.

    Renvoie la liste des structures valides et la liste des messages de rejet.
    Le séparateur est détecté automatiquement (``,``, ``;``, tabulation).
    """
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    elif isinstance(source, bytes):
        raw = source
    else:
        raw = source.read()
        if isinstance(raw, str):
            raw = raw.encode(encoding)

    text: str | None = None
    for candidate in (encoding, "utf-8", "utf-8-sig", "latin-1"):
        try:
            text = raw.decode(candidate)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        raise FacilityImportError("Encodage du CSV non reconnu (essayez UTF-8)")

    try:
        frame = pd.read_csv(io.StringIO(text), sep=None, engine="python")
    except Exception as error:  # pragma: no cover - dépend de pandas
        raise FacilityImportError(f"CSV illisible : {error}") from error

    if frame.empty:
        raise FacilityImportError("Le CSV ne contient aucune ligne")

    return _facilities_from_frame(frame, source_label="csv")


def _facilities_from_frame(
    frame: pd.DataFrame,
    *,
    source_label: str,
) -> tuple[list[Facility], list[str]]:
    lat_column = find_column(frame.columns, LATITUDE_ALIASES)
    lon_column = find_column(frame.columns, LONGITUDE_ALIASES)

    if lat_column is None or lon_column is None:
        raise FacilityImportError(
            "Colonnes de coordonnées introuvables. Attendu une colonne de latitude "
            f"({', '.join(LATITUDE_ALIASES[:4])}...) et une colonne de longitude "
            f"({', '.join(LONGITUDE_ALIASES[:4])}...). "
            f"Colonnes trouvées : {', '.join(str(c) for c in frame.columns)}"
        )

    name_column = find_column(frame.columns, NAME_ALIASES)
    extra_columns = [
        column for column in frame.columns
        if column not in {lat_column, lon_column, name_column}
    ]

    facilities: list[Facility] = []
    rejected: list[str] = []

    for position, (_, row) in enumerate(frame.iterrows(), start=1):
        try:
            latitude, longitude = validate_coordinates(row[lat_column], row[lon_column])
        except FacilityImportError as error:
            rejected.append(f"Ligne {position} : {error}")
            continue

        if name_column is not None and not pd.isna(row[name_column]):
            name = str(row[name_column]).strip()
        else:
            name = ""
        if not name:
            name = f"Structure {position}"

        attributes = {
            str(column): row[column]
            for column in extra_columns
            if not pd.isna(row[column])
        }
        facilities.append(
            Facility(
                name=name,
                latitude=latitude,
                longitude=longitude,
                source=source_label,
                attributes=attributes,
            )
        )

    if not facilities:
        raise FacilityImportError(
            "Aucune structure valide. " + (rejected[0] if rejected else "Fichier vide.")
        )
    return facilities, rejected


# --------------------------------------------------------------------------- #
# Fichiers géographiques                                                       #
# --------------------------------------------------------------------------- #


def reproject_to_wgs84(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reprojette en EPSG:4326.

    Un CRS absent est une erreur : le supposer conduirait à des coordonnées
    fausses et donc à des isochrones fausses.
    """
    if frame.crs is None:
        raise FacilityImportError(
            "Le fichier ne déclare aucun système de coordonnées (.prj manquant ?). "
            "Impossible de reprojeter sans risque : renseignez le CRS à la source."
        )
    if frame.crs.to_string() == WGS84:
        return frame
    return frame.to_crs(WGS84)


def _points_from_geodataframe(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Réduit une couche à des points : centroïdes pour polygones et lignes."""
    if frame.empty:
        raise FacilityImportError("La couche ne contient aucune entité")

    geom_types = set(frame.geometry.geom_type.dropna().unique())
    if geom_types <= {"Point"}:
        return frame

    if geom_types <= {"MultiPoint"} or "MultiPoint" in geom_types:
        frame = frame.explode(index_parts=False).reset_index(drop=True)
        geom_types = set(frame.geometry.geom_type.dropna().unique())
        if geom_types <= {"Point"}:
            return frame

    # Polygones (emprises de bâtiments hospitaliers) : le centroïde est le point
    # de routage. Calculé en projection équivalente pour rester à l'intérieur.
    projected = frame.to_crs(frame.estimate_utm_crs())
    centroids = projected.geometry.representative_point().to_crs(WGS84)
    result = frame.copy()
    result = result.set_geometry(centroids.values, crs=WGS84)
    return result


def read_geofile_facilities(
    path: str | Path,
    *,
    layer: str | None = None,
) -> tuple[list[Facility], list[str]]:
    """Lit un GeoJSON, GeoPackage, Shapefile ou tout format lisible par GDAL."""
    try:
        frame = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
    except Exception as error:
        raise FacilityImportError(f"Fichier géographique illisible : {error}") from error

    if frame.empty:
        raise FacilityImportError("Le fichier géographique est vide")

    frame = frame[~frame.geometry.isna()].copy()
    if frame.empty:
        raise FacilityImportError("Aucune géométrie exploitable dans le fichier")

    frame = reproject_to_wgs84(frame)
    frame = _points_from_geodataframe(frame)

    attribute_columns = [column for column in frame.columns if column != frame.geometry.name]
    table = pd.DataFrame(frame.drop(columns=frame.geometry.name))
    table["latitude"] = frame.geometry.y.values
    table["longitude"] = frame.geometry.x.values

    if find_column(attribute_columns, NAME_ALIASES) is None:
        table["nom"] = [f"Structure {index + 1}" for index in range(len(table))]

    return _facilities_from_frame(table, source_label=Path(str(path)).suffix.lstrip(".") or "geofile")


def read_shapefile_zip(data: bytes | Any) -> tuple[list[Facility], list[str]]:
    """Lit un Shapefile fourni sous forme d'archive ZIP.

    L'archive doit contenir au minimum ``.shp``, ``.shx``, ``.dbf`` et, pour une
    reprojection fiable, ``.prj``.
    """
    payload = data if isinstance(data, bytes) else data.read()

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        raise FacilityImportError("Archive ZIP invalide") from error

    names = archive.namelist()
    shp_members = [name for name in names if name.lower().endswith(".shp")]
    if not shp_members:
        raise FacilityImportError("Aucun fichier .shp dans l'archive ZIP")

    stem = Path(shp_members[0]).stem
    required = {".shp", ".shx", ".dbf"}
    present = {
        Path(name).suffix.lower()
        for name in names
        if Path(name).stem == stem
    }
    missing = required - present
    if missing:
        raise FacilityImportError(
            f"Shapefile incomplet, fichiers manquants : {', '.join(sorted(missing))}"
        )

    with tempfile.TemporaryDirectory() as directory:
        archive.extractall(directory)
        target = next(Path(directory).rglob("*.shp"))
        if ".prj" not in present:
            raise FacilityImportError(
                "Le Shapefile ne contient pas de fichier .prj : le système de "
                "coordonnées est inconnu et la reprojection serait une supposition."
            )
        return read_geofile_facilities(target)


# --------------------------------------------------------------------------- #
# Conversions                                                                  #
# --------------------------------------------------------------------------- #


def facilities_to_geodataframe(facilities: Sequence[Facility]) -> gpd.GeoDataFrame:
    """Convertit une liste de structures en GeoDataFrame EPSG:4326."""
    if not facilities:
        return gpd.GeoDataFrame(
            {"identifiant": [], "structure": [], "latitude": [], "longitude": [], "source": []},
            geometry=[],
            crs=WGS84,
        )
    records = [facility.to_dict() for facility in facilities]
    return gpd.GeoDataFrame(
        pd.DataFrame(records),
        geometry=[facility.geometry for facility in facilities],
        crs=WGS84,
    )


def deduplicate(facilities: Sequence[Facility], *, precision: int = 6) -> list[Facility]:
    """Supprime les doublons de position (mêmes coordonnées arrondies)."""
    seen: set[tuple[float, float]] = set()
    unique: list[Facility] = []
    for facility in facilities:
        key = (round(facility.latitude, precision), round(facility.longitude, precision))
        if key in seen:
            continue
        seen.add(key)
        unique.append(facility)
    return unique
