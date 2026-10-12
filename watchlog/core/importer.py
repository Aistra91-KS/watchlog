"""Import d'un export Bingers (.zip contenant library.csv, watches.csv, ratings.csv, lists.csv).

Principes :
- import idempotent : le relancer ne crée aucun doublon ;
- les éléments déjà dans la bibliothèque ne sont pas écrasés (Watchlog est la référence) ;
- on ne devine jamais une correspondance d'épisodes douteuse : les visionnages concernés
  sont mis « en attente » (table PendingWatch) et listés dans le rapport.
"""

import io
import logging
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO

import pandas as pd
from sqlalchemy import Engine
from sqlmodel import Session, select

from watchlog.core import catalog, library
from watchlog.core.config import POSTERS_DIR
from watchlog.core.dates import parse_datetime_utc
from watchlog.core.http import ApiError
from watchlog.core.models import (COMPLETED, EPISODE, FOR_LATER, MATCH_NOT_FOUND,
                                  MATCH_NUMBERING, MATCH_OK, MATCH_REMAPPED, MATCH_UNCERTAIN,
                                  MOVIE, SHOW, STOPPED, TO_WATCH, WATCHING, CustomList,
                                  CustomListItem, Episode, Movie, PendingWatch, Rating, Show)

log = logging.getLogger(__name__)

Progress = Callable[[int, int, str], None]

CSV_FILES = ("library", "watches", "ratings", "lists")

# Raisons lisibles des visionnages en attente
REASON_NOT_FOUND = "série non trouvée sur TVMaze"
REASON_UNCERTAIN = "série à confirmer (trouvée par recherche de titre)"
REASON_API_DOWN = "TVMaze indisponible pendant l'import"
REASON_NUMBERING = "numérotation différente de TVMaze"
REASON_SPECIAL = "épisode spécial"


# --- Lecture du zip ----------------------------------------------------------

def read_bingers_zip(source: str | Path | BinaryIO) -> dict[str, list[dict]]:
    """Lit les 4 CSV du zip. Renvoie {"library": [...], "watches": [...], ...}.

    Chaque ligne est un dict de chaînes (cellule vide -> ""). Fichier absent -> liste vide.
    """
    result: dict[str, list[dict]] = {name: [] for name in CSV_FILES}
    with zipfile.ZipFile(source) as archive:
        for member in archive.namelist():
            name = PurePosixPath(member).stem  # "dossier/library.csv" -> "library"
            if name in result and member.endswith(".csv"):
                content = archive.read(member)
                frame = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False,
                                    encoding="utf-8-sig")
                result[name] = frame.to_dict(orient="records")
    return result


