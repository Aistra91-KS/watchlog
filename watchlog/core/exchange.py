"""Échange de données avec l'app web (GitHub Pages) : un fichier JSON « watchlog ».

L'app web garde ses données dans le navigateur ; ce fichier sert à passer la bibliothèque du
PC au téléphone et retour. Les titres sont repérés par leurs identifiants publics (TVMaze,
TMDB), jamais par les id internes de la base, qui diffèrent d'un appareil à l'autre.

Fusion à l'import : les visionnages s'additionnent (jamais supprimés), les fiches manquantes
sont créées depuis le fichier (sans appel réseau), et pour un titre déjà présent le fichier
fait référence pour le statut, le favori et la note : c'est la copie la plus récente qu'on
importe. Les visionnages Bingers en attente et le cache des API ne sont pas échangés.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO

from sqlmodel import Session, select

from watchlog.core import settings
from watchlog.core.dates import parse_date, parse_datetime_utc, utcnow
from watchlog.core.library import clean_rating, plain_rating, upsert_watch
from watchlog.core.models import (EPISODE, MOVIE, SHOW, STATUSES, CustomList, CustomListItem,
                                  DismissedSuggestion, Episode, LibraryItem, Movie, Rating, Show,
                                  ThemePreference, Watch)

log = logging.getLogger(__name__)

# Identifiant du format et version (à augmenter si le format change de façon incompatible).
FORMAT = "watchlog"
VERSION = 1

# Champs de métadonnées copiés tels quels (textes et nombres).
SHOW_FIELDS = ("tvmaze_id", "tvdb_id", "tmdb_id", "imdb_id", "title", "original_title", "status",
               "network", "poster_url", "summary", "summary_fr", "genres", "is_manual",
               "match_status")
EPISODE_FIELDS = ("season", "number", "title", "title_fr", "runtime", "is_special",
                  "tvmaze_episode_id")
MOVIE_FIELDS = ("tmdb_id", "imdb_id", "title", "original_title", "year", "runtime", "poster_url",
                "overview", "genres", "is_manual")


# --- Conversions -------------------------------------------------------------

def iso_datetime(value: datetime | None) -> str | None:
    """Datetime UTC naïf -> « 2026-10-25T18:30:00Z » (None reste None)."""
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def iso_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _library(item: LibraryItem | None) -> dict | None:
    if item is None:
        return None
    return {"status": item.status, "favorite": item.favorite,
            "added_at": iso_datetime(item.added_at)}


def _watch(watch: Watch | None) -> dict | None:
    if watch is None:
        return None
    return {"watched_at": iso_datetime(watch.watched_at),
            "last_watched_at": iso_datetime(watch.last_watched_at), "plays": watch.plays}


def _rating(rating: Rating | None) -> dict | None:
    if rating is None:
        return None
    return {"rating": plain_rating(rating.rating), "emotions": rating.emotions,
            "favorite_character": rating.favorite_character, "note": rating.note}


# --- Export ------------------------------------------------------------------

def export_data(session: Session) -> dict:
    """Toute la bibliothèque au format d'échange (dictionnaire prêt pour json.dumps)."""
    def by_kind(model, kind):
        return {row.ref_id: row for row in session.exec(select(model).where(model.kind == kind))}

    library = {SHOW: by_kind(LibraryItem, SHOW), MOVIE: by_kind(LibraryItem, MOVIE)}
    watches = {EPISODE: by_kind(Watch, EPISODE), MOVIE: by_kind(Watch, MOVIE)}
    ratings = {k: by_kind(Rating, k) for k in (SHOW, EPISODE, MOVIE)}

    episodes_by_show: dict[int, list[Episode]] = {}
    for ep in session.exec(select(Episode).order_by(Episode.season, Episode.number)):
        episodes_by_show.setdefault(ep.show_id, []).append(ep)

    shows, show_refs = [], {}
    for show in session.exec(select(Show).order_by(Show.title)):
        entry = {name: getattr(show, name) for name in SHOW_FIELDS}
        entry["premiered"] = iso_date(show.premiered)
        entry["synced_at"] = iso_datetime(show.last_synced_at)
        entry["library"] = _library(library[SHOW].get(show.id))
        entry["rating"] = _rating(ratings[SHOW].get(show.id))
        entry["episodes"] = []
        for ep in episodes_by_show.get(show.id, []):
            item = {name: getattr(ep, name) for name in EPISODE_FIELDS}
            item["airdate"] = iso_date(ep.airdate)
            item["airstamp"] = iso_datetime(ep.airstamp)
            item["watch"] = _watch(watches[EPISODE].get(ep.id))
            item["rating"] = _rating(ratings[EPISODE].get(ep.id))
            entry["episodes"].append(item)
        shows.append(entry)
        show_refs[show.id] = _ref(entry)

    movies, movie_refs = [], {}
    for movie in session.exec(select(Movie).order_by(Movie.title)):
        entry = {name: getattr(movie, name) for name in MOVIE_FIELDS}
        entry["release_date_fr"] = iso_date(movie.release_date_fr)
        entry["release_date_original"] = iso_date(movie.release_date_original)
        entry["synced_at"] = iso_datetime(movie.last_synced_at)
        entry["library"] = _library(library[MOVIE].get(movie.id))
        entry["watch"] = _watch(watches[MOVIE].get(movie.id))
        entry["rating"] = _rating(ratings[MOVIE].get(movie.id))
        movies.append(entry)
        movie_refs[movie.id] = _ref(entry)

    lists = []
    for custom in session.exec(select(CustomList).order_by(CustomList.name)):
        items = []
        for row in session.exec(select(CustomListItem).where(CustomListItem.list_id == custom.id)):
            ref = (show_refs if row.kind == SHOW else movie_refs).get(row.ref_id)
            if ref is not None:
                items.append({"kind": row.kind, **ref})
        items.sort(key=lambda i: (i["kind"], i.get("title", "")))  # ordre stable d'un export à l'autre
        lists.append({"name": custom.name, "description": custom.description, "items": items})

    return {
        "format": FORMAT, "version": VERSION, "app": "python",
        "exported_at": iso_datetime(utcnow()),
        "shows": shows, "movies": movies, "lists": lists,
        "dismissed": [{"kind": d.kind, "tmdb_id": d.tmdb_id, "title": d.title,
                       "dismissed_at": iso_datetime(d.dismissed_at)}
                      for d in session.exec(select(DismissedSuggestion))],
        "theme_preferences": [{"feature": t.feature, "label": t.label, "weight": t.weight,
                               "updated_at": iso_datetime(t.updated_at)}
                              for t in session.exec(select(ThemePreference))],
        "settings": {"my_platforms": settings.my_platforms(session)},
    }


