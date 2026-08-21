# Outils SIG hors ligne

L’application web consomme directement les PMTiles et Parquet officiels OpenAccessLens. Ces scripts ne sont nécessaires que pour reproduire une analyse à partir de fichiers téléchargés localement sur HDX.

## Installation

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## 1. Raster HeiGIT vers isochrones vectorielles

Si une ressource HDX est fournie sous forme de GeoTIFF de temps d’accès (secondes) :

```bash
python scripts/vectorize_access_raster.py access_time.tif isochrones.gpkg
```

Le script :

1. respecte le masque NoData ;
2. classe les temps aux seuils 600, 1 200, …, 7 200 secondes ;
3. regroupe les cellules contiguës avec une connectivité de 8 ;
4. répare les géométries invalides ;
5. conserve le seuil dans `range` ;
6. écrit les polygones en EPSG:4326.

Aucune simplification n’est appliquée par défaut. Elle doit être demandée explicitement, dans les unités du SCR source :

```bash
python scripts/vectorize_access_raster.py access_time.tif isochrones.gpkg --simplify 25
```

Pour un raster déjà classé :

```bash
python scripts/vectorize_access_raster.py classes.tif isochrones.gpkg --native-classes
```

## 2. Jointure des structures et somme WorldPop

```bash
python scripts/compute_access.py \
  --facilities facilities.csv \
  --isochrones isochrones.gpkg \
  --population worldpop_population.tif \
  --output results.csv
```

Le script réalise une vraie jointure point-dans-polygone. Si les zones sont cumulatives et se superposent, il conserve le plus petit seuil. La population est calculée par somme zonale du raster WorldPop, sans buffer, appel de routage ou valeur aléatoire.

Le CSV d’entrée doit contenir au minimum :

```csv
nom,latitude,longitude
Hôpital 1,14.7167,-17.4677
Centre de santé 2,14.7150,-17.2730
```

## Remarque sur les données actuelles

Le catalogue OpenAccessLens utilisé par l’application publie déjà les isochrones en tuiles vectorielles PMTiles et les agrégats WorldPop en Parquet. La vectorisation ci-dessus vise seulement les ressources historiques ou téléchargements HDX distribués en raster. Toujours inspecter le SCR, l’unité, NoData et les métadonnées du fichier avant traitement.
