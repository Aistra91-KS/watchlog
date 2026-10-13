"""Tests de la liste « À regarder » et du calendrier (watchlog/core/queue.py)."""

from datetime import date, datetime, timedelta

import pytest
from sqlmodel import Session

from helpers_db import add_episode, add_movie, add_show, watch
from watchlog.core import queue
from watchlog.core.models import (EPISODE, FOR_LATER, MATCH_NUMBERING, MATCH_REMAPPED, MOVIE, STOPPED,
                                  TO_WATCH)
from watchlog.core.queue import NO_DATE, RELEASED, UPCOMING

TODAY = date(2026, 10, 12)


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def codes(items) -> list[str]:
    """Résumé lisible d'une liste : « Titre SxxEyy » pour un épisode, « Titre » pour un film."""
    return [f"{i.title} {i.code}".strip() for i in items]


def build(session, **kwargs):
    return queue.build_queue(session, today=TODAY, **kwargs)


# --- Tri et contenu ---------------------------------------------------------------

def test_sorted_by_date_shows_interleaved_with_movies(session):
    alpha = add_show(session, "Alpha")
    beta = add_show(session, "Beta")
    add_episode(session, alpha, 1, 1, airdate=date(2026, 1, 5))
    add_episode(session, alpha, 1, 2, airdate=date(2026, 1, 20))
    add_episode(session, beta, 1, 1, airdate=date(2026, 1, 10))
    add_episode(session, beta, 1, 2, airdate=date(2026, 1, 25))
    add_movie(session, "Film", release=date(2026, 1, 15))
    session.commit()

    items = build(session)
    assert codes(items) == ["Alpha S01E01", "Beta S01E01", "Film", "Alpha S01E02", "Beta S01E02"]
    assert [i.kind for i in items] == [EPISODE, EPISODE, MOVIE, EPISODE, EPISODE]
    assert items[0].episode_title == "Épisode 1x1" and items[0].runtime == 45


def test_same_date_sorted_by_title_then_episode(session):
    zeta = add_show(session, "Zeta")
    alpha = add_show(session, "alpha")
    for n in (2, 1):
        add_episode(session, zeta, 1, n, airdate=date(2026, 3, 1))
    add_episode(session, alpha, 2, 1, airdate=date(2026, 3, 1))
    session.commit()
    assert codes(build(session)) == ["alpha S02E01", "Zeta S01E01", "Zeta S01E02"]


def test_watched_episodes_and_movies_excluded(session):
    show = add_show(session, "Série")
    seen = add_episode(session, show, 1, 1, airdate=date(2026, 1, 1))
    add_episode(session, show, 1, 2, airdate=date(2026, 1, 8))
    movie = add_movie(session, "Film vu", release=date(2025, 1, 1))
    watch(session, seen)
    watch(session, movie)
    session.commit()
    assert codes(build(session)) == ["Série S01E02"]


def test_upcoming_and_no_date_at_the_end(session):
    show = add_show(session, "Série")
    add_episode(session, show, 1, 3)                                  # sans date
    add_episode(session, show, 1, 2, airdate=TODAY + timedelta(days=7))
    add_episode(session, show, 1, 1, airdate=TODAY)                   # aujourd'hui = sorti
    add_movie(session, "Film sans date")
    add_movie(session, "Film à venir", release=TODAY + timedelta(days=1))
    session.commit()

    items = build(session)
    assert codes(items) == ["Série S01E01", "Film à venir", "Série S01E02",
                            "Film sans date", "Série S01E03"]
    assert [i.availability for i in items] == [RELEASED, UPCOMING, UPCOMING, NO_DATE, NO_DATE]

    released = build(session, availabilities=(RELEASED,))
    assert codes(released) == ["Série S01E01"]
    no_date = build(session, availabilities=(NO_DATE,))
    assert codes(no_date) == ["Film sans date", "Série S01E03"]


def test_specials_excluded_by_default(session):
    show = add_show(session, "Série")
    add_episode(session, show, 1, 1, airdate=date(2026, 1, 1))
    special = add_episode(session, show, 1, None, airdate=date(2026, 1, 2))
    session.commit()

    assert codes(build(session)) == ["Série S01E01"]
    items = build(session, include_specials=True)
    assert codes(items) == ["Série S01E01", "Série S01 spécial"]
    assert items[1].ref_id == special.id and items[1].is_special


