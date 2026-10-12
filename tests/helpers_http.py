"""Outils communs aux tests des clients HTTP (aucun appel réseau réel).

- `FakeApi` : faux serveur branché via httpx.MockTransport, qui garde la liste
  des requêtes reçues (pour vérifier ce qui a été envoyé).
- `make_test_engine` : base SQLite temporaire (dans le dossier tmp_path de pytest).
- `age_cache` : vieillit artificiellement le cache pour tester son expiration.
"""

import json
from datetime import timedelta
from pathlib import Path

import httpx
from sqlmodel import Session, select

from watchlog.core.dates import utcnow
from watchlog.core.db import make_engine
from watchlog.core.http import CachedClient, RateLimiter
from watchlog.core.models import ApiCache

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    """Contenu JSON d'un fichier de tests/fixtures."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_test_engine(tmp_path: Path):
    return make_engine(tmp_path / "test.db")


class FakeApi:
    """Faux serveur : `handler(request)` fabrique la réponse, chaque requête est notée."""

    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def no_sleep(_seconds) -> None:
    """Remplace time.sleep : les tests ne doivent jamais attendre pour de vrai."""


def make_client(engine, api: FakeApi, delays: list | None = None, **kwargs) -> CachedClient:
    """CachedClient de test. Les attentes du backoff sont notées dans `delays`."""
    sleep = delays.append if delays is not None else no_sleep
    limiter = RateLimiter(1000, 1, sleep=no_sleep)
    return CachedClient("Test", "https://api.example.test", engine, limiter,
                        transport=api.transport, sleep=sleep, **kwargs)


def age_cache(engine, age: timedelta) -> None:
    """Fait comme si toutes les réponses en cache dataient d'il y a `age`."""
    with Session(engine) as s:
        for row in s.exec(select(ApiCache)).all():
            row.fetched_at = utcnow() - age
            s.add(row)
        s.commit()


def cache_keys(engine) -> list[str]:
    with Session(engine) as s:
        return [row.url for row in s.exec(select(ApiCache)).all()]
