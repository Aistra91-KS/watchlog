"""Tests des personnages proposés pour « Personnage préféré » (watchlog/core/cast.py)."""

import httpx
import pytest

from helpers_http import FakeApi, make_test_engine, no_sleep
from watchlog.core import cast
from watchlog.core.http import ApiError
from watchlog.core.models import Movie, Show
from watchlog.core.tmdb import TMDBClient
from watchlog.core.tvmaze import TVMazeClient

TVMAZE_CAST = [
    {"person": {"name": "Bryan Cranston", "image": {"medium": "https://static.tvmaze.com/p1.jpg"}},
     "character": {"name": "Walter White", "image": {"medium": "https://static.tvmaze.com/c1.jpg"}},
     "self": False, "voice": False},
    {"person": {"name": "Aaron Paul", "image": {"medium": "https://static.tvmaze.com/p2.jpg"}},
     "character": {"name": "Jesse Pinkman", "image": None}, "self": False, "voice": False},
    {"person": {"name": "Présentateur", "image": None}, "character": {"name": "Lui-même"},
     "self": True, "voice": False},
]
AGGREGATE = {"cast": [
    {"name": "Bryan Cranston", "profile_path": "/bc.jpg", "order": 0,
     "roles": [{"character": "Walter White", "episode_count": 62}]},
    {"name": "Bob Odenkirk", "profile_path": "/bo.jpg", "order": 5,
     "roles": [{"character": "Saul Goodman", "episode_count": 43}]},
    {"name": "Dean Norris", "profile_path": None, "order": 3,
     "roles": [{"character": "Hank Schrader", "episode_count": 62},
               {"character": "Self", "episode_count": 1}]},
]}
MOVIE_DETAILS = {"id": 550, "credits": {"cast": [
    {"name": "Brad Pitt", "character": "Tyler Durden", "profile_path": "/bp.jpg", "order": 1},
    {"name": "Edward Norton", "character": "Narrator", "profile_path": "/en.jpg", "order": 0},
    {"name": "Meat Loaf", "character": "Robert 'Bob' Paulson (uncredited)", "order": 2},
    {"name": "Inconnu", "character": "", "order": 3},
]}}


class CastTVMaze:
    def __init__(self, fail=False):
        self.fail = fail

    def get_cast(self, tvmaze_id):
        if self.fail:
            raise ApiError("TVMaze injoignable")
        return TVMAZE_CAST


class CastTMDB:
    def __init__(self, fail=False, available=True):
        self.fail, self.available = fail, available

    def _answer(self, data):
        if self.fail:
            raise ApiError("TMDB injoignable")
        return data if self.available else None

    def tv_aggregate_credits(self, tmdb_id):
        return self._answer(AGGREGATE)

    def details(self, media, tmdb_id):
        return self._answer(MOVIE_DETAILS)


SHOW = Show(id=1, title="Breaking Bad", tvmaze_id=169, tmdb_id=1396)
MOVIE = Movie(id=1, title="Fight Club", tmdb_id=550)


def labels(result: cast.Cast) -> list[str]:
    return [c.label for c in result.characters]


def test_clean_role():
    assert cast.clean_role("  Rick Sanchez (voice) ") == "Rick Sanchez"
    assert cast.clean_role("Bob (uncredited) (voice)") == "Bob"
    assert cast.clean_role("Himself") is None and cast.clean_role("") is None
    assert cast.clean_role(None) is None
    assert cast.clean_role("Bruce Wayne / Batman") == "Bruce Wayne / Batman"


def test_show_cast_tvmaze_first_then_recurring_tmdb_roles():
    result = cast.show_cast(CastTVMaze(), CastTMDB(), SHOW)
    assert labels(result) == ["Walter White (Bryan Cranston)", "Jesse Pinkman (Aaron Paul)",
                              "Hank Schrader (Dean Norris)", "Saul Goodman (Bob Odenkirk)"]
    walter, jesse = result.characters[:2]
    assert walter.photo_url == "https://static.tvmaze.com/c1.jpg"   # photo du personnage
    assert jesse.photo_url == "https://static.tvmaze.com/p2.jpg"    # sinon celle de l'acteur
    assert result.characters[3].photo_url == "https://image.tmdb.org/t/p/w185/bo.jpg"
    assert result.errors == []


def test_show_cast_keeps_what_answered_and_names_the_failure():
    result = cast.show_cast(CastTVMaze(fail=True), CastTMDB(), SHOW)
    assert labels(result)[0] == "Walter White (Bryan Cranston)"
    assert result.errors == ["TVMaze"]
    result = cast.show_cast(CastTVMaze(), CastTMDB(fail=True), SHOW)
    assert len(result.characters) == 2 and result.errors == ["TMDB"]


def test_show_cast_without_tmdb_or_ids():
    result = cast.show_cast(CastTVMaze(), CastTMDB(available=False), SHOW)
    assert len(result.characters) == 2 and result.errors == []
    manual = Show(id=2, title="Saisie manuelle", is_manual=True)
    assert cast.show_cast(CastTVMaze(fail=True), CastTMDB(fail=True), manual) == cast.Cast()


def test_show_cast_limited(monkeypatch):
    monkeypatch.setattr(cast, "MAX_CHARACTERS", 3)
    assert len(cast.show_cast(CastTVMaze(), CastTMDB(), SHOW).characters) == 3


def test_movie_cast_in_credits_order():
    result = cast.movie_cast(CastTMDB(), MOVIE)
    assert labels(result) == ["Narrator (Edward Norton)", "Tyler Durden (Brad Pitt)",
                              "Robert 'Bob' Paulson (Meat Loaf)"]
    assert result.characters[2].photo_url is None


def test_movie_cast_failure_and_no_id():
    assert cast.movie_cast(CastTMDB(fail=True), MOVIE).errors == ["TMDB"]
    assert cast.movie_cast(CastTMDB(available=False), MOVIE) == cast.Cast()
    assert cast.movie_cast(CastTMDB(fail=True), Movie(id=2, title="Manuel")) == cast.Cast()


# --- Clients ----------------------------------------------------------------------------

@pytest.fixture
def engine(tmp_path):
    return make_test_engine(tmp_path)


def test_tvmaze_get_cast(engine):
    api = FakeApi(lambda r: httpx.Response(200, json=TVMAZE_CAST))
    client = TVMazeClient(engine, transport=api.transport, sleep=no_sleep)
    assert client.get_cast(169) == TVMAZE_CAST
    assert client.get_cast(169) == TVMAZE_CAST     # deuxième fois : cache
    assert api.paths == ["/shows/169/cast"]


def test_tmdb_tv_aggregate_credits(engine):
    def handler(request):
        if request.url.path == "/3/authentication":
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json=AGGREGATE)

    api = FakeApi(handler)
    client = TMDBClient(engine, token="fake-token", api_key="", transport=api.transport,
                        sleep=no_sleep)
    assert client.tv_aggregate_credits(1396) == AGGREGATE
    assert api.paths[-1] == "/3/tv/1396/aggregate_credits"
    offline = TMDBClient(engine, token="", api_key="", transport=api.transport, sleep=no_sleep)
    assert offline.tv_aggregate_credits(1396) is None
