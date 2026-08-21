"""Modèles de données de l'application.

Toutes les valeurs numériques transportées ici proviennent d'un calcul réel
(routage, somme zonale, géométrie). Un champ vaut ``None`` lorsqu'il n'a pas pu
être calculé ; il n'est jamais rempli par une estimation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from shapely.geometry import Point


# --------------------------------------------------------------------------- #
# Structures de santé                                                          #
# --------------------------------------------------------------------------- #


@dataclass
class Facility:
    """Une structure de santé importée ou dessinée par l'utilisateur."""

    name: str
    latitude: float
    longitude: float
    identifier: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    source: str = "manuel"
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.latitude = float(self.latitude)
        self.longitude = float(self.longitude)
        self.name = str(self.name).strip() or f"Structure {self.identifier}"

    @property
    def geometry(self) -> Point:
        """Position en EPSG:4326, ordre (longitude, latitude)."""
        return Point(self.longitude, self.latitude)

    @property
    def ors_coordinates(self) -> list[float]:
        """Coordonnées au format attendu par openrouteservice : ``[lon, lat]``."""
        return [self.longitude, self.latitude]

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifiant": self.identifier,
            "structure": self.name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "source": self.source,
        }


# --------------------------------------------------------------------------- #
# Rasters de population                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class RasterMetadata:
    """Métadonnées vérifiées d'un raster de population.

    Ces champs sont lus dans le fichier, pas supposés, et sont exportés avec les
    résultats pour assurer la traçabilité.
    """

    path: str
    crs: str | None
    width: int
    height: int
    pixel_size_x: float
    pixel_size_y: float
    nodata: float | None
    dtype: str
    unit: str
    is_density: bool
    year: int | None
    source: str
    method: str
    bounds: tuple[float, float, float, float]
    approximate_resolution_m: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "raster": self.path,
            "crs": self.crs,
            "largeur_px": self.width,
            "hauteur_px": self.height,
            "taille_pixel_x": self.pixel_size_x,
            "taille_pixel_y": self.pixel_size_y,
            "resolution_approx_m": self.approximate_resolution_m,
            "nodata": self.nodata,
            "type_donnee": self.dtype,
            "unite_pixel": self.unit,
            "densite": self.is_density,
            "annee": self.year,
            "source": self.source,
            "methode": self.method,
            "emprise": list(self.bounds),
        }


# --------------------------------------------------------------------------- #
# Isochrones                                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class IsochroneBand:
    """Une bande d'accessibilité pour une structure et un seuil.

    ``geometry`` est la zone **cumulée** (≤ seuil) et ``ring_geometry`` la
    **couronne** comprise entre le seuil précédent et celui-ci.
    """

    facility_id: str
    facility_name: str
    threshold_seconds: int
    geometry: Any                      # shapely (Multi)Polygon cumulée, EPSG:4326
    ring_geometry: Any | None = None   # shapely (Multi)Polygon de la couronne
    previous_threshold_seconds: int | None = None

    # Renseignés par population.py / spatial_analysis.py
    population_cumulative: float | None = None
    population_interval: float | None = None
    area_km2_cumulative: float | None = None
    area_km2_interval: float | None = None
    population_share: float | None = None
    demographics: dict[str, float] = field(default_factory=dict)

    @property
    def threshold_minutes(self) -> int:
        return self.threshold_seconds // 60


@dataclass
class FacilityIsochrones:
    """Résultat de routage complet pour une structure."""

    facility: Facility
    profile: str
    bands: list[IsochroneBand] = field(default_factory=list)
    failed_thresholds: dict[int, str] = field(default_factory=dict)
    engine: str = ""
    engine_version: str = ""

    @property
    def succeeded(self) -> bool:
        return bool(self.bands)

    def band(self, threshold_seconds: int) -> IsochroneBand | None:
        for item in self.bands:
            if item.threshold_seconds == threshold_seconds:
                return item
        return None


# --------------------------------------------------------------------------- #
# Métadonnées d'analyse                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class AnalysisMetadata:
    """Traçabilité complète d'un calcul, exportée avec chaque résultat."""

    mode: str
    profile: str
    thresholds_seconds: list[int]
    routing_engine: str
    routing_engine_version: str
    routing_base_url: str
    population_source: str
    population_year: int | None
    population_raster: dict[str, Any] | None
    crs: str
    computed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    warnings: list[str] = field(default_factory=list)
    country_iso3: str | None = None
    category: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "pays_iso3": self.country_iso3,
            "categorie": self.category,
            "mode_deplacement": self.profile,
            "seuils_secondes": self.thresholds_seconds,
            "seuils_minutes": [value // 60 for value in self.thresholds_seconds],
            "moteur_routage": self.routing_engine,
            "version_moteur_routage": self.routing_engine_version,
            "url_moteur_routage": self.routing_base_url,
            "source_population": self.population_source,
            "annee_population": self.population_year,
            "raster_population": self.population_raster,
            "systeme_coordonnees": self.crs,
            "date_calcul": self.computed_at,
            "avertissements_methodologiques": self.warnings,
        }
