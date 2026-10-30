"""Accueil : l'essentiel tout de suite (activité récente, épisodes à suivre, films à voir,
prochaines sorties, derniers visionnages).

Rien de neuf à calculer : mêmes règles que À regarder et Calendrier (queue), Bibliothèque
(progress) et Statistiques (stats), regroupées pour un coup d'œil. Les jours d'import (Bingers)
sont ignorés comme ailleurs : leurs dates ne disent pas quand les titres ont été vus.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd
from sqlmodel import Session, select

from watchlog.core import progress, queue, stats
from watchlog.core.models import EPISODE, MOVIE, SHOW, Episode, LibraryItem, Movie, Show, Watch
from watchlog.core.queue import LOCAL_TZ, RELEASED, QueueItem

# Taille de chaque bloc (la page complète est à un clic : « Tout voir »)
CONTINUE_LIMIT = 6    # séries de « À suivre »
MOVIES_LIMIT = 6      # « Films à voir »
RECENT_LIMIT = 6      # « Derniers visionnages » (titres différents)
UPCOMING_DAYS = 7     # « Prochaines sorties » : aujourd'hui et les 7 jours suivants
WEEK_DAYS = 7         # « Vus ces 7 jours » : aujourd'hui et les 6 jours précédents
# Statuts suivis : ceux de la liste « À regarder » par défaut (en cours, à voir)
STATUSES = queue.DEFAULT_STATUSES


# --- Activité récente ------------------------------------------------------------------

@dataclass
class Period:
    """Visionnages d'une période (premier visionnage), jours d'import exclus."""
    episodes: int = 0
    movies: int = 0
    minutes: int = 0      # durée inconnue = moyenne des durées connues du lot (comme le rythme)
    ignored: int = 0      # visionnages datés d'un jour d'import : pas comptés

    @property
    def total(self) -> int:
        return self.episodes + self.movies


@dataclass
class Activity:
    week: Period          # les WEEK_DAYS derniers jours, aujourd'hui compris
    month: Period         # depuis le 1er du mois
    streak: int           # jours d'affilée avec au moins un visionnage (voir current_streak)
    watched_today: bool


def _period(frame: pd.DataFrame, start: date, end: date, bulk: set[date]) -> Period:
    # astype(bool) : sur un tableau vide, le masque serait de type object (voir stats.watch_pace).
    window = frame[frame["day"].map(
        lambda d: isinstance(d, date) and start <= d <= end).astype(bool)]
    imported = window["day"].map(lambda d: d in bulk).astype(bool)
    kept = window[~imported]
    minutes = sum(stats.estimated_minutes(list(kept.loc[kept["kind"] == kind, "runtime"]), kind)[0]
                  for kind in (EPISODE, MOVIE))
    return Period(episodes=int((kept["kind"] == EPISODE).sum()),
                  movies=int((kept["kind"] == MOVIE).sum()), minutes=minutes,
                  ignored=int(imported.sum()))


def current_streak(days: Iterable[date], today: date) -> int:
    """Jours d'affilée avec au moins un visionnage, jusqu'à aujourd'hui.

    Rien vu aujourd'hui mais vu hier : la série tient encore (la journée n'est pas finie),
    elle compte jusqu'à hier. Sinon 0.
    """
    seen = set(days)
    day = today if today in seen else today - timedelta(days=1)
    count = 0
    while day in seen:
        count += 1
        day -= timedelta(days=1)
    return count


def activity(frame: pd.DataFrame, today: date | None = None) -> Activity:
    """Semaine, mois en cours et jours d'affilée, à partir du tableau des visionnages
    (stats.watches_frame). Un revisionnage ne compte pas : seul le premier est daté."""
    today = today or datetime.now(LOCAL_TZ).date()
    bulk = set(stats.bulk_days(frame))
    days = {d for d in frame["day"] if isinstance(d, date) and d not in bulk}
    return Activity(week=_period(frame, today - timedelta(days=WEEK_DAYS - 1), today, bulk),
                    month=_period(frame, today.replace(day=1), today, bulk),
                    streak=current_streak(days, today), watched_today=today in days)


# --- À suivre, films à voir, sorties ---------------------------------------------------

@dataclass
class NextUp:
    """Le prochain épisode sorti d'une série, et ce qu'il en reste."""
    item: QueueItem
    remaining: int                  # épisodes sortis pas encore vus, celui-ci compris
    last_watched: datetime | None   # dernier visionnage daté (None : jamais, ou jour d'import)


def _import_days(session: Session) -> set[date]:
    """Jours d'import, sur le dernier visionnage de chaque ligne (comme la bibliothèque)."""
    dates = session.exec(select(progress.WATCH_DATE)
                         .where(progress.WATCH_DATE.is_not(None))).all()
    return set(stats.count_bulk_days(value.date() for value in dates))


