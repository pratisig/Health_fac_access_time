"""Clients de routage pour les isochrones du mode 2.

Valhalla FOSSGIS est le moteur par défaut : son serveur de démonstration public
ne demande aucune clé et couvre le graphe OpenStreetMap mondial. Son protocole
est distinct de celui d'openrouteservice (ORS) : les contours sont exprimés en
minutes, quatre contours au plus sont acceptés par requête et la portée maximale
est de 120 minutes.

Le calcul est toujours fait **vers la structure** :

* Valhalla reçoit ``reverse: true``. Ce paramètre est documenté par l'API comme
  une expansion inverse montrant la zone depuis laquelle la localisation peut
  être atteinte ;
* ORS reçoit ``location_type: destination``.

Si un serveur refuse ce sens inverse, son erreur est conservée pour chaque seuil
concerné. Le client ne réessaie jamais dans le sens opposé et ne fabrique aucune
géométrie de substitution.

Le serveur FOSSGIS est un service de démonstration mutualisé. Les appels sont
étranglés, identifiés par un User-Agent et mis en cache sur disque sans date
d'expiration. Les traitements intensifs doivent utiliser une instance dédiée.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

import requests

from .config import (
    HTTP_TIMEOUT_SECONDS,
    ORS_PUBLIC_BASE_URL,
    ORS_PUBLIC_MAX_INTERVALS,
    ORS_PUBLIC_MAX_LOCATIONS,
    ORS_PUBLIC_MAX_RANGE_SECONDS,
    ROUTING_THROTTLE_SECONDS,
    VALHALLA_FOSSGIS_MAX_CONTOURS,
    VALHALLA_FOSSGIS_MAX_RANGE_SECONDS,
    cache_dir,
    ors_api_key,
    ors_base_url,
    valhalla_base_url,
    valhalla_is_enabled,
)

ROUTING_USER_AGENT = (
    "Health_fac_access_time/2.0 "
    "(+https://github.com/pratisig/Health_fac_access_time)"
)


class RoutingError(RuntimeError):
    """Échec du moteur de routage, avec un message exploitable par l'utilisateur."""


class RoutingQuotaError(RoutingError):
    """Quota d'API atteint (HTTP 429) ou limite journalière dépassée."""


class RoutingUnsupportedError(RoutingError):
    """Le seuil ou le paramètre demandé dépasse la capacité déclarée du moteur."""


@dataclass
class RoutingCapabilities:
    """Capacités et protocole effectifs d'un moteur d'isochrones.

    ``max_range_seconds`` reste exprimé dans l'unité interne commune (secondes),
    tandis que ``time_unit`` décrit l'unité attendue sur le fil. ``direction``
    formalise le sens métier et les deux champs ``direction_*`` indiquent sa
    traduction dans le protocole du moteur.
    """

    base_url: str
    is_public: bool
    max_range_seconds: dict[str, int]
    max_contours: int
    max_locations: int
    engine: str = "openrouteservice"
    version: str = "inconnue"
    time_unit: str = "seconds"
    direction: str = "towards_location"
    direction_parameter: str = "location_type"
    direction_value: str | bool = "destination"
    api_key_required: bool = True
    supported_profiles: tuple[str, ...] = ()

    @property
    def max_intervals(self) -> int:
        """Alias historique ORS ; un intervalle est un contour temporel."""
        return self.max_contours

    def max_range_for(self, profile: str) -> int | None:
        """Portée temporelle maximale pour un profil, ``None`` si non plafonnée."""
        return self.max_range_seconds.get(profile)

    def supports(self, profile: str, seconds: int) -> bool:
        if self.supported_profiles and profile not in self.supported_profiles:
            return False
        limit = self.max_range_for(profile)
        return limit is None or seconds <= limit

    def unsupported(self, profile: str, thresholds: Iterable[int]) -> list[int]:
        """Seuils hors capacité, triés."""
        return sorted(value for value in thresholds if not self.supports(profile, value))

    def explain(self, profile: str, seconds: int) -> str:
        if self.supported_profiles and profile not in self.supported_profiles:
            return (
                f"Profil « {profile} » non pris en charge par le moteur "
                f"{self.engine}. Profils disponibles : "
                + ", ".join(self.supported_profiles)
                + "."
            )
        limit = self.max_range_for(profile)
        if limit is None or seconds <= limit:
            return ""
        if self.engine == "valhalla":
            return (
                f"Seuil {seconds / 60:g} min non calculable : le serveur Valhalla "
                f"FOSSGIS plafonne les contours temporels à {limit // 60} min "
                "(erreur Valhalla 151 au-delà). Aucune géométrie de substitution "
                "n'est produite."
            )
        return (
            f"Seuil {seconds / 60:g} min non calculable : le moteur "
            f"{self.base_url} plafonne le profil « {profile} » à "
            f"{limit // 60} min par requête isochrone. "
            "Renseignez ORS_BASE_URL vers une instance openrouteservice "
            "auto-hébergée pour lever cette limite."
        )


