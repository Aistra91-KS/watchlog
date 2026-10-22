"""Synchronisation : met à jour les séries (TVMaze) et les films (TMDB) de la bibliothèque.

Principe :
- Séries : TVMaze fournit la liste des séries modifiées sur les 7 derniers jours
  (/updates/shows?since=week). On ne recharge que celles qui ont changé depuis
  notre dernière synchro, ou dont la dernière synchro date de plus de 7 jours
  (au-delà, la liste « week » ne suffit plus pour savoir si elles ont changé).
- Films : on ne recharge que les films pas encore sortis (les dates peuvent bouger)
  ou jamais récupérés depuis TMDB. Le cache gère la durée de validité.
- Durées réelles et titres français des épisodes, résumé français de la série (TMDB) :
  pour les séries jamais vérifiées (durées ou textes français) et celles qui viennent
  d'être rechargées (TVMaze donne souvent la durée du créneau, pub comprise, et des textes
  en anglais). Une même requête par saison sert aux durées et aux titres.
- Ensuite : passage en « Terminé » si besoin, affiches manquantes, enregistrement.
- Enfin : date et rapport de la synchro gardés en base (menu de l'interface, synchro
  automatique au lancement si la dernière date de plus de AUTO_SYNC_AGE).

Une erreur sur un élément est notée dans le rapport mais n'arrête pas le reste.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Engine
from sqlmodel import Session, select

from watchlog.core import settings
from watchlog.core.config import DATA_DIR
from watchlog.core.dates import utcnow
from watchlog.core.http import ApiError
from watchlog.core.models import (MATCH_OK, MATCH_REMAPPED, MOVIE, SHOW, STOPPED,
                                  LibraryItem, Movie, Show)

log = logging.getLogger(__name__)

# La fenêtre "week" de TVMaze couvre 7 jours : au-delà, on recharge par sécurité.
MAX_AGE = timedelta(days=7)
# Synchro automatique au lancement de l'interface si la dernière date de plus de ça.
AUTO_SYNC_AGE = timedelta(hours=24)

# progress(fait, total, libellé) : pour afficher l'avancement (CLI ou interface).
Progress = Callable[[int, int, str], None]


@dataclass
class SyncReport:
    shows_checked: int = 0       # séries suivies examinées
    shows_updated: int = 0       # séries rechargées depuis TVMaze
    movies_checked: int = 0      # films examinés (pas sortis ou jamais récupérés)
    movies_updated: int = 0      # films rechargés depuis TMDB
    runtimes_updated: int = 0    # épisodes dont la durée a été corrigée par TMDB
    runtimes_unmatched: int = 0  # épisodes gardant la durée TVMaze (numérotation différente)
    runtimes_missing: int = 0    # saisons absentes de TMDB (durées TVMaze gardées)
    runtimes_no_tmdb: int = 0    # séries sans id TMDB : durées TVMaze gardées
    titles_fr: int = 0           # épisodes au titre français nouveau ou changé
    titles_fr_missing: int = 0   # épisodes sans titre français sur TMDB (anglais gardé)
    summaries_fr: int = 0        # séries avec un résumé français (parmi celles relues)
    summaries_fr_missing: int = 0  # séries relues sans résumé français sur TMDB
    completed: int = 0          # séries passées en « Terminé »
    posters: int = 0             # affiches téléchargées
    tvmaze_down: bool = False    # TVMaze injoignable : séries non mises à jour
    tmdb_down: bool = False      # TMDB non connecté : films ignorés
    errors: list[str] = field(default_factory=list)

    @property
    def problems(self) -> int:
        """Nombre de choses qui n'ont pas pu être mises à jour (API en panne, erreurs)."""
        return len(self.errors) + self.tvmaze_down + self.tmdb_down

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
        if self.runtimes_updated:
            lines.append(f"Durées réelles (TMDB) : {self.runtimes_updated} épisodes corrigés.")
        if self.runtimes_unmatched or self.runtimes_missing:
            lines.append(f"Durées TVMaze gardées : {self.runtimes_unmatched} épisodes à la "
                         f"numérotation différente sur TMDB, {self.runtimes_missing} saisons "
                         "absentes de TMDB.")
        if self.runtimes_no_tmdb:
            lines.append(f"Durées TVMaze gardées : {self.runtimes_no_tmdb} séries introuvables "
                         "sur TMDB.")
        if self.titles_fr or self.titles_fr_missing:
            lines.append(f"Titres d'épisodes en français (TMDB) : {self.titles_fr} ajoutés ou "
                         f"modifiés, {self.titles_fr_missing} sans traduction (anglais gardé).")
        if self.summaries_fr or self.summaries_fr_missing:
            lines.append(f"Résumés de séries en français (TMDB) : {self.summaries_fr} lus, "
                         f"{self.summaries_fr_missing} sans traduction (anglais gardé).")
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