def test_statuses_filter(session):
    for status in ("watching", TO_WATCH, FOR_LATER, STOPPED, "completed"):
        show = add_show(session, f"Série {status}", status=status)
        add_episode(session, show, 1, 1, airdate=date(2026, 1, 1))
    add_movie(session, "Film plus tard", release=date(2026, 1, 1), status=FOR_LATER)
    session.commit()

    # Par défaut : en cours + à voir
    assert codes(build(session)) == ["Série to_watch S01E01", "Série watching S01E01"]
    later = build(session, statuses=(FOR_LATER,))
    assert codes(later) == ["Film plus tard", "Série for_later S01E01"]
    assert {i.status for i in later} == {FOR_LATER}


def test_kinds_and_show_filter(session):
    alpha = add_show(session, "Alpha")
    beta = add_show(session, "Beta")
    add_episode(session, alpha, 1, 1, airdate=date(2026, 1, 1))
    add_episode(session, beta, 1, 1, airdate=date(2026, 1, 2))
    add_movie(session, "Film", release=date(2026, 1, 3))
    session.commit()

    assert codes(build(session, kinds=(MOVIE,))) == ["Film"]
    assert codes(build(session, kinds=(EPISODE,))) == ["Alpha S01E01", "Beta S01E01"]
    # Une série choisie : ses épisodes seulement (pas de films)
    assert codes(build(session, show_id=beta.id)) == ["Beta S01E01"]


def test_numbering_mismatch_show_excluded(session):
    broken = add_show(session, "Cassée", match_status=MATCH_NUMBERING)
    fixed = add_show(session, "Réparée", match_status=MATCH_REMAPPED)
    add_episode(session, broken, 1, 1, airdate=date(2026, 1, 1))
    add_episode(session, fixed, 1, 1, airdate=date(2026, 1, 1))
    session.commit()
    assert codes(build(session)) == ["Réparée S01E01"]


def test_next_only_keeps_first_unwatched_episode_of_each_show(session):
    alpha = add_show(session, "Alpha")
    beta = add_show(session, "Beta")
    watch(session, add_episode(session, alpha, 1, 1, airdate=date(2026, 1, 1)))
    add_episode(session, alpha, 1, 2, airdate=date(2026, 1, 8))
    add_episode(session, alpha, 2, 1, airdate=date(2026, 6, 1))
    # Beta : le S02E01 a une date plus ancienne que S01E05 (rediffusion) ; l'ordre
    # (saison, numéro) prime pour choisir le prochain épisode.
    add_episode(session, beta, 2, 1, airdate=date(2026, 1, 3))
    add_episode(session, beta, 1, 5, airdate=date(2026, 2, 1))
    add_movie(session, "Film", release=date(2026, 1, 5))
    session.commit()

    items = build(session, next_only=True)
    assert codes(items) == ["Film", "Alpha S01E02", "Beta S01E05"]


def test_next_only_with_specials_puts_special_after_regular_episodes(session):
    show = add_show(session, "Série")
    add_episode(session, show, 1, None, airdate=date(2026, 1, 1))
    add_episode(session, show, 1, 1, airdate=date(2026, 1, 8))
    session.commit()
    assert codes(build(session, next_only=True, include_specials=True)) == ["Série S01E01"]


# --- Dates locales -----------------------------------------------------------------

def test_local_date_converts_utc_airstamp_to_paris():
    # 23:30 UTC en été (UTC+2) = 01:30 le lendemain à Paris
    assert queue.local_date(datetime(2026, 7, 1, 23, 30), date(2026, 7, 1)) == date(2026, 7, 2)
    # En hiver (UTC+1) : 22:30 UTC = 23:30 le même jour
    assert queue.local_date(datetime(2026, 1, 15, 22, 30), date(2026, 1, 15)) == date(2026, 1, 15)
    # Sans airstamp : date TVMaze telle quelle ; rien du tout : None
    assert queue.local_date(None, date(2026, 7, 1)) == date(2026, 7, 1)
    assert queue.local_date(None, None) is None


def test_queue_uses_local_date(session):
    show = add_show(session, "Série US")
    add_episode(session, show, 1, 1, airdate=date(2026, 7, 1), airstamp=datetime(2026, 7, 1, 23, 30))
    session.commit()
    [item] = queue.build_queue(session, today=date(2026, 7, 1))
    assert item.release_date == date(2026, 7, 2)
    assert item.availability == UPCOMING     # pas encore sorti en France le 01/07
    [item] = queue.build_queue(session, today=date(2026, 7, 2))
    assert item.availability == RELEASED


def test_episode_code_and_week_start():
    assert queue.episode_code(9, 9) == "S09E09"
    assert queue.episode_code(1, None) == "S01 spécial"
    assert queue.week_start(date(2026, 10, 12)) == date(2026, 10, 12)   # lundi
    assert queue.week_start(date(2026, 10, 18)) == date(2026, 10, 12)   # dimanche


# --- Calendrier --------------------------------------------------------------------

