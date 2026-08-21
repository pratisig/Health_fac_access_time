# Outil SIG hors ligne

L'application consomme directement les PMTiles et Parquet officiels
OpenAccessLens, et calcule ses propres isochrones via openrouteservice. Ce
script ne sert qu'à un cas particulier : reproduire une analyse à partir d'une
ressource HDX distribuée sous forme de **raster de temps d'accès**.

> Le script `compute_access.py` a été retiré. Sa logique — localiser un point
> dans une isochrone existante puis lui attribuer l'agrégat correspondant —
> répondait à une autre question que celle de l'application. La somme zonale
> WorldPop dans des isochrones propres à chaque structure est désormais assurée
> par `src/population.py` et `src/spatial_analysis.py`, avec tests.

## Installation

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r ../requirements.txt
```

## Raster de temps d'accès vers isochrones vectorielles

```bash
python scripts/vectorize_access_raster.py access_time.tif isochrones.gpkg
```

Le script :

1. respecte le masque NoData ;
2. classe les temps aux seuils 600, 1 200, … 7 200 secondes ;
3. regroupe les cellules contiguës avec une connectivité de 8 ;
4. répare les géométries invalides ;
5. conserve le seuil dans `range` ;
6. écrit les polygones en EPSG:4326.

Aucune simplification n'est appliquée par défaut. Elle doit être demandée
explicitement, dans les unités du SCR source :

```bash
python scripts/vectorize_access_raster.py access_time.tif isochrones.gpkg --simplify 25
```

Pour un raster déjà classé :

```bash
python scripts/vectorize_access_raster.py classes.tif isochrones.gpkg --native-classes
```

Toujours inspecter le SCR, l'unité, la valeur NoData et les métadonnées du
fichier avant traitement.
