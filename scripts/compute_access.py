#!/usr/bin/env python3
"""Jointure locale déterministe : points -> isochrones HeiGIT -> WorldPop.

Ce script n'appelle aucun moteur d'isochrones et ne crée aucun buffer. Il est utile
lorsqu'on dispose d'un export vectoriel HeiGIT et du raster WorldPop correspondant.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd
from rasterstats import zonal_stats


def find_column(columns, candidates):
    normalized = {str(c).strip().lower(): c for c in columns}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    raise ValueError(f"Colonne absente (attendu : {', '.join(candidates)})")


def read_facilities(path: str) -> gpd.GeoDataFrame:
    if Path(path).suffix.lower() == ".csv":
        frame = pd.read_csv(path)
        lat = find_column(frame.columns, ["latitude", "lat"])
        lon = find_column(frame.columns, ["longitude", "lon", "lng"])
        name = find_column(frame.columns, ["nom", "name", "facility", "etablissement"])
        frame = frame.dropna(subset=[lat, lon]).copy()
        frame["nom"] = frame[name].astype(str)
        return gpd.GeoDataFrame(frame, geometry=gpd.points_from_xy(frame[lon], frame[lat]), crs="EPSG:4326")
    points = gpd.read_file(path)
    if not all(points.geometry.geom_type.isin(["Point", "MultiPoint"])):
        raise ValueError("Le fichier de structures doit contenir uniquement des points")
    if "nom" not in points:
        points["nom"] = [f"Structure {i + 1}" for i in range(len(points))]
    return points.to_crs(4326)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facilities", required=True, help="CSV ou fichier vectoriel de points")
    parser.add_argument("--isochrones", required=True, help="GeoPackage/GeoJSON/SHP vectoriel HeiGIT")
    parser.add_argument("--population", required=True, help="GeoTIFF WorldPop (population par pixel)")
    parser.add_argument("--range-field", default="range", help="Attribut du seuil en secondes")
    parser.add_argument("--output", default="results.csv")
    args = parser.parse_args()

    facilities = read_facilities(args.facilities)
    zones = gpd.read_file(args.isochrones)
    if args.range_field not in zones:
        raise ValueError(f"Attribut {args.range_field!r} absent des isochrones")
    zones = zones[[args.range_field, "geometry"]].dropna().copy()
    zones[args.range_field] = pd.to_numeric(zones[args.range_field], errors="raise")
    zones = zones.to_crs(4326)

    # Une géométrie par seuil. Les cellules/parties contiguës ont déjà été
    # regroupées lors de la vectorisation ; dissolve conserve les MultiPolygons.
    dissolved = zones.dissolve(by=args.range_field, as_index=False)
    pop = zonal_stats(dissolved.geometry, args.population, stats=["sum"], nodata=0, all_touched=False)
    dissolved["population_worldpop"] = [round(item.get("sum") or 0) for item in pop]

    joined = gpd.sjoin(facilities, dissolved, predicate="within", how="left")
    # Des isochrones cumulatives peuvent se recouvrir : le seuil minimal est le
    # temps d'accès pertinent, exactement comme dans l'application web.
    joined = joined.sort_values(args.range_field).groupby(joined.index).first()
    joined["temps_minutes"] = joined[args.range_field] / 60
    columns = ["nom", "geometry", args.range_field, "temps_minutes", "population_worldpop"]
    result = gpd.GeoDataFrame(joined[columns], geometry="geometry", crs=4326)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() in {".geojson", ".json"}:
        result.to_file(output, driver="GeoJSON")
    else:
        table = result.drop(columns="geometry")
        table["longitude"] = result.geometry.x
        table["latitude"] = result.geometry.y
        table.to_csv(output, index=False)
    print(f"{len(result)} structures écrites dans {output}")


if __name__ == "__main__":
    main()
