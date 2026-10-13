"""Liste « À regarder » : épisodes et films non vus, triés par date de sortie.

Les séries s'entremêlent selon leurs dates de diffusion. Les éléments à venir
ou sans date sont inclus (l'interface les grise), les sans-date à la fin.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from watchlog.core.dates import LOCAL_TZ
from watchlog.core.models import (COMPLETED, EPISODE, FOR_LATER, MATCH_OK, MATCH_REMAPPED, MOVIE,
                                  SHOW, TO_WATCH, WATCHING, Episode, LibraryItem, Movie, Show,
                                  Watch)


# Disponibilité d'un élément
RELEASED, UPCOMING, NO_DATE = "released", "upcoming", "no_date"
AVAILABILITY_LABELS = {RELEASED: "Sorti", UPCOMING: "À venir", NO_DATE: "Sans date"}

# Statuts affichés par défaut dans « À regarder »
DEFAULT_STATUSES = (WATCHING, TO_WATCH)


@dataclass
class QueueItem:
    kind: str                    # "episode" ou "movie"
    ref_id: int                  # id de l'épisode ou du film
    show_id: int | None          # série de l'épisode (None pour un film)
    title: str                   # titre de la série ou du film
    code: str                    # "S01E02", "S01 spécial" ou "" pour un film
    episode_title: str | None
    release_date: date | None    # date locale (France) de diffusion / sortie
    availability: str            # RELEASED, UPCOMING ou NO_DATE
    status: str                  # statut de la série ou du film dans la bibliothèque
    runtime: int | None
    poster_path: str | None
    season: int | None = None
    number: int | None = None
    is_special: bool = False
    poster_url: str | None = None   # affiche en ligne (TVMaze / TMDB)
    watched: bool = False           # seulement pour releases_between (vus compris)

    @property
    def kind_label(self) -> str:
        return "Épisode" if self.kind == EPISODE else "Film"


def local_date(airstamp: datetime | None, airdate: date | None) -> date | None:
    """Date de diffusion en heure française (airstamp UTC), sinon la date TVMaze."""
    if airstamp is not None:
        return airstamp.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).date()
    return airdate


def availability(release_date: date | None, today: date) -> str:
    if release_date is None:
        return NO_DATE
    return RELEASED if release_date <= today else UPCOMING


def episode_code(season: int, number: int | None) -> str:
    return f"S{season:02d}E{number:02d}" if number is not None else f"S{season:02d} spécial"


def sort_key(item: QueueItem):
    """Par date (sans date à la fin), puis titre, puis ordre des épisodes."""
    return (item.release_date is None, item.release_date or date.max, item.title.lower(),
            item.season or 0, item.number if item.number is not None else 10_000)


def _episode_items(session: Session, statuses, show_id, include_specials, today,
                   include_watched: bool = False, airdate_range: tuple[date, date] | None = None):
    watched = select(Watch.ref_id).where(Watch.kind == EPISODE)
    query = (
        select(Episode, Show, LibraryItem)
        .join(Show, Show.id == Episode.show_id)
        .join(LibraryItem, (LibraryItem.kind == SHOW) & (LibraryItem.ref_id == Show.id))
        .where(LibraryItem.status.in_(statuses))
        # Séries dont la numérotation n'est pas vérifiée : exclues (tout semblerait non vu).
        .where(Show.match_status.in_([MATCH_OK, MATCH_REMAPPED]))
    )
    if include_watched:
        watched_ids = set(session.exec(watched).all())
    else:
        query = query.where(Episode.id.not_in(watched))
    if airdate_range is not None:
        # Marge d'un jour : la date française (airstamp) peut différer de la date TVMaze.
        query = query.where(Episode.airdate >= airdate_range[0] - timedelta(days=1),
                            Episode.airdate <= airdate_range[1] + timedelta(days=1))
    if show_id is not None:
        query = query.where(Show.id == show_id)
    if not include_specials:
        query = query.where(Episode.is_special == False)  # noqa: E712 (syntaxe SQLAlchemy)
    items = []
    for ep, show, lib in session.exec(query):
        day = local_date(ep.airstamp, ep.airdate)
        items.append(QueueItem(
            kind=EPISODE, ref_id=ep.id, show_id=show.id, title=show.title,
            code=episode_code(ep.season, ep.number), episode_title=ep.title,
            release_date=day, availability=availability(day, today), status=lib.status,
            runtime=ep.runtime, poster_path=show.poster_path,
            season=ep.season, number=ep.number, is_special=ep.is_special,
            poster_url=show.poster_url, watched=include_watched and ep.id in watched_ids))
    return items


def _movie_items(session: Session, statuses, today, include_watched: bool = False):
    watched = select(Watch.ref_id).where(Watch.kind == MOVIE)
    query = (
        select(Movie, LibraryItem)
        .join(LibraryItem, (LibraryItem.kind == MOVIE) & (LibraryItem.ref_id == Movie.id))
        .where(LibraryItem.status.in_(statuses))
    )
    if include_watched:
        watched_ids = set(session.exec(watched).all())
    else:
        query = query.where(Movie.id.not_in(watched))
    items = []
    for movie, lib in session.exec(query):
        day = movie.release_date_fr or movie.release_date_original
        items.append(QueueItem(
            kind=MOVIE, ref_id=movie.id, show_id=None, title=movie.title, code="",
            episode_title=None, release_date=day, availability=availability(day, today),
            status=lib.status, runtime=movie.runtime, poster_path=movie.poster_path,
            poster_url=movie.poster_url, watched=include_watched and movie.id in watched_ids))
    return items


def build_queue(session: Session, kinds=(EPISODE, MOVIE), statuses=DEFAULT_STATUSES,
                availabilities=(RELEASED, UPCOMING, NO_DATE), show_id: int | None = None,
                include_specials: bool = False, next_only: bool = False,
                today: date | None = None) -> list[QueueItem]:
    """Construit la liste « À regarder ».

    - kinds : types affichés ("episode", "movie")
    - statuses : statuts de bibliothèque retenus (par défaut : en cours + à voir)
    - availabilities : sorti / à venir / sans date
    - show_id : une seule série (les films sont alors exclus)
    - include_specials : inclure les épisodes spéciaux
    - next_only : seulement le prochain épisode non vu de chaque série
    """
    today = today or datetime.now(LOCAL_TZ).date()
    items = []
    if EPISODE in kinds:
        episodes = _episode_items(session, statuses, show_id, include_specials, today)
        if next_only:
            episodes = _first_per_show(episodes)
        items += episodes
    if MOVIE in kinds and show_id is None:
        items += _movie_items(session, statuses, today)
    items = [item for item in items if item.availability in availabilities]
    return sorted(items, key=sort_key)


def _first_per_show(items: list[QueueItem]) -> list[QueueItem]:
    """Le prochain épisode de chaque série, dans l'ordre (saison, numéro)."""
    first: dict[int, QueueItem] = {}
    for item in items:
        best = first.get(item.show_id)
        if best is None or _episode_order(item) < _episode_order(best):
            first[item.show_id] = item
    return list(first.values())


