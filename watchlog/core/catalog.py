"""Écriture en base des métadonnées venues des API (TVMaze pour les séries, TMDB pour les films).

Utilisé par l'import Bingers ET par la synchronisation.
Ces fonctions ne font pas de commit : c'est l'appelant qui décide quand enregistrer.
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlmodel import Session, select

from watchlog.core.config import POSTERS_DIR
from watchlog.core.dates import parse_date, parse_datetime_utc, utcnow
from watchlog.core.models import Episode, Movie, Show
from watchlog.core.tmdb import TMDBClient, extract_release_dates

log = logging.getLogger(__name__)


# Genres TVMaze (anglais) -> libellés français, alignés sur ceux de TMDB quand ils existent.
TVMAZE_GENRES = {
    "Drama": "Drame", "Comedy": "Comédie", "Action": "Action", "Adventure": "Aventure",
    "Science-Fiction": "Science-Fiction", "Fantasy": "Fantastique", "Crime": "Crime",
    "Thriller": "Thriller", "Horror": "Horreur", "Mystery": "Mystère", "Romance": "Romance",
    "Family": "Familial", "Children": "Jeunesse", "Anime": "Anime", "Supernatural": "Surnaturel",
    "Music": "Musique", "Medical": "Médical", "Legal": "Judiciaire", "Espionage": "Espionnage",
    "History": "Histoire", "War": "Guerre", "Western": "Western", "Sports": "Sport",
    "Food": "Cuisine", "Travel": "Voyage", "Nature": "Nature", "DIY": "Bricolage",
    "Adult": "Adulte",
}


def show_genres(data: dict) -> str | None:
    """Genres d'une fiche TVMaze, traduits : « Drame, Science-Fiction » (None si aucun)."""
    names = [TVMAZE_GENRES.get(g, g) for g in data.get("genres") or []]
    return ", ".join(names) or None


def movie_genres(data: dict) -> str | None:
    """Genres d'une fiche TMDB (déjà en français) : « Action, Comédie » (None si aucun)."""
    names = [g["name"] for g in data.get("genres") or [] if g.get("name")]
    return ", ".join(names) or None


def split_genres(genres: str | None) -> list[str]:
    return [g.strip() for g in (genres or "").split(",") if g.strip()]


def clean_html(text: str | None) -> str | None:
    """TVMaze renvoie des résumés en HTML (<p>, <b>...) : on garde le texte brut."""
    if not text:
        return None
    return re.sub(r"<[^>]+>", "", text).strip() or None


# --- Séries (TVMaze) ---------------------------------------------------------

def _find_show(session: Session, tvmaze_id: int, tvdb_id: int | None) -> Show | None:
    """Série déjà en base : par id TVMaze, sinon par id TVDB (série pas encore rattachée)."""
    show = session.exec(select(Show).where(Show.tvmaze_id == tvmaze_id)).first()
    if show is None and tvdb_id is not None:
        show = session.exec(select(Show).where(Show.tvdb_id == tvdb_id,
                                               Show.tvmaze_id.is_(None))).first()
    return show


def upsert_show_from_tvmaze(session: Session, data: dict, episodes: list[dict],
                            title: str | None = None, original_title: str | None = None,
                            tvdb_id: int | None = None, tmdb_id: int | None = None) -> Show:
    """Crée ou met à jour une série et ses épisodes à partir des réponses TVMaze.

    - data : réponse de get_show (avec `_embedded.episodes`, sans les spéciaux)
    - episodes : réponse de get_episodes_with_specials (spéciaux compris)
    Le titre (`title`) d'une série existante n'est jamais modifié.
    `match_status` n'est pas touché pour une série existante.
    """
    externals = data.get("externals") or {}
    show = _find_show(session, data["id"], tvdb_id)
    if show is None:
        show = Show(title=title or data.get("name") or "Sans titre",
                    original_title=original_title or data.get("name"))
    show.tvmaze_id = data["id"]
    if show.original_title is None:
        show.original_title = original_title or data.get("name")
    if show.tvdb_id is None:
        show.tvdb_id = tvdb_id or externals.get("thetvdb")
    if tmdb_id is not None and show.tmdb_id is None:
        show.tmdb_id = tmdb_id
    show.imdb_id = externals.get("imdb") or show.imdb_id
    show.status = data.get("status")
    # Chaîne TV classique (network) ou plateforme (webChannel, ex. Netflix).
    channel = data.get("network") or data.get("webChannel") or {}
    show.network = channel.get("name")
    show.premiered = parse_date(data.get("premiered"))
    show.poster_url = (data.get("image") or {}).get("medium") or show.poster_url
    show.summary = clean_html(data.get("summary"))
    show.genres = show_genres(data) or show.genres
    show.last_synced_at = utcnow()
    session.add(show)
    session.flush()  # pour avoir show.id

    # Épisodes : la liste « avec spéciaux » + ceux de l'embed (au cas où la liste serait vide).
    all_episodes = {ep["id"]: ep for ep in (data.get("_embedded") or {}).get("episodes", [])}
    all_episodes.update({ep["id"]: ep for ep in episodes or []})

    existing = {ep.tvmaze_episode_id: ep for ep in
                session.exec(select(Episode).where(Episode.show_id == show.id))}
    for ep_data in all_episodes.values():
        episode = existing.get(ep_data["id"])
        if episode is None:
            # L'épisode a pu changer de série chez TVMaze (rare) : on le cherche partout.
            episode = session.exec(select(Episode).where(
                Episode.tvmaze_episode_id == ep_data["id"])).first()
        if episode is None:
            episode = Episode(show_id=show.id, season=0, tvmaze_episode_id=ep_data["id"])
        number = ep_data.get("number")  # None = épisode spécial
        episode.show_id = show.id
        episode.season = ep_data.get("season") or 0
        episode.number = number
        episode.is_special = number is None
        episode.title = ep_data.get("name")
        episode.airdate = parse_date(ep_data.get("airdate"))
        episode.airstamp = parse_datetime_utc(ep_data.get("airstamp"))
        # TVMaze donne souvent la durée du créneau (pub comprise) : la durée TMDB prime.
        episode.runtime = episode.tmdb_runtime or ep_data.get("runtime")
        session.add(episode)
    session.flush()
    return show


