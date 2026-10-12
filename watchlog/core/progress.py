"""Vue d'ensemble de la bibliothèque : progression de chaque série, état de chaque film.

Les compteurs des séries sont calculés en UNE requête SQL groupée (GROUP BY série),
et non épisode par épisode : la bibliothèque compte plusieurs milliers d'épisodes.
"""

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, case, func, or_
from sqlmodel import Session, select

from watchlog.core.dates import fmt_date
from watchlog.core.models import (EPISODE, MATCH_NOT_FOUND, MATCH_NUMBERING, MATCH_UNCERTAIN, MOVIE,
                                  SHOW, Episode, LibraryItem, Movie, PendingWatch, Show, Watch)
from watchlog.core.queue import LOCAL_TZ, episode_code

# Correspondances TVMaze à vérifier par l'utilisateur (page Paramètres)
MATCH_TO_CHECK = (MATCH_UNCERTAIN, MATCH_NOT_FOUND, MATCH_NUMBERING)

# Tris possibles de la bibliothèque
SORT_TITLE, SORT_ADDED = "title", "added"


@dataclass
class LibraryEntry:
    kind: str                         # "show" ou "movie"
    ref_id: int                       # id de la série ou du film
    title: str
    original_title: str | None
    status: str                       # statut dans la bibliothèque
    favorite: bool
    poster_path: str | None
    year: int | None
    added_at: datetime | None = None
    # Série
    premiered: date | None = None
    last_watched_code: str | None = None   # ex. "S09E09" (plus loin vu, hors spéciaux)
    remaining: int = 0                # épisodes réguliers non vus déjà diffusés
    upcoming: int = 0                 # épisodes réguliers non vus à venir
    total_regular: int = 0            # nombre d'épisodes réguliers (hors spéciaux)
    match_status: str | None = None
    # Film
    watched: bool = False
    release_date: date | None = None


def normalize(text: str | None) -> str:
    """Minuscules sans accents : « Été » -> « ete » (pour la recherche)."""
    decomposed = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower()


def _tomorrow_midnight_utc(today: date) -> datetime:
    """Demain 00:00 à Paris, converti en UTC naïf (même convention que la base).

    Un épisode est « déjà diffusé » si son airstamp (UTC) est avant cet instant,
    c'est-à-dire si sa date française est au plus tard aujourd'hui.
    """
    midnight = datetime.combine(today + timedelta(days=1), time(0, 0), tzinfo=LOCAL_TZ)
    return midnight.astimezone(timezone.utc).replace(tzinfo=None)


