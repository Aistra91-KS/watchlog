"""Tests du client TVMaze (faux serveur, aucun appel réseau)."""

from datetime import timedelta

import httpx
import pytest

from helpers_http import FakeApi, age_cache, make_test_engine, no_sleep
from watchlog.core.tvmaze import TVMazeClient, show_ttl


@pytest.fixture
def engine(tmp_path):
    return make_test_engine(tmp_path)


def make_tvmaze(engine, api):
    return TVMazeClient(engine, transport=api.transport, sleep=no_sleep)


def show(show_id, status):
    return {"id": show_id, "name": "Série test", "status": status,
            "_embedded": {"seasons": [], "episodes": []}}


def test_lookup_tvdb_follows_redirect(engine):
    def handler(request):
        if request.url.path == "/lookup/shows":
            # TVMaze répond par une redirection vers la fiche de la série
            return httpx.Response(301, headers={"Location": "https://api.tvmaze.com/shows/82"})
        if request.url.path == "/shows/82":
            return httpx.Response(200, json={"id": 82, "name": "Game of Thrones"})
        return httpx.Response(404)

    api = FakeApi(handler)
    result = make_tvmaze(engine, api).lookup_tvdb(121361)

    assert result == {"id": 82, "name": "Game of Thrones"}
    assert api.paths == ["/lookup/shows", "/shows/82"]
    assert api.requests[0].url.params["thetvdb"] == "121361"


def test_lookup_tvdb_unknown_returns_none(engine):
    api = FakeApi(lambda r: httpx.Response(404))
    assert make_tvmaze(engine, api).lookup_tvdb(999999999) is None


def test_show_ttl():
    assert show_ttl("Ended") == timedelta(days=7)
    assert show_ttl("Running") == timedelta(days=1)
    assert show_ttl(None) == timedelta(days=1)


def test_get_show_ended_cached_7_days(engine):
    api = FakeApi(lambda r: httpx.Response(200, json=show(1, "Ended")))
    client = make_tvmaze(engine, api)

    client.get_show(1)
    age_cache(engine, timedelta(days=3))
    client.get_show(1)            # série terminée : cache encore valide
    assert len(api.requests) == 1

    age_cache(engine, timedelta(days=8))
    client.get_show(1)            # plus de 7 jours : on redemande
    assert len(api.requests) == 2


def test_get_show_running_cached_1_day(engine):
    api = FakeApi(lambda r: httpx.Response(200, json=show(2, "Running")))
    client = make_tvmaze(engine, api)

    client.get_show(2)
    age_cache(engine, timedelta(hours=12))
    client.get_show(2)
    assert len(api.requests) == 1

    age_cache(engine, timedelta(days=2))
    client.get_show(2)
    assert len(api.requests) == 2


def test_get_show_asks_for_seasons_and_episodes(engine):
    api = FakeApi(lambda r: httpx.Response(200, json=show(3, "Running")))
    make_tvmaze(engine, api).get_show(3)

    assert api.requests[0].url.params.get_list("embed[]") == ["seasons", "episodes"]


def test_updated_since_converts_keys_to_int(engine):
    api = FakeApi(lambda r: httpx.Response(200, json={"1": 1760000000, "250": 1760000500}))

    result = make_tvmaze(engine, api).updated_since("day")

    assert result == {1: 1760000000, 250: 1760000500}
    assert api.requests[0].url.params["since"] == "day"
