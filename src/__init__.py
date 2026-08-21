"""Package d'analyse d'accessibilité aux structures de santé.

Modules
-------
``config``            constantes vérifiées, secrets, palettes
``models``            structures de données
``data_catalog``      catalogue et statistiques OpenAccessLens (mode 1)
``facility_io``       import et validation des structures
``routing``           client openrouteservice et capacités du moteur
``isochrones``        zones cumulatives, emboîtement, couronnes (mode 2)
``population``        WorldPop : URL, métadonnées, somme zonale
``spatial_analysis``  tableaux, unions, non-double-comptage
``maps``              rendus cartographiques
``charts``            graphiques
``exports``           CSV, GeoJSON, GeoPackage, rapport, métadonnées
"""

__version__ = "2.0.0"