def _int(value) -> int | None:
    """'1234' -> 1234 ; '' ou invalide -> None. (Accepte aussi '12.0'.)"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# --- Rapport -----------------------------------------------------------------

@dataclass
class ImportReport:
    shows: int = 0
    movies: int = 0
    episode_watches: int = 0
    movie_watches: int = 0
    ratings: int = 0
    ratings_skipped: int = 0
    lists: int = 0
    pending_watches: int = 0
    not_found: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)
    numbering: list[str] = field(default_factory=list)          # "Titre : explication"
    remapped_skipped: list[str] = field(default_factory=list)
    movies_without_metadata: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        """Résumé en français, une ligne par information."""
        lines = [
            f"Séries : {self.shows}",
            f"Films : {self.movies}",
            f"Épisodes vus : {self.episode_watches}",
            f"Films vus : {self.movie_watches}",
            f"Notes : {self.ratings}",
            f"Listes : {self.lists}",
            f"Visionnages en attente : {self.pending_watches}",
        ]
        if self.ratings_skipped:
            lines.append(f"Notes non reprises (élément introuvable ou non rattaché) : {self.ratings_skipped}")
        sections = [
            ("Séries introuvables sur TVMaze", self.not_found),
            ("Séries à confirmer", self.uncertain),
            ("Numérotation incohérente (visionnages en attente)", self.numbering),
            ("Séries déjà recalées à la main (visionnages d'épisodes ignorés)", self.remapped_skipped),
            ("Films sans métadonnées TMDB", self.movies_without_metadata),
        ]
        for title, items in sections:
            if items:
                lines.append(f"{title} ({len(items)}) :")
                lines.extend(f"  - {item}" for item in items)
        return lines


# --- Visionnages d'une série -------------------------------------------------

@dataclass
class EpisodeView:
    """Un épisode vu selon Bingers (numérotation TVDB), avant rattachement."""
    season: int
    number: int
    watched_at: object = None
    last_watched_at: object = None
    plays: int = 1


def _add_pending(session: Session, show_id: int, view: EpisodeView, reason: str) -> None:
    """Ajoute (ou fusionne) un visionnage en attente."""
    pending = session.exec(select(PendingWatch).where(
        PendingWatch.show_id == show_id, PendingWatch.season == view.season,
        PendingWatch.number == view.number)).first()
    if pending is None:
        pending = PendingWatch(show_id=show_id, season=view.season, number=view.number,
                               watched_at=view.watched_at, last_watched_at=view.last_watched_at,
                               plays=view.plays)
    else:
        pending.watched_at = library._earliest(pending.watched_at, view.watched_at)
        pending.last_watched_at = library._latest(pending.last_watched_at, view.last_watched_at)
        pending.plays = max(pending.plays, view.plays)
    pending.reason = reason
    session.add(pending)


def _numbering_problem(views: list[EpisodeView], regular: dict) -> str | None:
    """Explication si les épisodes vus ne collent pas à TVMaze, sinon None."""
    missing = [v for v in views if (v.season, v.number) not in regular]
    if not missing and len(views) <= len(regular):
        return None
    parts = []
    if missing:
        first = min(missing, key=lambda v: (v.season, v.number))
        parts.append(f"{len(missing)} épisode(s) vu(s) absent(s) chez TVMaze "
                     f"(ex. S{first.season:02d}E{first.number:02d})")
    parts.append(f"{len(views)} vus / {len(regular)} chez TVMaze")
    return " ; ".join(parts)


def place_watches(session: Session, show: Show, views: list[EpisodeView]) -> tuple[int, int]:
    """Rattache les visionnages d'une série trouvée sur TVMaze, ou les met en attente.

    Renvoie (nb de visionnages appliqués, nb mis en attente).
    Met à jour show.match_status si la numérotation est incohérente.
    """
    episodes = session.exec(select(Episode).where(Episode.show_id == show.id,
                                                  Episode.is_special == False)).all()  # noqa: E712
    regular = {(ep.season, ep.number): ep for ep in episodes}
    specials = [v for v in views if v.season == 0]
    normal = [v for v in views if v.season != 0]

    problem = _numbering_problem(normal, regular)
    if problem:
        show.match_status = MATCH_NUMBERING
        show.match_note = problem
        session.add(show)
        for view in views:
            _add_pending(session, show.id, view, REASON_NUMBERING)
        return 0, len(views)

    if show.match_status == MATCH_NUMBERING:  # TVMaze a corrigé sa numérotation
        show.match_status, show.match_note = MATCH_OK, None
        session.add(show)
    for view in normal:
        episode = regular[(view.season, view.number)]
        library.upsert_watch(session, EPISODE, episode.id, view.watched_at,
                             view.last_watched_at, view.plays)
        # S'il restait un ancien visionnage en attente pour cet épisode, il est maintenant appliqué.
        old = session.exec(select(PendingWatch).where(
            PendingWatch.show_id == show.id, PendingWatch.season == view.season,
            PendingWatch.number == view.number)).first()
        if old is not None:
            session.delete(old)
    for view in specials:
        _add_pending(session, show.id, view, REASON_SPECIAL)
    return len(normal), len(specials)


# --- Import ------------------------------------------------------------------

LIBRARY_STATUS = {"watching": WATCHING, "for_later": FOR_LATER, "stopped": STOPPED,
                  "following": TO_WATCH}


def _collect(rows: dict[str, list[dict]]) -> tuple[dict, dict]:
    """Regroupe les lignes Bingers par série et par film.

    Clé d'une série : ("tvdb", id) ou ("title", titre) si pas d'id TVDB.
    Clé d'un film : ("tmdb", id) ou ("title", titre).
    """
    shows: dict[tuple, dict] = {}
    movies: dict[tuple, dict] = {}

    def key(row, id_field):
        ident = _int(row.get(id_field))
        return (id_field, ident) if ident is not None else ("title", row.get("title", ""))

    for row in rows["library"]:
        if row.get("type") == "show":
            target, k = shows, key(row, "tvdb_id")
        elif row.get("type") == "movie":
            target, k = movies, key(row, "tmdb_id")
        else:
            continue
        target[k] = {"row": row, "in_library": True, "watches": []}

    for row in rows["watches"]:
        if row.get("type") == "episode":
            target, k = shows, key(row, "tvdb_id")
        elif row.get("type") == "movie":
            target, k = movies, key(row, "tmdb_id")
        else:
            continue
        # Visionnage d'un élément absent de library.csv : on l'ajoute quand même.
        entry = target.setdefault(k, {"row": row, "in_library": False, "watches": []})
        entry["watches"].append(row)
    return shows, movies


def _views(watch_rows: list[dict]) -> list[EpisodeView]:
    """Lignes watches.csv d'une série -> visionnages, fusionnés par (saison, numéro)."""
    merged: dict[tuple[int, int], EpisodeView] = {}
    for row in watch_rows:
        season, number = _int(row.get("season_number")), _int(row.get("episode_number"))
        if season is None or number is None:
            log.warning("Visionnage ignoré (numéro d'épisode manquant) : %s", row.get("title"))
            continue
        first = parse_datetime_utc(row.get("first_watched_at"))
        last = parse_datetime_utc(row.get("last_watched_at")) or first
        plays = _int(row.get("plays")) or 1
        view = merged.get((season, number))
        if view is None:
            merged[(season, number)] = EpisodeView(season, number, first, last, plays)
        else:
            view.watched_at = library._earliest(view.watched_at, first)
            view.last_watched_at = library._latest(view.last_watched_at, last)
            view.plays = max(view.plays, plays)
    return sorted(merged.values(), key=lambda v: (v.season, v.number))


