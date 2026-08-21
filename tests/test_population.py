"""Tests de la somme zonale WorldPop, du NoData et des métadonnées.

Les rasters utilisés ici sont **fabriqués volontairement** avec des valeurs
connues : c'est le seul moyen de vérifier qu'une somme zonale est exacte. Ce
sont des fixtures de test, pas des données affichées à l'utilisateur.
"""

from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Point, Polygon, box

from src.population import (
    WORLDPOP_PRODUCTS,
    PopulationDataError,
    agesex_urls,
    inspect_raster,
    raster_total,
    resolve_worldpop_url,
    zonal_sum,
    zonal_sums,
)


PIXEL = 0.01  # ~1,1 km à l'équateur


@pytest.fixture
def raster(tmp_path):
    """Grille 10x10, chaque pixel vaut 1 personne, coin haut-gauche (0, 1)."""
    path = tmp_path / "population.tif"
    data = np.ones((10, 10), dtype="float32")
    transform = from_origin(0.0, 1.0, PIXEL, PIXEL)

    with rasterio.open(
        path, "w", driver="GTiff", height=10, width=10, count=1,
        dtype="float32", crs="EPSG:4326", transform=transform, nodata=-99999.0,
    ) as dataset:
        dataset.write(data, 1)
    return path


@pytest.fixture
def raster_avec_nodata(tmp_path):
    """Même grille, mais la moitié gauche est NoData."""
    path = tmp_path / "population_nodata.tif"
    data = np.ones((10, 10), dtype="float32")
    data[:, :5] = -99999.0
    transform = from_origin(0.0, 1.0, PIXEL, PIXEL)

    with rasterio.open(
        path, "w", driver="GTiff", height=10, width=10, count=1,
        dtype="float32", crs="EPSG:4326", transform=transform, nodata=-99999.0,
    ) as dataset:
        dataset.write(data, 1)
    return path


# --------------------------------------------------------------------------- #
# Somme zonale                                                                 #
# --------------------------------------------------------------------------- #


def test_somme_zonale_couvre_tout_le_raster(raster):
    total = zonal_sum(raster, box(0.0, 0.9, 0.1, 1.0))
    assert total == pytest.approx(100.0)


def test_somme_zonale_partielle(raster):
    # Moitié gauche : colonnes 0 à 4, soit 50 pixels.
    total = zonal_sum(raster, box(0.0, 0.9, 0.05, 1.0))
    assert total == pytest.approx(50.0, rel=0.1)


def test_somme_zonale_geometrie_vide():
    assert zonal_sum("inexistant.tif", None) is None


def test_somme_zonale_hors_emprise_retourne_none(raster):
    # Volontairement loin du raster : None signifie « hors couverture »,
    # ce qui se distingue de 0 (« zone réellement inhabitée »).
    assert zonal_sum(raster, box(50.0, 50.0, 51.0, 51.0)) is None


def test_nodata_est_exclu(raster_avec_nodata):
    total = zonal_sum(raster_avec_nodata, box(0.0, 0.9, 0.1, 1.0))
    assert total == pytest.approx(50.0)


def test_valeurs_negatives_exclues(tmp_path):
    path = tmp_path / "negatif.tif"
    data = np.full((4, 4), -1.0, dtype="float32")
    data[0, 0] = 7.0
    with rasterio.open(
        path, "w", driver="GTiff", height=4, width=4, count=1,
        dtype="float32", crs="EPSG:4326", transform=from_origin(0.0, 1.0, PIXEL, PIXEL),
        nodata=None,
    ) as dataset:
        dataset.write(data, 1)

    assert zonal_sum(path, box(0.0, 0.96, 0.04, 1.0)) == pytest.approx(7.0)


def test_zone_entierement_nodata_vaut_zero(raster_avec_nodata):
    # Zone dans la moitié gauche masquée : 0 personne recensée, pas None,
    # car la zone est bien couverte par le raster.
    assert zonal_sum(raster_avec_nodata, box(0.001, 0.95, 0.03, 0.99)) == pytest.approx(0.0)


def test_sommes_multiples(raster):
    geometries = [box(0.0, 0.9, 0.05, 1.0), box(0.05, 0.9, 0.1, 1.0), None]
    results = zonal_sums(raster, geometries)

    assert len(results) == 3
    assert results[2] is None
    assert results[0] + results[1] == pytest.approx(100.0, rel=0.05)


def test_additivite_de_zones_disjointes(raster):
    """La somme de deux zones disjointes égale la somme de leur union."""
    left = box(0.0, 0.9, 0.05, 1.0)
    right = box(0.05, 0.9, 0.1, 1.0)
    union = box(0.0, 0.9, 0.1, 1.0)

    assert zonal_sum(raster, left) + zonal_sum(raster, right) == pytest.approx(
        zonal_sum(raster, union), rel=0.05
    )


def test_jointure_raster_polygone_polygone_troue(raster):
    """Un polygone troué exclut bien la population du trou."""
    exterior = [(0.0, 0.9), (0.1, 0.9), (0.1, 1.0), (0.0, 1.0)]
    hole = [(0.02, 0.92), (0.08, 0.92), (0.08, 0.98), (0.02, 0.98)]
    with_hole = Polygon(exterior, [hole])

    total_with_hole = zonal_sum(raster, with_hole)
    total_full = zonal_sum(raster, box(0.0, 0.9, 0.1, 1.0))

    assert total_with_hole < total_full
    assert total_with_hole == pytest.approx(total_full - Polygon(hole).area / (PIXEL ** 2), rel=0.3)


