"""Synchronisation : met à jour les séries (TVMaze) et les films (TMDB) de la bibliothèque.

Principe :
- Séries : TVMaze fournit la liste des séries modifiées sur les 7 derniers jours
  (/updates/shows?since=week). On ne recharge que celles qui ont changé depuis
  notre dernière synchro, ou dont la dernière synchro date de plus de 7 jours
  (au-delà, la liste « week » ne suffit plus pour savoir si elles ont changé).
- Films : on ne recharge que les films pas encore sortis (les dates peuvent bouger)
  ou jamais récupérés depuis TMDB. Le cache gère la durée de validité.
- Ensuite : passage en « Terminé » si besoin, affiches manquantes, enregistrement.

Une erreur sur un élément est notée dans le rapport mais n'arrête pas le reste.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Engine
from sqlmodel import Session, select

from watchlog.core.config import DATA_DIR
from watchlog.core.dates import utcnow
from watchlog.core.http import ApiError
from watchlog.core.models import (MATCH_OK, MATCH_REMAPPED, MOVIE, SHOW, STOPPED,
                                  LibraryItem, Movie, Show)

log = logging.getLogger(__name__)

# La fenêtre "week" de TVMaze couvre 7 jours : au-delà, on recharge par sécurité.
MAX_AGE = timedelta(days=7)

# progress(fait, total, libellé) : pour afficher l'avancement (CLI ou interface).
Progress = Callable[[int, int, str], None]


@dataclass
class SyncReport:
    shows_checked: int = 0       # séries suivies examinées
    shows_updated: int = 0       # séries rechargées depuis TVMaze
    movies_checked: int = 0      # films examinés (pas sortis ou jamais récupérés)
    movies_updated: int = 0      # films rechargés depuis TMDB
    completed: int = 0           # séries passées en « Terminé »
    posters: int = 0             # affiches téléchargées
    tvmaze_down: bool = False    # TVMaze injoignable : séries non mises à jour
    tmdb_down: bool = False      # TMDB non connecté : films ignorés
    errors: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        """Résumé lisible, une ligne par information."""
        lines = []
        if self.tvmaze_down:
            lines.append("TVMaze indisponible : séries non mises à jour.")
        else:
            lines.append(f"Séries : {self.shows_checked} suivies, {self.shows_updated} mises à jour.")
        if self.tmdb_down:
            lines.append("TMDB non connecté : films non mis à jour.")
        else:
            lines.append(f"Films : {self.movies_checked} à vérifier, {self.movies_updated} mis à jour.")
        if self.completed:
            lines.append(f"Séries passées en « Terminé » : {self.completed}.")
        if self.posters:
            lines.append(f"Affiches téléchargées : {self.posters}.")
        if self.errors:
            lines.append(f"Erreurs : {len(self.errors)} (détails dans data/watchlog.log).")
            lines.extend(f"  - {e}" for e in self.errors)
        else:
            lines.append("Aucune erreur.")
        return lines


# --- Sélection de ce qu'il faut mettre à jour --------------------------------

def show_needs_refresh(last_synced_at: datetime | None, updated_ts: int | None,
                       now: datetime) -> bool:
    """True si la série doit être rechargée.

    `updated_ts` : horodatage Unix (UTC) de dernière modification côté TVMaze,
    ou None si la série n'a pas changé cette semaine.
    """
    if last_synced_at is None or now - last_synced_at > MAX_AGE:
        return True
    if updated_ts is None:
        return False
    updated_at = datetime.fromtimestamp(updated_ts, timezone.utc).replace(tzinfo=None)
    return updated_at > last_synced_at


def library_shows(session: Session) -> list[Show]:
    """Séries de la bibliothèque à suivre : pas arrêtées, reliées à TVMaze et confirmées."""
    query = (
        select(Show)
        .join(LibraryItem, (LibraryItem.kind == SHOW) & (LibraryItem.ref_id == Show.id))
        .where(LibraryItem.status != STOPPED)
        .where(Show.tvmaze_id.is_not(None))
        .where(Show.match_status.in_([MATCH_OK, MATCH_REMAPPED]))
        .order_by(Show.title)
    )
    return list(session.exec(query))


def shows_to_refresh(session: Session, updates: dict[int, int],
                     now: datetime | None = None) -> list[Show]:
    """Séries de la bibliothèque qui ont changé, ou trop anciennes."""
    now = now or utcnow()
    return [show for show in library_shows(session)
            if show_needs_refresh(show.last_synced_at, updates.get(show.tvmaze_id), now)]


def is_released(movie: Movie, today: date) -> bool:
    """True si au moins une date de sortie connue est passée."""
    return any(d is not None and d <= today
               for d in (movie.release_date_fr, movie.release_date_original))


def movies_to_refresh(session: Session, today: date | None = None) -> list[Movie]:
    """Films de la bibliothèque (avec id TMDB) pas encore sortis ou jamais récupérés."""
    today = today or date.today()
    query = (
        select(Movie)
        .join(LibraryItem, (LibraryItem.kind == MOVIE) & (LibraryItem.ref_id == Movie.id))
        .where(Movie.tmdb_id.is_not(None))
        .order_by(Movie.title)
    )
    return [movie for movie in session.exec(query)
            if movie.last_synced_at is None or not is_released(movie, today)]


# --- Synchronisation ---------------------------------------------------------

def sync(engine: Engine, tvmaze, tmdb, progress: Progress | None = None,
         download_posters: bool = True) -> SyncReport:
    """Met à jour la bibliothèque et renvoie un rapport."""
    # Imports ici : ces modules dépendent de beaucoup d'autres.
    from watchlog.core import catalog, library

    report = SyncReport()
    progress = progress or (lambda done, total, label: None)

    with Session(engine) as session:
        # 1. Choix des séries à recharger.
        show_jobs = []  # (tvmaze_id, titre) : on garde des valeurs simples (voir rollback)
        try:
            updates = tvmaze.updated_since("week")
        except ApiError as exc:
            log.error("Synchro : liste des mises à jour TVMaze indisponible (%s)", exc)
            report.tvmaze_down = True
        else:
            report.shows_checked = len(library_shows(session))
            show_jobs = [(s.tvmaze_id, s.title) for s in shows_to_refresh(session, updates)]

        # 2. Choix des films à recharger.
        movie_jobs = []
        if not tmdb.available:
            report.tmdb_down = True
        else:
            movie_jobs = [(m.tmdb_id, m.title) for m in movies_to_refresh(session)]
            report.movies_checked = len(movie_jobs)

        total = len(show_jobs) + len(movie_jobs)
        done = 0

        # 3. Séries. On enregistre après chaque élément : une erreur ne perd que celui-là.
        for tvmaze_id, title in show_jobs:
            progress(done, total, title)
            try:
                if catalog.fetch_and_upsert_show(session, tvmaze, tvmaze_id, force=True):
                    session.commit()
                    report.shows_updated += 1
                else:
                    report.errors.append(f"Série « {title} » : introuvable sur TVMaze.")
            except Exception as exc:  # noqa: BLE001  (on continue avec les suivantes)
                session.rollback()
                log.error("Synchro série %s (TVMaze %s) : %s", title, tvmaze_id, exc)
                report.errors.append(f"Série « {title} » : {exc}")
            done += 1

        # 4. Films.
        for tmdb_id, title in movie_jobs:
            progress(done, total, title)
            try:
                data = tmdb.get_movie(tmdb_id)
                if data is None:
                    report.errors.append(f"Film « {title} » : introuvable sur TMDB.")
                else:
                    catalog.upsert_movie_from_tmdb(session, data)
                    session.commit()
                    report.movies_updated += 1
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                log.error("Synchro film %s (TMDB %s) : %s", title, tmdb_id, exc)
                report.errors.append(f"Film « {title} » : {exc}")
            done += 1
        progress(total, total, "Terminé")

        # 5. Séries terminées (tous les épisodes vus et série finie).
        report.completed = library.refresh_completed(session)
        session.commit()

        # 6. Affiches manquantes.
        if download_posters:
            report.posters = _download_missing_posters(session, tvmaze, tmdb, report)

        session.commit()
    return report


def _download_missing_posters(session: Session, tvmaze, tmdb, report: SyncReport) -> int:
    """Télécharge les affiches qui ont une URL mais pas de fichier local (ou fichier effacé)."""
    from watchlog.core import catalog

    def missing(obj) -> bool:
        return not obj.poster_path or not (DATA_DIR / obj.poster_path).exists()

    targets = [(show, tvmaze) for show in session.exec(
        select(Show).where(Show.poster_url.is_not(None))) if missing(show)]
    targets += [(movie, tmdb) for movie in session.exec(
        select(Movie).where(Movie.poster_url.is_not(None))) if missing(movie)]
    count = 0
    for obj, client in targets:
        try:
            catalog.ensure_poster(session, obj, client)
        except Exception as exc:  # noqa: BLE001
            log.warning("Affiche de %s non téléchargée : %s", obj.title, exc)
            continue
        if not missing(obj):
            count += 1
    return count