def _find_show(session: Session, tvdb_id: int | None, title: str) -> Show | None:
    if tvdb_id is not None:
        return session.exec(select(Show).where(Show.tvdb_id == tvdb_id)).first()
    return session.exec(select(Show).where(Show.tvdb_id.is_(None), Show.title == title)).first()


def _resolve_show(session: Session, tvmaze, show: Show | None, info: dict) -> tuple[Show, str | None]:
    """Cherche la série sur TVMaze. Renvoie (série, raison d'attente ou None si trouvée)."""
    tvdb_id, title, original = info["tvdb_id"], info["title"], info["original_title"]
    try:
        data = tvmaze.lookup_tvdb(tvdb_id) if tvdb_id is not None else None
        if data:
            found = catalog.fetch_and_upsert_show(
                session, tvmaze, data["id"], title=title, original_title=original or None,
                tvdb_id=tvdb_id, tmdb_id=info["tmdb_id"])
            if found is not None:
                if found.match_status in (MATCH_UNCERTAIN, MATCH_NOT_FOUND):
                    found.match_status, found.match_note = MATCH_OK, None
                    found.candidate_tvmaze_id = None
                return found, None
        # Pas d'id TVDB connu de TVMaze : recherche par titre (original, puis français).
        queries = [q for q in (original, title) if q]
        if len(queries) == 2 and queries[0] == queries[1]:
            queries.pop()  # même titre : une seule recherche
        results, query = [], ""
        for query in queries:
            results = tvmaze.search(query)
            if results:
                break
    except ApiError as exc:
        log.warning("TVMaze indisponible pour %s : %s", title, exc)
        session.rollback()
        show = _find_show(session, tvdb_id, title)  # l'objet a pu être annulé par le rollback
        show = show or _new_show(info)
        if show.tvmaze_id is None:
            show.match_status = MATCH_NOT_FOUND
            show.match_note = "TVMaze indisponible pendant l'import : relancer l'import plus tard"
        return show, REASON_API_DOWN

    show = show or _new_show(info)
    if results:
        candidate = results[0]["show"]
        year = (candidate.get("premiered") or "")[:4] or "année inconnue"
        show.match_status = MATCH_UNCERTAIN
        show.candidate_tvmaze_id = candidate["id"]
        show.match_note = (f"Pas trouvée par l'id TVDB {tvdb_id} ; proposition par titre : "
                           f"« {candidate.get('name')} » ({year}, TVMaze {candidate['id']})")
        return show, REASON_UNCERTAIN
    show.match_status = MATCH_NOT_FOUND
    show.candidate_tvmaze_id = None
    show.match_note = f"Introuvable sur TVMaze (id TVDB {tvdb_id}, recherche « {query} »)"
    return show, REASON_NOT_FOUND