def fetch_and_upsert_show(session: Session, tvmaze, tvmaze_id: int, force: bool = False,
                          **kwargs) -> Show | None:
    """Télécharge la fiche + les épisodes d'une série TVMaze et les enregistre.

    Renvoie None si la série n'existe pas chez TVMaze. Une ApiError remonte à l'appelant.
    `kwargs` : title, original_title, tvdb_id, tmdb_id (voir upsert_show_from_tvmaze).
    """
    data = tvmaze.get_show(tvmaze_id, force=force)
    if data is None:
        return None
    episodes = tvmaze.get_episodes_with_specials(tvmaze_id, data.get("status"), force=force)
    return upsert_show_from_tvmaze(session, data, episodes, **kwargs)


# --- Durées réelles et titres français des épisodes (TMDB) -------------------

# Écart maximal entre les dates TVMaze et TMDB d'un même épisode. Au-delà, la numérotation
# diffère sans doute (Lupin, Top Gear...) : la durée TMDB serait celle d'un autre épisode.
MAX_AIRDATE_GAP = timedelta(days=3)

# Titre que TMDB fabrique faute de traduction (« Épisode 5 », « Episode 5 ») : pas un vrai titre.
GENERIC_EPISODE_TITLE = re.compile(r"(é|e)pisod(e|io)\s*#?\s*[\d.]+|ep\.?\s*\d+|folge\s*\d+",
                                   re.IGNORECASE)


def french_episode_title(name: str | None) -> str | None:
    """Titre TMDB (fr-FR) utilisable, ou None s'il est vide ou générique (« Épisode 5 »)."""
    name = (name or "").strip()
    if not name or GENERIC_EPISODE_TITLE.fullmatch(name):
        return None
    return name


@dataclass
class EpisodesResult:
    runtimes: int = 0         # épisodes dont la durée a changé
    titles: int = 0           # épisodes dont le titre français est nouveau ou a changé
    no_title: int = 0         # épisodes retrouvés sans titre français sur TMDB (anglais gardé)
    unmatched: int = 0        # épisodes laissés aux données TVMaze (date TMDB différente)
    missing_seasons: int = 0  # saisons absentes de TMDB


def apply_tmdb_episodes(session: Session, tmdb, show: Show) -> EpisodesResult:
    """Complète les épisodes avec TMDB : durée réelle et titre français.

    TVMaze donne pour les séries des chaînes américaines la durée du créneau de diffusion
    (60 min pour un épisode de 42 min), ce qui gonfle les statistiques, et des titres en
    anglais. TMDB donne la durée réelle et le titre traduit, dans la même réponse.
    Correspondance par saison + numéro, vérifiée par la date de diffusion : sans date des
    deux côtés ou avec un écart, les données TVMaze restent (et c'est compté).
    Le titre TVMaze n'est jamais remplacé : le français va dans `title_fr`.
    Pas de commit. Une ApiError remonte à l'appelant.
    """
    result = EpisodesResult()
    seasons: dict[int, list[Episode]] = defaultdict(list)
    for episode in session.exec(select(Episode).where(Episode.show_id == show.id,
                                                      Episode.number.is_not(None),
                                                      Episode.season > 0)):
        seasons[episode.season].append(episode)
    for season, episodes in sorted(seasons.items()):
        data = tmdb.tv_season(show.tmdb_id, season)
        if data is None:
            log.warning("Durées TMDB : saison %s de %s (TMDB %s) introuvable",
                        season, show.title, show.tmdb_id)
            result.missing_seasons += 1
            continue
        tmdb_episodes = {ep.get("episode_number"): ep for ep in data.get("episodes") or []}
        for episode in episodes:
            tmdb_ep = tmdb_episodes.get(episode.number) or {}
            runtime = tmdb_ep.get("runtime")
            title = french_episode_title(tmdb_ep.get("name"))
            if not runtime and title is None and episode.title_fr is None:
                continue  # TMDB ne sait rien de plus : les données TVMaze restent
            tmdb_date = parse_date(tmdb_ep.get("air_date"))
            if (episode.airdate is None or tmdb_date is None
                    or abs(tmdb_date - episode.airdate) > MAX_AIRDATE_GAP):
                log.warning("Épisodes TMDB : %s S%02dE%02d écarté (date TVMaze %s, TMDB %s)",
                            show.title, season, episode.number, episode.airdate, tmdb_date)
                result.unmatched += 1
                continue
            if runtime:
                episode.tmdb_runtime = runtime
                if episode.runtime != runtime:
                    episode.runtime = runtime
                    result.runtimes += 1
            if title is None:
                result.no_title += 1
            elif title != episode.title_fr:
                result.titles += 1
            episode.title_fr = title   # None efface une traduction retirée de TMDB
            session.add(episode)
    now = utcnow()
    show.runtimes_checked_at = now
    show.texts_fr_checked_at = now
    session.add(show)
    # Pas de flush : il ouvrirait une écriture, et le cache d'API (autre connexion) attendrait
    # la fin de la transaction pour enregistrer la réponse suivante (« database is locked »).
    return result


