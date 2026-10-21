"""Tests des statistiques."""

from datetime import date, datetime

import pandas as pd
import pytest
from sqlmodel import Session

from helpers_db import add_episode, add_movie, add_show, watch
from watchlog.core import stats
from watchlog.core.models import (COMPLETED, EPISODE, FOR_LATER, MOVIE, SHOW, TO_WATCH, WATCHING,
                                  Episode, LibraryItem, Movie, Rating, Show, Watch)

TODAY = date(2026, 10, 12)


@pytest.fixture
def session(engine):
    """Une série terminée (2 épisodes vus, dont un revu), une en cours, deux films vus."""
    with Session(engine) as s:
        done = Show(title="Le Royaume")
        current = Show(title="Lupin")
        s.add_all([done, current])
        s.flush()
        e1 = Episode(show_id=done.id, season=1, number=1, runtime=50)
        e2 = Episode(show_id=done.id, season=1, number=2, runtime=40)
        e3 = Episode(show_id=current.id, season=1, number=1)          # durée inconnue
        fight = Movie(title="Fight Club", runtime=139)
        rare = Movie(title="Film Rare")                                # durée inconnue
        s.add_all([e1, e2, e3, fight, rare])
        s.flush()
        s.add_all([
            LibraryItem(kind=SHOW, ref_id=done.id, status=COMPLETED),
            LibraryItem(kind=SHOW, ref_id=current.id, status=WATCHING),
            LibraryItem(kind=MOVIE, ref_id=fight.id, status=COMPLETED),
            # 31/08 à 23 h UTC = 01/09 en France : compté en septembre.
            Watch(kind=EPISODE, ref_id=e1.id, watched_at=datetime(2026, 8, 31, 23), plays=1),
            Watch(kind=EPISODE, ref_id=e2.id, watched_at=datetime(2026, 10, 1, 20),
                  last_watched_at=datetime(2026, 10, 5, 20), plays=2),
            Watch(kind=EPISODE, ref_id=e3.id, watched_at=datetime(2026, 10, 2, 20)),
            Watch(kind=MOVIE, ref_id=fight.id, watched_at=datetime(2026, 10, 3, 20)),
            Watch(kind=MOVIE, ref_id=rare.id, watched_at=datetime(2020, 2, 2, 20)),  # trop ancien
            Rating(kind=SHOW, ref_id=done.id, rating=4),
            Rating(kind=MOVIE, ref_id=fight.id, rating=5),
            Rating(kind=EPISODE, ref_id=e2.id, rating=4),
            Rating(kind=EPISODE, ref_id=e1.id, rating=None, emotions="Ému"),  # pas de note
        ])
        s.commit()
        yield s


def test_last_months_crosses_year():
    assert stats.last_months(3, date(2026, 2, 15)) == ["2025-12", "2026-01", "2026-02"]


def test_watched_per_month(session):
    frame = stats.watched_per_month(session, months=12, today=TODAY)

    assert list(frame.columns) == ["mois", "episodes", "films"]
    assert len(frame) == 12
    assert frame["mois"].iloc[0] == "2025-11" and frame["mois"].iloc[-1] == "2026-10"
    by_month = frame.set_index("mois")
    assert by_month.loc["2026-10"].tolist() == [2, 1]
    assert by_month.loc["2026-09"].tolist() == [1, 0]   # fuseau français
    assert by_month.loc["2026-08"].tolist() == [0, 0]   # mois vide = 0
    assert frame["films"].sum() == 1                    # le film de 2020 est hors période


def test_time_spent(session):
    result = stats.time_spent(session)

    assert result["episode_minutes"] == 50 + 40 * 2     # durée × nombre de visionnages
    assert result["movie_minutes"] == 139
    assert result["total_minutes"] == 50 + 80 + 139
    assert result["episodes_without_runtime"] == 1
    assert result["movies_without_runtime"] == 1


def test_format_duration():
    assert stats.format_duration(18760) == "312 h 40 min"
    assert stats.format_duration(45) == "45 min"
    assert stats.format_duration(60) == "1 h 00 min"


def test_completed_shows(session):
    rows = stats.completed_shows(session)

    assert [row["title"] for row in rows] == ["Le Royaume"]
    assert rows[0]["episodes"] == 2
    assert rows[0]["last_watched"] == date(2026, 10, 5)


def test_top_ratings(session):
    rows = stats.top_ratings(session, limit=10)

    assert [(r["title"], r["rating"]) for r in rows] == [
        ("Fight Club", 5), ("Le Royaume", 4), ("Le Royaume · S01E02", 4)]
    assert rows[2]["kind"] == "Épisode"
    assert len(stats.top_ratings(session, limit=1)) == 1


def test_global_counts(session):
    counts = stats.global_counts(session)

    assert counts == {"shows": 2, "movies": 1, "completed_shows": 1,
                      "episodes_watched": 3, "movies_watched": 2, "ratings": 3}


# --- Temps restant pour finir la liste ------------------------------------------------

FINISH_TODAY = date(2026, 10, 21)


