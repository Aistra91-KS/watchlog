"""Tests de l'ajout de séries / films (recherche, ajout API, saisie manuelle, progression)."""

from datetime import date

import pytest
from sqlmodel import Session, select

from conftest import FakeTMDB, FakeTVMaze
from watchlog.core import adding, library
from watchlog.core.http import ApiError
from watchlog.core.models import (EPISODE, MATCH_NOT_FOUND, MATCH_OK, MOVIE, SHOW, TO_WATCH,
                                  WATCHING, Episode, Movie, Show)


class AkasTVMaze(FakeTVMaze):
    """Faux TVMaze avec des titres alternatifs (un titre français pour la série 101)."""

    def __init__(self, akas_fail=False, **kwargs):
        super().__init__(**kwargs)
        self.akas_fail = akas_fail

    def get_akas(self, tvmaze_id):
        if self.akas_fail:
            raise ApiError("TVMaze indisponible (simulé)")
        if tvmaze_id == 101:
            return [{"name": "El Reino", "country": {"code": "ES"}},
                    {"name": "Le Royaume", "country": {"code": "FR"}}]
        return [{"name": "Sans pays", "country": None}]


class SearchTMDB(FakeTMDB):
    """Faux TMDB dont la recherche renvoie Fight Club."""

    def search_movies(self, query):
        if not self.available:
            return []
        return [{"id": 550, "title": "Fight Club", "original_title": "Fight Club",
                 "release_date": "1999-10-15", "poster_path": "/fakeposter550.jpg",
                 "overview": "Le narrateur, sans identité précise, vit seul. " * 10},
                {"id": 999, "title": "Inconnu", "release_date": "", "poster_path": None}]


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


# --- Petits utilitaires ------------------------------------------------------------

def test_tvmaze_status_label():
    assert adding.tvmaze_status_label("Running") == "En cours de diffusion"
    assert adding.tvmaze_status_label("Ended") == "Terminée"
    assert adding.tvmaze_status_label("To Be Determined") == "Indéterminé"
    assert adding.tvmaze_status_label("In Development") == "En développement"
    assert adding.tvmaze_status_label(None) == ""
    assert adding.tvmaze_status_label("Autre") == "Autre"


def test_short_text():
    assert adding.short_text("<p><b>Court</b></p>") == "Court"
    assert adding.short_text(None) == ""
    long = adding.short_text("mot " * 100, length=20)
    assert long.endswith("…") and len(long) <= 21


def test_parse_episode_counts():
    assert adding.parse_episode_counts("10, 8, 12") == [10, 8, 12]
    assert adding.parse_episode_counts("10;8 12") == [10, 8, 12]
    for bad in ("", "  ", "10, abc", "10, 0", "-3"):
        with pytest.raises(ValueError):
            adding.parse_episode_counts(bad)


# --- Recherche ------------------------------------------------------------------------

def test_search_shows(session):
    tvmaze = FakeTVMaze()
    results = adding.search_shows(session, tvmaze, "The Hidden One")
    assert len(results) == 1
    result = results[0]
    assert result["id"] == 303 and result["title"] == "The Hidden One"
    assert result["year"] == 2022 and result["network"] == "HBO"
    assert result["status"] == "En cours de diffusion"
    assert result["poster_url"].endswith("303.jpg")
    assert "<" not in result["summary"]
    assert result["in_library"] is False and result["local_id"] is None
    assert adding.search_shows(session, tvmaze, "   ") == []
    assert adding.search_shows(session, tvmaze, "Rien") == []


def test_search_shows_marks_library(session):
    tvmaze = FakeTVMaze()
    show = adding.add_show(session, tvmaze, 303, WATCHING)
    session.commit()
    result = adding.search_shows(session, tvmaze, "The Hidden One")[0]
    assert result["in_library"] is True and result["local_id"] == show.id
    # Retirée de la bibliothèque : connue en base, mais plus « dans la bibliothèque »
    library.remove_from_library(session, SHOW, show.id)
    session.commit()
    result = adding.search_shows(session, tvmaze, "The Hidden One")[0]
    assert result["in_library"] is False and result["local_id"] == show.id


