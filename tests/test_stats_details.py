"""Tests des statistiques détaillées (activité, séries, films, genres) et des genres en base."""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import text
from sqlmodel import Session

from helpers_db import add_episode, add_movie, add_show
from watchlog.core import catalog, library, stats
from watchlog.core.db import add_missing_columns, make_engine
from watchlog.core.models import COMPLETED, EPISODE, MOVIE, SHOW, WATCHING, Watch

TODAY = date(2026, 10, 13)


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def seen(session, kind, ref_id, day, plays=1):
    """Visionnage daté (midi heure de Paris, pour tomber le bon jour)."""
    when = datetime.combine(day, datetime.min.time()) + timedelta(hours=10)
    session.add(Watch(kind=kind, ref_id=ref_id, watched_at=when, last_watched_at=when, plays=plays))
    session.flush()


@pytest.fixture
def data(session):
    """Deux séries, deux films, des visionnages répartis sur quelques jours."""
    drama = add_show(session, "Drama Show", genres="Drame, Thriller")
    comedy = add_show(session, "Comedy Show", genres="Comédie")
    eps = [add_episode(session, drama, 1, n, runtime=60) for n in range(1, 4)]
    eps += [add_episode(session, comedy, 1, 1, runtime=30)]
    seen(session, EPISODE, eps[0].id, date(2026, 10, 5))   # lundi
    seen(session, EPISODE, eps[1].id, date(2026, 10, 6))
    seen(session, EPISODE, eps[2].id, date(2026, 10, 6))
    seen(session, EPISODE, eps[3].id, date(2025, 3, 1))
    old = add_movie(session, "Vieux", genres="Drame", release_date_original=date(1994, 1, 1))
    old.runtime = 90
    new = add_movie(session, "Récent", genres="Action, Drame", release_date_original=date(2021, 5, 1))
    new.runtime = 150
    seen(session, MOVIE, old.id, date(2026, 10, 7), plays=2)
    seen(session, MOVIE, new.id, date(2026, 9, 1))
    library.set_rating(session, MOVIE, new.id, 5)
    session.flush()
    return {"drama": drama, "comedy": comedy, "old": old, "new": new}


def test_watches_frame(session, data):
    frame = stats.watches_frame(session)
    assert len(frame) == 6
    old = frame[frame["title"] == "Vieux"].iloc[0]
    assert old["minutes"] == 180 and old["release_year"] == 1994 and old["day"] == date(2026, 10, 7)


def test_per_year_and_months(session, data):
    frame = stats.watches_frame(session)
    yearly = stats.per_year(frame)
    assert yearly.to_dict("records") == [{"annee": 2025, "episodes": 1, "films": 0},
                                         {"annee": 2026, "episodes": 3, "films": 2}]
    monthly = stats.count_per_month(frame, 2, TODAY)
    assert monthly.to_dict("records") == [{"mois": "2026-09", "episodes": 0, "films": 1},
                                          {"mois": "2026-10", "episodes": 3, "films": 1}]
    hours = stats.hours_per_month(frame, 2, TODAY)
    assert hours.to_dict("records") == [{"mois": "2026-09", "episodes": 0.0, "films": 2.5},
                                        {"mois": "2026-10", "episodes": 3.0, "films": 3.0}]


def test_weekday_daily_and_records(session, data):
    frame = stats.watches_frame(session)
    weekdays = stats.per_weekday(frame)
    assert weekdays.loc[0, "visionnages"] == 1      # lundi 05/10
    assert weekdays.loc[1, "visionnages"] == 3      # mardis 06/10 (x2) et 01/09
    assert weekdays["visionnages"].sum() == 6
    daily = stats.daily_counts(frame, 10, TODAY)
    assert len(daily) == 10 and daily.iloc[-1]["day"] == TODAY
    assert int(daily.loc[daily["day"] == date(2026, 10, 6), "visionnages"].iloc[0]) == 2
    rec = stats.records(frame)
    assert rec["best_day"] == (date(2026, 10, 6), 2)
    assert rec["streak"] == (3, date(2026, 10, 5), date(2026, 10, 7))
    assert rec["best_month"] == ("2026-10", 4)


def test_records_empty():
    frame = stats.without_days(
        stats.pd.DataFrame(columns=["kind", "ref_id", "show_id", "title", "day", "plays", "runtime",
                                    "release_year", "genres", "minutes"]), [])
    assert stats.records(frame) == {"best_day": None, "streak": None, "best_month": None}
    assert stats.per_year(frame).empty and stats.movie_runtime_stats(frame) is None


def test_top_shows_genres_decades_runtimes(session, data):
    frame = stats.watches_frame(session)
    top = stats.top_shows_by_time(frame, 5)
    assert top.to_dict("records")[0] == {"title": "Drama Show", "heures": 3.0, "episodes": 3}
    assert stats.genre_counts(frame, MOVIE).to_dict("records") == [
        {"genre": "Drame", "titres": 2}, {"genre": "Action", "titres": 1}]
    # séries : une fois par série, pas par épisode
    assert stats.genre_counts(frame, EPISODE).to_dict("records") == [
        {"genre": "Comédie", "titres": 1}, {"genre": "Drame", "titres": 1},
        {"genre": "Thriller", "titres": 1}]
    # genres inconnus (NaN côté pandas) : ignorés sans planter
    unknown = pd.DataFrame({"kind": [EPISODE, EPISODE], "show_id": [1, 2],
                            "genres": [float("nan"), "Drame"]})
    assert stats.genre_counts(unknown, EPISODE).to_dict("records") == [
        {"genre": "Drame", "titres": 1}]
    assert stats.movie_decades(frame)[["libelle", "films"]].to_dict("records") == [
        {"libelle": "1990s", "films": 1}, {"libelle": "2020s", "films": 1}]
    assert stats.movie_runtime_stats(frame) == {"average": 120, "longest": ("Récent", 150),
                                                "shortest": ("Vieux", 90)}