def _ref(entry: dict) -> dict:
    """Ce qui suffit à retrouver un titre dans l'autre app (listes perso)."""
    keys = ("tvmaze_id", "tmdb_id", "title", "year")
    return {k: entry[k] for k in keys if entry.get(k) is not None}


def write_json(session: Session, dest: Path | BinaryIO) -> int:
    """Écrit le fichier d'échange (UTF-8). Renvoie le nombre de titres exportés."""
    data = export_data(session)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if isinstance(dest, (str, Path)):
        Path(dest).write_bytes(raw)
    else:
        dest.write(raw)
    return len(data["shows"]) + len(data["movies"])


# --- Import ------------------------------------------------------------------

@dataclass
class ExchangeReport:
    shows_added: int = 0
    movies_added: int = 0
    episodes_added: int = 0
    watches: int = 0              # visionnages ajoutés ou complétés
    library_changes: int = 0      # statut ou favori modifié, titre ajouté à la bibliothèque
    ratings: int = 0
    lists: int = 0
    skipped: list[str] = field(default_factory=list)   # entrées illisibles (titre + raison)

    def lines(self) -> list[str]:
        lines = [
            f"Séries ajoutées : {self.shows_added}",
            f"Films ajoutés : {self.movies_added}",
            f"Épisodes ajoutés : {self.episodes_added}",
            f"Visionnages ajoutés ou complétés : {self.watches}",
            f"Statuts / favoris mis à jour : {self.library_changes}",
            f"Notes mises à jour : {self.ratings}",
            f"Listes : {self.lists}",
        ]
        if self.skipped:
            lines.append(f"Entrées ignorées : {len(self.skipped)}")
            lines.extend(f"  - {s}" for s in self.skipped)
        return lines