def public_capabilities() -> RoutingCapabilities:
    """Capacités de l'API publique openrouteservice opérée par HeiGIT."""
    return RoutingCapabilities(
        base_url=ORS_PUBLIC_BASE_URL,
        is_public=True,
        max_range_seconds=dict(ORS_PUBLIC_MAX_RANGE_SECONDS),
        max_contours=ORS_PUBLIC_MAX_INTERVALS,
        max_locations=ORS_PUBLIC_MAX_LOCATIONS,
        engine="openrouteservice",
        time_unit="seconds",
        direction_parameter="location_type",
        direction_value="destination",
        api_key_required=True,
        supported_profiles=tuple(ORS_PUBLIC_MAX_RANGE_SECONDS),
    )


def self_hosted_capabilities(base_url: str) -> RoutingCapabilities:
    """Capacités supposées d'une instance ORS auto-hébergée configurable."""
    return RoutingCapabilities(
        base_url=base_url.rstrip("/"),
        is_public=False,
        max_range_seconds={},
        max_contours=ORS_PUBLIC_MAX_INTERVALS,
        max_locations=ORS_PUBLIC_MAX_LOCATIONS,
        engine="openrouteservice",
        time_unit="seconds",
        direction_parameter="location_type",
        direction_value="destination",
        api_key_required=False,
        supported_profiles=("driving-car", "foot-walking"),
    )


def valhalla_capabilities(base_url: str | None = None) -> RoutingCapabilities:
    """Capacités du serveur de démonstration Valhalla de FOSSGIS."""
    endpoint = (base_url or valhalla_base_url()).rstrip("/")
    maximum = VALHALLA_FOSSGIS_MAX_RANGE_SECONDS
    return RoutingCapabilities(
        base_url=endpoint,
        is_public=True,
        max_range_seconds={"driving-car": maximum, "foot-walking": maximum},
        max_contours=VALHALLA_FOSSGIS_MAX_CONTOURS,
        max_locations=1,
        engine="valhalla",
        time_unit="minutes",
        direction="towards_location",
        direction_parameter="reverse",
        direction_value=True,
        api_key_required=False,
        supported_profiles=("driving-car", "foot-walking"),
    )


def available_routing_engines() -> dict[str, RoutingCapabilities]:
    """Moteurs réellement utilisables avec la configuration courante.

    Valhalla est activé par défaut et peut être coupé explicitement par
    ``VALHALLA_ENABLED=false`` (utile aux exploitants et aux tests). ORS n'est
    proposé que lorsqu'une clé est présente, conformément au protocole public.
    """
    engines: dict[str, RoutingCapabilities] = {}
    if valhalla_is_enabled():
        engines["valhalla"] = valhalla_capabilities()
    if ors_api_key():
        base_url = ors_base_url()
        engines["openrouteservice"] = (
            public_capabilities()
            if base_url == ORS_PUBLIC_BASE_URL
            else self_hosted_capabilities(base_url)
        )
    return engines


def create_routing_client(engine: str) -> RoutingClient:
    """Construit le client associé à une clé retournée par ``available_*``."""
    if engine == "valhalla":
        return ValhallaClient()
    if engine == "openrouteservice":
        return ORSClient(location_type="destination")
    raise RoutingUnsupportedError(f"Moteur de routage inconnu ou indisponible : {engine}")


def chunk_thresholds(thresholds: Sequence[int], size: int) -> list[list[int]]:
    """Découpe des seuils triés/dédoublonnés selon le maximum de contours."""
    if size < 1:
        raise ValueError("La taille de lot doit être positive")
    ordered = sorted(set(int(value) for value in thresholds))
    return [ordered[start:start + size] for start in range(0, len(ordered), size)]


class RoutingClient(Protocol):
    """Interface commune consommée par l'orchestrateur d'isochrones."""

    capabilities: RoutingCapabilities | None

    def isochrones(
        self,
        coordinates: Sequence[float],
        profile: str,
        thresholds_seconds: Sequence[int],
        *,
        smoothing: float | None = None,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, str]]: ...

    def engine_info(self) -> dict[str, str]: ...


