"""Ajout de séries et de films à la bibliothèque (recherche TVMaze / TMDB ou saisie manuelle).

Ces fonctions ne font pas de commit : l'appelant (l'interface) enregistre.
Une ApiError (API injoignable) remonte à l'appelant, qui affiche un message ; seule
search_all la rattrape, pour montrer les séries même si TMDB est en panne (et inversement).
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from itertools import zip_longest

from sqlmodel import Session, select

from watchlog.core import catalog, library
from watchlog.core.http import ApiError
from watchlog.core.models import (COMPLETED, EPISODE, MATCH_OK, MOVIE, SHOW, STATUSES, WATCHING,
                                  Episode, Movie, Show)
from watchlog.core.tmdb import TMDBClient

# Statuts de diffusion TVMaze, traduits pour l'affichage.
TVMAZE_STATUS_LABELS = {
    "Running": "En cours de diffusion",
    "Ended": "Terminée",
    "To Be Determined": "Indéterminé",
    "In Development": "En développement",
}

SUMMARY_LENGTH = 220  # longueur maximale du résumé dans les résultats de recherche

# Recherche unique quand TMDB n'est pas utilisable (les séries sont quand même cherchées).
MOVIES_UNAVAILABLE = ("Films non cherchés : TMDB n'est pas connecté. "
                      "Tu peux ajouter un film dans « Saisie manuelle ».")

log = logging.getLogger(__name__)


def tvmaze_status_label(status: str | None) -> str:
    """'Running' -> 'En cours de diffusion' (statut inconnu : renvoyé tel quel)."""
    if not status:
        return ""
    return TVMAZE_STATUS_LABELS.get(status, status)


def short_text(text: str | None, length: int = SUMMARY_LENGTH) -> str:
    """Texte sans HTML, coupé proprement à `length` caractères (avec « … »)."""
    text = catalog.clean_html(text) or ""
    if len(text) <= length:
        return text
    cut = text[:length].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;.") + "…"


def _check_status(status: str) -> None:
    """Vérifie le statut AVANT de créer quoi que ce soit en base."""
    if status not in STATUSES:
        raise ValueError(f"Statut inconnu : {status}")


def _record_movie_watch(session: Session, movie: Movie, status: str,
                        watched_on: date | None) -> None:
    """Film ajouté en « Terminé » : il compte comme vu (date choisie, sinon date inconnue).

    Un visionnage déjà enregistré n'est pas modifié.
    """
    if status == COMPLETED and library.get_watch(session, MOVIE, movie.id) is None:
        library.set_watch_date(session, MOVIE, movie.id, watched_on)


def _year(value: str | None) -> int | None:
    """'2022-03-01' -> 2022 (None si vide)."""
    try:
        return int(value[:4]) if value else None
    except ValueError:
        return None


# --- Recherche -----------------------------------------------------------------

def _local_show(session: Session, tvmaze_id: int, tvdb_id: int | None = None) -> Show | None:
    """Série déjà en base : par id TVMaze, sinon par id TVDB (série importée non rattachée)."""
    show = session.exec(select(Show).where(Show.tvmaze_id == tvmaze_id)).first()
    if show is None and tvdb_id is not None:
        show = session.exec(select(Show).where(Show.tvdb_id == tvdb_id,
                                               Show.tvmaze_id.is_(None))).first()
    return show


def search_shows(session: Session, tvmaze, query: str) -> list[dict]:
    """Recherche de séries sur TVMaze. Renvoie une liste de dicts simples :

    id (TVMaze), title, original_title, year, network, status (traduit), poster_url,
    summary (court), local_id (id en base ou None), in_library (bool).
    """
    query = (query or "").strip()
    if not query:
        return []
    results = []
    for result in tvmaze.search(query):
        data = result.get("show") or {}
        if "id" not in data:
            continue
        externals = data.get("externals") or {}
        local = _local_show(session, data["id"], externals.get("thetvdb"))
        channel = data.get("network") or data.get("webChannel") or {}
        results.append({
            "id": data["id"],
            "title": data.get("name") or "Sans titre",
            "original_title": data.get("name"),
            "year": _year(data.get("premiered")),
            "network": channel.get("name"),
            "status": tvmaze_status_label(data.get("status")),
            "poster_url": (data.get("image") or {}).get("medium"),
            "summary": short_text(data.get("summary")),
            "local_id": local.id if local else None,
            "in_library": bool(local and library.get_library_item(session, SHOW, local.id)),
        })
    return results


def search_movies(session: Session, tmdb, query: str) -> list[dict]:
    """Recherche de films sur TMDB (liste vide si TMDB n'est pas connecté).

    Mêmes clés que search_shows (network et status valent None pour un film).
    """
    query = (query or "").strip()
    if not query:
        return []
    results = []
    for data in tmdb.search_movies(query):
        if "id" not in data:
            continue
        local = session.exec(select(Movie).where(Movie.tmdb_id == data["id"])).first()
        results.append({
            "id": data["id"],
            "title": data.get("title") or data.get("original_title") or "Sans titre",
            "original_title": data.get("original_title"),
            "year": _year(data.get("release_date")),
            "network": None,
            "status": None,
            "poster_url": TMDBClient.poster_url(data.get("poster_path")),
            "summary": short_text(data.get("overview")),
            "local_id": local.id if local else None,
            "in_library": bool(local and library.get_library_item(session, MOVIE, local.id)),
        })
    return results


@dataclass
class SearchOutcome:
    """Résultat de la recherche unique : séries et films mêlés, et les sources en panne.

    results : dicts de search_shows / search_movies, avec en plus « kind » (SHOW ou MOVIE).
    errors : type (SHOW / MOVIE) -> message lisible, pour la source qui n'a pas répondu.
    """
    results: list[dict] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def search_all(session: Session, tvmaze, tmdb, query: str) -> SearchOutcome:
    """Cherche à la fois les séries (TVMaze) et les films (TMDB).

    Une source en panne n'empêche pas l'autre : son erreur est journalisée et rendue dans
    `errors`. `tmdb` None ou non connecté : films non cherchés (dit dans `errors`).
    Les deux listes sont alternées, pour garder l'ordre de pertinence propre à chaque API.
    """
    outcome = SearchOutcome()
    if not (query or "").strip():
        return outcome
    shows: list[dict] = []
    movies: list[dict] = []
    try:
        shows = search_shows(session, tvmaze, query)
    except ApiError as exc:
        log.warning("Recherche TVMaze impossible pour « %s » : %s", query, exc)
        outcome.errors[SHOW] = f"Séries non cherchées : {exc}"
    if tmdb is None or not tmdb.available:
        outcome.errors[MOVIE] = MOVIES_UNAVAILABLE
    else:
        try:
            movies = search_movies(session, tmdb, query)
        except ApiError as exc:
            log.warning("Recherche TMDB impossible pour « %s » : %s", query, exc)
            outcome.errors[MOVIE] = f"Films non cherchés : {exc}"
    for show, movie in zip_longest(shows, movies):
        outcome.results += [dict(r, kind=k) for r, k in ((show, SHOW), (movie, MOVIE)) if r]
    return outcome


# --- Ajout depuis TVMaze / TMDB ------------------------------------------------

def french_title(tvmaze, tvmaze_id: int) -> str | None:
    """Titre français de la série (titres alternatifs TVMaze, pays FR), sinon None."""
    try:
        akas = tvmaze.get_akas(tvmaze_id)
    except ApiError:
        return None  # le titre français est un bonus : on se contente du nom TVMaze
    for aka in akas:
        if (aka.get("country") or {}).get("code") == "FR" and aka.get("name"):
            return aka["name"]
    return None


def add_show(session: Session, tvmaze, tvmaze_id: int, status: str) -> Show:
    """Ajoute une série TVMaze (fiche + épisodes) à la bibliothèque avec ce statut.

    Si la série est déjà en base, elle n'est pas dupliquée : on change seulement son statut.
    Une série importée mais pas encore rattachée (même id TVDB) est réutilisée telle quelle :
    son rattachement se fait dans Paramètres (pour reprendre les visionnages en attente).
    """
    _check_status(status)
    show = _local_show(session, tvmaze_id)
    if show is None:
        data = tvmaze.get_show(tvmaze_id)
        if data is None:
            raise ValueError(f"Série introuvable sur TVMaze (id {tvmaze_id}).")
        show = _local_show(session, tvmaze_id, (data.get("externals") or {}).get("thetvdb"))
        if show is None:
            name = data.get("name") or "Sans titre"
            title = french_title(tvmaze, tvmaze_id) or name
            show = catalog.fetch_and_upsert_show(session, tvmaze, tvmaze_id,
                                                 title=title, original_title=name)
            if show is None:
                raise ValueError(f"Série introuvable sur TVMaze (id {tvmaze_id}).")
    library.set_status(session, SHOW, show.id, status)
    session.flush()
    return show


def add_movie(session: Session, tmdb, tmdb_id: int, status: str,
              watched_on: date | None = None) -> Movie:
    """Ajoute un film TMDB à la bibliothèque (met à jour sa fiche s'il est déjà en base).

    Statut « Terminé » : le film est aussi marqué vu, à la date `watched_on` (None : inconnue).
    """
    _check_status(status)
    data = tmdb.get_movie(tmdb_id)
    if data is None:
        raise ValueError(f"Film introuvable sur TMDB (id {tmdb_id}), ou TMDB non connecté.")
    movie = catalog.upsert_movie_from_tmdb(session, data)
    library.set_status(session, MOVIE, movie.id, status)
    _record_movie_watch(session, movie, status, watched_on)
    session.flush()
    return movie


# --- « J'en suis à S2E5 » (série déjà commencée) --------------------------------

def _regular_episodes(session: Session, show_id: int) -> list[Episode]:
    """Épisodes numérotés (hors spéciaux) de la série, dans l'ordre (saison, numéro)."""
    return list(session.exec(
        select(Episode).where(Episode.show_id == show_id,
                              Episode.is_special == False,  # noqa: E712 (syntaxe SQLAlchemy)
                              Episode.number.is_not(None))
        .order_by(Episode.season, Episode.number)))


def episode_choices(session: Session, show_id: int,
                    today: date | None = None) -> dict[int, list[tuple[int, str | None]]]:
    """Saison -> [(numéro, titre)] des épisodes qu'on peut avoir vus : pour « j'en suis à ».

    Hors spéciaux et hors épisodes pas encore diffusés (date inconnue : gardé).
    """
    today = today or date.today()
    choices: dict[int, list[tuple[int, str | None]]] = {}
    for ep in _regular_episodes(session, show_id):
        if ep.airdate is None or ep.airdate <= today:
            choices.setdefault(ep.season, []).append((ep.number, ep.title))
    return choices


def mark_progress(session: Session, show_id: int, season: int, number: int,
                  day: date | None = None, status: str | None = WATCHING) -> int:
    """« J'en suis à S2E5 » : marque vus tous les épisodes jusqu'à celui-ci inclus.

    Hors spéciaux (leur place dans l'ordre est incertaine). Date `day` pour tous ; None =
    date inconnue, car ils ont été vus dans le passé et la date du jour fausserait les
    statistiques. Un épisode déjà vu garde sa date. `status` (« En cours » par défaut,
    None : inchangé) est aussi appliqué. Renvoie le nombre d'épisodes nouvellement vus.
    ValueError si l'épisode n'existe pas. Pas de commit.
    """
    if status is not None:
        _check_status(status)
    episodes = _regular_episodes(session, show_id)
    if not any((ep.season, ep.number) == (season, number) for ep in episodes):
        raise ValueError(f"Épisode S{season}E{number} introuvable pour cette série.")
    count = 0
    for ep in episodes:
        if (ep.season, ep.number) > (season, number):
            break
        if library.get_watch(session, EPISODE, ep.id) is None:
            library.set_watch_date(session, EPISODE, ep.id, day)
            count += 1
    if status is not None:
        library.set_status(session, SHOW, show_id, status)
    session.flush()
    return count


# --- Saisie manuelle -------------------------------------------------------------

def parse_episode_counts(text: str) -> list[int]:
    """'10, 8, 12' -> [10, 8, 12] : nombre d'épisodes de chaque saison.

    Accepte aussi les points-virgules et les espaces. ValueError si la saisie est invalide.
    """
    parts = text.replace(";", ",").replace(" ", ",").split(",")
    counts = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(f"« {part} » n'est pas un nombre d'épisodes.")
        counts.append(int(part))
    if not counts:
        raise ValueError("Indique le nombre d'épisodes de chaque saison, par exemple : 10, 8, 12.")
    if any(n < 1 for n in counts):
        raise ValueError("Chaque saison doit avoir au moins un épisode.")
    return counts


def add_manual_show(session: Session, title: str, episodes_per_season: list[int], status: str,
                    original_title: str | None = None, premiered: date | None = None) -> Show:
    """Série saisie à la main (hors TVMaze) : épisodes S1E1, S1E2... sans date ni titre."""
    _check_status(status)
    title = (title or "").strip()
    if not title:
        raise ValueError("Le titre est obligatoire.")
    if not episodes_per_season or any(n < 1 for n in episodes_per_season):
        raise ValueError("Chaque saison doit avoir au moins un épisode.")
    show = Show(title=title, original_title=(original_title or "").strip() or None,
                premiered=premiered, is_manual=True, match_status=MATCH_OK)
    session.add(show)
    session.flush()  # pour avoir show.id
    for season, count in enumerate(episodes_per_season, start=1):
        for number in range(1, count + 1):
            session.add(Episode(show_id=show.id, season=season, number=number))
    library.set_status(session, SHOW, show.id, status)
    session.flush()
    return show


def add_manual_movie(session: Session, title: str, status: str, year: int | None = None,
                     release_date: date | None = None, runtime: int | None = None,
                     overview: str | None = None, watched_on: date | None = None) -> Movie:
    """Film saisi à la main (hors TMDB). La date de sortie est considérée comme la date France.

    Statut « Terminé » : marqué vu à la date `watched_on` (None : inconnue).
    """
    _check_status(status)
    title = (title or "").strip()
    if not title:
        raise ValueError("Le titre est obligatoire.")
    if year is None and release_date is not None:
        year = release_date.year
    movie = Movie(title=title, year=year, release_date_fr=release_date, runtime=runtime or None,
                  overview=(overview or "").strip() or None, is_manual=True)
    session.add(movie)
    session.flush()
    library.set_status(session, MOVIE, movie.id, status)
    _record_movie_watch(session, movie, status, watched_on)
    session.flush()
    return movie
