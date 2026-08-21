"""Tests d'import, de validation des coordonnées et de reprojection."""

from __future__ import annotations

import io
import zipfile

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from src.facility_io import (
    FacilityImportError,
    deduplicate,
    facilities_to_geodataframe,
    find_column,
    read_csv_facilities,
    read_geofile_facilities,
    read_shapefile_zip,
    reproject_to_wgs84,
    validate_coordinates,
)
from src.models import Facility


# --------------------------------------------------------------------------- #
# Parsing CSV                                                                  #
# --------------------------------------------------------------------------- #


def test_csv_canonique():
    payload = (
        "nom,latitude,longitude\n"
        "Hôpital 1,14.7167,-17.4677\n"
        "Centre de santé 2,14.7150,-17.2730\n"
    ).encode("utf-8")

    facilities, rejected = read_csv_facilities(payload)

    assert len(facilities) == 2
    assert rejected == []
    assert facilities[0].name == "Hôpital 1"
    assert facilities[0].latitude == pytest.approx(14.7167)
    assert facilities[1].longitude == pytest.approx(-17.2730)


@pytest.mark.parametrize(
    "header",
    [
        "name,lat,lon",
        "structure,lat,lng",
        "établissement,latitude,longitude",
        "Nom,Y,X",
        "facility_name,LAT,LONG",
    ],
)
def test_csv_variantes_de_colonnes(header):
    payload = f"{header}\nPoste de santé,14.7,-17.4\n".encode("utf-8")
    facilities, _ = read_csv_facilities(payload)
    assert len(facilities) == 1
    assert facilities[0].latitude == pytest.approx(14.7)
    assert facilities[0].longitude == pytest.approx(-17.4)


def test_csv_separateur_point_virgule_et_virgule_decimale():
    payload = "nom;latitude;longitude\nCentre A;14,7167;-17,4677\n".encode("utf-8")
    facilities, _ = read_csv_facilities(payload)
    assert facilities[0].latitude == pytest.approx(14.7167)
    assert facilities[0].longitude == pytest.approx(-17.4677)


def test_csv_conserve_les_attributs_supplementaires():
    payload = "nom,latitude,longitude,type,lits\nHôpital,14.7,-17.4,régional,120\n".encode("utf-8")
    facilities, _ = read_csv_facilities(payload)
    assert facilities[0].attributes["type"] == "régional"


def test_csv_nomme_les_structures_sans_nom():
    payload = "latitude,longitude\n14.7,-17.4\n14.8,-17.5\n".encode("utf-8")
    facilities, _ = read_csv_facilities(payload)
    assert [facility.name for facility in facilities] == ["Structure 1", "Structure 2"]


def test_csv_sans_colonnes_de_coordonnees_leve_une_erreur():
    payload = "nom,region\nHôpital,Dakar\n".encode("utf-8")
    with pytest.raises(FacilityImportError, match="Colonnes de coordonnées"):
        read_csv_facilities(payload)


def test_csv_lignes_invalides_rejetees_sans_bloquer_les_valides():
    payload = (
        "nom,latitude,longitude\n"
        "Bonne,14.7,-17.4\n"
        "Latitude hors bornes,95.0,-17.4\n"
        "Non numérique,abc,-17.4\n"
        "Zéro,0,0\n"
    ).encode("utf-8")

    facilities, rejected = read_csv_facilities(payload)

    assert len(facilities) == 1
    assert len(rejected) == 3
    assert any("hors bornes" in message for message in rejected)


def test_csv_entierement_invalide_leve_une_erreur():
    payload = "nom,latitude,longitude\nMauvaise,999,999\n".encode("utf-8")
    with pytest.raises(FacilityImportError):
        read_csv_facilities(payload)


def test_csv_latin1():
    payload = "nom,latitude,longitude\nHôpital,14.7,-17.4\n".encode("latin-1")
    facilities, _ = read_csv_facilities(payload)
    assert len(facilities) == 1


# --------------------------------------------------------------------------- #
# Validation des coordonnées                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "latitude,longitude",
    [(14.7167, -17.4677), (-90, -180), (90, 180), (0, 10), ("14,7", "-17,4")],
)
def test_coordonnees_valides(latitude, longitude):
    lat, lon = validate_coordinates(latitude, longitude)
    assert -90 <= lat <= 90
    assert -180 <= lon <= 180


@pytest.mark.parametrize(
    "latitude,longitude",
    [(91, 0), (-91, 0), (0, 181), (0, -181), (None, 0), ("abc", 0), (0, 0), ("", "")],
)
def test_coordonnees_invalides(latitude, longitude):
    with pytest.raises(FacilityImportError):
        validate_coordinates(latitude, longitude)


# --------------------------------------------------------------------------- #
# Reprojection                                                                 #
# --------------------------------------------------------------------------- #


def test_reprojection_utm_vers_wgs84():
    # UTM 28N (EPSG:32628) couvre le Sénégal.
    frame = gpd.GeoDataFrame(
        {"nom": ["Dakar"]}, geometry=[Point(233_000, 1_628_000)], crs="EPSG:32628"
    )
    reprojected = reproject_to_wgs84(frame)

    assert reprojected.crs.to_string() == "EPSG:4326"
    assert -18.5 < reprojected.geometry.iloc[0].x < -16.5
    assert 13.5 < reprojected.geometry.iloc[0].y < 15.5