def _cache_path(
    base_url: str,
    engine: str,
    payload: dict[str, Any],
    profile: str,
) -> Path:
    """Clé de cache stable : moteur, URL, coordonnées, profil, seuils et sens."""
    key = json.dumps(
        {"base": base_url, "engine": engine, "profile": profile, **payload},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    directory = cache_dir() / "isochrones"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{digest}.geojson"


def _read_cache(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None


def _write_cache(path: Path | None, document: dict[str, Any]) -> None:
    if path is None:
        return
    try:
        path.write_text(json.dumps(document), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# openrouteservice                                                             #
# --------------------------------------------------------------------------- #


@dataclass
class ORSClient:
    """Client isochrones openrouteservice, avec cache disque et étranglement."""

    base_url: str = field(default_factory=ors_base_url)
    api_key: str | None = field(default_factory=ors_api_key)
    capabilities: RoutingCapabilities | None = None
    location_type: str = "destination"
    throttle_seconds: float = ROUTING_THROTTLE_SECONDS
    timeout: int = HTTP_TIMEOUT_SECONDS
    use_cache: bool = True
    session: requests.Session | None = None

    _last_call: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.capabilities is None:
            self.capabilities = (
                public_capabilities()
                if self.base_url == ORS_PUBLIC_BASE_URL
                else self_hosted_capabilities(self.base_url)
            )
        if self.session is None:
            self.session = requests.Session()

    def _cache_path(self, payload: dict[str, Any], profile: str) -> Path:
        return _cache_path(self.base_url, "openrouteservice", payload, profile)

    def _throttle(self) -> None:
        if self.throttle_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.throttle_seconds:
            time.sleep(self.throttle_seconds - elapsed)
        self._last_call = time.monotonic()

    def _request(self, profile: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key and self.capabilities and self.capabilities.api_key_required:
            raise RoutingError(
                "Aucune clé openrouteservice configurée. Renseignez le secret "
                "ORS_API_KEY, ou utilisez Valhalla FOSSGIS sans clé."
            )

        cache_file = self._cache_path(payload, profile) if self.use_cache else None
        cached = _read_cache(cache_file)
        if cached is not None:
            return cached

        headers = {
            "Accept": "application/geo+json, application/json",
            "Content-Type": "application/json",
            "User-Agent": ROUTING_USER_AGENT,
        }
        if self.api_key:
            headers["Authorization"] = self.api_key

        url = f"{self.base_url}/v2/isochrones/{profile}"
        self._throttle()
        try:
            assert self.session is not None
            response = self.session.post(
                url, json=payload, headers=headers, timeout=self.timeout
            )
        except requests.RequestException as error:
            raise RoutingError(f"Moteur de routage injoignable ({url}) : {error}") from error

        if response.status_code == 429:
            raise RoutingQuotaError(
                "Quota openrouteservice atteint (HTTP 429). Attendez la "
                "réinitialisation du quota ou réduisez le nombre de structures."
            )
        if response.status_code in (401, 403):
            raise RoutingError(
                f"Authentification openrouteservice refusée (HTTP {response.status_code}). "
                "Vérifiez ORS_API_KEY."
            )
        if response.status_code >= 400:
            raise RoutingError(f"Erreur du moteur de routage : {_describe_ors_error(response)}")

        try:
            document = response.json()
        except ValueError as error:
            raise RoutingError("Réponse du moteur de routage illisible (JSON invalide)") from error

        self._record_version(document)
        _write_cache(cache_file, document)
        return document

    def _record_version(self, document: dict[str, Any]) -> None:
        engine = (document.get("metadata") or {}).get("engine") or {}
        version = engine.get("version")
        if version and self.capabilities is not None:
            self.capabilities.version = str(version)

    def isochrones(
        self,
        coordinates: Sequence[float],
        profile: str,
        thresholds_seconds: Sequence[int],
        *,
        smoothing: float | None = None,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
        """Calcule les isochrones ORS cumulées d'un point."""
        assert self.capabilities is not None
        requested = sorted(set(int(value) for value in thresholds_seconds))
        failures: dict[int, str] = {}
        feasible: list[int] = []
        for seconds in requested:
            if self.capabilities.supports(profile, seconds):
                feasible.append(seconds)
            else:
                failures[seconds] = self.capabilities.explain(profile, seconds)

        features: dict[int, dict[str, Any]] = {}
        for batch in chunk_thresholds(feasible, self.capabilities.max_contours):
            payload: dict[str, Any] = {
                "locations": [list(coordinates)],
                "range": list(batch),
                "range_type": "time",
                "location_type": self.location_type,
                "attributes": ["area"],
                "units": "m",
            }
            if smoothing is not None:
                payload["smoothing"] = float(smoothing)

            try:
                document = self._request(profile, payload)
            except RoutingError as error:
                for seconds in batch:
                    failures[seconds] = str(error)
                continue

            for feature in document.get("features", []):
                properties = feature.get("properties") or {}
                value = properties.get("value")
                if value is None:
                    continue
                seconds = int(round(float(value)))
                if seconds in requested:
                    features[seconds] = feature

            _mark_missing(batch, features, failures)

        return features, failures

    def engine_info(self) -> dict[str, str]:
        assert self.capabilities is not None
        return {
            "engine": self.capabilities.engine,
            "version": self.capabilities.version,
            "base_url": self.base_url,
            "location_type": self.location_type,
            "direction": self.capabilities.direction,
        }


# --------------------------------------------------------------------------- #
# Valhalla FOSSGIS                                                             #
# --------------------------------------------------------------------------- #


VALHALLA_PROFILE_MAP: dict[str, str] = {
    "driving-car": "auto",
    "foot-walking": "pedestrian",
    # Les noms natifs sont acceptés pour un usage direct du client.
    "auto": "auto",
    "pedestrian": "pedestrian",
}


@dataclass
class ValhallaClient:
    """Client du service public Valhalla FOSSGIS, sans clé d'API.

    Le serveur accepte quatre contours au maximum (erreur 152) et 120 minutes
    au maximum (erreur 151). Les secondes internes sont converties en minutes
    dans la charge utile puis la propriété de réponse ``contour`` est reconvertie
    en secondes. ``reverse`` reste toujours vrai : il n'existe aucun repli vers
    une expansion au départ de la structure.
    """

    base_url: str = field(default_factory=valhalla_base_url)
    capabilities: RoutingCapabilities | None = None
    reverse: bool = True
    throttle_seconds: float = ROUTING_THROTTLE_SECONDS
    timeout: int = HTTP_TIMEOUT_SECONDS
    use_cache: bool = True
    session: requests.Session | None = None
    user_agent: str = ROUTING_USER_AGENT

    _last_call: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.capabilities is None:
            self.capabilities = valhalla_capabilities(self.base_url)
        if self.session is None:
            self.session = requests.Session()

    def _cache_path(self, payload: dict[str, Any], profile: str) -> Path:
        return _cache_path(self.base_url, "valhalla", payload, profile)

    def _throttle(self) -> None:
        if self.throttle_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.throttle_seconds:
            time.sleep(self.throttle_seconds - elapsed)
        self._last_call = time.monotonic()

    def _request(self, profile: str, payload: dict[str, Any]) -> dict[str, Any]:
        cache_file = self._cache_path(payload, profile) if self.use_cache else None
        cached = _read_cache(cache_file)
        if cached is not None:
            return cached

        headers = {
            "Accept": "application/geo+json, application/json",
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        self._throttle()
        try:
            assert self.session is not None
            response = self.session.post(
                self.base_url, json=payload, headers=headers, timeout=self.timeout
            )
        except requests.RequestException as error:
            raise RoutingError(
                f"Serveur Valhalla FOSSGIS injoignable ({self.base_url}) : {error}"
            ) from error

        if response.status_code == 429:
            raise RoutingQuotaError(
                "Serveur de démonstration Valhalla FOSSGIS saturé ou limité "
                "(HTTP 429). Attendez avant de relancer, réduisez le lot et laissez "
                "le cache éviter les appels déjà effectués."
            )
        if response.status_code >= 400:
            code, details = _describe_valhalla_error(response)
            if code in (151, 152):
                raise RoutingUnsupportedError(details)
            raise RoutingError(f"Erreur du serveur Valhalla FOSSGIS : {details}")

        try:
            document = response.json()
        except ValueError as error:
            raise RoutingError("Réponse Valhalla illisible (JSON invalide)") from error

        if not isinstance(document, dict):
            raise RoutingError("Réponse Valhalla illisible (objet GeoJSON attendu)")
        _write_cache(cache_file, document)
        return document

    def isochrones(
        self,
        coordinates: Sequence[float],
        profile: str,
        thresholds_seconds: Sequence[int],
        *,
        smoothing: float | None = None,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
        """Calcule des isochrones inverses et mappe ``contour`` vers les secondes."""
        del smoothing  # ORS seulement ; aucune approximation Valhalla n'est inventée.
        assert self.capabilities is not None
        requested = sorted(set(int(value) for value in thresholds_seconds))
        failures: dict[int, str] = {}

        costing = VALHALLA_PROFILE_MAP.get(profile)
        if costing is None:
            reason = self.capabilities.explain(profile, requested[0] if requested else 0)
            return {}, {seconds: reason for seconds in requested}

        feasible: list[int] = []
        capability_profile = (
            "driving-car" if costing == "auto" else "foot-walking"
        )
        for seconds in requested:
            if self.capabilities.supports(capability_profile, seconds):
                feasible.append(seconds)
            else:
                failures[seconds] = self.capabilities.explain(capability_profile, seconds)

        if len(coordinates) < 2:
            reason = "Coordonnées incomplètes : longitude et latitude sont requises."
            return {}, {seconds: reason for seconds in requested}
        longitude, latitude = float(coordinates[0]), float(coordinates[1])

        features: dict[int, dict[str, Any]] = {}
        for batch in chunk_thresholds(feasible, self.capabilities.max_contours):
            payload: dict[str, Any] = {
                "locations": [{"lat": latitude, "lon": longitude}],
                "costing": costing,
                "contours": [{"time": seconds / 60.0} for seconds in batch],
                "polygons": True,
                "reverse": True,
            }
            try:
                document = self._request(profile, payload)
            except RoutingError as error:
                for seconds in batch:
                    failures[seconds] = str(error)
                continue

            for feature in document.get("features", []):
                properties = feature.get("properties") or {}
                contour = properties.get("contour")
                if contour is None:
                    continue
                try:
                    seconds = int(round(float(contour) * 60.0))
                except (TypeError, ValueError):
                    continue
                if seconds in requested:
                    features[seconds] = feature

            _mark_missing(batch, features, failures, engine="Valhalla")

        return features, failures

    def engine_info(self) -> dict[str, str]:
        assert self.capabilities is not None
        return {
            "engine": self.capabilities.engine,
            "version": self.capabilities.version,
            "base_url": self.base_url,
            "location_type": "destination (reverse=true)",
            "direction": self.capabilities.direction,
        }


def _mark_missing(
    batch: Sequence[int],
    features: dict[int, dict[str, Any]],
    failures: dict[int, str],
    *,
    engine: str = "Le moteur",
) -> None:
    for seconds in batch:
        if seconds not in features:
            failures.setdefault(
                seconds,
                f"{engine} n'a renvoyé aucune géométrie pour ce seuil "
                "(point hors réseau routier, zone non couverte, ou contour absent).",
            )


def _describe_ors_error(response: requests.Response) -> str:
    """Extrait le message d'erreur structuré d'openrouteservice."""
    try:
        document = response.json()
    except ValueError:
        return f"HTTP {response.status_code} — {response.text[:200]}"

    error = document.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message", "")
        suffix = ""
        if code == 3004:
            suffix = (
                " — le seuil demandé dépasse la portée maximale du serveur. "
                "Une instance auto-hébergée est nécessaire au-delà de 60 min en voiture."
            )
        return f"HTTP {response.status_code} (code {code}) : {message}{suffix}"
    if isinstance(error, str):
        return f"HTTP {response.status_code} : {error}"
    return f"HTTP {response.status_code}"


def _describe_valhalla_error(response: requests.Response) -> tuple[int | None, str]:
    """Décrit notamment les erreurs Valhalla 151, 152 et les réponses non JSON."""
    try:
        document = response.json()
    except ValueError:
        return None, f"HTTP {response.status_code} — {response.text[:200]}"

    code: int | None = None
    raw_code = document.get("error_code")
    error = document.get("error")
    message = ""
    if isinstance(error, dict):
        raw_code = raw_code if raw_code is not None else error.get("code")
        message = str(error.get("message") or error.get("error") or "")
    elif error is not None:
        message = str(error)
    if not message:
        message = str(document.get("status") or document.get("message") or "")
    try:
        code = int(raw_code) if raw_code is not None else None
    except (TypeError, ValueError):
        code = None

    prefix = f"HTTP {response.status_code}"
    if code is not None:
        prefix += f" (code Valhalla {code})"
    if code == 151:
        return code, (
            f"{prefix} : {message or 'Exceeded max time'} — le contour dépasse "
            "120 min ; il est refusé sans extrapolation."
        )
    if code == 152:
        return code, (
            f"{prefix} : {message or 'Exceeded max contours'} — quatre contours "
            "au maximum sont acceptés par requête."
        )
    return code, f"{prefix} : {message}" if message else prefix