def read_json(source: str | Path | BinaryIO | bytes) -> dict:
    """Lit et vérifie un fichier d'échange. ValueError avec un message clair sinon."""
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    elif isinstance(source, bytes):
        raw = source
    else:
        raw = source.read()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Fichier illisible : ce n'est pas un JSON Watchlog.") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Ce fichier n'est pas un export Watchlog (format inconnu).")
    version = data.get("version")
    if not isinstance(version, int) or version > VERSION:
        raise ValueError(f"Version de fichier {version!r} non prise en charge "
                         f"(attendu : {VERSION} au plus). Mets Watchlog à jour.")
    return data


def import_data(session: Session, data: dict) -> ExchangeReport:
    """Fusionne un fichier d'échange dans la base (voir la docstring du module). Pas de commit."""
    report = ExchangeReport()
    show_ids: dict[tuple, int] = {}
    movie_ids: dict[tuple, int] = {}

    for entry in data.get("shows") or []:
        try:
            show = _import_show(session, entry, report)
            for key in _keys(entry, SHOW):
                show_ids[key] = show.id
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            _skip(report, "Série", entry, exc)

    for entry in data.get("movies") or []:
        try:
            movie = _import_movie(session, entry, report)
            for key in _keys(entry, MOVIE):
                movie_ids[key] = movie.id
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            _skip(report, "Film", entry, exc)

    session.flush()
    for entry in data.get("lists") or []:
        try:
            _import_list(session, entry, show_ids, movie_ids, report)
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            _skip(report, "Liste", {"title": entry.get("name") if isinstance(entry, dict) else None},
                  exc)
    _import_dismissed(session, data.get("dismissed") or [], report)
    _import_themes(session, data.get("theme_preferences") or [], report)
    platforms = (data.get("settings") or {}).get("my_platforms")
    if isinstance(platforms, list) and platforms and not settings.my_platforms(session):
        settings.set_my_platforms(session, platforms)
    return report


def import_json(session: Session, source) -> ExchangeReport:
    """read_json + import_data. Pas de commit."""
    return import_data(session, read_json(source))


