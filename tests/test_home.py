"""Tests de l'accueil (watchlog/core/home.py)."""

from datetime import date, datetime, timedelta

import pytest
from sqlmodel import Session

from helpers_db import add_episode, add_movie, add_show
from watchlog.core import home, stats
from watchlog.core.models import EPISODE, FOR_LATER, MOVIE, SHOW, STOPPED, TO_WATCH, Watch

TODAY = date(2026, 10, 12)


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def seen(session, item, when: datetime, last: datetime | None = None) -> None:
    """Visionnage daté (`last` : revisionnage plus récent)."""
    kind = MOVIE if item.__class__.__name__ == "Movie" else EPISODE
    session.add(Watch(kind=kind, ref_id=item.id, watched_at=when, last_watched_at=last or when))
    session.flush()


def noon(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12)


# --- Activité ------------------------------------------------------------------------

def test_current_streak_counts_until_today_or_yesterday():
    days = [TODAY, TODAY - timedelta(days=1), TODAY - timedelta(days=2),
            TODAY - timedelta(days=4)]
    assert home.current_streak(days, TODAY) == 3
    # Rien aujourd'hui : la série tient encore, jusqu'à hier.
    assert home.current_streak(days[1:], TODAY) == 2
    assert home.current_streak([TODAY - timedelta(days=2)], TODAY) == 0
    assert home.current_streak([], TODAY) == 0


def test_activity_week_month_and_streak(session):
    show = add_show(session, "Série")
    eps = [add_episode(session, show, 1, n, airdate=date(2026, 1, 1), runtime=None if n == 3
                       else 40) for n in range(1, 5)]
    movie = add_movie(session, "Film", release=date(2025, 1, 1))
    seen(session, eps[0], noon(TODAY))
    seen(session, eps[1], noon(TODAY - timedelta(days=1)))
    seen(session, eps[2], noon(TODAY - timedelta(days=6)))      # dernier jour de la semaine
    seen(session, eps[3], noon(TODAY - timedelta(days=7)))      # hors semaine, dans le mois
    seen(session, movie, noon(date(2026, 9, 30)))                # mois précédent
    session.commit()

    result = home.activity(stats.watches_frame(session), today=TODAY)
    assert (result.week.episodes, result.week.movies) == (3, 0)
    assert result.week.minutes == 120   # durée inconnue : moyenne des durées connues (40)
    assert (result.month.episodes, result.month.movies, result.month.total) == (4, 0, 4)
    assert result.streak == 2 and result.watched_today
    assert result.week.ignored == 0


def test_activity_ignores_import_days(session):
    show = add_show(session, "Importée")
    day = TODAY - timedelta(days=2)
    for n in range(1, stats.BULK_THRESHOLD + 1):
        seen(session, add_episode(session, show, 1, n, airdate=date(2020, 1, 1)), noon(day))
    session.commit()

    result = home.activity(stats.watches_frame(session), today=TODAY)
    assert result.week.total == 0 and result.week.ignored == stats.BULK_THRESHOLD
    assert result.streak == 0 and not result.watched_today


def test_activity_empty_library(session):
    result = home.activity(stats.watches_frame(session), today=TODAY)
    assert result.week.total == result.month.total == result.streak == 0


# --- À suivre ------------------------------------------------------------------------

def test_continue_watching_recent_shows_first_then_not_started(session):
    old = add_show(session, "Ancienne")
    recent = add_show(session, "Récente")
    fresh = add_show(session, "Pas commencée", status=TO_WATCH)
    later = add_show(session, "Plus tard", status=FOR_LATER)   # statut hors accueil
    eps = {show.title: [add_episode(session, show, 1, n, airdate=date(2026, 1, n))
                        for n in (1, 2, 3)] for show in (old, recent, fresh, later)}
    add_episode(session, recent, 1, 4, airdate=TODAY + timedelta(days=3))   # à venir
    seen(session, eps["Ancienne"][0], noon(TODAY - timedelta(days=40)))
    seen(session, eps["Récente"][0], noon(TODAY - timedelta(days=1)))
    seen(session, eps["Récente"][1], noon(TODAY - timedelta(days=1)))
    session.commit()

    entries, total = home.continue_watching(session, today=TODAY)
    assert total == 3
    assert [(e.item.title, e.item.code, e.remaining) for e in entries] == [
        ("Récente", "S01E03", 1), ("Ancienne", "S01E02", 2), ("Pas commencée", "S01E01", 3)]
    assert entries[0].last_watched == noon(TODAY - timedelta(days=1))
    assert entries[2].last_watched is None
    assert home.continue_watching(session, today=TODAY, limit=1)[0][0].item.title == "Récente"