def _new_show(info: dict) -> Show:
    return Show(title=info["title"] or info["original_title"] or "Sans titre",
                original_title=info["original_title"] or None,
                tvdb_id=info["tvdb_id"], tmdb_id=info["tmdb_id"])


def _import_show(session: Session, tvmaze, entry: dict, report: ImportReport) -> Show:
    row = entry["row"]
    info = {"tvdb_id": _int(row.get("tvdb_id")), "tmdb_id": _int(row.get("tmdb_id")),
            "title": row.get("title", ""), "original_title": row.get("original_title", "")}
    views = _views(entry["watches"])

    show = _find_show(session, info["tvdb_id"], info["title"])
    reason = None
    if show is None or show.tvmaze_id is None:
        show, reason = _resolve_show(session, tvmaze, show, info)
    session.add(show)
    session.flush()

    if reason is not None:  # série pas (encore) rattachée : tout en attente
        for view in views:
            _add_pending(session, show.id, view, reason)
        report.pending_watches += len(views)
        label = f"{show.title} : {show.match_note}"
        (report.uncertain if show.match_status == MATCH_UNCERTAIN else report.not_found).append(label)
    elif show.match_status == MATCH_REMAPPED:
        report.remapped_skipped.append(show.title)
    else:
        applied, pending = place_watches(session, show, views)
        report.episode_watches += applied
        report.pending_watches += pending
        if show.match_status == MATCH_NUMBERING:
            report.numbering.append(f"{show.title} : {show.match_note}")

    # Bibliothèque (un élément existant n'est pas modifié).
    if entry["in_library"]:
        status = LIBRARY_STATUS.get(row.get("list_status", ""), TO_WATCH)
        added_at = parse_datetime_utc(row.get("added_at"))
    else:
        status, added_at = WATCHING, None
    library.ensure_library_item(session, SHOW, show.id, status,
                                favorite=row.get("favorite") == "yes", added_at=added_at)
    return show


def _import_movie(session: Session, tmdb, entry: dict, report: ImportReport) -> Movie:
    row = entry["row"]
    tmdb_id = _int(row.get("tmdb_id"))
    title = row.get("title", "")
    if tmdb_id is not None:
        movie = session.exec(select(Movie).where(Movie.tmdb_id == tmdb_id)).first()
    else:
        movie = session.exec(select(Movie).where(Movie.tmdb_id.is_(None),
                                                 Movie.title == title)).first()

    # Métadonnées TMDB si le film est nouveau ou n'en a jamais eu.
    if (movie is None or movie.last_synced_at is None) and tmdb_id is not None and tmdb:
        try:
            data = tmdb.get_movie(tmdb_id)
        except ApiError as exc:
            log.warning("TMDB indisponible pour %s : %s", title, exc)
            data = None
        if data:
            movie = catalog.upsert_movie_from_tmdb(session, data)
    if movie is None:
        movie = Movie(tmdb_id=tmdb_id, title=title or "Sans titre",
                      original_title=row.get("original_title") or None,
                      year=_int(row.get("year")))
        session.add(movie)
        session.flush()
    if movie.last_synced_at is None:
        report.movies_without_metadata.append(title or movie.title)

    for watch_row in entry["watches"]:
        first = parse_datetime_utc(watch_row.get("first_watched_at"))
        last = parse_datetime_utc(watch_row.get("last_watched_at")) or first
        library.upsert_watch(session, MOVIE, movie.id, first, last,
                             _int(watch_row.get("plays")) or 1)
    if entry["watches"]:
        report.movie_watches += 1

    # Film « suivi » dans Bingers : terminé s'il a été vu, sinon à voir.
    list_status = row.get("list_status", "") if entry["in_library"] else ""
    if list_status in ("watching", "for_later", "stopped"):
        status = LIBRARY_STATUS[list_status]
    else:
        status = COMPLETED if entry["watches"] else TO_WATCH
    added_at = parse_datetime_utc(row.get("added_at")) if entry["in_library"] else None
    library.ensure_library_item(session, MOVIE, movie.id, status,
                                favorite=row.get("favorite") == "yes", added_at=added_at)
    return movie


