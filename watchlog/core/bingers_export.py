"""Export au format Bingers : un .zip que l'application Bingers sait réimporter.

Bingers ne lit que des formats connus (le sien, TV Time, Trakt...) : l'export « base
complète » de Watchlog (un CSV par table) lui est illisible. Ce module écrit le format
d'export de Bingers lui-même, l'inverse de importer.py :
- quatre fichiers à la racine du zip : library.csv, watches.csv, ratings.csv, lists.csv,
  en UTF-8 avec BOM et fins de ligne CRLF, dates ISO UTC « 2026-08-13T17:02:42.346Z » ;
- statuts : séries « watching » (en cours ou terminée : Bingers déduit la fin des
  épisodes vus), « following » (à voir), « for_later », « stopped » ; films « following »
  (vu = ligne dans watches) ;
- épisodes : numérotation TVMaze, la même que celle de Bingers (TVDB) sauf pour les séries
  recalées à la main (signalées dans le rapport). Les visionnages encore « en attente »
  repartent avec leur numérotation Bingers d'origine. Les épisodes spéciaux sont ignorés ;
- un visionnage sans date reçoit la date d'ajout du titre (Bingers date chaque visionnage) ;
- titres saisis à la main (sans id TVDB / TMDB) : Bingers ne peut pas les retrouver, ils
  sont ignorés et listés dans le rapport.
"""

import csv
import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from sqlmodel import Session, select

from watchlog.core.dates import utcnow
from watchlog.core.models import (EPISODE, FOR_LATER, MATCH_REMAPPED, MOVIE, SHOW, STOPPED,
                                  TO_WATCH, CustomList, CustomListItem, Episode, LibraryItem,
                                  Movie, PendingWatch, Rating, Show, Watch)

HEADERS = {
    "library": ["type", "title", "original_title", "year", "tvdb_id", "tmdb_id", "favorite",
                "list_status", "added_at", "for_later_at", "stopped_watching_at", "hidden_at"],
    "watches": ["type", "title", "tvdb_id", "tmdb_id", "season_number", "episode_number",
                "first_watched_at", "last_watched_at", "plays"],
    "ratings": ["type", "title", "tvdb_id", "tmdb_id", "season_number", "episode_number",
                "rating", "favorite_character", "emotions"],
    "lists": ["list_name", "list_description", "title", "type", "tvdb_id", "tmdb_id"],
}
# Autres statuts (en cours, terminée) : « watching ».
SHOW_STATUS = {TO_WATCH: "following", FOR_LATER: "for_later", STOPPED: "stopped"}


@dataclass
class BingersExport:
    shows: int = 0
    movies: int = 0
    episode_watches: int = 0
    movie_watches: int = 0
    ratings: int = 0
    list_items: int = 0
    undated: int = 0                                      # visionnages datés du jour d'ajout
    skipped: list[str] = field(default_factory=list)      # titres sans id (saisie manuelle)
    renumbered: list[str] = field(default_factory=list)   # séries recalées à la main

    def lines(self) -> list[str]:
        lines = [f"Séries : {self.shows} ({self.episode_watches} épisodes vus)",
                 f"Films : {self.movies} ({self.movie_watches} vus)",
                 f"Notes : {self.ratings}, éléments de listes : {self.list_items}"]
        if self.undated:
            lines.append(f"{self.undated} visionnage(s) sans date : datés du jour d'ajout du titre.")
        if self.renumbered:
            lines.append("Numérotation recalée à la main (épisodes à vérifier dans Bingers) : "
                         + ", ".join(self.renumbered))
        if self.skipped:
            lines.append("Ignorés (saisis à la main, inconnus de Bingers) : " + ", ".join(self.skipped))
        return lines


def iso(value: datetime | None) -> str:
    """Datetime UTC naïf -> « 2026-08-13T17:02:42.346Z » (format Bingers). None -> ""."""
    if value is None:
        return ""
    return f"{value:%Y-%m-%dT%H:%M:%S}.{value.microsecond // 1000:03d}Z"


def _text(value) -> str:
    return "" if value is None else str(value)


def _movie_year(movie: Movie) -> int | None:
    released = movie.release_date_original or movie.release_date_fr
    return movie.year or (released.year if released else None)


