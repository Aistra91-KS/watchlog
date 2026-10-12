"""Petites fonctions pour remplir une base de test directement (sans import ni API)."""

from datetime import date, datetime

from sqlmodel import Session

from watchlog.core.models import (EPISODE, MOVIE, SHOW, WATCHING, Episode, LibraryItem, Movie,
                                  Show, Watch)


def add_show(session: Session, title: str, status: str = WATCHING, favorite: bool = False,
             added_at: datetime | None = None, **fields) -> Show:
    """Crée une série et l'ajoute à la bibliothèque avec le statut donné."""
    show = Show(title=title, **fields)
    session.add(show)
    session.flush()
    session.add(LibraryItem(kind=SHOW, ref_id=show.id, status=status, favorite=favorite,
                            added_at=added_at))
    return show


def add_episode(session: Session, show: Show, season: int, number: int | None,
                airdate: date | None = None, airstamp: datetime | None = None,
                title: str | None = None, runtime: int | None = 45) -> Episode:
    """Crée un épisode (number=None : épisode spécial)."""
    episode = Episode(show_id=show.id, season=season, number=number, airdate=airdate,
                      airstamp=airstamp, title=title or f"Épisode {season}x{number}",
                      runtime=runtime, is_special=number is None)
    session.add(episode)
    session.flush()
    return episode


def add_movie(session: Session, title: str, release: date | None = None, status: str = WATCHING,
              favorite: bool = False, added_at: datetime | None = None, **fields) -> Movie:
    """Crée un film (sortie française = `release`) et l'ajoute à la bibliothèque."""
    movie = Movie(title=title, release_date_fr=release, runtime=120, **fields)
    session.add(movie)
    session.flush()
    session.add(LibraryItem(kind=MOVIE, ref_id=movie.id, status=status, favorite=favorite,
                            added_at=added_at))
    return movie


def watch(session: Session, item: Episode | Movie) -> None:
    """Marque vu un épisode ou un film."""
    kind = EPISODE if isinstance(item, Episode) else MOVIE
    session.add(Watch(kind=kind, ref_id=item.id, watched_at=datetime(2026, 1, 1)))
    session.flush()
