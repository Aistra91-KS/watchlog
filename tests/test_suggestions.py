"""Tests des suggestions (recommandations TMDB pondérées par la bibliothèque)."""

import httpx
import pytest
from sqlmodel import Session, select

from conftest import FakeTMDB, FakeTVMaze
from helpers_db import add_movie, add_show
from helpers_http import FakeApi, cache_keys, no_sleep
from watchlog.core import library, suggestions
from watchlog.core.http import ApiError
from watchlog.core.models import (COMPLETED, FOR_LATER, MOVIE, SHOW, STOPPED, TO_WATCH, WATCHING,
                                  DismissedSuggestion, Show)
from watchlog.core.tmdb import TMDBClient


def rec(tmdb_id, title, votes=100, genres=(18,), vote=7.5, tv=False):
    """Un titre recommandé, au format TMDB (séries : name / first_air_date)."""
    data = {"id": tmdb_id, "vote_count": votes, "vote_average": vote, "genre_ids": list(genres),
            "overview": f"Résumé de {title}", "poster_path": f"/p{tmdb_id}.jpg"}
    if tv:
        data.update(name=title, original_name=title, first_air_date="2020-01-01")
    else:
        data.update(title=title, original_title=title, release_date="2019-05-01")
    return data


class RecoTMDB(FakeTMDB):
    """Faux TMDB : recommandations, genres et ids externes fournis par le test."""

    def __init__(self, recos=None, external=None, find=None, fail_ids=(), **kwargs):
        super().__init__(**kwargs)
        self.recos = recos or {}          # (media, id TMDB) -> liste de titres
        self.external = external or {}    # id TMDB série -> {"tvdb_id": ..., "imdb_id": ...}
        self.find = find or {}            # id TVDB -> id TMDB
        self.fail_ids = set(fail_ids)     # ids dont les recommandations échouent (ApiError)
        self.reco_calls: list[tuple] = []

    def recommendations(self, media, tmdb_id):
        self.reco_calls.append((media, tmdb_id))
        if tmdb_id in self.fail_ids:
            raise ApiError("TMDB indisponible (simulé)")
        return self.recos.get((media, tmdb_id), [])

    def genres(self, media):
        return {18: "Drame", 35: "Comédie", 27: "Horreur"}

    def tv_external_ids(self, tmdb_id):
        return self.external.get(tmdb_id, {})

    def find_tv_by_tvdb(self, tvdb_id):
        return self.find.get(tvdb_id)


class ImdbTVMaze(FakeTVMaze):
    """Faux TVMaze qui connaît aussi un id IMDb (-> série 202)."""

    def lookup_imdb(self, imdb_id):
        self._check("lookup_imdb", imdb_id)
        return self.get_show(202) if imdb_id == "tt0202" else None


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


# --- Poids des sources ----------------------------------------------------------------

def test_source_weight():
    assert suggestions.source_weight(COMPLETED, False, None) == 1
    assert suggestions.source_weight(WATCHING, False, None) == 1
    assert suggestions.source_weight(TO_WATCH, False, None) == 0.5
    assert suggestions.source_weight(FOR_LATER, False, None) == 0.5
    assert suggestions.source_weight(STOPPED, False, None) == -1
    # la note remplace le statut
    assert suggestions.source_weight(STOPPED, False, 5) == 3
    assert suggestions.source_weight(COMPLETED, False, 1) == -2
    # favori : bonus, et un favori n'est jamais négatif
    assert suggestions.source_weight(COMPLETED, True, None) == 3
    assert suggestions.source_weight(STOPPED, True, None) == 2


def test_library_sources_order_and_limit(session, monkeypatch):
    monkeypatch.setattr(suggestions, "MAX_SOURCES", 2)
    add_movie(session, "Normal", status=COMPLETED, tmdb_id=1)
    add_movie(session, "Favori", status=COMPLETED, favorite=True, tmdb_id=2)
    add_movie(session, "Arrêté", status=STOPPED, tmdb_id=3)
    add_movie(session, "Sans id", status=COMPLETED, favorite=True)
    library.set_rating(session, MOVIE, 3, 1)  # Arrêté, très mal noté : -2
    session.flush()

    sources = suggestions.library_sources(session, MOVIE)

    assert [(s.title, s.weight) for s in sources] == [("Favori", 3), ("Arrêté", -2)]