def _show_counters(session: Session, today: date) -> dict[int, dict]:
    """Compteurs par série, en une seule requête groupée sur les épisodes réguliers."""
    cutoff = _tomorrow_midnight_utc(today)
    not_watched = Watch.id.is_(None)
    # Date française <= aujourd'hui (airstamp si connu, sinon airdate TVMaze)
    aired = or_(and_(Episode.airstamp.is_not(None), Episode.airstamp < cutoff),
                and_(Episode.airstamp.is_(None), Episode.airdate.is_not(None),
                     Episode.airdate <= today))
    future = or_(Episode.airstamp >= cutoff,
                 and_(Episode.airstamp.is_(None), Episode.airdate > today))
    # Plus loin vu : on code (saison, numéro) en un seul nombre saison * 10000 + numéro
    # pour pouvoir prendre le maximum en SQL (S09E09 -> 90009).
    position = Episode.season * 10000 + Episode.number
    query = (
        select(Episode.show_id,
               func.count(Episode.id),
               func.sum(case((and_(not_watched, aired), 1), else_=0)),
               func.sum(case((and_(not_watched, future), 1), else_=0)),
               func.max(case((Watch.id.is_not(None), position), else_=None)))
        .join(Watch, and_(Watch.kind == EPISODE, Watch.ref_id == Episode.id), isouter=True)
        .where(Episode.is_special == False)  # noqa: E712 (syntaxe SQLAlchemy)
        .where(Episode.number.is_not(None))
        .group_by(Episode.show_id)
    )
    counters = {}
    for show_id, total, remaining, upcoming, best in session.exec(query):
        last_code = episode_code(best // 10000, best % 10000) if best is not None else None
        counters[show_id] = {"total_regular": total, "remaining": remaining or 0,
                             "upcoming": upcoming or 0, "last_watched_code": last_code}
    return counters


def _show_entries(session: Session, statuses, today: date) -> list[LibraryEntry]:
    query = select(LibraryItem, Show).join(
        Show, (LibraryItem.kind == SHOW) & (LibraryItem.ref_id == Show.id))
    if statuses:
        query = query.where(LibraryItem.status.in_(statuses))
    counters = _show_counters(session, today)
    entries = []
    for lib, show in session.exec(query):
        entries.append(LibraryEntry(
            kind=SHOW, ref_id=show.id, title=show.title, original_title=show.original_title,
            status=lib.status, favorite=lib.favorite, poster_path=show.poster_path,
            year=show.premiered.year if show.premiered else None, added_at=lib.added_at,
            premiered=show.premiered, match_status=show.match_status,
            **counters.get(show.id, {})))
    return entries


def _movie_entries(session: Session, statuses) -> list[LibraryEntry]:
    query = (
        select(LibraryItem, Movie, Watch.id)
        .join(Movie, (LibraryItem.kind == MOVIE) & (LibraryItem.ref_id == Movie.id))
        .join(Watch, and_(Watch.kind == MOVIE, Watch.ref_id == Movie.id), isouter=True)
    )
    if statuses:
        query = query.where(LibraryItem.status.in_(statuses))
    entries = []
    for lib, movie, watch_id in session.exec(query):
        release = movie.release_date_fr or movie.release_date_original
        entries.append(LibraryEntry(
            kind=MOVIE, ref_id=movie.id, title=movie.title, original_title=movie.original_title,
            status=lib.status, favorite=lib.favorite, poster_path=movie.poster_path,
            year=movie.year or (release.year if release else None), added_at=lib.added_at,
            watched=watch_id is not None, release_date=release))
    return entries


def library_overview(session: Session, kind: str | None = None, status=None,
                     search: str | None = None, favorite_only: bool = False,
                     sort: str = SORT_TITLE, today: date | None = None) -> list[LibraryEntry]:
    """Séries et films de la bibliothèque avec leur progression.

    - kind : "show", "movie" ou None (les deux)
    - status : un statut ou une liste de statuts (None : tous)
    - search : texte cherché dans le titre ou le titre original (sans accents ni majuscules)
    - favorite_only : seulement les favoris
    - sort : SORT_TITLE (alphabétique) ou SORT_ADDED (derniers ajouts en premier)
    """
    today = today or datetime.now(LOCAL_TZ).date()
    statuses = [status] if isinstance(status, str) else status
    entries = []
    if kind in (None, SHOW):
        entries += _show_entries(session, statuses, today)
    if kind in (None, MOVIE):
        entries += _movie_entries(session, statuses)
    if favorite_only:
        entries = [e for e in entries if e.favorite]
    if search and search.strip():
        wanted = normalize(search.strip())
        entries = [e for e in entries
                   if wanted in normalize(e.title) or wanted in normalize(e.original_title)]
    if sort == SORT_ADDED:
        # Les plus récents d'abord ; les éléments sans date d'ajout à la fin.
        entries.sort(key=lambda e: e.added_at or datetime.min, reverse=True)
    else:
        entries.sort(key=lambda e: normalize(e.title))
    return entries


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("s" if count > 1 else "")


def progress_label(entry: LibraryEntry, today: date | None = None) -> str:
    """Résumé court : « S09E09 · 87 restants », « À jour », « Vu », « Sortie le 01/12/2026 »..."""
    today = today or datetime.now(LOCAL_TZ).date()
    if entry.kind == MOVIE:
        if entry.watched:
            return "Vu"
        if entry.release_date is not None and entry.release_date > today:
            return f"Sortie le {fmt_date(entry.release_date)}"
        return "Non vu"
    if entry.match_status in MATCH_TO_CHECK:
        return "Correspondance à vérifier"
    if entry.last_watched_code is None:
        if entry.remaining:
            return f"Pas commencé · {_plural(entry.remaining, 'restant')}"
        return "Pas commencé"
    if entry.remaining == 0:
        return "À jour"
    return f"{entry.last_watched_code} · {_plural(entry.remaining, 'restant')}"


def shows_to_check(session: Session) -> int:
    """Nombre de séries à vérifier : correspondance incertaine ou visionnages en attente."""
    pending = select(PendingWatch.show_id)
    query = select(func.count(Show.id)).where(
        or_(Show.match_status.in_(MATCH_TO_CHECK), Show.id.in_(pending)))
    return session.exec(query).one()