def test_search_movies(session):
    tmdb = SearchTMDB()
    results = adding.search_movies(session, tmdb, "fight")
    assert [r["id"] for r in results] == [550, 999]
    fight = results[0]
    assert fight["year"] == 1999 and fight["network"] is None
    assert fight["poster_url"] == "https://image.tmdb.org/t/p/w342/fakeposter550.jpg"
    assert fight["summary"].endswith("…")
    assert results[1]["year"] is None and results[1]["poster_url"] is None

    adding.add_movie(session, tmdb, 550, TO_WATCH)
    session.commit()
    assert adding.search_movies(session, tmdb, "fight")[0]["in_library"] is True
    # TMDB non connecté : aucun résultat
    assert adding.search_movies(session, SearchTMDB(available=False), "fight") == []


def test_search_api_error_propagates(session):
    with pytest.raises(ApiError):
        adding.search_shows(session, FakeTVMaze(fail=True), "The Hidden One")


class FailingSearchTMDB(SearchTMDB):
    """Faux TMDB connecté dont la recherche tombe en panne."""

    def search_movies(self, query):
        raise ApiError("TMDB indisponible (simulé)")


def test_search_all_mixes_shows_and_movies(session):
    tvmaze = FakeTVMaze(searches={"fight": "tvmaze_search_hidden.json"})
    outcome = adding.search_all(session, tvmaze, SearchTMDB(), "fight")
    assert outcome.errors == {}
    # Alternance série / film, puis le reste des films
    assert [(r["kind"], r["id"]) for r in outcome.results] == [
        (SHOW, 303), (MOVIE, 550), (MOVIE, 999)]
    assert outcome.results[0]["network"] == "HBO"
    empty = adding.search_all(session, tvmaze, SearchTMDB(), "  ")
    assert empty.results == [] and empty.errors == {}


def test_search_all_tmdb_unavailable(session, caplog):
    tvmaze = FakeTVMaze()
    for tmdb in (None, SearchTMDB(available=False)):
        outcome = adding.search_all(session, tvmaze, tmdb, "The Hidden One")
        assert [r["kind"] for r in outcome.results] == [SHOW]
        assert outcome.errors == {MOVIE: adding.MOVIES_UNAVAILABLE}
    outcome = adding.search_all(session, tvmaze, FailingSearchTMDB(), "The Hidden One")
    assert [r["kind"] for r in outcome.results] == [SHOW]
    assert "TMDB indisponible" in outcome.errors[MOVIE]
    assert "Recherche TMDB impossible" in caplog.text


def test_refresh_library_state(session):
    """Titre ajouté depuis l'Aperçu après la recherche : le résultat affiché suit."""
    tvmaze = FakeTVMaze(searches={"fight": "tvmaze_search_hidden.json"})
    tmdb = SearchTMDB()
    results = adding.search_all(session, tvmaze, tmdb, "fight").results
    assert results[0]["tvdb_id"] is None and results[1]["tvdb_id"] is None
    assert not any(r["in_library"] for r in results)
    show = adding.add_show(session, tvmaze, 303, WATCHING)
    movie = adding.add_movie(session, tmdb, 550, TO_WATCH)
    adding.refresh_library_state(session, results)
    assert [(r["in_library"], r["local_id"]) for r in results] == [
        (True, show.id), (True, movie.id), (False, None)]


def test_search_all_tvmaze_down(session, caplog):
    outcome = adding.search_all(session, FakeTVMaze(fail=True), SearchTMDB(), "fight")
    assert [r["kind"] for r in outcome.results] == [MOVIE, MOVIE]
    assert "TVMaze indisponible" in outcome.errors[SHOW] and MOVIE not in outcome.errors
    assert "Recherche TVMaze impossible" in caplog.text


# --- Ajout depuis les API ------------------------------------------------------------------

def test_add_show_uses_french_title(session):
    show = adding.add_show(session, AkasTVMaze(), 101, WATCHING)
    session.commit()
    assert show.title == "Le Royaume" and show.original_title == "The Kingdom"
    assert show.tvmaze_id == 101 and show.match_status == MATCH_OK
    assert len(session.exec(select(Episode).where(Episode.show_id == show.id)).all()) == 7
    assert library.get_library_item(session, SHOW, show.id).status == WATCHING


