# Health HeiGIT Isochrone

Application web statique d’analyse d’accessibilité aux structures de santé à partir des **données réelles HeiGIT / OpenAccessLens** et des agrégats démographiques **WorldPop**.

L’application remplace entièrement l’ancien prototype : elle ne dessine aucun cercle, ne génère aucun chiffre aléatoire et ne fournit aucun résultat de secours lorsque les sources officielles sont indisponibles.

## Fonctionnalités

- catalogue de pays chargé dynamiquement depuis OpenAccessLens ;
- choix entre `hospitals` et `primary_healthcare` ;
- affichage direct des isochrones vectorielles HeiGIT en PMTiles ;
- import CSV (`nom`, `latitude`, `longitude`) ;
- import Shapefile ponctuel (`.zip`, recommandé, ou `.shp`) ;
- ajout d’une ou plusieurs structures par clic sur la carte ;
- jointure spatiale de chaque point avec la plus petite classe de temps qui le contient ;
- lecture côté navigateur des agrégats WorldPop en Parquet avec DuckDB-Wasm ;
- indicateurs démographiques disponibles dynamiquement (population totale, groupes d’âge, etc.) ;
- résultats dans les popups, le tableau et un export CSV traçable.

## Structure vérifiée des données OpenAccessLens

Les chemins et le schéma ci-dessous sont ceux utilisés par l’application officielle [GIScience/open-access-lens](https://github.com/GIScience/open-access-lens).

### Catalogue

```text
https://hot.storage.heigit.org/heigit-hdx-public/access/aux/countries.yaml
```

Le YAML est indexé par code ISO alpha-3. L’application utilise le nom du pays et son code en minuscules.

### Isochrones

```text
/access/aux/tiles/{iso3}/{iso3}_{category}_isochrones.pmtiles
```

- format : PMTiles contenant des tuiles vectorielles ;
- géométrie : Polygon / MultiPolygon issue du calcul d’accessibilité HeiGIT ;
- rendu cartographique : coordonnées Web Mercator des tuiles, exposées par MapLibre en longitude/latitude ;
- attribut de classe : `range`, en **secondes** ;
- niveaux santé observés : 600, 1 200, …, 7 200 secondes, soit 10 à 120 minutes ;
- catégories : `hospitals` et `primary_healthcare` ;
- modèle : déplacement motorisé vers le service, fondé sur openrouteservice et OpenStreetMap.

Ces données sont déjà vectorielles. Il n’est donc ni nécessaire ni correct de créer des rayons autour des points importés. Le nom interne de la couche vectorielle est lu dans les métadonnées de chaque PMTiles au lieu d’être supposé.

### Statistiques de population

```text
/access/aux/stats/{iso3}/category={category}/data.parquet
```

Colonnes exploitées :

| colonne | signification |
|---|---|
| `admin_level` | niveau d’agrégation (`ADM0` pour le pays) |
| `range` | seuil en secondes |
| `population_type` | groupe démographique |
| `population` | population WorldPop dans le seuil |
| `population_share` | part de la population de référence |

Les statistiques ont déjà été produites par HeiGIT en superposant les isochrones à WorldPop 100 m. Elles sont lues directement ; aucune estimation arbitraire n’est appliquée.

## Interprétation correcte

Un point ajouté par l’utilisateur ne sert pas à fabriquer une nouvelle isochrone autour de lui. Il est localisé dans la surface d’accessibilité existante vers le service de santé HeiGIT. Si des surfaces cumulatives se superposent, l’application retient le plus petit `range` contenant le point.

La population affichée est l’agrégat national publié pour ce seuil d’accessibilité. Plusieurs points classés dans le même seuil ont donc la même valeur. Il ne faut pas additionner ces valeurs entre structures : leurs populations se recouvrent.

## Lancer localement

Aucune compilation n’est nécessaire :

```bash
python3 -m http.server 8000 --bind 0.0.0.0
```

Ouvrir <http://localhost:8000>. Une connexion réseau est nécessaire pour les bibliothèques, le fond de carte et les données HeiGIT.

## Exemple CSV

```csv
nom,latitude,longitude
Hôpital 1,14.7167,-17.4677
Centre de santé 2,14.7150,-17.2730
```

Les fichiers importés sont traités localement dans le navigateur. Pour un Shapefile, fournir de préférence un ZIP contenant au minimum `.shp`, `.shx` et `.dbf`. Les coordonnées doivent être en WGS84 (EPSG:4326).

## Publication GitHub Pages

Le dépôt reste une application statique. Dans **Settings → Pages**, publier la racine de la branche voulue. Aucun secret ni serveur applicatif n’est requis.

## Disponibilité des sources

Au moment de la refonte, l’application OpenAccessLens signalait une maintenance et des restrictions temporaires du serveur de données. L’interface gère explicitement ce cas : elle affiche une erreur et laisse les résultats vides, au lieu de revenir à des valeurs simulées.

## Sources et limites

- [OpenAccessLens](https://giscience.github.io/open-access-lens/)
- [HeiGIT sur HDX](https://data.humdata.org/organization/heidelberg-institute-for-geoinformation-technology)
- [WorldPop](https://www.worldpop.org/)
- [OpenStreetMap](https://www.openstreetmap.org/)

Le modèle ne représente ni le trafic en temps réel, ni le transport public, ni la capacité ou la qualité des établissements. Sa précision dépend également de la complétude d’OpenStreetMap et des incertitudes de WorldPop.