def build_rows(session: Session) -> tuple[dict[str, list[list[str]]], BingersExport]:
    """Lignes des quatre fichiers (sans en-tête) et rapport."""
    report = BingersExport()
    now = utcnow()
    rows: dict[str, list[list[str]]] = {name: [] for name in HEADERS}
    shows: dict[int, Show] = {}      # séries exportées, par id en base
    movies: dict[int, Movie] = {}
    added: dict[tuple[str, int], datetime] = {}

    items = session.exec(select(LibraryItem).order_by(LibraryItem.added_at.desc())).all()
    for item in items:
        obj = session.get(Show if item.kind == SHOW else Movie, item.ref_id)
        if obj is None:
            continue
        added_at = item.added_at or now
        added[(item.kind, obj.id)] = added_at
        if item.kind == SHOW:
            if not (obj.tvdb_id or obj.tmdb_id):
                report.skipped.append(obj.title)
                continue
            shows[obj.id] = obj
            status = SHOW_STATUS.get(item.status, "watching")
            year = obj.premiered.year if obj.premiered else None
            tvdb_id, tmdb_id = obj.tvdb_id, obj.tmdb_id
            if obj.match_status == MATCH_REMAPPED:
                report.renumbered.append(obj.title)
        else:
            if not obj.tmdb_id:
                report.skipped.append(obj.title)
                continue
            movies[obj.id] = obj
            status, year, tvdb_id, tmdb_id = "following", _movie_year(obj), None, obj.tmdb_id
        rows["library"].append([
            item.kind, obj.title, obj.original_title or obj.title, _text(year), _text(tvdb_id),
            _text(tmdb_id), "yes" if item.favorite else "no", status, iso(added_at),
            iso(now) if status == "for_later" else "", iso(now) if status == "stopped" else "", ""])
    report.shows, report.movies = len(shows), len(movies)

    def dates(watch, fallback: datetime) -> tuple[str, str]:
        first = watch.watched_at or watch.last_watched_at
        if first is None:
            report.undated += 1
            first = fallback
        return iso(first), iso(watch.last_watched_at or first)

    def show_ids(show: Show) -> list[str]:
        return [_text(show.tvdb_id), _text(show.tmdb_id)]

    # Épisodes vus (numérotation TVMaze), puis visionnages en attente (numérotation Bingers).
    episode_watches = session.exec(select(Watch, Episode).join(
        Episode, (Watch.kind == EPISODE) & (Watch.ref_id == Episode.id))).all()
    for watch, episode in sorted(episode_watches, key=lambda pair: (
            pair[1].show_id, pair[1].season, pair[1].number or 0)):
        show = shows.get(episode.show_id)
        if show is None or episode.is_special or episode.number is None:
            continue
        first, last = dates(watch, added[(SHOW, show.id)])
        rows["watches"].append(["episode", show.title, *show_ids(show), str(episode.season),
                                str(episode.number), first, last, str(watch.plays or 1)])
        report.episode_watches += 1
    for pending in session.exec(select(PendingWatch).order_by(
            PendingWatch.show_id, PendingWatch.season, PendingWatch.number)).all():
        show = shows.get(pending.show_id)
        if show is None or not pending.season:
            continue
        first, last = dates(pending, added[(SHOW, show.id)])
        rows["watches"].append(["episode", show.title, *show_ids(show), str(pending.season),
                                str(pending.number), first, last, str(pending.plays or 1)])
        report.episode_watches += 1
    for watch in session.exec(select(Watch).where(Watch.kind == MOVIE)).all():
        movie = movies.get(watch.ref_id)
        if movie is None:
            continue
        first, last = dates(watch, added[(MOVIE, movie.id)])
        rows["watches"].append(["movie", movie.title, "", str(movie.tmdb_id), "", "", first, last,
                                str(watch.plays or 1)])
        report.movie_watches += 1

    # Notes, émotions et personnage préféré.
    for rating in session.exec(select(Rating)).all():
        if rating.rating is None and not rating.emotions and not rating.favorite_character:
            continue
        if rating.kind == MOVIE:
            movie = movies.get(rating.ref_id)
            if movie is None:
                continue
            ref = [movie.title, "", str(movie.tmdb_id), "", ""]
        elif rating.kind == SHOW:
            show = shows.get(rating.ref_id)
            if show is None:
                continue
            ref = [show.title, *show_ids(show), "", ""]
        else:
            episode = session.get(Episode, rating.ref_id)
            show = shows.get(episode.show_id) if episode else None
            if show is None or episode.is_special or episode.number is None:
                continue
            ref = [show.title, *show_ids(show), str(episode.season), str(episode.number)]
        rows["ratings"].append([rating.kind, *ref, _text(rating.rating),
                                rating.favorite_character or "", rating.emotions or ""])
        report.ratings += 1

    # Listes personnelles.
    for custom in session.exec(select(CustomList).order_by(CustomList.name)).all():
        for entry in session.exec(select(CustomListItem).where(
                CustomListItem.list_id == custom.id)).all():
            if entry.kind == SHOW and entry.ref_id in shows:
                show = shows[entry.ref_id]
                ref = [show.title, SHOW, *show_ids(show)]
            elif entry.kind == MOVIE and entry.ref_id in movies:
                movie = movies[entry.ref_id]
                ref = [movie.title, MOVIE, "", str(movie.tmdb_id)]
            else:
                continue
            rows["lists"].append([custom.name, custom.description or "", *ref])
            report.list_items += 1
    return rows, report


def export_bingers_zip(session: Session, dest: Path | BinaryIO) -> BingersExport:
    """Écrit le .zip au format Bingers. Renvoie le rapport (à afficher)."""
    rows, report = build_rows(session)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, header in HEADERS.items():
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\r\n")
            writer.writerow(header)
            writer.writerows(rows[name])
            archive.writestr(f"{name}.csv", buffer.getvalue().encode("utf-8-sig"))
    return report