# --- Calcul -----------------------------------------------------------------------------

def test_compute_scores_excludes_and_explains(session):
    add_movie(session, "Fight Club", status=COMPLETED, favorite=True, tmdb_id=550)
    add_movie(session, "Seven", status=COMPLETED, tmdb_id=807)
    add_movie(session, "Déjà là", status=TO_WATCH, tmdb_id=900)
    session.add(DismissedSuggestion(kind=MOVIE, tmdb_id=901, title="Écarté"))
    session.flush()
    tmdb = RecoTMDB(recos={
        ("movie", 550): [rec(10, "Memento"), rec(900, "Déjà là"), rec(901, "Écarté"),
                         rec(11, "Confidentiel", votes=3)],
        ("movie", 807): [rec(12, "Zodiac", genres=(27,)), rec(10, "Memento")],
        ("movie", 900): [rec(12, "Zodiac", genres=(27,))],
    })

    result = suggestions.compute(session, tmdb, MOVIE)

    assert [s.title for s in result] == ["Memento", "Zodiac"]
    memento, zodiac = result
    assert memento.because == ["Fight Club", "Seven"]   # le favori compte plus
    assert zodiac.because == ["Seven", "Déjà là"]
    assert zodiac.genres == ["Horreur"]
    assert memento.year == 2019
    assert memento.poster_url.endswith("/p10.jpg")
    assert memento.score > zodiac.score


def test_compute_negative_source_pushes_away(session):
    add_show(session, "Aimée", status=COMPLETED, tmdb_id=1)
    add_show(session, "Arrêtée", status=STOPPED, tmdb_id=2)
    session.flush()
    tmdb = RecoTMDB(recos={
        ("tv", 1): [rec(20, "Proche des deux", tv=True), rec(21, "Bonne", tv=True)],
        ("tv", 2): [rec(20, "Proche des deux", tv=True), rec(22, "Mauvaise", tv=True)],
    })

    result = suggestions.compute(session, tmdb, SHOW)

    # "Proche des deux" : +1 puis -1 -> 0, retirée ; "Mauvaise" : négative, retirée
    assert [s.title for s in result] == ["Bonne"]
    assert result[0].because == ["Aimée"]


def test_compute_skips_failing_sources(session):
    add_movie(session, "A", status=COMPLETED, tmdb_id=1)
    add_movie(session, "B", status=COMPLETED, tmdb_id=2)
    session.flush()
    tmdb = RecoTMDB(recos={("movie", 2): [rec(30, "Trouvé")]}, fail_ids={1})
    progress = []

    result = suggestions.compute(session, tmdb, MOVIE, progress=lambda d, t: progress.append((d, t)))

    assert [s.title for s in result] == ["Trouvé"]
    assert progress == [(1, 2), (2, 2)]


def test_compute_all_sources_failing_raises(session):
    add_movie(session, "A", status=COMPLETED, tmdb_id=1)
    session.flush()
    with pytest.raises(ApiError):
        suggestions.compute(session, RecoTMDB(fail_ids={1}), MOVIE)


def test_compute_empty_library(session):
    assert suggestions.compute(session, RecoTMDB(), MOVIE) == []


def test_resolve_show_tmdb_ids(session):
    known = add_show(session, "Connue", tvdb_id=1001)
    unknown = add_show(session, "Inconnue", tvdb_id=9999)
    session.flush()

    found = suggestions.resolve_show_tmdb_ids(session, RecoTMDB(find={1001: 77}))

    assert found == 1
    assert known.tmdb_id == 77 and unknown.tmdb_id is None


# --- Actions ------------------------------------------------------------------------------