def test_watched_movies(session, data):
    movies = stats.watched_movies(session)
    assert [m["title"] for m in movies] == ["Vieux", "Récent"]   # plus récent d'abord
    assert movies[1]["rating"] == 5 and movies[0]["plays"] == 2 and movies[0]["year"] == 1994


def test_show_progress(session):
    show = add_show(session, "En cours", status=WATCHING)
    eps = [add_episode(session, show, 1, n, airdate=date(2026, 1, n)) for n in range(1, 5)]
    add_episode(session, show, 1, 5, airdate=date(2027, 1, 1))     # pas encore diffusé
    add_episode(session, show, 1, None, airdate=date(2026, 1, 9))  # spécial : ignoré
    add_show(session, "Finie", status=COMPLETED)
    seen(session, EPISODE, eps[0].id, date(2026, 2, 1))
    progress = stats.show_progress(session, TODAY)
    assert progress[["title", "vus", "diffuses", "pourcentage"]].to_dict("records") == [
        {"title": "En cours", "vus": 1, "diffuses": 4, "pourcentage": 25}]


def test_rating_distribution(session, data):
    dist = stats.rating_distribution(session)
    assert dist["nombre"].tolist() == [0, 0, 0, 0, 1]


def test_rating_distribution_with_half_ratings(session, data):
    """Une demi-note a sa barre seulement si elle sert."""
    library.set_rating(session, SHOW, data["drama"].id, 4.5)
    library.set_rating(session, SHOW, data["comedy"].id, 4.0)
    session.flush()
    dist = stats.rating_distribution(session)
    assert dist["note"].tolist() == [1, 2, 3, 4, 4.5, 5]
    assert dist["nombre"].tolist() == [0, 0, 0, 1, 1, 1]
    top = stats.top_ratings(session)
    assert [(row["title"], row["rating"]) for row in top] == [
        ("Récent", 5), ("Drama Show", 4.5), ("Comedy Show", 4)]


def test_bulk_days(session, data):
    show = data["drama"]
    for n in range(10, 15):
        ep = add_episode(session, show, 2, n)
        seen(session, EPISODE, ep.id, date(2026, 8, 13))
    frame = stats.watches_frame(session)
    bulk = stats.bulk_days(frame, threshold=5)
    assert bulk == {date(2026, 8, 13): 5}
    assert len(stats.without_days(frame, bulk)) == len(frame) - 5
    assert stats.records(stats.without_days(frame, bulk))["best_day"] == (date(2026, 10, 6), 2)


def test_count_bulk_days_seuil_partage():
    """Une seule définition du seuil : les suggestions utilisent celle des statistiques."""
    from watchlog.core import suggestions
    days = [date(2026, 8, 13)] * stats.BULK_THRESHOLD + [date(2026, 8, 14)] * 3
    assert stats.count_bulk_days(days) == {date(2026, 8, 13): stats.BULK_THRESHOLD}
    assert stats.count_bulk_days(days[1:]) == {}
    assert not hasattr(suggestions, "BULK_THRESHOLD")


# --- Genres en base -------------------------------------------------------------------------

def test_genre_texts():
    assert catalog.show_genres({"genres": ["Drama", "Science-Fiction", "Inconnu"]}) == \
        "Drame, Science-Fiction, Inconnu"
    assert catalog.show_genres({"genres": []}) is None
    assert catalog.movie_genres({"genres": [{"id": 1, "name": "Comédie"}]}) == "Comédie"
    assert catalog.split_genres("Drame, Comédie") == ["Drame", "Comédie"]
    assert catalog.split_genres(None) == []


def test_upsert_movie_keeps_genres(session):
    movie = catalog.upsert_movie_from_tmdb(session, {"id": 1, "title": "Film",
                                                     "genres": [{"name": "Drame"}]})
    assert movie.genres == "Drame"
    catalog.upsert_movie_from_tmdb(session, {"id": 1, "title": "Film"})   # sans genres : gardés
    assert movie.genres == "Drame"


def test_backfill_genres_from_cache(session):
    show = add_show(session, "Série", tvmaze_id=101)
    movie = add_movie(session, "Film", tmdb_id=550)
    other = add_movie(session, "Pas en cache", tmdb_id=999)

    class Http:
        def __init__(self, cache):
            self.cache = cache

        def peek(self, path, params=None):
            return self.cache.get(path)

    class Client:
        def __init__(self, cache):
            self.http = Http(cache)

    tvmaze = Client({"/shows/101": {"genres": ["Comedy"]}})
    tmdb = Client({"/movie/550": {"genres": [{"name": "Drame"}]}})
    assert catalog.backfill_genres(session, tvmaze, tmdb) == 2
    assert show.genres == "Comédie" and movie.genres == "Drame" and other.genres is None


def test_add_missing_columns(tmp_path):
    engine = make_engine(tmp_path / "old.db")
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE "movie" DROP COLUMN "genres"'))
    assert add_missing_columns(engine) == ["movie.genres"]
    assert add_missing_columns(engine) == []      # rien à refaire
