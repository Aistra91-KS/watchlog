"""Tests des statistiques."""

from datetime import date, datetime

import pytest
from sqlmodel import Session

from watchlog.core import stats
from watchlog.core.models import (COMPLETED, EPISODE, MOVIE, SHOW, WATCHING, Episode, LibraryItem,
                                  Movie, Rating, Show, Watch)

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