def test_dismiss_and_restore(session):
    suggestions.dismiss(session, MOVIE, 10, "Memento")
    suggestions.dismiss(session, MOVIE, 10, "Memento")   # pas de doublon
    session.flush()
    assert [d.title for d in suggestions.dismissed(session)] == ["Memento"]
    assert 10 in suggestions.excluded_ids(session, MOVIE)
    assert 10 not in suggestions.excluded_ids(session, SHOW)

    assert suggestions.undismiss_all(session) == 1
    session.flush()
    assert suggestions.dismissed(session) == []


def test_add_suggested_show_via_tvdb(session):
    tmdb = RecoTMDB(external={5000: {"tvdb_id": 1001, "imdb_id": None}})

    show = suggestions.add_suggested_show(session, tmdb, FakeTVMaze(), 5000, TO_WATCH,
                                          title="Titre français")

    assert show.tvmaze_id == 101 and show.tmdb_id == 5000
    assert show.title == "Titre français"   # TVMaze n'a pas de titre FR (akas vides)
    assert library.get_library_item(session, SHOW, show.id).status == TO_WATCH


def test_add_suggested_show_via_imdb(session):
    tmdb = RecoTMDB(external={6000: {"tvdb_id": 4242, "imdb_id": "tt0202"}})
    tvmaze = ImdbTVMaze()

    show = suggestions.add_suggested_show(session, tmdb, tvmaze, 6000, WATCHING)

    assert show.tvmaze_id == 202 and show.tmdb_id == 6000
    assert ("lookup_tvdb", 4242) in tvmaze.calls and ("lookup_imdb", "tt0202") in tvmaze.calls


def test_add_suggested_show_not_on_tvmaze(session):
    tmdb = RecoTMDB(external={7000: {"tvdb_id": 4242}})
    with pytest.raises(ValueError, match="introuvable sur TVMaze"):
        suggestions.add_suggested_show(session, tmdb, ImdbTVMaze(), 7000, TO_WATCH)
    assert session.exec(select(Show)).all() == []


def test_add_suggested_movie(session):
    movie = suggestions.add_suggested_movie(session, RecoTMDB(), 550, TO_WATCH)
    assert movie.tmdb_id == 550
    assert library.get_library_item(session, MOVIE, movie.id).status == TO_WATCH


# --- Client TMDB : nouveaux appels ----------------------------------------------------------

def tmdb_server(request):
    path = request.url.path
    if path == "/3/authentication":
        return httpx.Response(200, json={"success": True})
    if path == "/3/tv/42/recommendations":
        return httpx.Response(200, json={"results": [rec(1, "Une", tv=True)]})
    if path == "/3/genre/movie/list":
        return httpx.Response(200, json={"genres": [{"id": 18, "name": "Drame"}]})
    if path == "/3/find/1001":
        return httpx.Response(200, json={"tv_results": [{"id": 77}], "movie_results": []})
    if path == "/3/find/9999":
        return httpx.Response(200, json={"tv_results": []})
    if path == "/3/tv/42/external_ids":
        return httpx.Response(200, json={"tvdb_id": 1001, "imdb_id": "tt1"})
    return httpx.Response(404)


def test_tmdb_client_new_calls(engine):
    api = FakeApi(tmdb_server)
    client = TMDBClient(engine, token="fake-token", api_key="", transport=api.transport,
                        sleep=no_sleep)

    assert [r["name"] for r in client.recommendations("tv", 42)] == ["Une"]
    assert client.recommendations("tv", 42)  # 2e appel : cache
    assert client.recommendations("movie", 1) == []   # 404
    assert client.genres("movie") == {18: "Drame"}
    assert client.find_tv_by_tvdb(1001) == 77
    assert client.find_tv_by_tvdb(9999) is None
    assert client.tv_external_ids(42)["tvdb_id"] == 1001

    assert api.paths.count("/3/tv/42/recommendations") == 1
    assert "/tv/42/recommendations?language=fr-FR" in cache_keys(engine)
    assert all("fake-token" not in key for key in cache_keys(engine))


def test_tmdb_client_unavailable_returns_empty(engine):
    client = TMDBClient(engine, token="", api_key="")
    assert client.recommendations("tv", 42) == []
    assert client.genres("tv") == {}
    assert client.tv_external_ids(42) == {}
    assert client.find_tv_by_tvdb(1001) is None
