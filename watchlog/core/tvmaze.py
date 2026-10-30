"""Client TVMaze (séries). API publique sans clé. Données sous licence CC BY-SA."""

import time
from datetime import timedelta

import httpx
from sqlalchemy import Engine

from watchlog.core.http import CachedClient, RateLimiter

BASE_URL = "https://api.tvmaze.com"
ATTRIBUTION = "Données séries : TVMaze.com"

ENDED = "Ended"


def show_ttl(status: str | None) -> timedelta:
    """Validité du cache : 7 jours pour une série terminée, 1 jour sinon."""
    return timedelta(days=7) if status == ENDED else timedelta(days=1)


class TVMazeClient:
    def __init__(self, engine: Engine, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, clock=time.monotonic):
        # Limite TVMaze : environ 20 requêtes / 10 s par IP.
        limiter = RateLimiter(20, 10, clock=clock, sleep=sleep)
        self.http = CachedClient("TVMaze", BASE_URL, engine, limiter,
                                 transport=transport, sleep=sleep)

    def lookup_tvdb(self, tvdb_id: int) -> dict | None:
        """Série TVMaze correspondant à un id TVDB (la redirection 301 est suivie). None si inconnue."""
        return self.http.get_json("/lookup/shows", {"thetvdb": tvdb_id}, ttl=timedelta(days=30))

    def lookup_imdb(self, imdb_id: str) -> dict | None:
        """Série TVMaze correspondant à un id IMDb (ex. "tt0944947"). None si inconnue."""
        return self.http.get_json("/lookup/shows", {"imdb": imdb_id}, ttl=timedelta(days=30))

    def search(self, query: str) -> list[dict]:
        """Recherche par titre : liste de {"score": float, "show": {...}}."""
        return self.http.get_json("/search/shows", {"q": query}, ttl=timedelta(days=1)) or []

    def get_show(self, tvmaze_id: int, force: bool = False) -> dict | None:
        """Fiche complète avec saisons et épisodes (hors spéciaux) dans `_embedded`."""
        params = [("embed[]", "seasons"), ("embed[]", "episodes")]
        return self.http.get_json(f"/shows/{tvmaze_id}", params, force=force,
                                  ttl=lambda data: show_ttl(data.get("status")))

    def get_episodes_with_specials(self, tvmaze_id: int, status: str | None,
                                   force: bool = False) -> list[dict]:
        """Tous les épisodes, spéciaux compris (number = null pour un spécial).

        L'embed de get_show n'inclut pas les spéciaux, d'où cet appel séparé.
        """
        return self.http.get_json(f"/shows/{tvmaze_id}/episodes", {"specials": 1},
                                  ttl=show_ttl(status), force=force) or []

    def get_cast(self, tvmaze_id: int) -> list[dict]:
        """Distribution principale : [{"person": {...}, "character": {...}, "self", "voice"}].

        Photo du personnage (character.image) quand TVMaze en a une. Gardée 30 jours.
        """
        return self.http.get_json(f"/shows/{tvmaze_id}/cast", ttl=timedelta(days=30)) or []

    def get_akas(self, tvmaze_id: int) -> list[dict]:
        """Titres alternatifs (pour trouver un titre français : country.code == "FR")."""
        return self.http.get_json(f"/shows/{tvmaze_id}/akas", ttl=timedelta(days=30)) or []

    def updated_since(self, since: str = "week") -> dict[int, int]:
        """{tvmaze_id: timestamp de dernière modification} pour since = day, week ou month."""
        data = self.http.get_json("/updates/shows", {"since": since}) or {}
        return {int(k): v for k, v in data.items()}

    def download(self, url: str, dest) -> bool:
        return self.http.download(url, dest)
