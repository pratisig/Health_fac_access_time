"""Moteur de routage : client openrouteservice (HeiGIT).

Choix du moteur
---------------
openrouteservice est retenu parce que c'est **le moteur qui a produit les
données OpenAccessLens elles-mêmes** (HeiGIT calcule ses isochrones nationales
avec ORS sur OpenStreetMap). Utiliser le même moteur en mode 2 garantit que les
zones de desserte calculées ici sont méthodologiquement comparables aux
isochrones territoriales du mode 1.

Limites dures de l'API publique
-------------------------------
D'après https://openrouteservice.org/restrictions/ :

===========================  =========
Option                       Maximum
===========================  =========
Localisations par requête    5
Intervalles par requête      10
Portée temps, profils auto   1 h
Portée temps, profils pieds  20 h
===========================  =========

Conséquence directe : **les seuils de 70 à 120 minutes en voiture sont
impossibles sur l'API publique.** Le serveur répond par l'erreur 3004
(``Parameter 'range=...' is out of range``). L'algorithme rapide qui lève cette
limite n'est activé que sur les instances auto-hébergées.

L'application ne contourne pas cette limite : les seuils hors capacité sont
signalés comme non calculés, avec leur motif. Aucun cercle, aucun tampon, aucune
extrapolation ne les remplace. Pour obtenir les douze seuils, il faut renseigner
``ORS_BASE_URL`` vers une instance openrouteservice dédiée.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import requests

from .config import (
    HTTP_TIMEOUT_SECONDS,
    ORS_PUBLIC_BASE_URL,
    ORS_PUBLIC_MAX_INTERVALS,
    ORS_PUBLIC_MAX_LOCATIONS,
    ORS_PUBLIC_MAX_RANGE_SECONDS,
    ROUTING_THROTTLE_SECONDS,
    cache_dir,
    ors_api_key,
    ors_base_url,
)


class RoutingError(RuntimeError):
    """Échec du moteur de routage, avec un message exploitable par l'utilisateur."""


class RoutingQuotaError(RoutingError):
    """Quota d'API atteint (HTTP 429) ou limite journalière dépassée."""


class RoutingUnsupportedError(RoutingError):
    """Le seuil demandé dépasse la capacité déclarée du moteur."""


@dataclass
class RoutingCapabilities:
    """Capacités effectives du moteur ciblé."""

    base_url: str
    is_public: bool
    max_range_seconds: dict[str, int]
    max_intervals: int
    max_locations: int
    engine: str = "openrouteservice"
    version: str = "inconnue"

    def max_range_for(self, profile: str) -> int | None:
        """Portée temporelle maximale pour un profil, ``None`` si non plafonnée."""
        return self.max_range_seconds.get(profile)

    def supports(self, profile: str, seconds: int) -> bool:
        limit = self.max_range_for(profile)
        return limit is None or seconds <= limit

    def unsupported(self, profile: str, thresholds: Iterable[int]) -> list[int]:
        """Seuils hors capacité, triés."""
        return sorted(value for value in thresholds if not self.supports(profile, value))

    def explain(self, profile: str, seconds: int) -> str:
        limit = self.max_range_for(profile)
        if limit is None or seconds <= limit:
            return ""
        return (
            f"Seuil {seconds // 60} min non calculable : le moteur "
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
        max_intervals=ORS_PUBLIC_MAX_INTERVALS,
        max_locations=ORS_PUBLIC_MAX_LOCATIONS,
    )


def self_hosted_capabilities(base_url: str) -> RoutingCapabilities:
    """Capacités supposées d'une instance auto-hébergée.

    Une instance dédiée est configurable (``maximum_range_time`` côté serveur) ;
    aucune limite n'est donc présumée côté client. Si le serveur refuse malgré
    tout un seuil, l'erreur remontée est celle du serveur, pas une invention.
    """
    return RoutingCapabilities(
        base_url=base_url,
        is_public=False,
        max_range_seconds={},
        max_intervals=ORS_PUBLIC_MAX_INTERVALS,
        max_locations=ORS_PUBLIC_MAX_LOCATIONS,
    )


