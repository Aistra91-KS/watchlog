"""Vue d'ensemble de la bibliothèque : progression de chaque série, état de chaque film.

Les compteurs des séries sont calculés en UNE requête SQL groupée (GROUP BY série),
et non épisode par épisode : la bibliothèque compte plusieurs milliers d'épisodes.
"""

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, case, func, or_
from sqlmodel import Session, select

from watchlog.core import stats
from watchlog.core.dates import fmt_date
from watchlog.core.models import (EPISODE, MATCH_NOT_FOUND, MATCH_NUMBERING, MATCH_UNCERTAIN, MOVIE,
                                  SHOW, WATCHING, Episode, LibraryItem, Movie, PendingWatch, Show,
                                  Watch)
from watchlog.core.queue import LOCAL_TZ, episode_code, local_date

# Correspondances TVMaze à vérifier par l'utilisateur (page Paramètres)
MATCH_TO_CHECK = (MATCH_UNCERTAIN, MATCH_NOT_FOUND, MATCH_NUMBERING)

# Durées approximatives pour « Vu il y a 3 mois » (libellé, pas un calcul de calendrier)
DAYS_PER_MONTH, DAYS_PER_YEAR = 30, 365

# Tris possibles de la bibliothèque. SORT_RECENT : dernier visionnage d'abord ;
# SORT_PAUSED : séries « En cours » dont le dernier visionnage est le plus ancien (à reprendre).
SORT_TITLE, SORT_ADDED, SORT_RECENT, SORT_PAUSED = "title", "added", "recent", "paused"


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
    last_watched: datetime | None = None   # dernier visionnage daté (UTC naïf), épisodes compris
    import_day: bool = False          # dernier visionnage daté d'un jour d'import : date inconnue
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


# Date d'un visionnage : le plus récent (revisionnage), sinon le premier.
WATCH_DATE = func.coalesce(Watch.last_watched_at, Watch.watched_at)


def _show_last_watched(session: Session) -> dict[int, datetime]:
    """Dernier visionnage daté de chaque série (spéciaux compris), en une requête groupée."""
    query = (select(Episode.show_id, func.max(WATCH_DATE))
             .join(Watch, and_(Watch.kind == EPISODE, Watch.ref_id == Episode.id))
             .group_by(Episode.show_id))
    return {show_id: last for show_id, last in session.exec(query) if last is not None}


def _show_entries(session: Session, statuses, today: date) -> list[LibraryEntry]:
    query = select(LibraryItem, Show).join(
        Show, (LibraryItem.kind == SHOW) & (LibraryItem.ref_id == Show.id))
    if statuses:
        query = query.where(LibraryItem.status.in_(statuses))
    counters = _show_counters(session, today)
    last_watched = _show_last_watched(session)
    entries = []
    for lib, show in session.exec(query):
        entries.append(LibraryEntry(
            kind=SHOW, ref_id=show.id, title=show.title, original_title=show.original_title,
            status=lib.status, favorite=lib.favorite, poster_path=show.poster_path,
            year=show.premiered.year if show.premiered else None, added_at=lib.added_at,
            last_watched=last_watched.get(show.id),
            premiered=show.premiered, match_status=show.match_status,
            **counters.get(show.id, {})))
    return entries


def _movie_entries(session: Session, statuses) -> list[LibraryEntry]:
    query = (
        select(LibraryItem, Movie, Watch.id, WATCH_DATE)
        .join(Movie, (LibraryItem.kind == MOVIE) & (LibraryItem.ref_id == Movie.id))
        .join(Watch, and_(Watch.kind == MOVIE, Watch.ref_id == Movie.id), isouter=True)
    )
    if statuses:
        query = query.where(LibraryItem.status.in_(statuses))
    entries = []
    for lib, movie, watch_id, watched_at in session.exec(query):
        release = movie.release_date_fr or movie.release_date_original
        entries.append(LibraryEntry(
            kind=MOVIE, ref_id=movie.id, title=movie.title, original_title=movie.original_title,
            status=lib.status, favorite=lib.favorite, poster_path=movie.poster_path,
            year=movie.year or (release.year if release else None), added_at=lib.added_at,
            last_watched=watched_at,
            watched=watch_id is not None, release_date=release))
    return entries