def test_continue_watching_import_day_is_not_recent(session):
    imported = add_show(session, "Importée")
    other = add_show(session, "Regardée")
    for n in range(1, stats.BULK_THRESHOLD + 2):
        episode = add_episode(session, imported, 1, n, airdate=date(2020, 1, 1))
        if n <= stats.BULK_THRESHOLD:
            seen(session, episode, noon(TODAY))      # tout l'historique daté du jour d'import
    seen(session, add_episode(session, other, 1, 1, airdate=date(2026, 1, 1)),
         noon(TODAY - timedelta(days=30)))
    add_episode(session, other, 1, 2, airdate=date(2026, 1, 8))
    session.commit()

    entries, _ = home.continue_watching(session, today=TODAY)
    assert [e.item.title for e in entries] == ["Regardée", "Importée"]
    assert entries[1].last_watched is None


def test_continue_watching_skips_shows_with_nothing_released(session):
    show = add_show(session, "À jour")
    seen(session, add_episode(session, show, 1, 1, airdate=date(2026, 1, 1)), noon(TODAY))
    add_episode(session, show, 1, 2, airdate=TODAY + timedelta(days=1))
    session.commit()
    assert home.continue_watching(session, today=TODAY) == ([], 0)


# --- Films à voir, sorties -------------------------------------------------------------

def test_movies_to_watch_last_added_first(session):
    add_movie(session, "Ancien ajout", release=date(2020, 1, 1), added_at=datetime(2025, 1, 1))
    add_movie(session, "Dernier ajout", release=date(2010, 1, 1), added_at=datetime(2026, 9, 1))
    add_movie(session, "Même jour, récent", release=date(2024, 1, 1),
              added_at=datetime(2025, 1, 1))
    add_movie(session, "À voir", release=date(2023, 1, 1), status=TO_WATCH,
              added_at=datetime(2026, 1, 1))
    add_movie(session, "Pas sorti", release=TODAY + timedelta(days=5),
              added_at=datetime(2026, 10, 1))
    add_movie(session, "Arrêté", release=date(2020, 1, 1), status=STOPPED)
    seen(session, add_movie(session, "Déjà vu", release=date(2020, 1, 1),
                            added_at=datetime(2026, 10, 1)), noon(TODAY))
    session.commit()

    movies, total = home.movies_to_watch(session, today=TODAY, limit=3)
    assert total == 4
    assert [m.title for m in movies] == ["Dernier ajout", "À voir", "Même jour, récent"]


def test_upcoming_grouped_by_day(session):
    show = add_show(session, "Série")
    add_episode(session, show, 2, 1, airdate=TODAY)
    add_episode(session, show, 2, 2, airdate=TODAY)
    add_episode(session, show, 2, 3, airdate=TODAY + timedelta(days=7))
    add_episode(session, show, 2, 4, airdate=TODAY + timedelta(days=8))   # trop loin
    add_movie(session, "Film", release=TODAY + timedelta(days=2))
    session.commit()

    days = home.upcoming(session, today=TODAY)
    assert [(day, [entry.label for entry in entries]) for day, entries in days] == [
        (TODAY, ["S02E01–E02"]), (TODAY + timedelta(days=2), ["Film"]),
        (TODAY + timedelta(days=7), ["S02E03"])]


# --- Derniers visionnages ----------------------------------------------------------------

def test_recent_watches_one_line_per_title(session):
    show = add_show(session, "Série")
    e1 = add_episode(session, show, 1, 1, airdate=date(2026, 1, 1))
    e2 = add_episode(session, show, 1, 2, airdate=date(2026, 1, 2))
    film = add_movie(session, "Film", release=date(2020, 1, 1))
    revu = add_movie(session, "Revu", release=date(2020, 1, 1))
    seen(session, e1, noon(TODAY - timedelta(days=3)))
    seen(session, e2, noon(TODAY - timedelta(days=1)))
    seen(session, film, noon(TODAY - timedelta(days=2)))
    seen(session, revu, noon(date(2020, 5, 1)), last=noon(TODAY))   # revisionnage d'aujourd'hui
    session.commit()

    recent = home.recent_watches(session)
    assert [(r.kind, r.title, r.code) for r in recent] == [
        (MOVIE, "Revu", ""), (SHOW, "Série", "S01E02"), (MOVIE, "Film", "")]
    assert [r.title for r in home.recent_watches(session, limit=2)] == ["Revu", "Série"]


def test_recent_watches_ignore_import_days(session):
    show = add_show(session, "Importée")
    for n in range(1, stats.BULK_THRESHOLD + 1):
        seen(session, add_episode(session, show, 1, n, airdate=date(2020, 1, 1)), noon(TODAY))
    film = add_movie(session, "Film", release=date(2020, 1, 1))
    seen(session, film, noon(TODAY - timedelta(days=5)))
    session.commit()
    assert [r.title for r in home.recent_watches(session)] == ["Film"]
