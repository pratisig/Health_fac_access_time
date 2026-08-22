---
title: Population accessible aux structures de santé
emoji: 🏥
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: gpl-3.0
---

# Population accessible aux structures de santé

Application géospatiale Streamlit calculant **combien de personnes peuvent
atteindre chaque structure de santé** en 10, 20, 30 … 120 minutes, par somme
zonale WorldPop dans de vraies isochrones routières.

> **Remplacement du prototype.** L'ancienne page statique (`index.html`,
> `app.js`, `styles.css`) a été supprimée. Elle ne pouvait pas répondre à la
> question posée : elle se contentait de **localiser** un point dans les
> isochrones nationales HeiGIT puis d'afficher l'**agrégat national** du seuil
> correspondant. Deux structures tombant dans la même classe affichaient donc la
> même population — celle du pays entier, pas celle qu'elles desservent. Le
> calcul demandé exige une somme zonale raster dans des polygones propres à
> chaque structure, impossible dans un navigateur : d'où le passage à Python
> côté serveur.

---

## Les deux modes, strictement séparés

### Mode 1 — Accessibilité territoriale (HeiGIT / OpenAccessLens)

> « Quel est le niveau actuel d'accessibilité territoriale aux services de santé ? »

Affiche les données nationales **déjà publiées** par HeiGIT : catalogue
dynamique des pays, choix `hospitals` / `primary_healthcare`, PMTiles officiels
rendus par MapLibre, classes de 10 à 120 minutes sélectionnables une à une ou
toutes, statistiques démographiques agrégées, et localisation des structures
importées sur cette carte.

Aucune géométrie n'est recalculée : la carte affiche les tuiles HeiGIT telles
quelles, et les statistiques sont lues sans correction.

### Mode 2 — Zone de desserte propre à chaque structure

> « Combien de personnes peuvent atteindre cette structure précise en 10, 20 … 120 minutes ? »

Chaque structure importée ou dessinée devient **la destination de ses propres
isochrones**, calculées par Valhalla (par défaut, sans clé) ou openrouteservice
(si `ORS_API_KEY` existe) sur OpenStreetMap, puis croisées avec WorldPop. Pour
chaque structure et chaque seuil : géométrie réelle, temps en secondes et en
minutes, borne précédente réelle de la couronne, population cumulée, population
de la couronne, superficie et part de la population de référence.

L'emboîtement est garanti par construction :

```
zone ≤ 10 ⊂ zone ≤ 20 ⊂ zone ≤ 30 ⊂ … ⊂ zone ≤ 120
```

