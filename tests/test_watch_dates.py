"""Tests de la date de visionnage modifiable (films vus il y a longtemps, épisodes)."""

from datetime import date, datetime

import pytest
from sqlmodel import Session

from conftest import FakeTMDB
from helpers_db import add_episode, add_movie, add_show
from watchlog.core import adding, library
from watchlog.core.dates import local_day_to_utc
from watchlog.core.models import COMPLETED, EPISODE, MOVIE, TO_WATCH, Watch
from watchlog.core.queue import local_date


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.mark.parametrize("day", [date(2010, 1, 15), date(2024, 7, 1), date(2023, 3, 26)])
def test_local_day_to_utc_keeps_the_day(day):
    # hiver (UTC+1), été (UTC+2) et jour du changement d'heure
    assert local_date(local_day_to_utc(day), None) == day


def test_set_watch_date_marks_watched(session):
    movie = add_movie(session, "Vieux film", status=COMPLETED)

    watch = library.set_watch_date(session, MOVIE, movie.id, date(2005, 6, 1))

    assert local_date(watch.watched_at, None) == date(2005, 6, 1)
    assert watch.last_watched_at == watch.watched_at and watch.plays == 1


def test_set_watch_date_single_play_moves_both_dates(session):
    movie = add_movie(session, "Film")
    library.mark_watched(session, MOVIE, movie.id, datetime(2026, 10, 1, 20))

    watch = library.set_watch_date(session, MOVIE, movie.id, date(2001, 1, 1))

    assert watch.watched_at.year == 2001 and watch.last_watched_at.year == 2001


def test_set_watch_date_several_plays_keeps_latest(session):
    movie = add_movie(session, "Revu")
    session.add(Watch(kind=MOVIE, ref_id=movie.id, watched_at=datetime(2015, 1, 1),
                      last_watched_at=datetime(2025, 1, 1), plays=3))
    session.flush()

    watch = library.set_watch_date(session, MOVIE, movie.id, date(2012, 5, 5))
    assert watch.watched_at.year == 2012 and watch.last_watched_at.year == 2025

    watch = library.set_watch_date(session, MOVIE, movie.id, date(2026, 2, 2))
    assert watch.last_watched_at.year == 2026   # nouvelle date plus récente : avancée


def test_set_watch_date_none_means_unknown(session):
    show = add_show(session, "Série")
    episode = add_episode(session, show, 1, 1)
    library.mark_watched(session, EPISODE, episode.id)

    watch = library.set_watch_date(session, EPISODE, episode.id, None)

    assert watch.watched_at is None and watch.last_watched_at is None
    assert library.get_watch(session, EPISODE, episode.id) is not None   # toujours vu


# --- Ajout d'un film en « Terminé » -----------------------------------------------------------

def test_add_movie_completed_records_watch(session):
    movie = adding.add_movie(session, FakeTMDB(), 550, COMPLETED, watched_on=date(1999, 11, 10))
    watch = library.get_watch(session, MOVIE, movie.id)
    assert local_date(watch.watched_at, None) == date(1999, 11, 10)


def test_add_movie_completed_without_date(session):
    movie = adding.add_movie(session, FakeTMDB(), 550, COMPLETED)
    watch = library.get_watch(session, MOVIE, movie.id)
    assert watch is not None and watch.watched_at is None


def test_add_movie_to_watch_records_nothing(session):
    movie = adding.add_movie(session, FakeTMDB(), 550, TO_WATCH, watched_on=date(2000, 1, 1))
    assert library.get_watch(session, MOVIE, movie.id) is None


def test_add_movie_completed_keeps_existing_watch(session):
    movie = adding.add_movie(session, FakeTMDB(), 550, TO_WATCH)
    library.mark_watched(session, MOVIE, movie.id, datetime(2020, 2, 2, 20))

    adding.add_movie(session, FakeTMDB(), 550, COMPLETED, watched_on=date(2000, 1, 1))

    assert library.get_watch(session, MOVIE, movie.id).watched_at == datetime(2020, 2, 2, 20)


def test_add_manual_movie_completed_with_date(session):
    movie = adding.add_manual_movie(session, "Film perso", COMPLETED, watched_on=date(1995, 3, 3))
    watch = library.get_watch(session, MOVIE, movie.id)
    assert local_date(watch.watched_at, None) == date(1995, 3, 3)
