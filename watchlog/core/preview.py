"""Aperçu d'un film ou d'une série TMDB pas forcément dans la bibliothèque (page « Aperçu »).

Utilisé depuis les suggestions : résumé, casting, bande-annonce, titres similaires.
"""

from dataclasses import dataclass, field
from datetime import date

from sqlmodel import Session, select

from watchlog.core import adding
from watchlog.core.dates import parse_date
from watchlog.core.http import ApiError
from watchlog.core.models import MOVIE, SHOW, LibraryItem, Movie, Show
from watchlog.core.tmdb import TMDBClient

TMDB_MEDIA = {SHOW: "tv", MOVIE: "movie"}
BACKDROP_BASE = "https://image.tmdb.org/t/p/w1280"
PROFILE_BASE = "https://image.tmdb.org/t/p/w185"
MAX_CAST = 10

# Statuts TMDB traduits
STATUS_LABELS = {
    "Returning Series": "En cours de diffusion", "Ended": "Terminée", "Canceled": "Annulée",
    "In Production": "En production", "Planned": "Prévue", "Pilot": "Pilote",
    "Released": "Sorti", "Post Production": "Post-production", "Rumored": "Annoncé",
}


@dataclass
class Person:
    name: str
    role: str | None
    photo_url: str | None


@dataclass
class Preview:
    kind: str
    tmdb_id: int
    title: str
    original_title: str | None
    released: date | None
    tagline: str | None
    overview: str
    genres: list[str]
    poster_url: str | None
    backdrop_url: str | None
    vote_average: float
    vote_count: int
    status: str | None
    runtime: int | None = None            # film : durée ; série : durée d'un épisode
    seasons: int | None = None
    episodes: int | None = None
    networks: list[str] = field(default_factory=list)
    makers: list[str] = field(default_factory=list)   # réalisation (film) / création (série)
    cast: list[Person] = field(default_factory=list)
    trailer_url: str | None = None

    @property
    def year(self) -> int | None:
        return self.released.year if self.released else None


def pick_trailer(videos: list[dict]) -> str | None:
    """Meilleure vidéo YouTube : bande-annonce > teaser, en français d'abord, officielle d'abord."""
    candidates = [v for v in videos if v.get("site") == "YouTube" and v.get("key")
                  and v.get("type") in ("Trailer", "Teaser")]
    if not candidates:
        return None
    best = min(candidates, key=lambda v: (v["type"] != "Trailer", v.get("iso_639_1") != "fr",
                                          not v.get("official")))
    return f"https://www.youtube.com/watch?v={best['key']}"


def from_tmdb(kind: str, data: dict) -> Preview:
    """Fiche TMDB (avec credits et videos) -> Preview."""
    credits = data.get("credits") or {}
    if kind == SHOW:
        title, original = data.get("name"), data.get("original_name")
        released = parse_date(data.get("first_air_date"))
        runtimes = data.get("episode_run_time") or []
        runtime = runtimes[0] if runtimes else None
        makers = [c["name"] for c in data.get("created_by") or [] if c.get("name")]
    else:
        title, original = data.get("title"), data.get("original_title")
        released = parse_date(data.get("release_date"))
        runtime = data.get("runtime") or None
        makers = [c["name"] for c in credits.get("crew") or [] if c.get("job") == "Director"]
    cast = [Person(c.get("name") or "", c.get("character") or None,
                   f"{PROFILE_BASE}{c['profile_path']}" if c.get("profile_path") else None)
            for c in (credits.get("cast") or [])[:MAX_CAST]]
    return Preview(
        kind=kind, tmdb_id=data["id"], title=title or original or "Sans titre",
        original_title=original, released=released, tagline=data.get("tagline") or None,
        overview=adding.short_text(data.get("overview"), length=2000),
        genres=[g["name"] for g in data.get("genres") or [] if g.get("name")],
        poster_url=TMDBClient.poster_url(data.get("poster_path")),
        backdrop_url=f"{BACKDROP_BASE}{data['backdrop_path']}" if data.get("backdrop_path") else None,
        vote_average=float(data.get("vote_average") or 0), vote_count=int(data.get("vote_count") or 0),
        status=STATUS_LABELS.get(data.get("status"), data.get("status")),
        runtime=runtime, seasons=data.get("number_of_seasons"), episodes=data.get("number_of_episodes"),
        networks=[n["name"] for n in data.get("networks") or [] if n.get("name")],
        makers=makers, cast=cast,
        trailer_url=pick_trailer((data.get("videos") or {}).get("results") or []))


def load(tmdb, kind: str, tmdb_id: int) -> Preview | None:
    """Aperçu depuis TMDB. None si introuvable ; ApiError si TMDB ne répond pas."""
    data = tmdb.details(TMDB_MEDIA[kind], tmdb_id)
    return from_tmdb(kind, data) if data else None


def similar(tmdb, kind: str, tmdb_id: int, limit: int = 6) -> list[dict]:
    """Titres similaires (recommandations TMDB) : id, title, year, poster_url."""
    try:
        results = tmdb.recommendations(TMDB_MEDIA[kind], tmdb_id)
    except ApiError:
        return []
    items = []
    for r in results[:limit]:
        released = parse_date(r.get("first_air_date") if kind == SHOW else r.get("release_date"))
        items.append({"id": r["id"], "title": r.get("name") or r.get("title") or "Sans titre",
                      "year": released.year if released else None,
                      "poster_url": TMDBClient.poster_url(r.get("poster_path"))})
    return items


def library_id(session: Session, kind: str, tmdb_id: int) -> int | None:
    """Id en base du titre s'il est dans la bibliothèque (pour proposer sa fiche), sinon None."""
    table = Show if kind == SHOW else Movie
    obj = session.exec(select(table).where(table.tmdb_id == tmdb_id)).first()
    if obj is None:
        return None
    in_library = session.exec(select(LibraryItem).where(
        LibraryItem.kind == kind, LibraryItem.ref_id == obj.id)).first()
    return obj.id if in_library else None
