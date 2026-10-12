"""Client HTTP commun : cache SQLite, limitation du débit, réessais, erreurs propres."""

import json
import logging
import time
from collections import deque
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx
from sqlalchemy import Engine
from sqlmodel import Session

from watchlog.core.dates import utcnow
from watchlog.core.models import ApiCache

log = logging.getLogger(__name__)

# Durée de validité : fixe, ou calculée à partir de la réponse en cache
# (ex. série terminée -> 7 jours, en cours -> 1 jour).
Ttl = timedelta | Callable[[object], timedelta]


class ApiError(Exception):
    """API injoignable ou réponse inattendue. Le message est lisible et sans secret."""


class RateLimiter:
    """Au plus `max_calls` appels sur une fenêtre glissante de `period` secondes."""

    def __init__(self, max_calls: int, period: float, clock=time.monotonic, sleep=time.sleep):
        self.max_calls, self.period = max_calls, period
        self.clock, self.sleep = clock, sleep
        self.calls: deque[float] = deque()

    def wait(self) -> None:
        now = self.clock()
        while self.calls and now - self.calls[0] >= self.period:
            self.calls.popleft()
        if len(self.calls) >= self.max_calls:
            self.sleep(self.period - (now - self.calls[0]))
            self.calls.popleft()
        self.calls.append(self.clock())


class CachedClient:
    """GET JSON avec cache dans la table api_cache.

    - `auth_params` (ex. api_key) sont envoyés mais jamais utilisés comme clé de cache.
    - En cas de panne, si une ancienne réponse existe en cache, on la renvoie quand même.
    """

    def __init__(self, name: str, base_url: str, engine: Engine, limiter: RateLimiter,
                 headers: dict | None = None, auth_params: dict | None = None,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep,
                 max_retries: int = 4):
        self.name = name
        self.engine = engine
        self.limiter = limiter
        self.auth_params = auth_params or {}
        self.sleep = sleep
        self.max_retries = max_retries
        self.client = httpx.Client(base_url=base_url, headers=headers, timeout=15,
                                   follow_redirects=True, transport=transport)
        # Client sans en-têtes d'authentification, pour les images (autre domaine).
        self.files = httpx.Client(timeout=15, follow_redirects=True, transport=transport)

    # --- cache -------------------------------------------------------------

    @staticmethod
    def cache_key(path: str, params: list[tuple] | dict | None) -> str:
        return path + ("?" + urlencode(params) if params else "")

    def _cache_get(self, key: str) -> ApiCache | None:
        with Session(self.engine) as s:
            return s.get(ApiCache, key)

    def _cache_put(self, key: str, data) -> None:
        with Session(self.engine) as s:
            row = s.get(ApiCache, key) or ApiCache(url=key, response_json="", fetched_at=utcnow())
            row.response_json = json.dumps(data, ensure_ascii=False)
            row.fetched_at = utcnow()
            s.add(row)
            s.commit()

    # --- requêtes ----------------------------------------------------------

    def get_json(self, path: str, params: list[tuple] | dict | None = None,
                 ttl: Ttl | None = None, force: bool = False):
        """Renvoie le JSON de `path`, ou None si 404.

        ttl=None : pas de cache du tout. force=True : ignore le cache mais le met à jour.
        """
        key = self.cache_key(path, params)
        cached = self._cache_get(key) if ttl is not None else None
        if cached and not force:
            data = json.loads(cached.response_json)
            validity = ttl(data) if callable(ttl) else ttl
            if utcnow() - cached.fetched_at < validity:
                return data

        try:
            response = self._request(path, params)
        except ApiError:
            if cached:  # mieux vaut une donnée un peu ancienne que rien
                log.warning("%s indisponible, utilisation du cache pour %s", self.name, key)
                return json.loads(cached.response_json)
            raise

        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ApiError(f"{self.name} : réponse inattendue (HTTP {response.status_code})")
        data = response.json()
        if ttl is not None:
            self._cache_put(key, data)
        return data

    def _request(self, path: str, params) -> httpx.Response:
        """Un GET avec limitation du débit et backoff exponentiel (429, 5xx, réseau)."""
        all_params = list(params.items() if isinstance(params, dict) else params or [])
        all_params += list(self.auth_params.items())
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                response = self.client.get(path, params=all_params)
            except httpx.TransportError as exc:
                reason = f"erreur réseau ({type(exc).__name__})"
            else:
                if response.status_code == 429:
                    reason = "trop de requêtes (429)"
                elif response.status_code >= 500:
                    reason = f"erreur serveur ({response.status_code})"
                else:
                    return response
            if attempt == self.max_retries:
                break
            delay = 2 ** attempt
            if reason.endswith("(429)"):
                # Retry-After vaut en général un nombre de secondes, mais peut aussi
                # être une date HTTP : dans ce cas on garde le délai exponentiel.
                retry_after = response.headers.get("Retry-After", "").strip()
                if retry_after.isdigit():
                    delay = max(delay, int(retry_after))
            log.info("%s %s : %s, nouvel essai dans %s s", self.name, path, reason, delay)
            self.sleep(delay)
        log.error("%s %s : abandon après %s essais (%s)", self.name, path, self.max_retries + 1, reason)
        raise ApiError(f"{self.name} indisponible : {reason}")

    def download(self, url: str, dest: Path) -> bool:
        """Télécharge un fichier (affiche) une seule fois. Renvoie True si le fichier existe."""
        if dest.exists():
            return True
        try:
            response = self.files.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Affiche non téléchargée (%s) : %s", url, type(exc).__name__)
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        return True
