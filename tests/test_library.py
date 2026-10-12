"""Tests des actions de bibliothèque et de l'écriture du catalogue."""

from datetime import date, datetime

import pytest
from sqlmodel import Session, select

from conftest import FakeTMDB, FakeTVMaze, load_json
from watchlog.core import catalog, library
from watchlog.core.models import (COMPLETED, EPISODE, MATCH_UNCERTAIN, MOVIE, SHOW, STOPPED,
                                  TO_WATCH, WATCHING, Episode, LibraryItem, Movie, Show, Watch)


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def kingdom(session) -> Show:
    """Série terminée de 2 saisons (S2E3 jamais diffusé) + 1 spécial en saison 1."""
    show = catalog.upsert_show_from_tvmaze(session, load_json("tvmaze_show_101.json"),
                                           load_json("tvmaze_episodes_101.json"),
                                           title="Le Royaume", tvdb_id=1001)
    session.commit()
    return show


def ep(session, tvmaze_episode_id) -> Episode:
    return session.exec(select(Episode).where(Episode.tvmaze_episode_id == tvmaze_episode_id)).one()


def watched_tvmaze_ids(session) -> list[int]:
    query = (select(Episode.tvmaze_episode_id).join(Watch, Watch.ref_id == Episode.id)
             .where(Watch.kind == EPISODE))
    return sorted(session.exec(query).all())


# --- catalog -------------------------------------------------------------------

def test_upsert_show_is_idempotent_and_keeps_title(session, kingdom):
    show_id = kingdom.id
    kingdom.title = "Titre personnalisé"
    session.add(kingdom)
    session.commit()
    again = catalog.upsert_show_from_tvmaze(session, load_json("tvmaze_show_101.json"),
                                            load_json("tvmaze_episodes_101.json"), title="Autre")
    session.commit()
    assert again.id == show_id
    assert again.title == "Titre personnalisé"
    assert len(session.exec(select(Show)).all()) == 1
    assert len(session.exec(select(Episode)).all()) == 7


def test_upsert_show_keeps_match_status(session, kingdom):
    kingdom.match_status = MATCH_UNCERTAIN
    session.add(kingdom)
    session.commit()
    catalog.upsert_show_from_tvmaze(session, load_json("tvmaze_show_101.json"), [])
    assert kingdom.match_status == MATCH_UNCERTAIN


def test_upsert_show_uses_embedded_episodes_when_list_empty(session):
    show = catalog.upsert_show_from_tvmaze(session, load_json("tvmaze_show_202.json"), [])
    assert show.title == "Lupin" and show.original_title == "Lupin"
    assert show.tvdb_id == 2002 and show.imdb_id == "tt2002"
    assert len(session.exec(select(Episode)).all()) == 6


def test_fetch_and_upsert_show(session):
    tvmaze = FakeTVMaze()
    show = catalog.fetch_and_upsert_show(session, tvmaze, 101, title="Le Royaume")
    assert show.title == "Le Royaume" and show.tvmaze_id == 101
    assert catalog.fetch_and_upsert_show(session, tvmaze, 999) is None


def test_upsert_movie_from_tmdb(session):
    data = load_json("fake_movie_550.json")
    movie = catalog.upsert_movie_from_tmdb(session, data)
    again = catalog.upsert_movie_from_tmdb(session, data)
    assert again.id == movie.id
    assert movie.year == 1999 and movie.release_date_fr == date(1999, 11, 10)
    assert movie.poster_url == "https://image.tmdb.org/t/p/w342/fakeposter550.jpg"


def test_ensure_poster_downloads_once(session, kingdom, tmp_path):
    tvmaze = FakeTVMaze()
    catalog.ensure_poster(session, kingdom, tvmaze, tmp_path / "posters")
    catalog.ensure_poster(session, kingdom, tvmaze, tmp_path / "posters")
    assert kingdom.poster_path == f"posters/show_{kingdom.id}.jpg"
    assert (tmp_path / "posters" / f"show_{kingdom.id}.jpg").read_bytes() == b"fake-image"
    assert len(tvmaze.downloads) == 1

    movie = Movie(title="Sans affiche")
    session.add(movie)
    session.flush()
    catalog.ensure_poster(session, movie, FakeTMDB(), tmp_path / "posters")
    assert movie.poster_path is None