def _episode_order(item: QueueItem):
    # Un spécial (sans numéro) passe après les épisodes réguliers de sa saison.
    return (item.season or 0, item.number if item.number is not None else 10_000,
            item.release_date or date.max)


def calendar(session: Session, days: int = 60, today: date | None = None,
             include_specials: bool = False) -> list[QueueItem]:
    """Sorties non vues des `days` prochains jours (aujourd'hui compris), séries et films.

    Toutes les séries/films suivis, sauf ceux arrêtés ou terminés.
    """
    today = today or datetime.now(LOCAL_TZ).date()
    end = today + timedelta(days=days)
    items = build_queue(session, statuses=(WATCHING, TO_WATCH, FOR_LATER),
                        availabilities=(RELEASED, UPCOMING), include_specials=include_specials,
                        today=today)
    return [item for item in items if today <= item.release_date <= end]


def week_start(day: date) -> date:
    """Lundi de la semaine de `day` (pour grouper le calendrier par semaine)."""
    return day - timedelta(days=day.weekday())


def releases_between(session: Session, start: date, end: date, include_specials: bool = False,
                     today: date | None = None) -> list[QueueItem]:
    """Sorties entre `start` et `end` inclus, vues ou non (champ `watched`), triées par date.

    Séries et films de la bibliothèque, sauf ceux arrêtés. Pour la vue « Mois » du calendrier.
    """
    today = today or datetime.now(LOCAL_TZ).date()
    statuses = (WATCHING, TO_WATCH, FOR_LATER, COMPLETED)
    items = _episode_items(session, statuses, None, include_specials, today,
                           include_watched=True, airdate_range=(start, end))
    items += _movie_items(session, statuses, today, include_watched=True)
    items = [i for i in items if i.release_date is not None and start <= i.release_date <= end]
    return sorted(items, key=sort_key)


@dataclass
class DayEntry:
    """Une ligne du calendrier : un film, ou les épisodes d'une série sortis le même jour."""
    items: list[QueueItem]

    @property
    def first(self) -> QueueItem:
        return self.items[0]

    @property
    def watched(self) -> bool:
        return all(i.watched for i in self.items)

    @property
    def label(self) -> str:
        """« S01E03 », « S01E01–E08 » (même saison, à la suite) ou « 8 épisodes »."""
        if self.first.kind == MOVIE:
            return "Film"
        if len(self.items) == 1:
            return self.first.code
        numbers = [i.number for i in self.items]
        seasons = {i.season for i in self.items}
        if len(seasons) == 1 and None not in numbers and numbers == list(
                range(numbers[0], numbers[0] + len(numbers))):
            return f"{self.first.code}–E{numbers[-1]:02d}"
        return f"{len(self.items)} épisodes"


def group_day(items: list[QueueItem]) -> list[DayEntry]:
    """Regroupe les épisodes d'une même série (sortis le même jour) ; films à part."""
    entries: list[DayEntry] = []
    by_show: dict[int, DayEntry] = {}
    for item in items:
        if item.kind == EPISODE and item.show_id in by_show:
            by_show[item.show_id].items.append(item)
            continue
        entry = DayEntry([item])
        entries.append(entry)
        if item.kind == EPISODE:
            by_show[item.show_id] = entry
    return entries