Les zones représentent les lieux **depuis lesquels on atteint** la structure,
et non l'inverse. Ce sens est transmis explicitement : `reverse: true` pour
Valhalla et `location_type: destination` pour ORS. `reverse` est documenté par
[l'API Valhalla](https://valhalla.github.io/valhalla/api/isochrone/api-reference/)
comme une expansion inverse montrant d'où la localisation peut être atteinte.
Si un serveur compatible refuse ce paramètre, l'erreur est affichée pour les
seuils concernés : l'application ne bascule jamais silencieusement dans le sens
opposé.

---

## Population cumulée et population par couronne

Deux notions distinctes, jamais confondues :

| Grandeur | Définition | Additionnable ? |
|---|---|---|
| `population_cumulee` | population de la zone **≤ seuil** | ❌ non, chaque valeur contient les précédentes |
| `population_intervalle` | population de la **couronne** entre deux seuils | ✅ oui, les couronnes sont disjointes |

**Choix de mise en œuvre.** La population d'intervalle n'est pas obtenue en
soustrayant deux totaux, mais par somme zonale dans la géométrie de différence
`zone_k \ zone_{k-1}`. Elle est donc structurellement positive, sans artefact
d'arrondi. L'identité attendue reste vérifiée :

```
population_0_10   = population_cumulee_10
population_10_20  = population_cumulee_20 − population_cumulee_10
population_20_30  = population_cumulee_30 − population_cumulee_20
```

`check_consistency()` contrôle à chaque exécution la croissance du cumul, la
positivité des couronnes et l'égalité `Σ couronnes = cumul final`, et affiche
tout écart plutôt que de le masquer. Si les seuils choisis sont par exemple
10, 30 et 60 minutes, les intervalles sont libellés **0–10, 10–30 et 30–60**
dans les graphiques, cartes, tableaux, CSV, GeoJSON, GeoPackage et rapport HTML
(`seuil_precedent_min`) : aucun pas fixe de dix minutes n'est inventé.

### Plusieurs structures : jamais de double comptage

Trois valeurs sont affichées séparément pour chaque seuil :

| Colonne | Signification |
|---|---|
| `population_somme_brute` | somme des populations par structure — **surestime** dès qu'il y a recouvrement |
| `population_union` | somme WorldPop dans l'**union géométrique** — population réellement couverte |
| `population_chevauchement` | différence, soit la population desservie par au moins deux structures |

---

## Structure réelle des données vérifiée

### OpenAccessLens

Chemins et schéma confirmés dans le dépôt amont
[`GIScience/open-access-lens`](https://github.com/GIScience/open-access-lens/blob/main/src/config.ts) :

| Ressource | Chemin |
|---|---|
| Catalogue | `/access/aux/countries.yaml` |
| Isochrones | `/access/aux/tiles/{iso3}/{iso3}_{category}_isochrones.pmtiles` |
| Statistiques | `/access/aux/stats/{iso3}/category={category}/data.parquet` |

* attribut de temps : **`range`, en secondes** ;
* seuils santé : `600, 1200, … 7200` s — **exactement** les douze valeurs
  présentes dans le Parquet, d'après le commentaire de `RANGE_OPTIONS` amont
  (« Verified against the actual Parquet data ») ;
* catégories : `hospitals`, `primary_healthcare` ;
* colonnes du Parquet : `admin_level`, `range`, `population_type`,
  `population`, `population_share` ;
* méthode HeiGIT : isochrones openrouteservice sur OSM, croisées avec WorldPop 100 m,
  agrégées par pays et unités administratives.

Le nom de la couche vectorielle des PMTiles est **lu dans les métadonnées de
l'archive**, jamais supposé.

### WorldPop

Produits sélectionnables, avec résolution d'URL **vérifiée à l'exécution** par
requête HTTP (aucun chemin n'est présumé valide) :

| Produit | Résolution | Années | Poids indicatif (pays moyen) |
|---|---|---|---|
| Non contraint | 100 m | 2000–2020 | 100–500 Mo |
| Non contraint, ajusté ONU | 100 m | 2000–2020 | 100–500 Mo |
| Contraint par le bâti | 100 m | 2020 | 100–500 Mo |
| Contraint, ajusté ONU | 100 m | 2020 | 100–500 Mo |
| **Non contraint agrégé (défaut)** | **1 km** | 2000–2020 | **2–20 Mo** |

Pour chaque raster chargé, l'application lit et affiche : CRS, résolution en
degrés et en mètres, valeur NoData, type de donnée, unité du pixel, nature
comptage ou densité, année et méthode WorldPop. Ces métadonnées accompagnent
tous les exports. Un raster de densité est pondéré par la surface réelle de
chaque pixel, calculée en fonction de la latitude.

---

## Moteurs d’isochrones : Valhalla par défaut, ORS en option

### Valhalla FOSSGIS — chemin principal sans clé

Le mode 2 fonctionne par défaut, sans secret, avec le serveur de démonstration
mondial FOSSGIS :

```
https://valhalla1.openstreetmap.de/isochrone
```

Le client applique exactement le protocole Valhalla :

* `POST` avec `locations: [{"lat": …, "lon": …}]` ;
* profils applicatifs `driving-car` → `costing: "auto"` et `foot-walking` →
  `costing: "pedestrian"` ;
* temps internes convertis de secondes vers `contours[].time` en minutes ;
* `polygons: true` et `reverse: true` ;
* réponse `properties.contour` reconvertie de minutes vers secondes ;
* quatre contours maximum par appel : les douze seuils sont découpés en
  **trois requêtes de quatre**, puis recombinés ;
* limite explicite à 120 minutes. Un seuil supérieur est refusé avant l'appel,
  sans cercle, tampon ou extrapolation.

Le serveur FOSSGIS est une **démonstration mutualisée**, pas une infrastructure
de calcul en masse. L'application respecte sa politique d'usage équitable :
requêtes espacées de 1,6 s, `User-Agent` identifiant le projet, cache disque
agressif indexé par la charge utile complète et réutilisé sans expiration. Dans
l'interface, un avertissement demande de traiter de petits lots et d'éviter les
recalculs. Pour un usage intensif ou concurrent, déployer sa propre instance
Valhalla et renseigner `VALHALLA_BASE_URL`.

Les erreurs sont conservées par seuil avec un message exploitable :

| Réponse | Signification et conduite |
|---|---|
| Valhalla `151` | contour au-delà de 120 min : refus sans extrapolation |
| Valhalla `152` | trop de contours : le client normal en envoie au plus quatre |
| HTTP `429` | serveur mutualisé limité/saturé : attendre, réduire le lot, laisser agir le cache |
| JSON/géométrie absente | seuil laissé vide avec son motif |

`VALHALLA_ENABLED=false` permet à un exploitant de désactiver ce backend. Si
Valhalla est désactivé et qu'aucune configuration ORS utilisable n'existe, le
bouton d'analyse est désactivé avec un message explicite.

### openrouteservice — option avec clé

openrouteservice n'apparaît dans le sélecteur que si `ORS_API_KEY` est définie.
Il reste utile pour comparer le mode 2 au moteur employé par HeiGIT pour
OpenAccessLens, ou pour cibler une instance dédiée via `ORS_BASE_URL`.

L'API publique ORS impose notamment :

| Option | Maximum public |
|---|---|
| Localisations par requête | 5 |
| Intervalles par requête | 10 |
| **Portée temps, profils motorisés** | **1 h** |
| Portée temps, profils piétons | 20 h |

En voiture, les seuils 70 à 120 minutes sont donc refusés en amont avec leur
motif sur l'API publique ; en marche, les douze passent. Les douze contours sont
découpés en deux appels (10 + 2). `total_pop` d'ORS n'est jamais utilisé : les
populations viennent exclusivement des sommes zonales WorldPop.

Le fichier [`docker-compose.ors.yml`](docker-compose.ors.yml) fournit une
configuration ORS auto-hébergée pour lever la limite motorisée publique :

```yaml
ors.endpoints.isochrones.maximum_range_time: 7200
ors.endpoints.isochrones.maximum_intervals: 12
ors.engine.profiles.driving-car.preparation.methods.fastisochrones.enabled: "true"
```

### Comparaison des backends

| Critère | Valhalla FOSSGIS (défaut) | ORS public | Instance dédiée |
|---|---|---|---|
| Secret | **aucun** | `ORS_API_KEY` | selon le déploiement |
| 120 min voiture | **oui** | non, 60 min max | oui si configurée |
| Contours/appel | 4 (12 = 3 appels) | 10 (12 = 2 appels) | configurable |
| Sens vers la structure | `reverse: true` | `location_type: destination` | idem moteur |
| Comparabilité au mode 1 HeiGIT | moteur différent | **même famille ORS** | ORS : oui |
| Usage recommandé | démonstration légère, cache | quota ORS et comparaison | production/intensif |

Le sélecteur latéral affiche pour chaque choix le besoin de clé, le seuil
maximum, le nombre de contours par requête, l'unité temporelle et le sens de
calcul. **Jamais de substitut :** un seuil non calculé reste absent avec son
motif ; aucun cercle, tampon, aléa ou extrapolation ne le remplace.

---

## Installation et lancement

```bash
git clone https://github.com/pratisig/Health_fac_access_time.git
cd Health_fac_access_time

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

streamlit run app.py
```

Ouvrir <http://localhost:8501>. **Aucune clé n'est nécessaire** : Valhalla
FOSSGIS est sélectionné par défaut et les deux modes fonctionnent immédiatement.

Configuration facultative dans `.streamlit/secrets.toml` ou l'environnement :

```toml
# Ajoute openrouteservice au sélecteur
ORS_API_KEY = "..."
# ORS_BASE_URL = "https://mon-ors.example/ors"

# Cible une instance Valhalla dédiée compatible
# VALHALLA_BASE_URL = "https://mon-valhalla.example/isochrone"
# VALHALLA_ENABLED = "true"
```

### Tests

```bash
pytest -q
node tests/map_component_harness.js /chemin/vers/map.html  # 9 scénarios JS
```

Couverture : parcours `streamlit.testing.v1.AppTest`, protocoles Valhalla et
ORS, découpage 4 × 3, sens inverse, erreurs 151/152/429, parsing CSV et variantes de colonnes, validation des coordonnées,
reprojection (et refus d'un fichier sans CRS), tri et emboîtement des seuils,
population cumulée, population par intervalle, absence de valeur négative,
union de plusieurs zones, non-double-comptage, gestion du NoData, jointure
raster-polygone (y compris polygone troué), échecs du moteur de routage (réseau,
quota 429, clé invalide, erreur 3004, réponse partielle, JSON invalide),
annulation d'un lot, absence de données WorldPop, et validité des exports.

---

## Import des structures

| Format | Détails |
|---|---|
| CSV | séparateur détecté (`,` `;` tabulation), virgule décimale acceptée, encodages UTF-8 / UTF-8-BOM / Latin-1 |
| GeoJSON | reprojeté en EPSG:4326 |
| GeoPackage | reprojeté en EPSG:4326 |
| Shapefile ZIP | `.shp`, `.shx`, `.dbf` requis ; `.prj` **obligatoire** |
| Dessin | marqueurs placés sur la carte |

Alias de colonnes reconnus :

* **nom** — `nom`, `name`, `structure`, `etablissement`, `établissement`,
  `facility`, `libelle`, `designation`, `label`…
* **latitude** — `latitude`, `lat`, `y`, `ycoord`, `coord_y`…
* **longitude** — `longitude`, `lon`, `lng`, `long`, `x`, `xcoord`, `coord_x`…

```csv
nom,latitude,longitude
Hôpital 1,14.7167,-17.4677
Centre de santé 2,14.7150,-17.2730
```

Toute coordonnée est validée : non numérique, hors bornes WGS84, ou `(0, 0)`
— rejetée ligne par ligne, avec le motif, sans bloquer les lignes valides. Un
Shapefile sans `.prj` est refusé plutôt que reprojeté au jugé. Les polygones
sont réduits à un point garanti intérieur, jamais au centroïde brut.

---

## Sorties

**Tableaux.** Format long (une ligne par structure et par seuil) et format
matrice (une colonne par seuil), avec bascule entre population cumulée,
population par intervalle, part de la population, superficie cumulée et
superficie de couronne.

**Graphiques.** Courbe cumulée, histogramme par couronne, comparaison
cumul/couronne par structure, classement des structures à un seuil, couverture
combinée avec chevauchements, extension spatiale, profil démographique.

**Exports.** CSV long, CSV matrice, GeoJSON des zones cumulées, GeoJSON des
couronnes, GeoPackage multi-couches (`isochrones_cumulees`, `couronnes`,
`structures`, `tableau_long`, `metadonnees`), rapport HTML imprimable en PDF, et
métadonnées JSON. Chaque export embarque : structure, coordonnées, mode de
déplacement, seuil, **seuil précédent réel** (`seuil_precedent_min`), population
cumulée, population par intervalle, superficie,
source et année WorldPop, moteur et version de routage, date du calcul, système
de coordonnées et avertissements méthodologiques.

Les superficies sont calculées en projection équivalente **EPSG:8857 (Equal
Earth)**, jamais en degrés carrés.

---

## Performance et cache

* `st.cache_data` pour le catalogue, les statistiques et la population totale ;
* `st.cache_resource` pour les rasters téléchargés ;
* cache disque des isochrones, indexé par coordonnées, profil, seuils, sens et
  moteur — une reprise après interruption ne recalcule rien ;
* barre de progression par structure puis par zone ;
* étranglement des requêtes (1,6 s) pour respecter les quotas ;
* erreurs isolées **par structure** : un échec n'interrompt pas le lot ;
* annulation possible en cours d'exécution ;
* limite de 25 structures par lot ;
* messages explicites en cas de quota (HTTP 429), de clé absente ou de donnée
  manquante.

Le répertoire de cache est configurable par `HEALTH_ACCESS_CACHE_DIR`, à faire
pointer vers un volume persistant en production.

**Secrets.** Aucune clé n'est écrite dans le dépôt. Lecture par `st.secrets`
puis variables d'environnement ; `.streamlit/secrets.toml` est ignoré par Git.

### Dépannage du routage et des cartes

| Symptôme | Diagnostic / action |
|---|---|
| « Aucun moteur de routage utilisable » | enlever `VALHALLA_ENABLED=false`, vérifier `VALHALLA_BASE_URL`, ou ajouter `ORS_API_KEY` |
| Valhalla `151` | le seuil excède 120 min ; il restera vide, sans extrapolation |
| Valhalla `152` | serveur incompatible ou limite différente ; l'application envoie normalement des lots de quatre maximum |
| HTTP `429` | attendre avant de relancer, réduire structures/seuils et conserver `HEALTH_ACCESS_CACHE_DIR` |
| Erreur liée à `reverse` | l'instance ne prend pas en charge l'isochrone inverse ; aucune bascule silencieuse vers le sens départ n'est faite |
| ORS `3004` | portée publique dépassée (60 min voiture) ; choisir Valhalla ou une instance ORS dédiée |
| Isochrone absente | lire le motif par seuil : point hors réseau, zone non couverte ou géométrie absente ; aucun substitut n'est créé |
| PMTiles visibles mais fond cartographique indisponible | les couches restent indépendantes : le style MapLibre est construit inline par `buildStyle()` et ne dépend pas d'un document `tiles.openfreemap.org` |

Pour diagnostiquer un comportement ancien, vider le cache Streamlit puis
relancer ; ne supprimer le cache disque d'isochrones que si l'endpoint ou les
données du moteur ont réellement changé.

---

## Déploiement

| Plateforme | RAM | Disque persistant | Docker | WorldPop 100 m | ORS dédié | Verdict |
|---|---|---|---|---|---|---|
| **Hugging Face Spaces** | 16 Go (payant) / 2 Go (gratuit) | ✅ optionnel | ✅ | ✅ | ✅ possible | **recommandé** |
| Streamlit Community Cloud | ~1 Go | ❌ | ❌ | ⚠️ 1 km seulement | ❌ | démo légère |
| Render | 512 Mo → 4 Go+ | ✅ payant | ✅ | ✅ selon plan | ✅ service séparé | bon compromis |
| Railway | à l'usage | ✅ | ✅ | ✅ | ✅ | facturation à l'usage |

### Hugging Face Spaces (cible retenue)

Le [`Dockerfile`](Dockerfile) est prêt : SDK `docker`, port 7860, cache sur
`/data/cache`. L'en-tête YAML de ce README sert de configuration du Space.

1. créer un Space **Docker** et y pousser ce dépôt ;
2. aucune variable secrète n'est requise pour Valhalla ; ajouter `ORS_API_KEY`
   seulement pour proposer aussi ORS ;
3. activer le disque persistant pour conserver les caches WorldPop et routage.

### Streamlit Community Cloud — limites à connaître

Environ 1 Go de RAM, pas de disque persistant, pas de Docker. Le mode 2
fonctionne **sans secret** avec Valhalla FOSSGIS jusqu'à 120 minutes, mais le
raster **1 km est fortement recommandé** et les caches sont perdus à chaque
redémarrage. Le serveur FOSSGIS restant mutualisé, cette configuration convient
à une démonstration légère, pas à une production concurrente ni à de grands lots.

### Architecture séparée, si les limites deviennent bloquantes

Nécessaire au-delà de quelques pays en 100 m ou pour un usage concurrent :

```
Streamlit (UI)  →  FastAPI (calcul, files d'attente)  →  Valhalla ou ORS dédié
                              ↓
                   Stockage objet S3 (rasters COG)  +  cache Redis/disque
```

Les rasters WorldPop convertis en **COG** permettent la lecture par fenêtre
depuis S3, sans télécharger le pays entier — c'est le principal levier de
réduction mémoire. Cette architecture n'est pas implémentée ici : elle demande
une infrastructure supplémentaire.

---

## Fonctionnalités qui exigent une infrastructure supplémentaire

Signalées explicitement, conformément au cahier des charges :

1. **Calcul intensif ou fortement concurrent** — ne doit pas reposer sur le
   serveur de démonstration FOSSGIS. Les seuils 70–120 min en voiture sont bien
   disponibles en démonstration avec Valhalla, mais une production en volume
   exige Valhalla ou ORS auto-hébergé.
2. **Profil démographique par sexe et âge** — implémenté
   (`population.demographic_profile`), mais nécessite le téléchargement de 36
   rasters nationaux par pays et par année. Inutilisable sur une plateforme sans
   disque persistant ; non branché par défaut dans l'interface.
3. **PDF généré côté serveur** — remplacé par un rapport HTML autonome,
   imprimable en PDF depuis le navigateur, pour éviter d'embarquer un moteur de
   rendu de plusieurs centaines de mégaoctets.
4. **Limites administratives dans le mode 2** — demandent une source de
   frontières (GADM, geoBoundaries) non intégrée ; le mode 1 les obtient déjà
   via les agrégats HeiGIT.
5. **Calcul asynchrone / reprise longue durée** — le cache disque permet la
   reprise, mais une vraie file d'attente suppose l'architecture FastAPI
   ci-dessus.

---

## Limites méthodologiques

* isochrones calculées sur OpenStreetMap : qualité dépendante de la complétude
  locale de la cartographie ;
* aucun trafic temps réel, aucune congestion, aucune fermeture temporaire,
  aucune saisonnalité (pluies, pistes impraticables) ;
* vitesses issues du profil de routage, non observées ;
* WorldPop est une estimation modélisée dasymétrique, incertaine en zone peu
  dense ou peu cartographiée ;
* la population accessible ne présume ni de la capacité, ni de la qualité, ni de
  la disponibilité effective des services ;
* transport public et barrières financières ou culturelles non modélisés ;
* aucune barrière frontalière : une isochrone peut franchir une frontière.

Ces avertissements sont affichés dans l'interface et inclus dans chaque export.

---

## Architecture du code

```
app.py                      interface Streamlit, orchestration des deux modes
src/
  config.py                 constantes vérifiées, secrets, palettes, limites
  models.py                 Facility, IsochroneBand, RasterMetadata, AnalysisMetadata
  data_catalog.py           catalogue et statistiques OpenAccessLens (mode 1)
  facility_io.py            import CSV / GeoJSON / GPKG / SHP, validation, reprojection
  routing.py                clients Valhalla/ORS, capacités, sens, quotas, cache
  isochrones.py             emboîtement, couronnes, superficies (mode 2)
  population.py             WorldPop : URL, téléchargement, métadonnées, somme zonale
  spatial_analysis.py       tableaux, unions, non-double-comptage, cohérence
  maps.py                   MapLibre/PMTiles (mode 1), Folium (mode 2)
  charts.py                 graphiques Plotly
  exports.py                CSV, GeoJSON, GeoPackage, rapport HTML, métadonnées
tests/                      tests Python, AppTest et banc JavaScript (9 scénarios)
scripts/                    outil hors ligne de vectorisation raster HeiGIT
Dockerfile                  Hugging Face Spaces
docker-compose.ors.yml      instance openrouteservice dédiée (seuils > 60 min)
.streamlit/                 config.toml et modèle de secrets
```

---

## Sources et licences

* [OpenAccessLens](https://giscience.github.io/open-access-lens/) — HeiGIT, GPLv3
* [HeiGIT sur HDX](https://data.humdata.org/organization/heidelberg-institute-for-geoinformation-technology)
* [Valhalla](https://valhalla.github.io/valhalla/) et son serveur de démonstration FOSSGIS — backend sans clé par défaut
* [openrouteservice](https://openrouteservice.org/) — HeiGIT, backend facultatif
* [WorldPop](https://www.worldpop.org/) — CC BY 4.0
* [OpenStreetMap](https://www.openstreetmap.org/) — ODbL

Aucune valeur affichée ou exportée par cette application n'est simulée,
aléatoire, extrapolée ou arbitraire. Toute donnée indisponible est signalée
comme telle.
