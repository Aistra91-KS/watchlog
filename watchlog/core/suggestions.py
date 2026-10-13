"""Suggestions de séries et de films à partir de la bibliothèque (recommandations TMDB).

Principe :
- chaque titre de la bibliothèque est une « source » avec un poids : favori ou bien noté
  = fort, terminé / en cours = normal, arrêté ou mal noté = négatif ;
- pour chaque source, TMDB donne ~20 titres recommandés (ce que regardent aussi les gens
  qui ont vu ce titre) ; un titre recommandé gagne le poids de la source, un peu moins s'il
  est loin dans la liste ;
- on additionne, on retire ce qui est déjà dans la bibliothèque ou écarté, on trie.

Les réponses TMDB sont gardées 30 jours en cache : seul le premier calcul est long.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlmodel import Session, select

from watchlog.core import adding, providers
from watchlog.core.dates import utcnow
from watchlog.core.http import ApiError
from watchlog.core.models import (FOR_LATER, MOVIE, SHOW, STOPPED, TO_WATCH,
                                  DismissedSuggestion, LibraryItem, Movie, Rating, Show)
from watchlog.core.tmdb import TMDBClient

# Type de média côté TMDB
TMDB_MEDIA = {SHOW: "tv", MOVIE: "movie"}

# Poids d'une note (1 à 5) : elle remplace le poids du statut.
RATING_WEIGHTS = {5: 3.0, 4: 2.0, 3: 1.0, 2: -1.0, 1: -2.0}
FAVORITE_BONUS = 2.0
STATUS_WEIGHTS = {STOPPED: -1.0, TO_WATCH: 0.5, FOR_LATER: 0.5}  # autres statuts : 1
DEFAULT_WEIGHT = 1.0

MAX_SOURCES = 200   # sources par type (séries / films) : limite le nombre d'appels TMDB
MIN_VOTES = 20      # titres trop confidentiels ou pas encore sortis : ignorés
MAX_REASONS = 3     # titres cités dans « Parce que tu as vu… »


@dataclass
class Source:
    kind: str
    tmdb_id: int
    title: str
    weight: float


@dataclass
class Suggestion:
    kind: str                     # show / movie
    tmdb_id: int
    title: str
    original_title: str | None
    year: int | None
    overview: str
    poster_url: str | None
    genres: list[str]
    vote_average: float
    vote_count: int = 0
    score: float = 0.0
    match: int = 0                # correspondance en % (100 = meilleure suggestion du type)
    because: list[str] = field(default_factory=list)  # titres de la bibliothèque à l'origine
    streaming: list[str] = field(default_factory=list)  # plateformes abonnement / gratuites (FR)
    rent_or_buy: bool = False     # disponible en location ou à l'achat (FR)


def source_weight(status: str, favorite: bool, rating: int | None) -> float:
    """Poids d'un titre de la bibliothèque dans le calcul des suggestions."""
    if rating in RATING_WEIGHTS:
        weight = RATING_WEIGHTS[rating]
    else:
        weight = STATUS_WEIGHTS.get(status, DEFAULT_WEIGHT)
    if favorite:
        weight = max(weight, 0) + FAVORITE_BONUS
    return weight


def resolve_show_tmdb_ids(session: Session, tmdb) -> int:
    """Complète l'id TMDB des séries qui n'en ont pas (via leur id TVDB). Pas de commit.

    Cas des séries ajoutées depuis TVMaze, qui ne donne pas l'id TMDB. Renvoie le nombre trouvé.
    """
    found = 0
    for show in session.exec(select(Show).where(Show.tmdb_id.is_(None),
                                                Show.tvdb_id.is_not(None))).all():
        try:
            tmdb_id = tmdb.find_tv_by_tvdb(show.tvdb_id)
        except ApiError:
            continue
        if tmdb_id:
            show.tmdb_id = tmdb_id
            session.add(show)
            found += 1
    return found


