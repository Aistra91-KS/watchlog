"""Fixtures communes : base temporaire, faux export Bingers, faux clients TVMaze / TMDB.

Aucun appel réseau : les faux clients renvoient les fichiers JSON de tests/fixtures/.
"""

import csv
import io
import json
import zipfile
from pathlib import Path

import pytest

from watchlog.core.db import make_engine
from watchlog.core.http import ApiError

FIXTURES = Path(__file__).parent / "fixtures"

# En-têtes réels des CSV Bingers
BINGERS_HEADERS = {
    "library": ["type", "title", "original_title", "year", "tvdb_id", "tmdb_id", "favorite",
                "list_status", "added_at", "for_later_at", "stopped_watching_at", "hidden_at"],
    "watches": ["type", "title", "tvdb_id", "tmdb_id", "season_number", "episode_number",
                "first_watched_at", "last_watched_at", "plays"],
    "ratings": ["type", "title", "tvdb_id", "tmdb_id", "season_number", "episode_number",
                "rating", "favorite_character", "emotions"],
    "lists": ["list_name", "list_description", "title", "type", "tvdb_id", "tmdb_id"],
}


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- Base --------------------------------------------------------------------

@pytest.fixture
def engine(tmp_path):
    return make_engine(tmp_path / "test.db")


# --- Faux clients ------------------------------------------------------------

class FakeTVMaze:
    """Mêmes méthodes que TVMazeClient, données lues dans tests/fixtures/tvmaze_*.json."""

    def __init__(self, tvdb_map=None, searches=None, fail=False):
        # id TVDB -> id TVMaze (les autres ids TVDB sont inconnus)
        self.tvdb_map = {1001: 101, 2002: 202} if tvdb_map is None else tvdb_map
        # titre recherché -> fichier de résultats
        self.searches = {"The Hidden One": "tvmaze_search_hidden.json"} if searches is None else searches
        self.fail = fail          # True : simule une panne (ApiError) sur tous les appels
        self.calls: list[tuple] = []
        self.downloads: list[str] = []

    def _check(self, *call):
        self.calls.append(call)
        if self.fail:
            raise ApiError("TVMaze indisponible : erreur réseau (simulée)")

    def lookup_tvdb(self, tvdb_id):
        self._check("lookup_tvdb", tvdb_id)
        tvmaze_id = self.tvdb_map.get(tvdb_id)
        if tvmaze_id is None:
            return None
        data = load_json(f"tvmaze_show_{tvmaze_id}.json")
        data.pop("_embedded", None)  # /lookup/shows ne renvoie pas l'embed
        return data

    def search(self, query):
        self._check("search", query)
        name = self.searches.get(query)
        return load_json(name) if name else []

    def get_show(self, tvmaze_id, force=False):
        self._check("get_show", tvmaze_id)
        path = FIXTURES / f"tvmaze_show_{tvmaze_id}.json"
        return load_json(path.name) if path.exists() else None

    def get_episodes_with_specials(self, tvmaze_id, status, force=False):
        self._check("get_episodes_with_specials", tvmaze_id)
        path = FIXTURES / f"tvmaze_episodes_{tvmaze_id}.json"
        return load_json(path.name) if path.exists() else []

    def get_akas(self, tvmaze_id):
        return []

    def updated_since(self, since="week"):
        self._check("updated_since", since)
        return {}

    def download(self, url, dest):
        self.downloads.append(url)
        dest = Path(dest)
        if dest.exists():
            return True
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-image")
        return True


class FakeTMDB:
    """Mêmes méthodes que TMDBClient, films lus dans tests/fixtures/fake_movie_<id>.json."""

    def __init__(self, available=True):
        self.available = available
        self.configured = available
        self.downloads: list[str] = []

    def check_connection(self):
        return self.available, "TMDB connecté." if self.available else "TMDB non connecté."

    def get_movie(self, tmdb_id, force=False):
        if not self.available:
            return None
        path = FIXTURES / f"fake_movie_{tmdb_id}.json"
        return load_json(path.name) if path.exists() else None

    def search_movies(self, query):
        return []

    @staticmethod
    def poster_url(poster_path):
        return f"https://image.tmdb.org/t/p/w342{poster_path}" if poster_path else None

    def download(self, url, dest):
        self.downloads.append(url)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-image")
        return True


@pytest.fixture
def tvmaze():
    return FakeTVMaze()


@pytest.fixture
def tmdb():
    return FakeTMDB()


# --- Faux export Bingers -----------------------------------------------------

def write_bingers_zip(path: Path, library=(), watches=(), ratings=(), lists=()) -> Path:
    """Écrit un zip au format Bingers à partir de listes de dicts (colonnes absentes = "")."""
    tables = {"library": library, "watches": watches, "ratings": ratings, "lists": lists}
    with zipfile.ZipFile(path, "w") as archive:
        for name, rows in tables.items():
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=BINGERS_HEADERS[name], restval="")
            writer.writeheader()
            writer.writerows(rows)
            archive.writestr(f"{name}.csv", buffer.getvalue().encode("utf-8"))
    return path


