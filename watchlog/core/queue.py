"""Liste « À regarder » : épisodes et films non vus, triés par date de sortie.

Les séries s'entremêlent selon leurs dates de diffusion. Les éléments à venir
ou sans date sont inclus (l'interface les grise), les sans-date à la fin.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from watchlog.core.models import (EPISODE, FOR_LATER, MATCH_OK, MATCH_REMAPPED, MOVIE, SHOW, TO_WATCH,
                                  WATCHING, Episode, LibraryItem, Movie, Show, Watch)

LOCAL_TZ = ZoneInfo("Europe/Paris")

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


def _episode_items(session: Session, statuses, show_id, include_specials, today):
    watched = select(Watch.ref_id).where(Watch.kind == EPISODE)
    query = (
        select(Episode, Show, LibraryItem)
        .join(Show, Show.id == Episode.show_id)
        .join(LibraryItem, (LibraryItem.kind == SHOW) & (LibraryItem.ref_id == Show.id))
        .where(LibraryItem.status.in_(statuses))
        # Séries dont la numérotation n'est pas vérifiée : exclues (tout semblerait non vu).
        .where(Show.match_status.in_([MATCH_OK, MATCH_REMAPPED]))
        .where(Episode.id.not_in(watched))
    )
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
            season=ep.season, number=ep.number, is_special=ep.is_special))
    return items


def _movie_items(session: Session, statuses, today):
    watched = select(Watch.ref_id).where(Watch.kind == MOVIE)
    query = (
        select(Movie, LibraryItem)
        .join(LibraryItem, (LibraryItem.kind == MOVIE) & (LibraryItem.ref_id == Movie.id))
        .where(LibraryItem.status.in_(statuses))
        .where(Movie.id.not_in(watched))
    )
    items = []
    for movie, lib in session.exec(query):
        day = movie.release_date_fr or movie.release_date_original
        items.append(QueueItem(
            kind=MOVIE, ref_id=movie.id, show_id=None, title=movie.title, code="",
            episode_title=None, release_date=day, availability=availability(day, today),
            status=lib.status, runtime=movie.runtime, poster_path=movie.poster_path))
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