def _runtime_candidates(session: Session):
    """Séries de la bibliothèque à la numérotation fiable (requête, sans filtre sur l'id TMDB)."""
    return (
        select(Show)
        .join(LibraryItem, (LibraryItem.kind == SHOW) & (LibraryItem.ref_id == Show.id))
        .where(Show.match_status.in_([MATCH_OK, MATCH_REMAPPED]))
        .order_by(Show.title)
    )


def shows_for_runtimes(session: Session, refreshed_tvmaze_ids=()) -> list[Show]:
    """Séries à relire sur TMDB (durées, textes français) : rechargées ou jamais vérifiées.

    `texts_fr_checked_at` vide = passe initiale des textes français, une fois pour toute la
    bibliothèque, y compris les séries dont les durées étaient déjà lues.
    Toute la bibliothèque, arrêtées comprises (leurs épisodes vus comptent dans les stats).
    """
    refreshed = set(refreshed_tvmaze_ids)
    query = _runtime_candidates(session).where(Show.tmdb_id.is_not(None))
    return [show for show in session.exec(query)
            if show.runtimes_checked_at is None or show.texts_fr_checked_at is None
            or show.tvmaze_id in refreshed]


def shows_without_tmdb(session: Session) -> list[Show]:
    """Séries dont on ne peut pas lire les durées TMDB, faute d'id : à signaler au rapport."""
    return list(session.exec(_runtime_candidates(session).where(Show.tmdb_id.is_(None))))


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
    from watchlog.core import catalog, library, suggestions

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

        # 3. Choix des séries dont on lit les durées réelles (TMDB).
        runtime_jobs = []  # (id, titre)
        if not report.tmdb_down:
            # Séries ajoutées depuis TVMaze : leur id TMDB n'est connu qu'une fois cherché.
            suggestions.resolve_show_tmdb_ids(session, tmdb)
            session.commit()
            report.runtimes_no_tmdb = len(shows_without_tmdb(session))
            refreshed = [tvmaze_id for tvmaze_id, _ in show_jobs]
            runtime_jobs = [(s.id, s.title) for s in shows_for_runtimes(session, refreshed)]

        total = len(show_jobs) + len(movie_jobs) + len(runtime_jobs)
        done = 0

        # 4. Séries. On enregistre après chaque élément : une erreur ne perd que celui-là.
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

        # 5. Films.
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

        # 6. Durées réelles, titres et résumé français (après les séries : nouveaux épisodes
        # compris). Durées et résumé enregistrés ensemble : une erreur fait tout refaire.
        # Aucune écriture en base avant le commit (pas de flush, pas de requête) : le cache
        # d'API s'écrit par une autre connexion, qui serait bloquée (« database is locked »).
        for show_id, title in runtime_jobs:
            progress(done, total, f"Durées et textes français : {title}")
            try:
                show = session.get(Show, show_id)
                result = catalog.apply_tmdb_episodes(session, tmdb, show)
                has_summary = catalog.apply_tmdb_summary(session, tmdb, show)
                session.commit()
                report.runtimes_updated += result.runtimes
                report.runtimes_unmatched += result.unmatched
                report.runtimes_missing += result.missing_seasons
                report.titles_fr += result.titles
                report.titles_fr_missing += result.no_title
                report.summaries_fr += has_summary
                report.summaries_fr_missing += not has_summary
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                log.error("Synchro durées / textes %s (série %s) : %s", title, show_id, exc)
                report.errors.append(f"Durées et textes de « {title} » : {exc}")
            done += 1
        progress(total, total, "Terminé")

        # 7. Séries terminées (tous les épisodes vus et série finie).
        report.completed = library.refresh_completed(session)
        session.commit()

        # 8. Affiches manquantes.
        if download_posters:
            report.posters = _download_missing_posters(session, tvmaze, tmdb, report)

        # 9. Date et rapport, pour l'interface.
        remember(session, report)
        session.commit()
    return report


def remember(session: Session, report: SyncReport, now: datetime | None = None) -> None:
    """Garde le rapport, et la date de la synchro si TVMaze a répondu. Pas de commit.

    TVMaze en panne : la date n'avance pas, la prochaine ouverture réessaiera.
    """
    now = now or utcnow()
    if not report.tvmaze_down:
        settings.set_value(session, settings.LAST_SYNC_AT, now.isoformat())
    settings.set_value(session, settings.LAST_SYNC_REPORT,
                       {"at": now.isoformat(), "lines": report.lines(),
                        "problems": report.problems})


def last_sync_at(session: Session) -> datetime | None:
    """Fin de la dernière synchro réussie (UTC naïf), None si jamais faite."""
    value = settings.get_value(session, settings.LAST_SYNC_AT)
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        log.warning("Date de dernière synchro illisible : %r", value)
        return None


def sync_due(session: Session, now: datetime | None = None,
             max_age: timedelta = AUTO_SYNC_AGE) -> bool:
    """True si la dernière synchro réussie date de plus de `max_age` (ou n'a jamais eu lieu)."""
    last = last_sync_at(session)
    return last is None or (now or utcnow()) - last > max_age


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