def continue_watching(session: Session, today: date | None = None,
                      limit: int = CONTINUE_LIMIT) -> tuple[list[NextUp], int]:
    """« À suivre » : le prochain épisode sorti (hors spéciaux) de chaque série en cours ou
    à voir.

    Séries regardées le plus récemment d'abord, puis celles pas encore commencées (ou vues
    un jour d'import) par date de sortie, comme le tri « Dernier visionnage » de À regarder.
    Renvoie (les `limit` premières, nombre de séries qui ont un épisode à voir).
    """
    items = queue.build_queue(session, kinds=(EPISODE,), statuses=STATUSES,
                              availabilities=(RELEASED,), today=today)
    by_show: dict[int, list[QueueItem]] = {}
    for item in items:
        by_show.setdefault(item.show_id, []).append(item)
    bulk = _import_days(session)
    last = {show_id: when for show_id, when in queue.show_activity(session).items()
            if when.date() not in bulk}
    entries = [NextUp(min(episodes, key=lambda i: (i.season, i.number)), len(episodes),
                      last.get(show_id))
               for show_id, episodes in by_show.items()]
    entries.sort(key=lambda e: queue.sort_key(e.item))
    entries.sort(key=lambda e: e.last_watched or datetime.min, reverse=True)
    return entries[:limit], len(entries)


def movies_to_watch(session: Session, today: date | None = None,
                    limit: int = MOVIES_LIMIT) -> tuple[list[QueueItem], int]:
    """Films sortis pas encore vus (en cours ou à voir), les derniers ajoutés d'abord : ce
    qu'on a eu envie de voir récemment (à égalité, le plus récent d'abord).

    Renvoie (les `limit` premiers, nombre total).
    """
    items = queue.build_queue(session, kinds=(MOVIE,), statuses=STATUSES,
                              availabilities=(RELEASED,), today=today)
    added = dict(session.exec(select(LibraryItem.ref_id, LibraryItem.added_at)
                              .where(LibraryItem.kind == MOVIE)).all())
    items.sort(key=lambda i: (added.get(i.ref_id) or datetime.min, i.release_date or date.min),
               reverse=True)
    return items[:limit], len(items)


def upcoming(session: Session, today: date | None = None,
             days: int = UPCOMING_DAYS) -> list[tuple[date, list[queue.DayEntry]]]:
    """Sorties non vues d'aujourd'hui aux `days` jours suivants (mêmes titres que le
    calendrier), par jour ; les épisodes d'une série sortis le même jour sur une ligne."""
    by_day: dict[date, list[QueueItem]] = {}
    for item in queue.calendar(session, days=days, today=today):
        by_day.setdefault(item.release_date, []).append(item)
    return [(day, queue.group_day(items)) for day, items in sorted(by_day.items())]


# --- Derniers visionnages ----------------------------------------------------------------

@dataclass
class RecentWatch:
    """Le dernier visionnage d'un titre (série ou film)."""
    kind: str                 # SHOW ou MOVIE : le titre, pas l'épisode
    ref_id: int               # id de la série ou du film
    title: str
    code: str                 # dernier épisode vu (« S02E05 »), "" pour un film
    when: datetime            # UTC naïf
    poster_path: str | None
    poster_url: str | None


def recent_watches(session: Session, limit: int = RECENT_LIMIT) -> list[RecentWatch]:
    """Les `limit` titres vus le plus récemment (revisionnages compris), un par titre.

    Visionnages datés d'un jour d'import ignorés : ce n'est pas quand ils ont été vus.
    """
    bulk = _import_days(session)
    when_col = progress.WATCH_DATE
    found: dict[tuple[str, int], RecentWatch] = {}
    episodes = session.exec(
        select(when_col, Episode, Show)
        .join(Episode, (Watch.kind == EPISODE) & (Watch.ref_id == Episode.id))
        .join(Show, Show.id == Episode.show_id)
        .where(when_col.is_not(None)).order_by(when_col.desc()))
    for when, episode, show in episodes:
        if when.date() in bulk or (SHOW, show.id) in found:
            continue
        found[(SHOW, show.id)] = RecentWatch(
            SHOW, show.id, show.title, queue.episode_code(episode.season, episode.number), when,
            show.poster_path, show.poster_url)
        if len(found) >= limit:
            break
    movies = session.exec(
        select(when_col, Movie)
        .join(Movie, (Watch.kind == MOVIE) & (Watch.ref_id == Movie.id))
        .where(when_col.is_not(None)).order_by(when_col.desc()))
    count = 0
    for when, movie in movies:
        if when.date() in bulk:
            continue
        found[(MOVIE, movie.id)] = RecentWatch(MOVIE, movie.id, movie.title, "", when,
                                               movie.poster_path, movie.poster_url)
        count += 1
        if count >= limit:
            break
    return sorted(found.values(), key=lambda r: r.when, reverse=True)[:limit]