def library_sources(session: Session, kind: str) -> list[Source]:
    """Titres de la bibliothèque (avec id TMDB) et leur poids, les plus marquants d'abord."""
    table = Show if kind == SHOW else Movie
    ratings = {r.ref_id: r.rating for r in session.exec(
        select(Rating).where(Rating.kind == kind, Rating.rating.is_not(None))).all()}
    rows = session.exec(select(LibraryItem, table)
                        .join(table, table.id == LibraryItem.ref_id)
                        .where(LibraryItem.kind == kind, table.tmdb_id.is_not(None))).all()
    sources = []
    for item, obj in rows:
        weight = source_weight(item.status, item.favorite, ratings.get(obj.id))
        sources.append((item.added_at, Source(kind, obj.tmdb_id, obj.title, weight)))
    # Les plus marquants (|poids|), puis les plus récemment ajoutés.
    sources.sort(key=lambda pair: pair[0] or datetime.min, reverse=True)
    sources.sort(key=lambda pair: abs(pair[1].weight), reverse=True)
    return [source for _added, source in sources[:MAX_SOURCES]]


def excluded_ids(session: Session, kind: str) -> set[int]:
    """Ids TMDB à ne jamais suggérer : titres déjà en base et titres écartés."""
    table = Show if kind == SHOW else Movie
    known = set(session.exec(select(table.tmdb_id).where(table.tmdb_id.is_not(None))).all())
    dismissed = set(session.exec(select(DismissedSuggestion.tmdb_id)
                                 .where(DismissedSuggestion.kind == kind)).all())
    return known | dismissed


def _year(value: str | None) -> int | None:
    try:
        return int(value[:4]) if value else None
    except ValueError:
        return None


def _to_suggestion(kind: str, data: dict, genre_names: dict[int, str]) -> Suggestion:
    if kind == SHOW:
        title, original, released = data.get("name"), data.get("original_name"), data.get("first_air_date")
    else:
        title, original, released = data.get("title"), data.get("original_title"), data.get("release_date")
    return Suggestion(
        kind=kind, tmdb_id=data["id"], title=title or original or "Sans titre",
        original_title=original, year=_year(released),
        overview=adding.short_text(data.get("overview"), length=300),
        poster_url=TMDBClient.poster_url(data.get("poster_path")),
        genres=[genre_names[g] for g in data.get("genre_ids", []) if g in genre_names],
        vote_average=float(data.get("vote_average") or 0),
        vote_count=int(data.get("vote_count") or 0))


def compute(session: Session, tmdb, kind: str, limit: int = 60,
            progress: Callable[[int, int], None] | None = None) -> list[Suggestion]:
    """Suggestions triées (meilleures d'abord) pour kind = SHOW ou MOVIE.

    `progress(fait, total)` est appelé après chaque source. Une source dont TMDB ne répond
    pas est ignorée ; ApiError seulement si aucune n'a pu être lue.
    """
    media = TMDB_MEDIA[kind]
    sources = [s for s in library_sources(session, kind) if s.weight != 0]
    excluded = excluded_ids(session, kind)
    try:
        genre_names = tmdb.genres(media)
    except ApiError:
        genre_names = {}

    candidates: dict[int, Suggestion] = {}
    reasons: dict[int, dict[str, float]] = defaultdict(dict)  # id -> {titre source: apport}
    failures = 0
    for done, source in enumerate(sources, start=1):
        try:
            results = tmdb.recommendations(media, source.tmdb_id)
        except ApiError:
            failures += 1
            results = []
        for rank, data in enumerate(results):
            if "id" not in data or data["id"] in excluded:
                continue
            if (data.get("vote_count") or 0) < MIN_VOTES:
                continue
            # 1 pour le premier recommandé, ~0,5 pour le dernier de la liste
            contribution = source.weight * (1 - rank / (2 * len(results)))
            suggestion = candidates.get(data["id"])
            if suggestion is None:
                suggestion = candidates[data["id"]] = _to_suggestion(kind, data, genre_names)
            suggestion.score += contribution
            if contribution > 0:
                reasons[data["id"]][source.title] = contribution
        if progress:
            progress(done, len(sources))
    if sources and failures == len(sources):
        raise ApiError("TMDB indisponible : aucune recommandation n'a pu être lue.")

    ranked = sorted((s for s in candidates.values() if s.score > 0),
                    key=lambda s: (s.score, s.vote_average), reverse=True)[:limit]
    top = ranked[0].score if ranked else 1
    for suggestion in ranked:
        titles = reasons[suggestion.tmdb_id]
        suggestion.because = sorted(titles, key=titles.get, reverse=True)[:MAX_REASONS]
        suggestion.match = max(1, round(100 * suggestion.score / top))
    return ranked


STREAMING_OFFERS = ("Abonnement", "Gratuit", "Gratuit avec pub")