def test_remaining_time(engine):
    """Sorti et pas vu, hors spéciaux ; durée inconnue = moyenne des durées connues du lot."""
    with Session(engine) as s:
        current = add_show(s, "Silo", status=WATCHING, match_status="ok")
        aired = date(2026, 1, 1)
        watch(s, add_episode(s, current, 1, 1, airdate=aired, runtime=60))   # déjà vu
        add_episode(s, current, 1, 2, airdate=aired, runtime=None)          # durée estimée
        add_episode(s, current, 1, 3, airdate=aired, runtime=40)
        add_episode(s, current, 1, 4, airdate=date(2027, 1, 1), runtime=50)  # à venir
        add_episode(s, current, 1, None, airdate=aired, runtime=90)          # spécial
        later = add_show(s, "3%", status=FOR_LATER, match_status="ok")
        add_episode(s, later, 1, 1, airdate=aired, runtime=30)
        add_movie(s, "Matrix", release=date(1999, 6, 23), status=TO_WATCH)  # 120 min
        add_movie(s, "Pas sorti", release=date(2027, 5, 1), status=TO_WATCH)
        watch(s, add_movie(s, "Déjà vu", release=date(2020, 1, 1), status=COMPLETED))

        result = stats.remaining_time(s, today=FINISH_TODAY)
        with_later = stats.remaining_time(s, statuses=(WATCHING, TO_WATCH, FOR_LATER),
                                          today=FINISH_TODAY)

    assert result == {"episodes": 2, "episode_minutes": 80, "movies": 1, "movie_minutes": 120,
                      "total_minutes": 200, "estimated": 1}
    assert with_later["episodes"] == 3
    assert with_later["episode_minutes"] == 30 + 40 + 35   # inconnue = moyenne (30 + 40) / 2


def pace_frame() -> pd.DataFrame:
    rows = [(EPISODE, date(2026, 10, 20), 60),
            (EPISODE, date(2026, 10, 15), None),   # durée inconnue
            (MOVIE, date(2026, 10, 14), 120),
            (EPISODE, date(2026, 10, 12), 60),      # jour d'import
            (EPISODE, date(2026, 10, 1), 30),
            (EPISODE, None, 45)]                    # date inconnue : jamais comptée
    return pd.DataFrame(rows, columns=["kind", "day", "runtime"])


def test_watch_pace_window():
    pace = stats.watch_pace(pace_frame(), 7, today=FINISH_TODAY)

    assert pace["start"] == date(2026, 10, 15)
    assert (pace["days"], pace["watches"], pace["minutes"], pace["estimated"]) == (7, 2, 120, 1)
    assert pace["per_day"] == pytest.approx(120 / 7)


def test_watch_pace_without_bulk_days():
    pace = stats.watch_pace(pace_frame(), 30, today=FINISH_TODAY)

    assert pace["days"] == 30 and pace["watches"] == 5
    assert pace["minutes"] == (60 + 50 + 60 + 30) + 120


def test_watch_pace_starts_at_first_bulk_day():
    """Avant le jour d'import, l'historique n'est pas daté : la période ne remonte pas plus loin."""
    pace = stats.watch_pace(pace_frame(), 365, bulk={date(2026, 10, 12)}, today=FINISH_TODAY)

    assert pace["start"] == date(2026, 10, 12)
    assert pace["days"] == 9                      # 10 jours, moins le jour d'import
    assert pace["watches"] == 3
    assert pace["minutes"] == 60 + 60 + 120
    assert pace["per_day"] == pytest.approx(240 / 9)


def test_finish_estimate():
    assert stats.finish_estimate(300, 100, FINISH_TODAY) == {"days": 3,
                                                            "date": date(2026, 10, 24)}
    assert stats.finish_estimate(301, 100, FINISH_TODAY)["days"] == 4   # jour entamé
    assert stats.finish_estimate(0, 100, FINISH_TODAY)["days"] == 0
    assert stats.finish_estimate(300, 0, FINISH_TODAY) is None


def test_format_span():
    today = FINISH_TODAY                                          # 21/10/2026
    assert stats.format_span(today, date(2026, 10, 22)) == "1 jour"
    assert stats.format_span(today, date(2026, 11, 2)) == "12 jours"
    assert stats.format_span(today, date(2027, 1, 21)) == "3 mois"
    assert stats.format_span(today, date(2027, 2, 15)) == "3 mois et 25 jours"
    assert stats.format_span(today, date(2027, 10, 21)) == "1 an"
    assert stats.format_span(today, date(2028, 12, 26)) == "2 ans, 2 mois et 5 jours"
    assert stats.format_span(today, today) == "0 jour"


def test_format_span_short_months():
    """31 janvier + 1 mois = 28 février : pas de mois sauté ni de jour négatif."""
    assert stats.format_span(date(2026, 1, 31), date(2026, 2, 28)) == "1 mois"
    assert stats.format_span(date(2026, 1, 31), date(2026, 3, 2)) == "1 mois et 2 jours"
