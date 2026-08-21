# Calcul des résultats réels — option B

Ce script fait le calcul par établissement : une isochrone est demandée pour chaque point, puis la population WorldPop est sommée dans cette géométrie.

## Préparation

1. Installer Python 3.10+ et les dépendances :

```bash
python -m venv .venv
. .venv/bin/activate       # Windows : .venv\\Scripts\\activate
pip install -r requirements.txt
```

2. Créer une clé gratuite OpenRouteService : <https://openrouteservice.org/dev/#/signup>.
3. Télécharger le GeoTIFF WorldPop 100 m du pays et de l’année voulus depuis <https://www.worldpop.org/geodata/>.
4. Préparer `facilities.csv` avec au minimum :

```csv
nom,latitude,longitude
HF Dakar,14.7167,-17.4677
Clinique Rufisque,14.715,-17.273
```

## Calcul

```bash
export ORS_API_KEY="votre_cle"
python scripts/compute_access.py \
  --facilities facilities.csv \
  --population sen_ppp_2020.tif \
  --minutes 30,60 \
  --profile driving-car \
  --output public/results.json
```

Le résultat `results.json` est compatible avec l’interface web. Le calcul est local : la clé ORS n’est jamais publiée sur GitHub Pages. Pour un grand nombre de points, le quota ORS et le poids du raster doivent être pris en compte.

## Limites méthodologiques

- Il s’agit d’un temps motorisé ou piéton calculé sur le réseau OpenStreetMap par ORS, pas d’un temps réel avec trafic.
- WorldPop fournit une estimation de population par cellule ; le résultat est une somme zonale, pas un comptage individuel.
- Si le calcul par point n’est pas possible (quota de routage ou données indisponibles), l’alternative est l’option A : exploiter les indicateurs agrégés HeiGIT disponibles par pays sur HDX.