# --- Visionnages ------------------------------------------------------------------

def test_upsert_watch_merges(session):
    library.upsert_watch(session, MOVIE, 1, datetime(2021, 1, 1), datetime(2021, 1, 1), 1)
    library.upsert_watch(session, MOVIE, 1, datetime(2020, 1, 1), datetime(2022, 1, 1), 3)
    library.upsert_watch(session, MOVIE, 1, datetime(2020, 6, 1), None, 2)
    watch = session.exec(select(Watch)).one()
    assert watch.watched_at == datetime(2020, 1, 1)
    assert watch.last_watched_at == datetime(2022, 1, 1)
    assert watch.plays == 3


def test_mark_and_unmark_watched(session):
    first = library.mark_watched(session, MOVIE, 7, when=datetime(2024, 5, 1))
    second = library.mark_watched(session, MOVIE, 7)
    assert first is second and first.watched_at == datetime(2024, 5, 1)
    library.unmark_watched(session, MOVIE, 7)
    library.unmark_watched(session, MOVIE, 7)  # déjà retiré : pas d'erreur
    session.flush()
    assert session.exec(select(Watch)).all() == []


def test_mark_season_watched(session, kingdom):
    assert library.mark_season_watched(session, kingdom.id, 1) == 3
    assert watched_tvmaze_ids(session) == [1011, 1012, 1013]
    # Spécial de la saison 1 en plus, sans dupliquer les autres
    assert library.mark_season_watched(session, kingdom.id, 1, include_specials=True) == 1
    assert watched_tvmaze_ids(session) == [1011, 1012, 1013, 1019]


def test_mark_watched_until_excludes_specials(session, kingdom):
    target = ep(session, 1021)  # S02E01
    assert library.mark_watched_until(session, target.id) == 4
    assert watched_tvmaze_ids(session) == [1011, 1012, 1013, 1021]
    # Relancer ne duplique rien et ne touche pas aux dates existantes
    before = session.exec(select(Watch)).all()[0].watched_at
    assert library.mark_watched_until(session, target.id) == 0
    assert len(session.exec(select(Watch)).all()) == 4
    assert session.exec(select(Watch)).all()[0].watched_at == before


def test_mark_watched_until_with_specials(session, kingdom):
    library.mark_watched_until(session, ep(session, 1022).id, include_specials=True)
    assert watched_tvmaze_ids(session) == [1011, 1012, 1013, 1019, 1021, 1022]


def test_mark_watched_until_keeps_existing(session, kingdom):
    s1e2 = ep(session, 1012)
    library.mark_watched(session, EPISODE, s1e2.id, when=datetime(2020, 1, 1))
    assert library.mark_watched_until(session, ep(session, 1013).id) == 2
    watch = library.get_watch(session, EPISODE, s1e2.id)
    assert watch.watched_at == datetime(2020, 1, 1)


# --- Statuts ------------------------------------------------------------------------

def test_ensure_library_item_does_not_modify(session):
    item = library.ensure_library_item(session, MOVIE, 3, WATCHING, favorite=True)
    same = library.ensure_library_item(session, MOVIE, 3, TO_WATCH, favorite=False)
    assert same is item and same.status == WATCHING and same.favorite is True
    assert same.added_at is not None


def test_set_status(session):
    library.set_status(session, SHOW, 5, STOPPED)
    library.set_status(session, SHOW, 5, WATCHING)
    session.flush()
    items = session.exec(select(LibraryItem)).all()
    assert len(items) == 1 and items[0].status == WATCHING
    with pytest.raises(ValueError):
        library.set_status(session, SHOW, 5, "n'importe quoi")


