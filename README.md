# ACCESS/CARE

Prototype d’outil d’analyse de l’accès aux établissements de santé.

## Fonctionnalités

- import d’un CSV de points (colonnes `nom`, `latitude`, `longitude`) ;
- chargement automatique de résultats réels dans `public/results.json` lorsqu’ils ont été générés par le pipeline option B ;
- choix du temps d’accès : 15, 30, 45 ou 60 minutes ;
- carte interactive Leaflet avec points, zones de temps et popup ;
- synthèse et détail par établissement ;
- export des résultats au format CSV ;
- données d’exemple chargées à l’ouverture pour découvrir l’interface.

## Lancer le prototype

Le projet est volontairement sans dépendance ni étape de compilation. Depuis ce dossier :

```bash
python3 -m http.server 8000 --bind 0.0.0.0
```

Puis ouvrir `http://localhost:8000`.

> Sans `public/results.json`, les données d’exemple restent des estimations d’interface. Pour calculer de vraies valeurs par établissement (option B), voir [`scripts/README.md`](scripts/README.md). Le pipeline local utilise un GeoTIFF WorldPop et des isochrones OpenRouteService, puis produit un JSON que GitHub Pages peut publier. Les fichiers importés dans le navigateur restent locaux.