def test_add_show_without_french_title(session):
    show = adding.add_show(session, AkasTVMaze(), 303, TO_WATCH)
    assert show.title == "The Hidden One" and show.original_title == "The Hidden One"
    # Panne sur les titres alternatifs : on garde le nom TVMaze
    show = adding.add_show(session, AkasTVMaze(akas_fail=True), 202, TO_WATCH)
    assert show.title == "Lupin"


def test_add_show_twice_does_not_duplicate(session):
    tvmaze = AkasTVMaze()
    first = adding.add_show(session, tvmaze, 101, TO_WATCH)
    session.commit()
    first.title = "Mon titre"
    session.add(first)
    session.commit()
    calls = len(tvmaze.calls)
    again = adding.add_show(session, tvmaze, 101, WATCHING)
    session.commit()
    assert again.id == first.id and again.title == "Mon titre"
    assert len(tvmaze.calls) == calls  # rien n'est retéléchargé
    assert len(session.exec(select(Show)).all()) == 1
    assert library.get_library_item(session, SHOW, first.id).status == WATCHING


def test_add_show_reuses_unmatched_import(session):
    """Série importée non trouvée (même id TVDB) : réutilisée, pas de doublon."""
    old = Show(title="Le Royaume", tvdb_id=1001, match_status=MATCH_NOT_FOUND)
    session.add(old)
    session.commit()
    show = adding.add_show(session, AkasTVMaze(), 101, WATCHING)
    assert show.id == old.id and show.tvmaze_id is None
    assert show.match_status == MATCH_NOT_FOUND  # à rattacher dans Paramètres
    assert len(session.exec(select(Show)).all()) == 1
    assert library.get_library_item(session, SHOW, show.id).status == WATCHING


def test_add_show_errors(session):
    with pytest.raises(ValueError):
        adding.add_show(session, FakeTVMaze(), 999, WATCHING)   # inconnue chez TVMaze
    with pytest.raises(ValueError):
        adding.add_show(session, FakeTVMaze(), 101, "inconnu")
    with pytest.raises(ApiError):
        adding.add_show(session, FakeTVMaze(fail=True), 101, WATCHING)


def test_add_movie(session):
    tmdb = FakeTMDB()
    movie = adding.add_movie(session, tmdb, 550, TO_WATCH)
    session.commit()
    assert movie.title == "Fight Club" and movie.release_date_fr == date(1999, 11, 10)
    assert library.get_library_item(session, MOVIE, movie.id).status == TO_WATCH
    again = adding.add_movie(session, tmdb, 550, WATCHING)
    assert again.id == movie.id
    assert len(session.exec(select(Movie)).all()) == 1
    assert library.get_library_item(session, MOVIE, movie.id).status == WATCHING
    with pytest.raises(ValueError):
        adding.add_movie(session, tmdb, 12345, TO_WATCH)          # inconnu
    with pytest.raises(ValueError):
        adding.add_movie(session, FakeTMDB(available=False), 550, TO_WATCH)


# --- Saisie manuelle ----------------------------------------------------------------------

def test_add_manual_show(session):
    show = adding.add_manual_show(session, "  Ma série  ", [3, 2], WATCHING,
                                  original_title="My Show", premiered=date(2020, 1, 1))
    session.commit()
    assert show.title == "Ma série" and show.original_title == "My Show"
    assert show.is_manual and show.match_status == MATCH_OK and show.tvmaze_id is None
    episodes = session.exec(select(Episode).where(Episode.show_id == show.id)
                            .order_by(Episode.season, Episode.number)).all()
    assert [(e.season, e.number) for e in episodes] == [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2)]
    assert all(e.airdate is None and not e.is_special for e in episodes)
    assert library.get_library_item(session, SHOW, show.id).status == WATCHING


def test_add_manual_show_errors(session):
    with pytest.raises(ValueError):
        adding.add_manual_show(session, "  ", [3], WATCHING)
    with pytest.raises(ValueError):
        adding.add_manual_show(session, "Titre", [], WATCHING)
    with pytest.raises(ValueError):
        adding.add_manual_show(session, "Titre", [3], "inconnu")
    assert session.exec(select(Show)).all() == []