def test_calendar_next_60_days(session):
    current = add_show(session, "En cours")
    later = add_show(session, "Plus tard", status=FOR_LATER)
    stopped = add_show(session, "Arrêtée", status=STOPPED)
    add_episode(session, current, 1, 1, airdate=TODAY - timedelta(days=1))    # passé : non
    add_episode(session, current, 1, 2, airdate=TODAY)                        # aujourd'hui : oui
    add_episode(session, current, 1, 3, airdate=TODAY + timedelta(days=60))  # limite : oui
    add_episode(session, current, 1, 4, airdate=TODAY + timedelta(days=61))  # trop loin : non
    add_episode(session, current, 1, 5)                                       # sans date : non
    add_episode(session, later, 1, 1, airdate=TODAY + timedelta(days=3))
    add_episode(session, stopped, 1, 1, airdate=TODAY + timedelta(days=3))
    seen = add_episode(session, current, 1, 6, airdate=TODAY + timedelta(days=5))
    watch(session, seen)
    add_movie(session, "Film", release=TODAY + timedelta(days=10))
    session.commit()

    items = queue.calendar(session, 60, today=TODAY)
    assert codes(items) == ["En cours S01E02", "Plus tard S01E01", "Film", "En cours S01E03"]
    assert [i.release_date for i in items] == [TODAY, TODAY + timedelta(days=3),
                                               TODAY + timedelta(days=10), TODAY + timedelta(days=60)]


# --- Calendrier : sorties d'une période (vues comprises) et regroupement ----------------

def test_releases_between_includes_watched_and_excludes_stopped(session):
    from watchlog.core.models import COMPLETED

    alpha = add_show(session, "Alpha", poster_url="https://img/alpha.jpg")
    seen = add_episode(session, alpha, 1, 1, airdate=date(2026, 10, 1))
    add_episode(session, alpha, 1, 2, airdate=date(2026, 10, 8))
    add_episode(session, alpha, 1, 3, airdate=date(2026, 11, 2))          # hors période
    stopped = add_show(session, "Arrêtée", status=STOPPED)
    add_episode(session, stopped, 1, 1, airdate=date(2026, 10, 3))
    done = add_show(session, "Finie", status=COMPLETED)
    add_episode(session, done, 1, 1, airdate=date(2026, 10, 4))
    film = add_movie(session, "Film vu", release=date(2026, 10, 20), status=COMPLETED)
    add_movie(session, "Film plus tard", release=date(2026, 12, 1))
    watch(session, seen)
    watch(session, film)

    items = queue.releases_between(session, date(2026, 10, 1), date(2026, 10, 31), today=TODAY)

    assert codes(items) == ["Alpha S01E01", "Finie S01E01", "Alpha S01E02", "Film vu"]
    assert [i.watched for i in items] == [True, False, False, True]
    assert items[0].poster_url == "https://img/alpha.jpg"


def test_releases_between_uses_french_date(session):
    alpha = add_show(session, "Alpha")
    # Diffusé le 31/10 à 23 h aux États-Unis (airdate) = 1er novembre en France (airstamp UTC)
    add_episode(session, alpha, 1, 1, airdate=date(2026, 10, 31),
                airstamp=datetime(2026, 11, 1, 3, 0))
    october = queue.releases_between(session, date(2026, 10, 1), date(2026, 10, 31), today=TODAY)
    november = queue.releases_between(session, date(2026, 11, 1), date(2026, 11, 30), today=TODAY)
    assert october == [] and codes(november) == ["Alpha S01E01"]


def test_group_day_merges_episodes_of_a_show(session):
    alpha = add_show(session, "Alpha")
    beta = add_show(session, "Beta")
    day = date(2026, 10, 14)
    for n in (1, 2, 3):
        add_episode(session, alpha, 2, n, airdate=day)
    add_episode(session, beta, 1, 1, airdate=day)
    add_episode(session, beta, 1, 4, airdate=day)
    add_movie(session, "Film", release=day)

    entries = queue.group_day(queue.releases_between(session, day, day, today=TODAY))

    assert [(e.first.title, e.label) for e in entries] == [
        ("Alpha", "S02E01–E03"), ("Beta", "2 épisodes"), ("Film", "Film")]
    assert not entries[0].watched


def test_day_entry_single_and_watched(session):
    alpha = add_show(session, "Alpha")
    ep = add_episode(session, alpha, 1, 5, airdate=date(2026, 10, 2))
    watch(session, ep)
    entries = queue.group_day(queue.releases_between(session, date(2026, 10, 2), date(2026, 10, 2),
                                                     today=TODAY))
    assert entries[0].label == "S01E05" and entries[0].watched
