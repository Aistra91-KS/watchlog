"""Statistiques : visionnages par mois, temps passé, séries terminées, meilleures notes.

Remarque : les dates de visionnage importées de Bingers sont parfois des dates
d'import (beaucoup d'épisodes le même jour). C'est normal, on les garde telles quelles.
"""

from datetime import date, datetime

import pandas as pd
from sqlmodel import Session, func, select

from watchlog.core.models import (COMPLETED, EPISODE, MOVIE, SHOW, Episode, LibraryItem, Movie,
                                  Rating, Show, Watch)
from watchlog.core.queue import LOCAL_TZ, episode_code, local_date


def _month(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def last_months(months: int, today: date) -> list[str]:
    """Les `months` derniers mois, du plus ancien au mois en cours : ["2025-11", ..., "2026-10"]."""
    year, month = today.year, today.month
    result = []
    for _ in range(months):
        result.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(result))


def watched_per_month(session: Session, months: int = 12, today: date | None = None) -> pd.DataFrame:
    """Épisodes et films vus par mois (date du premier visionnage, heure française).

    Colonnes : mois ("2026-10"), episodes, films. Un mois sans visionnage vaut 0.
    """
    today = today or datetime.now(LOCAL_TZ).date()
    counts = {m: {"episodes": 0, "films": 0} for m in last_months(months, today)}
    for kind, watched_at, last_watched_at in session.exec(
            select(Watch.kind, Watch.watched_at, Watch.last_watched_at)):
        when = watched_at or last_watched_at
        if when is None:
            continue
        month = _month(local_date(when, None))  # UTC -> date française
        if month in counts:
            counts[month]["episodes" if kind == EPISODE else "films"] += 1
    return pd.DataFrame([{"mois": m, **c} for m, c in counts.items()],
                        columns=["mois", "episodes", "films"])


def time_spent(session: Session) -> dict:
    """Temps passé devant l'écran, en minutes : somme de durée × nombre de visionnages.

    Les éléments sans durée connue ne sont pas comptés dans le temps, mais à part.
    """
    result = {"episode_minutes": 0, "movie_minutes": 0, "total_minutes": 0,
              "episodes_without_runtime": 0, "movies_without_runtime": 0}
    episodes = session.exec(select(Episode.runtime, Watch.plays)
                            .join(Watch, (Watch.kind == EPISODE) & (Watch.ref_id == Episode.id)))
    for runtime, plays in episodes:
        if runtime:
            result["episode_minutes"] += runtime * (plays or 1)
        else:
            result["episodes_without_runtime"] += 1
    movies = session.exec(select(Movie.runtime, Watch.plays)
                          .join(Watch, (Watch.kind == MOVIE) & (Watch.ref_id == Movie.id)))
    for runtime, plays in movies:
        if runtime:
            result["movie_minutes"] += runtime * (plays or 1)
        else:
            result["movies_without_runtime"] += 1
    result["total_minutes"] = result["episode_minutes"] + result["movie_minutes"]
    return result


def format_duration(minutes: int) -> str:
    """312 h 40 min (ou 40 min si moins d'une heure)."""
    hours, rest = divmod(int(minutes), 60)
    if hours == 0:
        return f"{rest} min"
    return f"{hours:,} h {rest:02d} min".replace(",", " ")


def completed_shows(session: Session) -> list[dict]:
    """Séries au statut « Terminé », par ordre alphabétique.

    Chaque élément : {"show_id", "title", "episodes" (vus), "last_watched" (date ou None)}.
    """
    query = (select(Show)
             .join(LibraryItem, (LibraryItem.kind == SHOW) & (LibraryItem.ref_id == Show.id))
             .where(LibraryItem.status == COMPLETED))
    result = []
    for show in session.exec(query):
        count, last = session.exec(
            select(func.count(Watch.id), func.max(Watch.last_watched_at))
            .join(Episode, (Watch.kind == EPISODE) & (Watch.ref_id == Episode.id))
            .where(Episode.show_id == show.id)).one()
        result.append({"show_id": show.id, "title": show.title, "episodes": count,
                       "last_watched": local_date(last, None) if last else None})
    return sorted(result, key=lambda row: row["title"].lower())


def rating_title(session: Session, kind: str, ref_id: int) -> str | None:
    """Titre lisible d'un élément noté : série, film, ou « Série · S01E02 »."""
    if kind == SHOW:
        show = session.get(Show, ref_id)
        return show.title if show else None
    if kind == MOVIE:
        movie = session.get(Movie, ref_id)
        return movie.title if movie else None
    episode = session.get(Episode, ref_id)
    if episode is None:
        return None
    show = session.get(Show, episode.show_id)
    return f"{show.title if show else '?'} · {episode_code(episode.season, episode.number)}"


KIND_LABELS = {SHOW: "Série", EPISODE: "Épisode", MOVIE: "Film"}


def top_ratings(session: Session, limit: int = 10) -> list[dict]:
    """Meilleures notes (note non vide), de la plus haute à la plus basse.

    Chaque élément : {"title", "kind" (libellé), "rating"}.
    """
    rows = []
    for rating in session.exec(select(Rating).where(Rating.rating != None)):  # noqa: E711
        title = rating_title(session, rating.kind, rating.ref_id)
        if title is not None:
            rows.append({"title": title, "kind": KIND_LABELS.get(rating.kind, rating.kind),
                         "rating": rating.rating})
    rows.sort(key=lambda row: (-row["rating"], row["title"].lower()))
    return rows[:limit]


def global_counts(session: Session) -> dict:
    """Chiffres clés : séries et films dans la bibliothèque, épisodes et films vus."""
    def count(query) -> int:
        return session.exec(query).one()

    return {
        "shows": count(select(func.count(LibraryItem.id)).where(LibraryItem.kind == SHOW)),
        "movies": count(select(func.count(LibraryItem.id)).where(LibraryItem.kind == MOVIE)),
        "completed_shows": count(select(func.count(LibraryItem.id))
                                 .where(LibraryItem.kind == SHOW, LibraryItem.status == COMPLETED)),
        "episodes_watched": count(select(func.count(Watch.id)).where(Watch.kind == EPISODE)),
        "movies_watched": count(select(func.count(Watch.id)).where(Watch.kind == MOVIE)),
        "ratings": count(select(func.count(Rating.id)).where(Rating.rating != None)),  # noqa: E711
    }