def test_add_manual_movie(session):
    movie = adding.add_manual_movie(session, "Mon film", TO_WATCH, release_date=date(2024, 5, 2),
                                    runtime=95, overview="  Résumé  ")
    session.commit()
    assert movie.is_manual and movie.tmdb_id is None
    assert movie.year == 2024 and movie.release_date_fr == date(2024, 5, 2)
    assert movie.runtime == 95 and movie.overview == "Résumé"
    assert library.get_library_item(session, MOVIE, movie.id).status == TO_WATCH
    other = adding.add_manual_movie(session, "Vieux film", WATCHING, year=1950)
    assert other.year == 1950 and other.release_date_fr is None and other.overview is None
    with pytest.raises(ValueError):
        adding.add_manual_movie(session, "", TO_WATCH)


# --- « J'en suis à S2E5 » -------------------------------------------------------------------

def _watched(session, show_id):
    """(saison, numéro ou None) -> date du premier visionnage, pour les épisodes vus."""
    episodes = session.exec(select(Episode).where(Episode.show_id == show_id)).all()
    return {(ep.season, ep.number): watch.watched_at for ep in episodes
            if (watch := library.get_watch(session, EPISODE, ep.id)) is not None}


def test_episode_choices(session):
    show = adding.add_show(session, AkasTVMaze(), 101, TO_WATCH)
    choices = adding.episode_choices(session, show.id, today=date(2021, 1, 5))
    # Spécial exclu ; S2E2 (diffusé le 10/01/2021) pas encore sorti ; S2E3 sans date gardé
    assert {s: [n for n, _t in eps] for s, eps in choices.items()} == {1: [1, 2, 3], 2: [1, 3]}
    manual = adding.add_manual_show(session, "Ma série", [2, 1], TO_WATCH)
    assert adding.episode_choices(session, manual.id) == {1: [(1, None), (2, None)],
                                                          2: [(1, None)]}
    assert adding.episode_choices(session, 9999) == {}


def test_mark_progress_unknown_date(session):
    show = adding.add_show(session, AkasTVMaze(), 101, TO_WATCH)
    assert adding.mark_progress(session, show.id, 2, 1) == 4
    session.commit()
    watched = _watched(session, show.id)
    # S1E1-3 + S2E1, sans le spécial ni la suite ; date inconnue (pas la date du jour)
    assert set(watched) == {(1, 1), (1, 2), (1, 3), (2, 1)}
    assert all(when is None for when in watched.values())
    assert library.get_library_item(session, SHOW, show.id).status == WATCHING


def test_mark_progress_with_date_keeps_existing(session):
    show = adding.add_manual_show(session, "Ma série", [3, 3], TO_WATCH)
    first = session.exec(select(Episode).where(Episode.show_id == show.id,
                                               Episode.season == 1, Episode.number == 1)).one()
    library.set_watch_date(session, EPISODE, first.id, date(2019, 5, 1))
    assert adding.mark_progress(session, show.id, 2, 2, day=date(2024, 3, 10),
                                status=None) == 4
    watched = _watched(session, show.id)
    assert set(watched) == {(1, 1), (1, 2), (1, 3), (2, 1), (2, 2)}
    assert watched[(1, 1)].year == 2019                     # déjà vu : date gardée
    # Date choisie (stockée en UTC naïf : la veille au soir possible selon le fuseau)
    assert {watched[k].date() for k in watched if k != (1, 1)} <= {date(2024, 3, 9),
                                                                   date(2024, 3, 10)}
    assert library.get_library_item(session, SHOW, show.id).status == TO_WATCH
    assert adding.mark_progress(session, show.id, 2, 2) == 0   # relancer : rien de plus


def test_mark_progress_errors(session):
    show = adding.add_manual_show(session, "Ma série", [2], TO_WATCH)
    with pytest.raises(ValueError):
        adding.mark_progress(session, show.id, 3, 1)           # saison inexistante
    with pytest.raises(ValueError):
        adding.mark_progress(session, show.id, 1, 1, status="inconnu")
    assert _watched(session, show.id) == {}