def test_refresh_completed(session, kingdom):
    library.ensure_library_item(session, SHOW, kingdom.id, WATCHING)
    library.mark_season_watched(session, kingdom.id, 1)
    library.mark_watched(session, EPISODE, ep(session, 1021).id)
    assert library.refresh_completed(session) == 0   # S02E02 pas vu

    # S02E03 n'a pas de date de diffusion et le spécial n'est pas vu : pas bloquant.
    library.mark_watched(session, EPISODE, ep(session, 1022).id)
    assert library.refresh_completed(session) == 1
    assert library.get_library_item(session, SHOW, kingdom.id).status == COMPLETED
    assert library.refresh_completed(session) == 0   # déjà terminée


def test_refresh_completed_ignores_running_and_future(session, kingdom):
    library.ensure_library_item(session, SHOW, kingdom.id, WATCHING)
    library.mark_season_watched(session, kingdom.id, 1)
    library.mark_season_watched(session, kingdom.id, 2)
    # Avant la diffusion de la saison 2 : tout ce qui était diffusé était vu -> terminée
    assert library.refresh_completed(session, today=date(2020, 6, 1)) == 1
    library.set_status(session, SHOW, kingdom.id, WATCHING)
    kingdom.status = "Running"
    session.add(kingdom)
    assert library.refresh_completed(session) == 0


# --- Notes, favoris, retrait ---------------------------------------------------------

def test_set_rating_create_update_and_delete(session):
    from watchlog.core.models import Rating
    rating = library.set_rating(session, MOVIE, 4, 4, note="  Très bien  ", emotions=["touched"])
    session.flush()
    assert rating.rating == 4 and rating.note == "Très bien" and rating.emotions == "touched"
    again = library.set_rating(session, MOVIE, 4, 5, emotions="scared, touched")
    assert again is rating and again.rating == 5 and again.note is None
    assert library.split_emotions(again.emotions) == ["scared", "touched"]
    assert library.get_rating(session, MOVIE, 4) is rating
    # Tout vide : la ligne est supprimée
    assert library.set_rating(session, MOVIE, 4, None, note="  ", emotions=[]) is None
    session.flush()
    assert session.exec(select(Rating)).all() == []
    # Supprimer une note qui n'existe pas : pas d'erreur
    assert library.set_rating(session, MOVIE, 9, None) is None


def test_set_rating_keeps_favorite_character(session):
    from watchlog.core.models import Rating
    session.add(Rating(kind=MOVIE, ref_id=1, rating=3, favorite_character="Tyler"))
    session.flush()
    rating = library.set_rating(session, MOVIE, 1, None)
    assert rating is not None and rating.rating is None and rating.favorite_character == "Tyler"


def test_set_rating_rejects_invalid_value(session):
    with pytest.raises(ValueError):
        library.set_rating(session, SHOW, 1, 6)
    with pytest.raises(ValueError):
        library.set_rating(session, SHOW, 1, 0)


def test_set_favorite(session):
    library.ensure_library_item(session, SHOW, 2, WATCHING)
    item = library.set_favorite(session, SHOW, 2, True)
    assert item.favorite is True and item.status == WATCHING
    assert library.set_favorite(session, SHOW, 2, False).favorite is False
    # Élément absent de la bibliothèque : il est ajouté (statut « À voir »)
    new = library.set_favorite(session, MOVIE, 8, True)
    assert new.favorite is True and new.status == TO_WATCH


def test_remove_from_library_keeps_history(session, kingdom):
    from watchlog.core.models import Rating
    library.ensure_library_item(session, SHOW, kingdom.id, WATCHING)
    library.mark_season_watched(session, kingdom.id, 1)
    library.set_rating(session, SHOW, kingdom.id, 4)
    session.commit()
    assert library.remove_from_library(session, SHOW, kingdom.id) is True
    session.commit()
    assert library.get_library_item(session, SHOW, kingdom.id) is None
    assert len(session.exec(select(Watch)).all()) == 3
    assert session.exec(select(Rating)).one().rating == 4
    assert session.get(Show, kingdom.id) is not None
    assert library.remove_from_library(session, SHOW, kingdom.id) is False