@pytest.fixture
def make_bingers_zip(tmp_path):
    """Fabrique de zips : make_bingers_zip(library=[...], watches=[...], ...) -> chemin."""
    counter = iter(range(1000))

    def make(**tables) -> Path:
        return write_bingers_zip(tmp_path / f"bingers-{next(counter)}.zip", **tables)

    return make


def _ep_watch(title, tvdb_id, season, number, when="2024-01-10T20:00:00.000Z", plays="1"):
    return {"type": "episode", "title": title, "tvdb_id": str(tvdb_id),
            "season_number": str(season), "episode_number": str(number),
            "first_watched_at": when, "last_watched_at": when, "plays": plays}


# Petit jeu de données représentatif (voir tests/test_importer.py pour les résultats attendus)
SAMPLE_LIBRARY = [
    {"type": "show", "title": "Le Royaume", "original_title": "The Kingdom", "year": "2020",
     "tvdb_id": "1001", "favorite": "yes", "list_status": "watching",
     "added_at": "2023-05-01T10:00:00.000Z"},
    {"type": "show", "title": "Lupin", "original_title": "Lupin", "year": "2021",
     "tvdb_id": "2002", "favorite": "no", "list_status": "stopped"},
    {"type": "show", "title": "Le Caché", "original_title": "The Hidden One", "year": "2022",
     "tvdb_id": "3003", "favorite": "no", "list_status": "for_later"},
    {"type": "show", "title": "Série Fantôme", "original_title": "Nowhere Show", "year": "2019",
     "tvdb_id": "4004", "favorite": "no", "list_status": "following"},
    {"type": "movie", "title": "Fight Club", "original_title": "Fight Club", "year": "1999",
     "tmdb_id": "550", "favorite": "yes", "list_status": "following"},
    {"type": "movie", "title": "Film Pas Vu", "original_title": "Unseen Movie", "year": "2024",
     "tmdb_id": "551", "favorite": "no", "list_status": "following"},
    {"type": "movie", "title": "Film Rare", "original_title": "Rare Movie", "year": "1970",
     "tmdb_id": "552", "favorite": "no", "list_status": "following"},
]

SAMPLE_WATCHES = [
    _ep_watch("Le Royaume", 1001, 1, 1, "2024-01-01T20:00:00.000Z"),
    _ep_watch("Le Royaume", 1001, 1, 2),
    _ep_watch("Le Royaume", 1001, 1, 3),
    _ep_watch("Le Royaume", 1001, 2, 1, plays="2"),
    _ep_watch("Le Royaume", 1001, 0, 1),   # spécial -> en attente
    *[_ep_watch("Lupin", 2002, 1, n) for n in range(1, 5)],  # S1E1-E4 (TVDB)
    _ep_watch("Le Caché", 3003, 1, 1),
    _ep_watch("Série Fantôme", 4004, 1, 1),
    {"type": "movie", "title": "Fight Club", "tmdb_id": "550",
     "first_watched_at": "2020-02-02T21:00:00.000Z",
     "last_watched_at": "2023-03-03T21:00:00.000Z", "plays": "2"},
    {"type": "movie", "title": "Film Rare", "tmdb_id": "552",
     "first_watched_at": "2021-06-01T21:00:00.000Z",
     "last_watched_at": "2021-06-01T21:00:00.000Z", "plays": "1"},
]

SAMPLE_RATINGS = [
    {"type": "movie", "title": "Fight Club", "tmdb_id": "550", "rating": "5",
     "favorite_character": "Tyler", "emotions": "Choqué"},
    {"type": "show", "title": "Le Royaume", "tvdb_id": "1001", "rating": "4"},
    {"type": "episode", "title": "Le Royaume", "tvdb_id": "1001", "season_number": "1",
     "episode_number": "1", "rating": "3"},
    # Lupin : numérotation incohérente -> note d'épisode non reprise
    {"type": "episode", "title": "Lupin", "tvdb_id": "2002", "season_number": "1",
     "episode_number": "3", "rating": "4"},
]

SAMPLE_LISTS = [
    {"list_name": "Coups de cœur", "list_description": "Les meilleurs", "title": "Fight Club",
     "type": "movie", "tmdb_id": "550"},
    {"list_name": "Coups de cœur", "list_description": "Les meilleurs", "title": "Le Royaume",
     "type": "show", "tvdb_id": "1001"},
]


@pytest.fixture
def sample_zip(make_bingers_zip):
    return make_bingers_zip(library=SAMPLE_LIBRARY, watches=SAMPLE_WATCHES,
                            ratings=SAMPLE_RATINGS, lists=SAMPLE_LISTS)