def _find_ref(session: Session, row: dict) -> tuple[str, int] | None:
    """(kind, ref_id) d'une ligne de ratings.csv / lists.csv, ou None si introuvable."""
    kind = row.get("type")
    if kind == "movie":
        tmdb_id = _int(row.get("tmdb_id"))
        query = (select(Movie).where(Movie.tmdb_id == tmdb_id) if tmdb_id is not None
                 else select(Movie).where(Movie.title == row.get("title")))
        movie = session.exec(query).first()
        return (MOVIE, movie.id) if movie else None
    if kind not in ("show", "episode"):
        return None
    show = _find_show(session, _int(row.get("tvdb_id")), row.get("title", ""))
    if show is None:
        return None
    if kind == "show":
        return SHOW, show.id
    # Épisode : seulement si la série est rattachée normalement (numérotation fiable).
    season, number = _int(row.get("season_number")), _int(row.get("episode_number"))
    if show.match_status != MATCH_OK or not season or number is None:
        return None
    episode = session.exec(select(Episode).where(
        Episode.show_id == show.id, Episode.season == season, Episode.number == number,
        Episode.is_special == False)).first()  # noqa: E712
    return (EPISODE, episode.id) if episode else None


def _import_ratings_and_lists(session: Session, rows: dict, report: ImportReport) -> None:
    for row in rows["ratings"]:
        ref = _find_ref(session, row)
        rating = _int(row.get("rating"))
        has_content = rating is not None or row.get("emotions") or row.get("favorite_character")
        if ref is None or not has_content:
            report.ratings_skipped += 1
            continue
        kind, ref_id = ref
        existing = session.exec(select(Rating).where(Rating.kind == kind,
                                                     Rating.ref_id == ref_id)).first()
        if existing is None:  # une note déjà présente n'est pas écrasée
            session.add(Rating(kind=kind, ref_id=ref_id, rating=rating,
                               emotions=row.get("emotions") or None,
                               favorite_character=row.get("favorite_character") or None))
        report.ratings += 1

    list_names = set()
    for row in rows["lists"]:
        name = row.get("list_name", "").strip()
        if not name:
            continue
        custom = session.exec(select(CustomList).where(CustomList.name == name)).first()
        if custom is None:
            custom = CustomList(name=name, description=row.get("list_description") or None)
            session.add(custom)
            session.flush()
        list_names.add(name)
        ref = _find_ref(session, row)
        if ref is None:
            continue
        kind, ref_id = ref
        exists = session.exec(select(CustomListItem).where(
            CustomListItem.list_id == custom.id, CustomListItem.kind == kind,
            CustomListItem.ref_id == ref_id)).first()
        if exists is None:
            session.add(CustomListItem(list_id=custom.id, kind=kind, ref_id=ref_id))
    report.lists = len(list_names)


def import_bingers(source, engine: Engine, tvmaze, tmdb, progress: Progress | None = None,
                   download_posters: bool = True, posters_dir: Path = POSTERS_DIR) -> ImportReport:
    """Importe un export Bingers dans la base. Renvoie un rapport lisible.

    On enregistre (commit) après chaque série/film : une panne d'API ne fait pas tout perdre.
    """
    progress = progress or (lambda done, total, label: None)
    rows = read_bingers_zip(source)
    shows, movies = _collect(rows)
    report = ImportReport()
    total = len(shows) + len(movies) + 1
    done = 0
    log.info("Import Bingers : %s séries, %s films", len(shows), len(movies))

    with Session(engine) as session:
        for entry in shows.values():
            progress(done, total, f"Série : {entry['row'].get('title', '')}")
            show = _import_show(session, tvmaze, entry, report)
            session.commit()
            report.shows += 1
            if download_posters:
                _poster(session, show, tvmaze, posters_dir)
            done += 1

        for entry in movies.values():
            progress(done, total, f"Film : {entry['row'].get('title', '')}")
            movie = _import_movie(session, tmdb, entry, report)
            session.commit()
            report.movies += 1
            if download_posters:
                _poster(session, movie, tmdb, posters_dir)
            done += 1

        progress(done, total, "Notes et listes")
        _import_ratings_and_lists(session, rows, report)
        library.refresh_completed(session)
        session.commit()
        progress(total, total, "Terminé")
    log.info("Import Bingers terminé : %s", " | ".join(report.lines()[:7]))
    return report