def test_reprojection_conserve_le_wgs84():
    frame = gpd.GeoDataFrame({"nom": ["A"]}, geometry=[Point(-17.4, 14.7)], crs="EPSG:4326")
    assert reproject_to_wgs84(frame).crs.to_string() == "EPSG:4326"


def test_reprojection_sans_crs_est_refusee():
    frame = gpd.GeoDataFrame({"nom": ["A"]}, geometry=[Point(-17.4, 14.7)])
    with pytest.raises(FacilityImportError, match="système de coordonnées"):
        reproject_to_wgs84(frame)


def test_geojson_reprojete(tmp_path):
    path = tmp_path / "structures.geojson"
    gpd.GeoDataFrame(
        {"nom": ["Poste"]}, geometry=[Point(233_000, 1_628_000)], crs="EPSG:32628"
    ).to_file(path, driver="GeoJSON")

    facilities, _ = read_geofile_facilities(path)

    assert len(facilities) == 1
    assert -18.5 < facilities[0].longitude < -16.5


def test_polygone_reduit_a_un_point_interieur(tmp_path):
    path = tmp_path / "emprises.geojson"
    polygon = Polygon([(-17.5, 14.7), (-17.4, 14.7), (-17.4, 14.8), (-17.5, 14.8)])
    gpd.GeoDataFrame({"nom": ["Hôpital"]}, geometry=[polygon], crs="EPSG:4326").to_file(
        path, driver="GeoJSON"
    )

    facilities, _ = read_geofile_facilities(path)

    assert len(facilities) == 1
    assert polygon.contains(Point(facilities[0].longitude, facilities[0].latitude))


def test_geopackage(tmp_path):
    path = tmp_path / "structures.gpkg"
    gpd.GeoDataFrame(
        {"nom": ["A", "B"]},
        geometry=[Point(-17.4, 14.7), Point(-17.3, 14.8)],
        crs="EPSG:4326",
    ).to_file(path, layer="structures", driver="GPKG")

    facilities, _ = read_geofile_facilities(path)
    assert len(facilities) == 2


# --------------------------------------------------------------------------- #
# Shapefile ZIP                                                                #
# --------------------------------------------------------------------------- #


def _shapefile_zip(tmp_path, *, with_prj: bool = True) -> bytes:
    directory = tmp_path / "shp"
    directory.mkdir(exist_ok=True)
    gpd.GeoDataFrame(
        {"nom": ["Hôpital régional"]}, geometry=[Point(-17.4677, 14.7167)], crs="EPSG:4326"
    ).to_file(directory / "structures.shp", driver="ESRI Shapefile")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for item in directory.iterdir():
            if not with_prj and item.suffix.lower() == ".prj":
                continue
            archive.write(item, item.name)
    return buffer.getvalue()


def test_shapefile_zip(tmp_path):
    facilities, _ = read_shapefile_zip(_shapefile_zip(tmp_path))
    assert len(facilities) == 1
    assert facilities[0].name == "Hôpital régional"


def test_shapefile_zip_sans_prj_est_refuse(tmp_path):
    with pytest.raises(FacilityImportError, match=".prj"):
        read_shapefile_zip(_shapefile_zip(tmp_path, with_prj=False))


def test_zip_invalide():
    with pytest.raises(FacilityImportError):
        read_shapefile_zip(b"ceci n'est pas un zip")


def test_zip_sans_shp(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("lisez-moi.txt", "vide")
    with pytest.raises(FacilityImportError, match=".shp"):
        read_shapefile_zip(buffer.getvalue())


# --------------------------------------------------------------------------- #
# Utilitaires                                                                  #
# --------------------------------------------------------------------------- #


def test_find_column_prefere_la_correspondance_exacte():
    columns = ["latitude_source", "latitude", "longitude"]
    assert find_column(columns, ("latitude", "lat")) == "latitude"


def test_deduplication():
    facilities = [
        Facility(name="A", latitude=14.7, longitude=-17.4),
        Facility(name="B", latitude=14.7, longitude=-17.4),
        Facility(name="C", latitude=14.8, longitude=-17.4),
    ]
    assert len(deduplicate(facilities)) == 2


def test_conversion_en_geodataframe():
    facilities = [Facility(name="A", latitude=14.7, longitude=-17.4)]
    frame = facilities_to_geodataframe(facilities)

    assert frame.crs.to_string() == "EPSG:4326"
    assert frame.geometry.iloc[0].x == pytest.approx(-17.4)


def test_geodataframe_vide_conserve_le_crs():
    assert facilities_to_geodataframe([]).crs.to_string() == "EPSG:4326"


def test_facility_ors_coordinates_est_lon_lat():
    facility = Facility(name="A", latitude=14.7167, longitude=-17.4677)
    assert facility.ors_coordinates == [-17.4677, 14.7167]
