#!/usr/bin/env python3
"""Vectorise un raster de temps d'accès HeiGIT sans créer de rayons.

Les pixels sont classés par seuil, puis rasterio.features.shapes regroupe les
cellules contiguës de même valeur. Les géométries invalides sont réparées et
l'attribut ``range`` (secondes) est conservé.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape
from shapely.validation import make_valid

DEFAULT_THRESHOLDS = list(range(600, 7201, 600))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raster", help="GeoTIFF de temps d'accès, valeurs en secondes")
    parser.add_argument("output", help="Sortie .gpkg, .geojson ou .shp")
    parser.add_argument("--thresholds", default=",".join(map(str, DEFAULT_THRESHOLDS)),
                        help="Seuils en secondes, séparés par des virgules")
    parser.add_argument("--native-classes", action="store_true",
                        help="Conserver les valeurs entières du raster au lieu de les reclasser")
    parser.add_argument("--simplify", type=float, default=0,
                        help="Tolérance optionnelle dans les unités du SCR source (0 = aucune)")
    args = parser.parse_args()

    thresholds = np.array(sorted({int(v) for v in args.thresholds.split(",")}), dtype=np.int32)
    if not len(thresholds):
        raise ValueError("Au moins un seuil est requis")

    with rasterio.open(args.raster) as src:
        raw = src.read(1, masked=True)
        valid = ~np.ma.getmaskarray(raw) & np.isfinite(raw.filled(np.nan))
        values = raw.filled(0)
        if args.native_classes:
            classified = values.astype(np.int32)
            valid &= classified > 0
        else:
            # Chaque temps est affecté au premier seuil supérieur ou égal.
            indices = np.searchsorted(thresholds, values, side="left")
            valid &= values >= 0
            valid &= indices < len(thresholds)
            classified = np.zeros(values.shape, dtype=np.int32)
            classified[valid] = thresholds[indices[valid]]

        records = []
        for geometry, value in shapes(classified, mask=valid, transform=src.transform, connectivity=8):
            geom = make_valid(shape(geometry))
            if args.simplify > 0:
                geom = make_valid(geom.simplify(args.simplify, preserve_topology=True))
            if not geom.is_empty:
                records.append({"range": int(value), "geometry": geom})
        crs = src.crs

    if not records:
        raise RuntimeError("Aucun polygone produit : vérifier NoData, unités et seuils")
    zones = gpd.GeoDataFrame(records, crs=crs)
    # Un MultiPolygon par classe facilite la jointure tout en conservant les
    # composantes non contiguës comme parties séparées.
    zones = zones.dissolve(by="range", as_index=False)
    zones.geometry = zones.geometry.map(make_valid)
    zones = zones.to_crs(4326)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    driver = "GPKG" if output.suffix.lower() == ".gpkg" else "GeoJSON" if output.suffix.lower() in {".json", ".geojson"} else None
    zones.to_file(output, driver=driver)
    print(f"{len(zones)} classes écrites dans {output} (EPSG:4326) : {zones['range'].tolist()}")


if __name__ == "__main__":
    main()