def _poster(session: Session, obj, client, posters_dir: Path) -> None:
    """Télécharge l'affiche sans jamais interrompre l'import."""
    if client is None:
        return
    try:
        catalog.ensure_poster(session, obj, client, posters_dir)
        session.commit()
    except Exception as exc:  # noqa: BLE001  (une affiche ratée n'est pas grave)
        session.rollback()
        log.warning("Affiche non téléchargée pour %s : %s", obj.title, exc)


# --- Résolution manuelle (appelée depuis l'interface) ------------------------

def _pending_views(session: Session, show_id: int) -> list[EpisodeView]:
    pendings = session.exec(select(PendingWatch).where(PendingWatch.show_id == show_id)).all()
    return [EpisodeView(p.season, p.number, p.watched_at, p.last_watched_at, p.plays)
            for p in pendings]


def confirm_match(engine: Engine, tvmaze, show_id: int, tvmaze_id: int) -> str:
    """Rattache une série (incertaine ou non trouvée) à la fiche TVMaze `tvmaze_id`.

    Récupère les épisodes puis réapplique les visionnages en attente,
    avec la même vérification de numérotation qu'à l'import.
    """
    with Session(engine) as session:
        show = session.get(Show, show_id)
        if show is None:
            return "Série introuvable dans la base."
        other = session.exec(select(Show).where(Show.tvmaze_id == tvmaze_id,
                                                Show.id != show_id)).first()
        if other is not None:
            return f"Impossible : la fiche TVMaze {tvmaze_id} est déjà rattachée à « {other.title} »."
        try:
            data = tvmaze.get_show(tvmaze_id)
            episodes = (tvmaze.get_episodes_with_specials(tvmaze_id, data.get("status"))
                        if data else [])
        except ApiError:
            return "TVMaze indisponible : réessaie plus tard."
        if data is None:
            return f"Aucune série TVMaze avec l'id {tvmaze_id}."

        if show.tvmaze_id is not None and show.tvmaze_id != tvmaze_id:
            # Changement de fiche : on retire les anciens épisodes, sauf s'ils ont été vus.
            old = session.exec(select(Episode).where(Episode.show_id == show.id)).all()
            if any(library.get_watch(session, EPISODE, ep.id) for ep in old):
                return "Impossible : des épisodes de la fiche actuelle sont déjà marqués vus."
            for ep in old:
                session.delete(ep)
        show.tvmaze_id = tvmaze_id
        session.add(show)
        session.flush()
        catalog.upsert_show_from_tvmaze(session, data, episodes)
        show.match_status, show.match_note, show.candidate_tvmaze_id = MATCH_OK, None, None

        applied, pending = place_watches(session, show, _pending_views(session, show.id))
        library.refresh_completed(session)
        session.commit()
        message = f"« {show.title} » rattachée à TVMaze (« {data.get('name')} »)"
        if show.match_status == MATCH_NUMBERING:
            return (f"{message}, mais la numérotation diffère : {show.match_note}. "
                    f"{pending} visionnage(s) restent en attente.")
        message += f" : {applied} visionnage(s) appliqué(s)"
        if pending:
            message += f", {pending} épisode(s) spécial(aux) en attente"
        return message + "."


