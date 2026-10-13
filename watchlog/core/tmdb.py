"""Client TMDB (films).

Authentification : jeton de lecture (en-tête Bearer) en priorité,
sinon clé d'API (paramètre api_key). Les valeurs ne sont jamais loguées ni affichées.
"""

import logging
import time
from datetime import date, timedelta

import httpx
from sqlalchemy import Engine

from watchlog.core import config
from watchlog.core.dates import parse_date
from watchlog.core.http import ApiError, CachedClient, RateLimiter

log = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"
POSTER_BASE = "https://image.tmdb.org/t/p/w342"
ATTRIBUTION = "This product uses the TMDB API but is not endorsed or certified by TMDB."

# Types de sortie TMDB retenus pour la date France : 3 = cinéma, 4 = numérique.
FR_RELEASE_TYPES = (3, 4)


def extract_release_dates(movie: dict) -> tuple[date | None, date | None]:
    """(date de sortie en France, date de sortie d'origine) d'une fiche film TMDB."""
    original = parse_date(movie.get("release_date"))
    fr_dates = []
    for country in (movie.get("release_dates") or {}).get("results", []):
        if country.get("iso_3166_1") == "FR":
            for release in country.get("release_dates", []):
                if release.get("type") in FR_RELEASE_TYPES:
                    d = parse_date(release.get("release_date"))
                    if d:
                        fr_dates.append(d)
    return (min(fr_dates) if fr_dates else None), original


def movie_ttl(movie: dict) -> timedelta:
    """30 jours pour un film déjà sorti, 1 jour sinon."""
    fr, original = extract_release_dates(movie)
    released = any(d and d <= date.today() for d in (fr, original))
    return timedelta(days=30) if released else timedelta(days=1)


class TMDBClient:
    def __init__(self, engine: Engine, token: str | None = None, api_key: str | None = None,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep,
                 clock=time.monotonic):
        token = config.tmdb_read_token() if token is None else token
        api_key = config.tmdb_api_key() if api_key is None else api_key
        headers, auth_params = {}, {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif api_key:
            auth_params["api_key"] = api_key
        self.configured = bool(token or api_key)
        limiter = RateLimiter(20, 1, clock=clock, sleep=sleep)
        self.http = CachedClient("TMDB", BASE_URL, engine, limiter, headers=headers,
                                 auth_params=auth_params, transport=transport, sleep=sleep)
        self._connected: bool | None = None

    def check_connection(self) -> tuple[bool, str]:
        """Appel de test GET /authentication. Renvoie (connecté ?, message lisible)."""
        if not self.configured:
            self._connected = False
            return False, "TMDB non connecté : identifiants absents dans .env (films sans métadonnées)."
        try:
            response = self.http._request("/authentication", None)
        except ApiError:
            self._connected = False
            return False, "TMDB non connecté : API injoignable (films sans métadonnées pour l'instant)."
        if response.status_code == 200 and response.json().get("success"):
            self._connected = True
            return True, "TMDB connecté."
        self._connected = False
        log.warning("TMDB : identifiants refusés (HTTP %s)", response.status_code)
        return False, "TMDB non connecté : identifiants refusés, vérifie .env (films sans métadonnées)."

    @property
    def available(self) -> bool:
        """True si TMDB est utilisable (le test de connexion est fait une seule fois)."""
        if self._connected is None:
            self.check_connection()
        return bool(self._connected)

    def get_movie(self, tmdb_id: int, force: bool = False) -> dict | None:
        """Fiche film en français avec les dates de sortie par pays. None si inconnu ou TMDB indisponible."""
        if not self.available:
            return None
        return self.http.get_json(f"/movie/{tmdb_id}",
                                  {"language": "fr-FR", "append_to_response": "release_dates"},
                                  ttl=movie_ttl, force=force)

    def search_movies(self, query: str) -> list[dict]:
        if not self.available:
            return []
        data = self.http.get_json("/search/movie", {"query": query, "language": "fr-FR"},
                                  ttl=timedelta(days=1))
        return (data or {}).get("results", [])

    def recommendations(self, media: str, tmdb_id: int) -> list[dict]:
        """Titres recommandés par TMDB pour un film (media="movie") ou une série ("tv").

        Première page seulement (20 titres, les plus pertinents). Liste vide si TMDB est indisponible.
        """
        if not self.available:
            return []
        data = self.http.get_json(f"/{media}/{tmdb_id}/recommendations", {"language": "fr-FR"},
                                  ttl=timedelta(days=30))
        return (data or {}).get("results", [])

    def genres(self, media: str) -> dict[int, str]:
        """{id du genre: nom en français} pour media = "movie" ou "tv"."""
        if not self.available:
            return {}
        data = self.http.get_json(f"/genre/{media}/list", {"language": "fr-FR"},
                                  ttl=timedelta(days=30))
        return {g["id"]: g["name"] for g in (data or {}).get("genres", [])}

    def details(self, media: str, tmdb_id: int) -> dict | None:
        """Fiche complète d'un film ("movie") ou d'une série ("tv") avec casting et vidéos.

        Vidéos en français, sinon en anglais. None si inconnu ou TMDB indisponible.
        """
        if not self.available:
            return None
        return self.http.get_json(f"/{media}/{tmdb_id}",
                                  {"language": "fr-FR", "append_to_response": "credits,videos",
                                   "include_video_language": "fr,en,null"},
                                  ttl=timedelta(days=7))

    def watch_providers(self, media: str, tmdb_id: int) -> dict:
        """Plateformes par pays ({"FR": {"flatrate": [...], "rent": [...], ...}}), données JustWatch.

        Gardé 7 jours : les catalogues changent souvent. {} si TMDB est indisponible.
        """
        if not self.available:
            return {}
        data = self.http.get_json(f"/{media}/{tmdb_id}/watch/providers", ttl=timedelta(days=7))
        return (data or {}).get("results", {})

    def tv_external_ids(self, tmdb_id: int) -> dict:
        """Identifiants externes d'une série TMDB (tvdb_id, imdb_id...). {} si inconnue."""
        if not self.available:
            return {}
        return self.http.get_json(f"/tv/{tmdb_id}/external_ids", ttl=timedelta(days=30)) or {}

    def find_tv_by_tvdb(self, tvdb_id: int) -> int | None:
        """Id TMDB de la série qui a cet id TVDB, sinon None."""
        if not self.available:
            return None
        data = self.http.get_json(f"/find/{tvdb_id}", {"external_source": "tvdb_id"},
                                  ttl=timedelta(days=30)) or {}
        results = data.get("tv_results") or []
        return results[0]["id"] if results else None

    @staticmethod
    def poster_url(poster_path: str | None) -> str | None:
        return f"{POSTER_BASE}{poster_path}" if poster_path else None

    def download(self, url: str, dest) -> bool:
        return self.http.download(url, dest)