def chunk_thresholds(thresholds: Sequence[int], size: int) -> list[list[int]]:
    """Découpe les seuils en requêtes respectant la limite d'intervalles.

    Chaque lot reste trié ; l'union des lots reproduit exactement l'entrée triée.
    """
    if size < 1:
        raise ValueError("La taille de lot doit être positive")
    ordered = sorted(set(int(value) for value in thresholds))
    return [ordered[start:start + size] for start in range(0, len(ordered), size)]


# --------------------------------------------------------------------------- #
# Client                                                                       #
# --------------------------------------------------------------------------- #


@dataclass
class ORSClient:
    """Client isochrones openrouteservice, avec cache disque et étranglement.

    Parameters
    ----------
    location_type:
        ``"destination"`` calcule les zones **depuis lesquelles on atteint** le
        point : c'est le sens correct pour « qui peut rejoindre cette
        structure ». ``"start"`` calculerait l'inverse.
    """

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

    # -- cache ------------------------------------------------------------- #

    def _cache_path(self, payload: dict[str, Any], profile: str) -> Path:
        """Clé de cache : coordonnées, profil, seuils, sens et moteur."""
        key = json.dumps(
            {"base": self.base_url, "profile": profile, **payload},
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        directory = cache_dir() / "isochrones"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{digest}.geojson"

    # -- requête ----------------------------------------------------------- #

    def _throttle(self) -> None:
        if self.throttle_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.throttle_seconds:
            time.sleep(self.throttle_seconds - elapsed)
        self._last_call = time.monotonic()

    def _request(self, profile: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key and self.capabilities and self.capabilities.is_public:
            raise RoutingError(
                "Aucune clé openrouteservice configurée. Renseignez le secret "
                "ORS_API_KEY (Streamlit secrets ou variable d'environnement). "
                "Une clé gratuite s'obtient sur https://openrouteservice.org/dev/#/signup."
            )

        cache_file = self._cache_path(payload, profile) if self.use_cache else None
        if cache_file is not None and cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cache_file.unlink(missing_ok=True)

        headers = {
            "Accept": "application/geo+json, application/json",
            "Content-Type": "application/json",
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
                "réinitialisation du quota, réduisez le nombre de structures, ou "
                "utilisez une instance auto-hébergée."
            )
        if response.status_code in (401, 403):
            raise RoutingError(
                f"Authentification openrouteservice refusée (HTTP {response.status_code}). "
                "Vérifiez ORS_API_KEY."
            )
        if response.status_code >= 400:
            raise RoutingError(f"Erreur du moteur de routage : {_describe_error(response)}")

        try:
            document = response.json()
        except ValueError as error:
            raise RoutingError("Réponse du moteur de routage illisible (JSON invalide)") from error

        self._record_version(document)

        if cache_file is not None:
            try:
                cache_file.write_text(json.dumps(document), encoding="utf-8")
            except OSError:
                pass
        return document

    def _record_version(self, document: dict[str, Any]) -> None:
        engine = (document.get("metadata") or {}).get("engine") or {}
        version = engine.get("version")
        if version and self.capabilities is not None:
            self.capabilities.version = str(version)

    # -- API publique ------------------------------------------------------ #

    def isochrones(
        self,
        coordinates: Sequence[float],
        profile: str,
        thresholds_seconds: Sequence[int],
        *,
        smoothing: float | None = None,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
        """Calcule les isochrones cumulées d'un point.

        Returns
        -------
        (features, failures)
            ``features`` associe chaque seuil obtenu à sa *feature* GeoJSON,
            ``failures`` associe chaque seuil manquant au motif de l'échec.
        """
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
        for batch in chunk_thresholds(feasible, self.capabilities.max_intervals):
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
                features[int(round(float(value)))] = feature

            for seconds in batch:
                if seconds not in features:
                    failures.setdefault(
                        seconds,
                        "Le moteur n'a renvoyé aucune géométrie pour ce seuil "
                        "(point hors réseau routier, ou zone non couverte par OpenStreetMap).",
                    )

        return features, failures

    def engine_info(self) -> dict[str, str]:
        """Moteur et version, tels que déclarés par le serveur."""
        assert self.capabilities is not None
        return {
            "engine": self.capabilities.engine,
            "version": self.capabilities.version,
            "base_url": self.base_url,
            "location_type": self.location_type,
        }


def _describe_error(response: requests.Response) -> str:
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
