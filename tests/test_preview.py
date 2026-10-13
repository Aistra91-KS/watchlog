"""Tests de l'aperçu d'un titre TMDB (page Aperçu, depuis les suggestions)."""

from datetime import date

from sqlmodel import Session

from conftest import FakeTMDB
from helpers_db import add_movie
from watchlog.core import library, preview
from watchlog.core.models import MOVIE, SHOW

MOVIE_DATA = {
    "id": 27205, "title": "Inception", "original_title": "Inception", "release_date": "2010-07-15",
    "tagline": "Votre esprit abrite la scène du crime.", "overview": "<p>Dom Cobb…</p>",
    "genres": [{"id": 28, "name": "Action"}], "poster_path": "/p.jpg", "backdrop_path": "/b.jpg",
    "vote_average": 8.4, "vote_count": 38000, "status": "Released", "runtime": 148,
    "credits": {"cast": [{"name": "Leonardo DiCaprio", "character": "Cobb", "profile_path": "/l.jpg"},
                         {"name": "Inconnu", "character": "", "profile_path": None}],
                "crew": [{"name": "Christopher Nolan", "job": "Director"},
                         {"name": "Hans Zimmer", "job": "Original Music Composer"}]},
    "videos": {"results": [
        {"site": "YouTube", "type": "Clip", "key": "clip", "iso_639_1": "fr", "official": True},
        {"site": "YouTube", "type": "Trailer", "key": "en1", "iso_639_1": "en", "official": True},
        {"site": "YouTube", "type": "Trailer", "key": "fr1", "iso_639_1": "fr", "official": False},
        {"site": "Vimeo", "type": "Trailer", "key": "v", "iso_639_1": "fr", "official": True}]},
}

SHOW_DATA = {
    "id": 1403, "name": "Marvel : Les Agents du S.H.I.E.L.D.",
    "original_name": "Marvel's Agents of S.H.I.E.L.D.", "first_air_date": "2013-09-24",
    "episode_run_time": [43], "number_of_seasons": 7, "number_of_episodes": 136, "status": "Ended",
    "networks": [{"name": "ABC"}], "created_by": [{"name": "Joss Whedon"}],
    "credits": {"cast": []}, "videos": {"results": []},
}


class DetailsTMDB(FakeTMDB):
    def details(self, media, tmdb_id):
        return {("movie", 27205): MOVIE_DATA, ("tv", 1403): SHOW_DATA}.get((media, tmdb_id))

    def recommendations(self, media, tmdb_id):
        return [{"id": 9, "title": "Interstellar", "release_date": "2014-11-05", "poster_path": "/i.jpg"},
                {"id": 10, "name": "Sans date"}]


def test_movie_preview():
    item = preview.load(DetailsTMDB(), MOVIE, 27205)

    assert item.title == "Inception" and item.year == 2010 and item.released == date(2010, 7, 15)
    assert item.overview == "Dom Cobb…" and item.genres == ["Action"]
    assert item.makers == ["Christopher Nolan"] and item.runtime == 148
    assert item.status == "Sorti"
    assert item.cast[0].name == "Leonardo DiCaprio" and item.cast[0].photo_url.endswith("/l.jpg")
    assert item.cast[1].role is None and item.cast[1].photo_url is None
    assert item.trailer_url == "https://www.youtube.com/watch?v=fr1"   # bande-annonce FR d'abord
    assert item.backdrop_url.endswith("/b.jpg")


def test_show_preview():
    item = preview.load(DetailsTMDB(), SHOW, 1403)

    assert item.original_title == "Marvel's Agents of S.H.I.E.L.D."
    assert (item.seasons, item.episodes, item.runtime) == (7, 136, 43)
    assert item.networks == ["ABC"] and item.makers == ["Joss Whedon"]
    assert item.status == "Terminée" and item.trailer_url is None and item.cast == []


def test_preview_unknown():
    assert preview.load(DetailsTMDB(), MOVIE, 1) is None


def test_pick_trailer_fallback_to_teaser():
    videos = [{"site": "YouTube", "type": "Teaser", "key": "t", "iso_639_1": "en"}]
    assert preview.pick_trailer(videos).endswith("v=t")
    assert preview.pick_trailer([]) is None


def test_similar():
    items = preview.similar(DetailsTMDB(), MOVIE, 27205)
    assert items[0] == {"id": 9, "title": "Interstellar", "year": 2014,
                        "poster_url": "https://image.tmdb.org/t/p/w342/i.jpg"}
    assert items[1]["year"] is None


def test_library_id(engine):
    with Session(engine) as session:
        movie = add_movie(session, "Inception", tmdb_id=27205)
        session.flush()
        assert preview.library_id(session, MOVIE, 27205) == movie.id
        assert preview.library_id(session, MOVIE, 1) is None
        library.remove_from_library(session, MOVIE, movie.id)
        session.flush()
        assert preview.library_id(session, MOVIE, 27205) is None   # en base mais retiré