def test_point_sans_surface_ne_capte_aucun_pixel(raster):
    assert zonal_sum(raster, Point(0.05, 0.95)) in (0.0, None)


# --------------------------------------------------------------------------- #
# Population totale                                                            #
# --------------------------------------------------------------------------- #


def test_population_totale_du_raster(raster):
    assert raster_total(raster) == pytest.approx(100.0)


def test_population_totale_ignore_le_nodata(raster_avec_nodata):
    assert raster_total(raster_avec_nodata) == pytest.approx(50.0)


# --------------------------------------------------------------------------- #
# Densité                                                                      #
# --------------------------------------------------------------------------- #


def test_densite_est_ponderee_par_la_surface(tmp_path):
    """Un raster de densité doit être multiplié par la surface du pixel."""
    path = tmp_path / "densite.tif"
    data = np.full((10, 10), 100.0, dtype="float32")  # 100 hab/km²
    with rasterio.open(
        path, "w", driver="GTiff", height=10, width=10, count=1,
        dtype="float32", crs="EPSG:4326", transform=from_origin(0.0, 1.0, PIXEL, PIXEL),
        nodata=None,
    ) as dataset:
        dataset.write(data, 1)

    geometry = box(0.0, 0.9, 0.1, 1.0)
    as_count = zonal_sum(path, geometry, is_density=False)
    as_density = zonal_sum(path, geometry, is_density=True)

    assert as_count == pytest.approx(10_000.0)
    # 100 pixels d'environ 1,23 km² à ~0,95° de latitude, à 100 hab/km².
    assert as_density == pytest.approx(12_300.0, rel=0.15)
    assert as_density != as_count


# --------------------------------------------------------------------------- #
# Métadonnées                                                                  #
# --------------------------------------------------------------------------- #


def test_metadonnees_lues_dans_le_fichier(raster):
    metadata = inspect_raster(raster, year=2020, method="test")

    assert metadata.crs == "EPSG:4326"
    assert metadata.width == 10 and metadata.height == 10
    assert metadata.pixel_size_x == pytest.approx(PIXEL)
    assert metadata.nodata == pytest.approx(-99999.0)
    assert metadata.dtype == "float32"
    assert metadata.unit == "personnes par pixel"
    assert metadata.is_density is False
    assert metadata.year == 2020
    assert metadata.approximate_resolution_m == pytest.approx(1113.0, rel=0.05)


def test_metadonnees_serialisables(raster):
    document = inspect_raster(raster, year=2020).to_dict()
    assert document["crs"] == "EPSG:4326"
    assert document["unite_pixel"] == "personnes par pixel"
    assert "emprise" in document


def test_raster_absent_leve_une_erreur_explicite(tmp_path):
    with pytest.raises(PopulationDataError, match="introuvable"):
        inspect_raster(tmp_path / "absent.tif")


def test_raster_corrompu_leve_une_erreur(tmp_path):
    path = tmp_path / "corrompu.tif"
    path.write_bytes(b"pas un geotiff")
    with pytest.raises(PopulationDataError, match="illisible"):
        inspect_raster(path)


def test_detection_de_densite_par_le_nom(tmp_path):
    path = tmp_path / "sen_pd_2020_1km_density.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=2, width=2, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(0.0, 1.0, PIXEL, PIXEL),
    ) as dataset:
        dataset.write(np.ones((2, 2), dtype="float32"), 1)

    metadata = inspect_raster(path)
    assert metadata.is_density is True
    assert metadata.unit == "personnes par km²"


# --------------------------------------------------------------------------- #
# Catalogue WorldPop                                                           #
# --------------------------------------------------------------------------- #


def test_url_worldpop_bien_formee():
    urls = WORLDPOP_PRODUCTS["unconstrained_100m"].urls("sen", 2020)
    assert urls[0].endswith("/Global_2000_2020/2020/SEN/sen_ppp_2020.tif")


def test_url_1km_bien_formee():
    urls = WORLDPOP_PRODUCTS["unconstrained_1km"].urls("SEN", 2015)
    assert urls[0].endswith("/Global_2000_2020_1km/2015/SEN/sen_ppp_2015_1km_Aggregated.tif")


def test_url_agesex_bien_formee():
    assert agesex_urls("sen", 2020, "f", 15)[0].endswith("/SEN/sen_f_15_2020.tif")


def test_annee_indisponible_refusee():
    with pytest.raises(PopulationDataError, match="Année"):
        resolve_worldpop_url(WORLDPOP_PRODUCTS["constrained_100m"], "sen", 2005)


def test_absence_de_donnees_worldpop_est_signalee(monkeypatch):
    """Sans raster disponible, l'erreur est explicite — jamais une valeur inventée."""
    monkeypatch.setattr("src.population.url_exists", lambda url, timeout=30: False)

    with pytest.raises(PopulationDataError, match="Aucun raster WorldPop"):
        resolve_worldpop_url(WORLDPOP_PRODUCTS["unconstrained_1km"], "sen", 2020)


def test_resolution_retient_la_premiere_url_disponible(monkeypatch):
    product = WORLDPOP_PRODUCTS["constrained_100m"]
    available = product.urls("sen", 2020)[1]  # la variante BSGM
    monkeypatch.setattr("src.population.url_exists", lambda url, timeout=30: url == available)

    assert resolve_worldpop_url(product, "sen", 2020) == available