def _sort(entries: list[LibraryEntry], sort: str) -> list[LibraryEntry]:
    """Trie les éléments ; les dates inconnues vont toujours à la fin."""
    entries.sort(key=lambda e: normalize(e.title))  # départage stable : par titre
    if sort == SORT_ADDED:
        entries.sort(key=lambda e: e.added_at or datetime.min, reverse=True)
    elif sort == SORT_RECENT:
        entries.sort(key=lambda e: e.last_watched or datetime.min, reverse=True)
    elif sort == SORT_PAUSED:
        entries.sort(key=lambda e: (e.last_watched is None, e.last_watched or datetime.min))
    return entries


def library_overview(session: Session, kind: str | None = None, status=None,
                     search: str | None = None, favorite_only: bool = False,
                     sort: str = SORT_TITLE, today: date | None = None) -> list[LibraryEntry]:
    """Séries et films de la bibliothèque avec leur progression.

    - kind : "show", "movie" ou None (les deux)
    - status : un statut ou une liste de statuts (None : tous)
    - search : texte cherché dans le titre ou le titre original (sans accents ni majuscules)
    - favorite_only : seulement les favoris
    - sort : SORT_TITLE (alphabétique), SORT_ADDED (derniers ajouts en premier),
      SORT_RECENT (vus récemment en premier) ou SORT_PAUSED (séries « En cours » seulement,
      la plus longtemps délaissée en premier : un film n'est jamais « en pause »)
    """
    today = today or datetime.now(LOCAL_TZ).date()
    statuses = [status] if isinstance(status, str) else status
    if sort == SORT_PAUSED:
        statuses = [s for s in statuses if s == WATCHING] if statuses else [WATCHING]
        if kind == MOVIE or not statuses:
            return []
        kind = SHOW
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
    _forget_import_days(session, entries)
    return _sort(entries, sort)


def _forget_import_days(session: Session, entries: list[LibraryEntry]) -> None:
    """Un dernier visionnage daté d'un jour d'import (Bingers) ne dit rien : date inconnue.

    Sinon « Vu récemment » / « En pause » classeraient l'historique importé à sa date
    d'import. Même détection que les statistiques et les suggestions (stats.count_bulk_days).
    """
    if not any(e.last_watched for e in entries):
        return
    dates = session.exec(select(WATCH_DATE).where(WATCH_DATE.is_not(None))).all()
    bulk = set(stats.count_bulk_days(value.date() for value in dates))
    for entry in entries:
        if entry.last_watched and entry.last_watched.date() in bulk:
            entry.last_watched, entry.import_day = None, True


def last_watched_label(entry: LibraryEntry, today: date | None = None) -> str:
    """« Vu aujourd'hui », « Vu il y a 3 mois »... ; chaîne vide si rien n'a été vu."""
    if entry.last_watched is None:
        if entry.import_day:
            return "Date inconnue (import)"
        seen = entry.watched if entry.kind == MOVIE else entry.last_watched_code is not None
        return "Date de visionnage inconnue" if seen else ""
    today = today or datetime.now(LOCAL_TZ).date()
    days = (today - local_date(entry.last_watched, None)).days
    if days <= 0:
        return "Vu aujourd'hui"
    if days == 1:
        return "Vu hier"
    if days < DAYS_PER_MONTH:
        return f"Vu il y a {days} jours"
    if days < DAYS_PER_YEAR:
        return f"Vu il y a {days // DAYS_PER_MONTH} mois"
    return f"Vu il y a {_plural(days // DAYS_PER_YEAR, 'an')}"


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