def apply_tmdb_summary(session: Session, tmdb, show: Show) -> bool:
    """Lit le résumé français de la série sur TMDB (`summary_fr`, celui de TVMaze reste).

    Renvoie True si TMDB a un résumé français. Série inconnue de TMDB : False, et c'est
    journalisé. Pas de commit. Une ApiError remonte à l'appelant.
    """
    data = tmdb.details("tv", show.tmdb_id)
    if data is None:
        log.warning("Résumé TMDB : %s (TMDB %s) introuvable", show.title, show.tmdb_id)
        return False
    show.summary_fr = (data.get("overview") or "").strip() or None
    session.add(show)
    return show.summary_fr is not None


# --- Films (TMDB) ------------------------------------------------------------

def upsert_movie_from_tmdb(session: Session, data: dict) -> Movie:
    """Crée ou met à jour un film à partir de la fiche TMDB (get_movie)."""
    movie = session.exec(select(Movie).where(Movie.tmdb_id == data["id"])).first()
    if movie is None:
        movie = Movie(tmdb_id=data["id"], title=data.get("title") or "Sans titre")
    release_fr, release_original = extract_release_dates(data)
    movie.original_title = data.get("original_title") or movie.original_title
    movie.imdb_id = data.get("imdb_id") or movie.imdb_id
    movie.release_date_fr = release_fr
    movie.release_date_original = release_original
    if release_original:
        movie.year = release_original.year
    movie.runtime = data.get("runtime") or movie.runtime
    movie.overview = data.get("overview") or movie.overview
    movie.genres = movie_genres(data) or movie.genres
    movie.poster_url = TMDBClient.poster_url(data.get("poster_path")) or movie.poster_url
    movie.last_synced_at = utcnow()
    session.add(movie)
    session.flush()
    return movie


# --- Affiches ----------------------------------------------------------------

def ensure_poster(session: Session, obj: Show | Movie, client,
                  posters_dir: Path = POSTERS_DIR) -> None:
    """Télécharge l'affiche une seule fois dans posters_dir (show_<id>.jpg / movie_<id>.jpg).

    `poster_path` contient le chemin relatif au dossier data/ (ex. "posters/show_3.jpg").
    """
    if not obj.poster_url or obj.id is None:
        return
    prefix = "show" if isinstance(obj, Show) else "movie"
    dest = Path(posters_dir) / f"{prefix}_{obj.id}.jpg"
    if obj.poster_path and dest.exists():
        return
    if client.download(obj.poster_url, dest):
        obj.poster_path = f"posters/{dest.name}"
        session.add(obj)


# --- Genres des titres importés avant leur ajout ---------------------------------

def backfill_genres(session: Session, tvmaze, tmdb) -> int:
    """Complète les genres manquants à partir des réponses déjà en cache (aucun appel réseau).

    Les titres sans réponse en cache seront complétés à la prochaine synchronisation.
    Pas de commit. Renvoie le nombre de titres complétés.
    """
    count = 0
    for show in session.exec(select(Show).where(Show.genres.is_(None),
                                                Show.tvmaze_id.is_not(None))).all():
        data = tvmaze.http.peek(f"/shows/{show.tvmaze_id}",
                                [("embed[]", "seasons"), ("embed[]", "episodes")])
        if data and show_genres(data):
            show.genres = show_genres(data)
            session.add(show)
            count += 1
    for movie in session.exec(select(Movie).where(Movie.genres.is_(None),
                                                  Movie.tmdb_id.is_not(None))).all():
        data = tmdb.http.peek(f"/movie/{movie.tmdb_id}",
                              {"language": "fr-FR", "append_to_response": "release_dates"})
        if data and movie_genres(data):
            movie.genres = movie_genres(data)
            session.add(movie)
            count += 1
    return count