def attach_availability(tmdb, items: list[Suggestion]) -> None:
    """Complète chaque suggestion avec ses plateformes en France (pour les filtres)."""
    for item in items:
        data = providers.availability(tmdb, item.kind, item.tmdb_id)
        if data is None:
            continue
        names = []
        for offer in STREAMING_OFFERS:
            names += [p.name for p in data.offers.get(offer, []) if p.name not in names]
        item.streaming = names
        item.rent_or_buy = bool(data.offers.get("Location") or data.offers.get("Achat"))


# --- Actions depuis la page Suggestions --------------------------------------------

def dismiss(session: Session, kind: str, tmdb_id: int, title: str | None = None) -> None:
    """« Pas intéressé » : le titre ne sera plus suggéré. Pas de commit."""
    exists = session.exec(select(DismissedSuggestion).where(
        DismissedSuggestion.kind == kind, DismissedSuggestion.tmdb_id == tmdb_id)).first()
    if exists is None:
        session.add(DismissedSuggestion(kind=kind, tmdb_id=tmdb_id, title=title,
                                        dismissed_at=utcnow()))


def dismissed(session: Session) -> list[DismissedSuggestion]:
    """Titres écartés, les plus récents d'abord."""
    return list(session.exec(select(DismissedSuggestion)
                             .order_by(DismissedSuggestion.dismissed_at.desc())).all())


def undismiss_all(session: Session) -> int:
    """Remet tous les titres écartés dans les suggestions. Pas de commit."""
    rows = dismissed(session)
    for row in rows:
        session.delete(row)
    return len(rows)


def add_suggested_show(session: Session, tmdb, tvmaze, tmdb_id: int, status: str,
                       title: str | None = None) -> Show:
    """Ajoute une série suggérée (id TMDB) : on retrouve la fiche TVMaze via TVDB, sinon IMDb.

    `title` : titre français TMDB, gardé si TVMaze n'en connaît pas.
    """
    ids = tmdb.tv_external_ids(tmdb_id)
    data = None
    if ids.get("tvdb_id"):
        data = tvmaze.lookup_tvdb(ids["tvdb_id"])
    if data is None and ids.get("imdb_id"):
        data = tvmaze.lookup_imdb(ids["imdb_id"])
    if data is None:
        raise ValueError("Série introuvable sur TVMaze : essaie de la chercher dans la page Ajouter.")
    show = adding.add_show(session, tvmaze, data["id"], status)
    if show.tmdb_id is None:
        show.tmdb_id = tmdb_id
    if title and show.title == show.original_title:
        show.title = title
    session.add(show)
    session.flush()
    return show


def add_suggested_movie(session: Session, tmdb, tmdb_id: int, status: str,
                        watched_on: date | None = None) -> Movie:
    return adding.add_movie(session, tmdb, tmdb_id, status, watched_on=watched_on)


# --- Filtres et tri (page Suggestions) ---------------------------------------------

SORT_RELEVANCE, SORT_RATING, SORT_RECENT = "Pertinence", "Note TMDB", "Plus récents"
SORTS = (SORT_RELEVANCE, SORT_RATING, SORT_RECENT)


def filter_items(items: list[Suggestion], kinds: tuple[str, ...] = (SHOW, MOVIE),
                 genres: tuple[str, ...] = (), platforms: tuple[str, ...] = (),
                 streaming_only: bool = False, years: tuple[int, int] | None = None,
                 min_vote: float = 0.0, sort: str = SORT_RELEVANCE) -> list[Suggestion]:
    """Suggestions filtrées puis triées.

    genres / platforms : au moins un en commun (vide = pas de filtre). years : (min, max)
    inclus ; un titre sans année n'est gardé que si aucun filtre d'années n'est donné.
    """
    result = []
    for item in items:
        if item.kind not in kinds:
            continue
        if genres and not set(genres) & set(item.genres):
            continue
        if platforms and not set(platforms) & set(item.streaming):
            continue
        if streaming_only and not item.streaming:
            continue
        if years and (item.year is None or not years[0] <= item.year <= years[1]):
            continue
        if item.vote_average < min_vote:
            continue
        result.append(item)
    if sort == SORT_RATING:
        result.sort(key=lambda s: (s.vote_average, s.vote_count), reverse=True)
    elif sort == SORT_RECENT:
        result.sort(key=lambda s: (s.year or 0, s.match), reverse=True)
    else:
        result.sort(key=lambda s: (s.match, s.score), reverse=True)
    return result