def preview_absolute_mapping(session: Session, show_id: int) -> list[tuple[PendingWatch, Episode | None]]:
    """Aperçu « par ordre absolu » : le n-ième visionnage en attente -> le n-ième épisode TVMaze.

    Les deux listes sont triées par (saison, numéro). Les spéciaux (saison 0) sont exclus.
    Episode = None s'il y a plus de visionnages que d'épisodes.
    """
    pendings = session.exec(select(PendingWatch)
                            .where(PendingWatch.show_id == show_id, PendingWatch.season != 0)
                            .order_by(PendingWatch.season, PendingWatch.number)).all()
    episodes = session.exec(select(Episode)
                            .where(Episode.show_id == show_id, Episode.is_special == False)  # noqa: E712
                            .order_by(Episode.season, Episode.number)).all()
    return [(pending, episodes[i] if i < len(episodes) else None)
            for i, pending in enumerate(pendings)]


def preview_direct_mapping(session: Session, show_id: int) -> list[tuple[PendingWatch, Episode | None]]:
    """Aperçu « numéros identiques » : SxxEyy Bingers -> même SxxEyy chez TVMaze, s'il existe.

    Utile quand seules quelques saisons diffèrent (ex. un épisode en plus dans une saison) :
    l'ordre absolu décalerait alors tous les épisodes suivants.
    """
    pendings = session.exec(select(PendingWatch)
                            .where(PendingWatch.show_id == show_id, PendingWatch.season != 0)
                            .order_by(PendingWatch.season, PendingWatch.number)).all()
    episodes = session.exec(select(Episode).where(Episode.show_id == show_id,
                                                  Episode.is_special == False)).all()  # noqa: E712
    by_number = {(ep.season, ep.number): ep for ep in episodes}
    return [(pending, by_number.get((pending.season, pending.number))) for pending in pendings]


def apply_direct_mapping(engine: Engine, show_id: int) -> str:
    """Applique la correspondance « numéros identiques » ; le reste reste en attente."""
    with Session(engine) as session:
        show = session.get(Show, show_id)
        if show is None:
            return "Série introuvable dans la base."
        pairs = preview_direct_mapping(session, show_id)
        return _apply_pairs(session, show, pairs, "numéros identiques")


def discard_pending(engine: Engine, show_id: int) -> str:
    """Supprime les visionnages en attente d'une série (choix de l'utilisateur)."""
    with Session(engine) as session:
        show = session.get(Show, show_id)
        pendings = session.exec(select(PendingWatch).where(PendingWatch.show_id == show_id)).all()
        for pending in pendings:
            session.delete(pending)
        if show is not None and show.match_status == MATCH_NUMBERING:
            show.match_status = MATCH_REMAPPED
            show.match_note = "Visionnages en attente ignorés"
            session.add(show)
        session.commit()
        title = show.title if show else "?"
        return f"« {title} » : {len(pendings)} visionnage(s) en attente ignoré(s)."


def _apply_pairs(session: Session, show: Show, pairs, method: str) -> str:
    """Crée les visionnages des paires (attente -> épisode) et marque la série recalée."""
    if not pairs:
        return f"« {show.title} » : aucun visionnage en attente à recaler."
    if all(episode is None for _, episode in pairs):
        return f"« {show.title} » : aucun épisode TVMaze correspondant."
    applied = 0
    for pending, episode in pairs:
        if episode is None:
            continue
        library.upsert_watch(session, EPISODE, episode.id, pending.watched_at,
                             pending.last_watched_at, pending.plays)
        session.delete(pending)
        applied += 1
    left = len(pairs) - applied
    show.match_status = MATCH_REMAPPED
    show.match_note = f"Correspondance « {method} » appliquée ({applied} visionnages)"
    session.add(show)
    library.refresh_completed(session)
    session.commit()
    message = f"« {show.title} » : {applied} visionnage(s) recalé(s)"
    if left:
        message += f", {left} sans épisode correspondant (toujours en attente)"
    return message + "."


def apply_absolute_mapping(engine: Engine, show_id: int) -> str:
    """Applique la correspondance par ordre absolu (voir preview_absolute_mapping)."""
    with Session(engine) as session:
        show = session.get(Show, show_id)
        if show is None:
            return "Série introuvable dans la base."
        pairs = preview_absolute_mapping(session, show_id)
        return _apply_pairs(session, show, pairs, "ordre absolu")