def _check_rating(value, where: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(f"avis illisible ({where})")
    try:
        clean_rating(value.get("rating"))
    except ValueError:
        raise ValueError(f"note invalide ({where}) : {value.get('rating')!r}") from None


def _check_watch(value, where: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(f"visionnage illisible ({where})")
    plays = value.get("plays")
    if plays is not None and (not isinstance(plays, int) or plays < 1):
        raise ValueError(f"nombre de visionnages invalide ({where})")
    _check_types(value, where, instants=("watched_at", "last_watched_at"))


# Champs typés (mêmes listes que web/js/core/exchange.js)
TEXT_FIELDS = ("title", "original_title", "status", "network", "poster_url", "summary",
               "summary_fr", "genres", "overview", "imdb_id", "match_status")
INT_FIELDS = ("tvmaze_id", "tvdb_id", "tmdb_id", "year", "runtime")
DAY_FIELDS = ("premiered", "release_date_fr", "release_date_original")


def _check_types(obj: dict, where: str, text=(), ints=(), days=(), instants=()) -> None:
    for name in text:
        if obj.get(name) is not None and not isinstance(obj[name], str):
            raise ValueError(f"{name} invalide ({where})")
    for name in ints:
        value = obj.get(name)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"{name} invalide ({where})")
    for name in days:
        value = obj.get(name)
        if value is not None and (not isinstance(value, str) or len(value) != 10
                                  or parse_date(value) is None):
            raise ValueError(f"date {name} invalide ({where})")
    for name in instants:
        value = obj.get(name)
        if value is not None and (not isinstance(value, str) or parse_datetime_utc(value) is None):
            raise ValueError(f"date {name} invalide ({where})")


def validate_entry(entry, kind: str) -> None:
    """Vérifie une série ou un film AVANT toute écriture : une entrée invalide est ignorée en
    entier, jamais importée à moitié (même règle que l'app web). ValueError sinon."""
    if not isinstance(entry, dict):
        raise ValueError("entrée illisible")
    if not isinstance(entry.get("title"), str) or not entry["title"].strip():
        raise ValueError("titre manquant")
    _check_types(entry, "titre", TEXT_FIELDS, INT_FIELDS, DAY_FIELDS, ("synced_at",))
    library = entry.get("library")
    if library is not None and (not isinstance(library, dict)
                                or library.get("status") not in STATUSES):
        status = library.get("status") if isinstance(library, dict) else library
        raise ValueError(f"statut inconnu : {status!r}")
    if library is not None:
        _check_types(library, "bibliothèque", instants=("added_at",))
    _check_rating(entry.get("rating"), "titre")
    if kind == MOVIE:
        _check_watch(entry.get("watch"), "film")
        return
    episodes = entry.get("episodes")
    if episodes is not None and not isinstance(episodes, list):
        raise ValueError("épisodes illisibles")
    for item in episodes or []:
        if not isinstance(item, dict) or not isinstance(item.get("season"), int):
            raise ValueError("épisode sans saison")
        number = item.get("number")
        if number is not None and not isinstance(number, int):
            raise ValueError("numéro d'épisode invalide")
        where = f"S{item['season']}E{number if number is not None else '?'}"
        _check_types(item, where, ("title", "title_fr"), ("runtime", "tvmaze_episode_id"),
                     ("airdate",), ("airstamp",))
        _check_watch(item.get("watch"), where)
        _check_rating(item.get("rating"), where)


def _ignored(report: ExchangeReport, label: str, row) -> None:
    log.warning("Import JSON : %s : %r", label, row)
    report.skipped.append(label)


def _skip(report: ExchangeReport, label: str, entry, exc: Exception) -> None:
    title = entry.get("title") if isinstance(entry, dict) else None
    log.warning("Import JSON : %s ignoré (%s) : %s", label, title, exc)
    report.skipped.append(f"{label} « {title or '?'} » : donnée invalide ({exc})")


def _keys(entry: dict, kind: str) -> list[tuple]:
    """Clés possibles d'un titre, de la plus sûre à la moins sûre."""
    keys = []
    if kind == SHOW:
        if entry.get("tvmaze_id"):
            keys.append(("tvmaze", int(entry["tvmaze_id"])))
        if entry.get("tvdb_id"):
            keys.append(("tvdb", int(entry["tvdb_id"])))
    elif entry.get("tmdb_id"):
        keys.append(("tmdb", int(entry["tmdb_id"])))
    if isinstance(entry.get("title"), str) and entry["title"]:
        keys.append(("title", entry["title"].strip().casefold(), entry.get("year")))
    return keys


def _find_show(session: Session, entry: dict) -> Show | None:
    if entry.get("tvmaze_id"):
        show = session.exec(select(Show).where(Show.tvmaze_id == entry["tvmaze_id"])).first()
        if show:
            return show
    if entry.get("tvdb_id"):
        show = session.exec(select(Show).where(Show.tvdb_id == entry["tvdb_id"])).first()
        if show:
            return show
    if not entry.get("tvmaze_id"):  # saisie manuelle : repérée par son titre
        return session.exec(select(Show).where(Show.tvmaze_id.is_(None),
                                               Show.title == entry["title"])).first()
    return None


def _find_movie(session: Session, entry: dict) -> Movie | None:
    if entry.get("tmdb_id"):
        return session.exec(select(Movie).where(Movie.tmdb_id == entry["tmdb_id"])).first()
    return session.exec(select(Movie).where(Movie.tmdb_id.is_(None), Movie.title == entry["title"],
                                            Movie.year == entry.get("year"))).first()


def _fill(obj, entry: dict, names) -> None:
    """Complète les champs vides d'une fiche existante (ne remplace rien)."""
    for name in names:
        if getattr(obj, name) in (None, "") and entry.get(name) not in (None, ""):
            setattr(obj, name, entry[name])


def _import_show(session: Session, entry: dict, report: ExchangeReport) -> Show:
    validate_entry(entry, SHOW)
    show = _find_show(session, entry)
    if show is None:
        show = Show(**{n: entry.get(n) for n in SHOW_FIELDS if entry.get(n) is not None})
        show.premiered = parse_date(entry.get("premiered"))
        show.last_synced_at = parse_datetime_utc(entry.get("synced_at"))
        session.add(show)
        session.flush()
        report.shows_added += 1
    else:
        _fill(show, entry, SHOW_FIELDS)
        show.premiered = show.premiered or parse_date(entry.get("premiered"))
    session.add(show)
    _import_library(session, SHOW, show.id, entry.get("library"), report)
    _import_rating(session, SHOW, show.id, entry.get("rating"), report)

    existing = list(session.exec(select(Episode).where(Episode.show_id == show.id)))
    by_tvmaze = {e.tvmaze_episode_id: e for e in existing if e.tvmaze_episode_id}
    by_number = {(e.season, e.number): e for e in existing if e.number is not None}
    # Spécial sans id TVMaze (série saisie à la main) : retrouvé par saison + titre.
    by_special = {(e.season, e.title or ""): e for e in existing if e.number is None}
    for item in entry.get("episodes") or []:
        ep = by_tvmaze.get(item.get("tvmaze_episode_id")) if item.get("tvmaze_episode_id") else None
        if ep is None and item.get("number") is not None:
            ep = by_number.get((int(item["season"]), int(item["number"])))
        if ep is None and item.get("number") is None and not item.get("tvmaze_episode_id"):
            ep = by_special.get((item["season"], item.get("title") or ""))
        if ep is None:
            ep = Episode(show_id=show.id, **{n: item.get(n) for n in EPISODE_FIELDS
                                             if item.get(n) is not None})
            ep.airdate = parse_date(item.get("airdate"))
            ep.airstamp = parse_datetime_utc(item.get("airstamp"))
            session.add(ep)
            session.flush()
            report.episodes_added += 1
        _import_watch(session, EPISODE, ep.id, item.get("watch"), report)
        _import_rating(session, EPISODE, ep.id, item.get("rating"), report)
    return show


def _import_movie(session: Session, entry: dict, report: ExchangeReport) -> Movie:
    validate_entry(entry, MOVIE)
    movie = _find_movie(session, entry)
    if movie is None:
        movie = Movie(**{n: entry.get(n) for n in MOVIE_FIELDS if entry.get(n) is not None})
        movie.last_synced_at = parse_datetime_utc(entry.get("synced_at"))
        session.add(movie)
        session.flush()
        report.movies_added += 1
    else:
        _fill(movie, entry, MOVIE_FIELDS)
    movie.release_date_fr = movie.release_date_fr or parse_date(entry.get("release_date_fr"))
    movie.release_date_original = (movie.release_date_original
                                   or parse_date(entry.get("release_date_original")))
    session.add(movie)
    _import_library(session, MOVIE, movie.id, entry.get("library"), report)
    _import_watch(session, MOVIE, movie.id, entry.get("watch"), report)
    _import_rating(session, MOVIE, movie.id, entry.get("rating"), report)
    return movie


def _import_library(session: Session, kind: str, ref_id: int, value: dict | None,
                    report: ExchangeReport) -> None:
    if not value:
        return
    status = value.get("status")
    if status not in STATUSES:
        raise ValueError(f"statut inconnu : {status!r}")
    item = session.exec(select(LibraryItem).where(LibraryItem.kind == kind,
                                                  LibraryItem.ref_id == ref_id)).first()
    favorite = bool(value.get("favorite"))
    if item is None:
        # Date d'ajout inconnue dans le fichier : elle reste inconnue (pas la date du jour).
        item = LibraryItem(kind=kind, ref_id=ref_id, status=status, favorite=favorite,
                           added_at=parse_datetime_utc(value.get("added_at")))
    elif item.status == status and item.favorite == favorite:
        return
    else:
        item.status, item.favorite = status, favorite
    session.add(item)
    report.library_changes += 1


def _import_watch(session: Session, kind: str, ref_id: int, value: dict | None,
                  report: ExchangeReport) -> None:
    if not value:
        return
    before = session.exec(select(Watch).where(Watch.kind == kind, Watch.ref_id == ref_id)).first()
    snapshot = (before.watched_at, before.last_watched_at, before.plays) if before else None
    watch = upsert_watch(session, kind, ref_id, parse_datetime_utc(value.get("watched_at")),
                         parse_datetime_utc(value.get("last_watched_at")),
                         int(value.get("plays") or 1))
    if snapshot != (watch.watched_at, watch.last_watched_at, watch.plays):
        report.watches += 1


def _import_rating(session: Session, kind: str, ref_id: int, value: dict | None,
                   report: ExchangeReport) -> None:
    if not value:
        return
    try:
        rating = clean_rating(value.get("rating"))
    except ValueError:
        raise ValueError(f"note invalide : {value.get('rating')!r}") from None
    row = session.exec(select(Rating).where(Rating.kind == kind, Rating.ref_id == ref_id)).first()
    fields = {"rating": rating, "emotions": value.get("emotions"),
              "favorite_character": value.get("favorite_character"), "note": value.get("note")}
    if row is not None and all(getattr(row, k) == v for k, v in fields.items()):
        return
    row = row or Rating(kind=kind, ref_id=ref_id)
    for name, v in fields.items():
        setattr(row, name, v)
    session.add(row)
    report.ratings += 1


def _import_list(session: Session, entry: dict, show_ids: dict, movie_ids: dict,
                 report: ExchangeReport) -> None:
    name = entry.get("name") if isinstance(entry, dict) else None
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        _ignored(report, "Liste sans nom ignorée", entry)
        return
    custom = session.exec(select(CustomList).where(CustomList.name == name)).first()
    if custom is None:
        custom = CustomList(name=name, description=entry.get("description"))
        session.add(custom)
        session.flush()
        report.lists += 1
    present = {(i.kind, i.ref_id) for i in
               session.exec(select(CustomListItem).where(CustomListItem.list_id == custom.id))}
    items = entry.get("items")
    for item in items if isinstance(items, list) else []:
        kind = item.get("kind") if isinstance(item, dict) else None
        ids = show_ids if kind == SHOW else movie_ids
        ref_id = next((ids[k] for k in _keys(item, kind) if k in ids), None) if kind else None
        if ref_id is None:
            log.warning("Import JSON : élément de la liste %s introuvable : %s", name, item)
            title = item.get("title", "?") if isinstance(item, dict) else "?"
            report.skipped.append(f"Liste « {name} » : « {title} » introuvable")
        elif (kind, ref_id) not in present:
            session.add(CustomListItem(list_id=custom.id, kind=kind, ref_id=ref_id))
            present.add((kind, ref_id))


def _import_dismissed(session: Session, rows: list, report: ExchangeReport) -> None:
    for row in rows:
        if (not isinstance(row, dict) or not row.get("tmdb_id")
                or row.get("kind") not in (SHOW, MOVIE)):
            _ignored(report, "Titre écarté illisible ignoré", row)
            continue
        exists = session.exec(select(DismissedSuggestion).where(
            DismissedSuggestion.kind == row["kind"],
            DismissedSuggestion.tmdb_id == row["tmdb_id"])).first()
        if exists is None:
            session.add(DismissedSuggestion(
                kind=row["kind"], tmdb_id=row["tmdb_id"], title=row.get("title"),
                dismissed_at=parse_datetime_utc(row.get("dismissed_at")) or utcnow()))


def _import_themes(session: Session, rows: list, report: ExchangeReport) -> None:
    """Préférences de thèmes : la plus récente l'emporte."""
    for row in rows:
        if (not isinstance(row, dict) or not row.get("feature")
                or row.get("weight") not in (1, -1)):
            _ignored(report, "Préférence de thème illisible ignorée", row)
            continue
        when = parse_datetime_utc(row.get("updated_at"))
        pref = session.get(ThemePreference, row["feature"])
        if pref is not None and pref.updated_at and when and pref.updated_at >= when:
            continue
        pref = pref or ThemePreference(feature=row["feature"], label="", weight=0)
        pref.label = row.get("label") or row["feature"]
        pref.weight = row["weight"]
        pref.updated_at = when or utcnow()
        session.add(pref)
