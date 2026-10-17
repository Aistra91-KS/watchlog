"""Tests des plateformes de visionnage (données JustWatch via TMDB)."""

import httpx
import pytest
from sqlmodel import Session

from conftest import FakeTMDB
from helpers_db import add_show
from helpers_http import FakeApi, cache_keys, no_sleep
from watchlog.core import providers
from watchlog.core.http import ApiError
from watchlog.core.models import MOVIE, SHOW
from watchlog.core.tmdb import TMDBClient

FR = {
    "link": "https://www.themoviedb.org/movie/27205/watch?locale=FR",
    "flatrate": [{"provider_name": "Netflix", "logo_path": "/n.png", "display_priority": 2},
                 {"provider_name": "Canal+", "logo_path": "/c.png", "display_priority": 1}],
    "rent": [{"provider_name": "Apple TV", "logo_path": None, "display_priority": 6}],
    "buy": [],
}


class ProvidersTMDB(FakeTMDB):
    def __init__(self, results=None, fail=False, find=None, **kwargs):
        super().__init__(**kwargs)
        self.results, self.fail, self.find = results or {}, fail, find or {}
        self.calls = []

    def watch_providers(self, media, tmdb_id):
        self.calls.append((media, tmdb_id))
        if self.fail:
            raise ApiError("TMDB indisponible (simulé)")
        return self.results if self.available else {}

    def find_tv_by_tvdb(self, tvdb_id):
        return self.find.get(tvdb_id)


def test_parse_orders_offers_and_providers():
    data = providers.parse(FR)
    assert list(data.offers) == ["Abonnement", "Location"]   # « Achat » vide : absent
    assert [p.name for p in data.offers["Abonnement"]] == ["Canal+", "Netflix"]
    assert data.offers["Abonnement"][0].logo_url == "https://image.tmdb.org/t/p/w92/c.png"
    assert data.offers["Location"][0].logo_url is None
    assert data.link.endswith("locale=FR") and not data.empty


def test_availability_uses_france_and_media():
    tmdb = ProvidersTMDB(results={"FR": FR, "US": {"flatrate": [{"provider_name": "Hulu"}]}})
    data = providers.availability(tmdb, SHOW, 42)
    assert "Abonnement" in data.offers and tmdb.calls == [("tv", 42)]


def test_availability_nothing_in_france():
    data = providers.availability(ProvidersTMDB(results={"US": FR}), MOVIE, 1)
    assert data is not None and data.empty


@pytest.mark.parametrize("tmdb, tmdb_id", [
    (ProvidersTMDB(results={"FR": FR}), None),          # pas d'id TMDB
    (ProvidersTMDB(fail=True), 1),                      # panne
    (ProvidersTMDB(available=False), 1),                # TMDB non connecté
])
def test_availability_unknown(tmdb, tmdb_id):
    assert providers.availability(tmdb, MOVIE, tmdb_id) is None


def test_show_tmdb_id_resolved_once(engine):
    with Session(engine) as session:
        show = add_show(session, "Série TVMaze", tvdb_id=1001)
        tmdb = ProvidersTMDB(find={1001: 77})
        assert providers.show_tmdb_id(session, tmdb, show) == 77
        assert show.tmdb_id == 77
        other = add_show(session, "Inconnue", tvdb_id=5)
        assert providers.show_tmdb_id(session, tmdb, other) is None


def test_tmdb_client_watch_providers(engine):
    def server(request):
        if request.url.path == "/3/authentication":
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/3/movie/27205/watch/providers":
            return httpx.Response(200, json={"id": 27205, "results": {"FR": FR}})
        return httpx.Response(404)

    api = FakeApi(server)
    client = TMDBClient(engine, token="fake-token", api_key="", transport=api.transport,
                        sleep=no_sleep)
    assert client.watch_providers("movie", 27205)["FR"]["link"] == FR["link"]
    client.watch_providers("movie", 27205)
    assert api.paths.count("/3/movie/27205/watch/providers") == 1   # 2e appel : cache
    assert "/movie/27205/watch/providers" in cache_keys(engine)
    assert client.watch_providers("tv", 1) == {}   # 404



def test_availability_raise_errors_on_outage():
    class DownTMDB:
        available = True

        def watch_providers(self, media, tmdb_id):
            raise ApiError("TMDB indisponible (simulé)")

    assert providers.availability(DownTMDB(), "movie", 1) is None
    with pytest.raises(ApiError):
        providers.availability(DownTMDB(), "movie", 1, raise_errors=True)
