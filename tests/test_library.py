"""Tests des actions de bibliothèque et de l'écriture du catalogue."""

from datetime import date, datetime

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from conftest import FakeTMDB, FakeTVMaze, load_json
from watchlog.core import catalog, library
from watchlog.core.dates import local_day_to_utc
from watchlog.core.db import make_engine
from watchlog.core.models import (COMPLETED, EPISODE, FOR_LATER, MATCH_UNCERTAIN, MOVIE, SHOW,
                                  STOPPED, TO_WATCH, WATCHING, Episode, LibraryItem, Movie, Show, Watch)


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


def _status(session, show) -> str:
    return library.get_library_item(session, SHOW, show.id).status


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


def test_watched_movie_becomes_completed(session):
    """Vu d'où qu'on le marque (À regarder, Accueil, fiche) : le film passe en « Terminé »."""
    for movie_id, status in ((1, TO_WATCH), (2, WATCHING), (3, STOPPED)):
        library.set_status(session, MOVIE, movie_id, status)
    library.mark_watched(session, MOVIE, 1)
    library.set_watch_date(session, MOVIE, 2, None)     # vu, date inconnue
    library.add_rewatch(session, MOVIE, 3)              # pas encore vu : premier visionnage
    library.mark_watched(session, MOVIE, 4)             # hors bibliothèque : pas ajouté
    for movie_id in (1, 2, 3):
        assert library.get_library_item(session, MOVIE, movie_id).status == COMPLETED
    assert library.get_library_item(session, MOVIE, 4) is None


def test_watched_movie_keeps_status_chosen_later(session):
    """Déjà vu : changer la date ou ajouter un revisionnage ne touche pas au statut choisi."""
    library.set_status(session, MOVIE, 1, TO_WATCH)
    library.mark_watched(session, MOVIE, 1, when=datetime(2024, 5, 1))
    library.set_status(session, MOVIE, 1, WATCHING)
    library.set_watch_date(session, MOVIE, 1, date(2024, 6, 1))
    library.add_rewatch(session, MOVIE, 1)
    assert library.get_library_item(session, MOVIE, 1).status == WATCHING


def test_marking_episode_updates_show_status(session, kingdom):
    """À voir -> En cours au premier épisode vu, -> Terminé au dernier, et retour."""
    library.set_status(session, SHOW, kingdom.id, WATCHING)
    assert _status(session, kingdom) == TO_WATCH          # rien vu : jamais « En cours »
    library.mark_watched(session, EPISODE, ep(session, 1011).id)
    assert _status(session, kingdom) == WATCHING
    library.mark_season_watched(session, kingdom.id, 1)
    library.mark_season_watched(session, kingdom.id, 2)
    assert _status(session, kingdom) == COMPLETED
    library.unmark_watched(session, EPISODE, ep(session, 1022).id)
    assert _status(session, kingdom) == WATCHING
    for episode in session.exec(select(Episode).where(Episode.show_id == kingdom.id)).all():
        library.unmark_watched(session, EPISODE, episode.id)
    assert _status(session, kingdom) == TO_WATCH


def test_show_status_follows_first_watch_by_date_or_rewatch(session, kingdom):
    library.set_status(session, SHOW, kingdom.id, TO_WATCH)
    library.set_watch_date(session, EPISODE, ep(session, 1011).id, None)
    assert _status(session, kingdom) == WATCHING
    library.unmark_watched(session, EPISODE, ep(session, 1011).id)
    library.add_rewatch(session, EPISODE, ep(session, 1012).id)
    assert _status(session, kingdom) == WATCHING


def test_unwatched_movie_goes_back(session):
    """Plus vu : un film « Terminé » revient en « À voir », ou au statut d'avant (« Annuler »)."""
    library.set_status(session, MOVIE, 1, TO_WATCH)
    library.mark_watched(session, MOVIE, 1)
    library.unmark_watched(session, MOVIE, 1)
    assert library.get_library_item(session, MOVIE, 1).status == TO_WATCH

    library.set_status(session, MOVIE, 2, WATCHING)
    library.mark_watched(session, MOVIE, 2)
    library.unmark_watched(session, MOVIE, 2, back_to=WATCHING)
    assert library.get_library_item(session, MOVIE, 2).status == WATCHING

    library.set_status(session, MOVIE, 3, COMPLETED)
    library.unmark_watched(session, MOVIE, 3, back_to="inconnu")   # statut invalide : À voir
    assert library.get_library_item(session, MOVIE, 3).status == TO_WATCH

    library.set_status(session, MOVIE, 4, STOPPED)                 # pas « Terminé » : gardé
    library.unmark_watched(session, MOVIE, 4)
    assert library.get_library_item(session, MOVIE, 4).status == STOPPED


def test_complete_watched_movies(session):
    """Réparation : films vus restés « À voir » -> « Terminé » ; les autres statuts gardés."""
    statuses = {1: TO_WATCH, 2: TO_WATCH, 3: WATCHING, 4: STOPPED}
    for movie_id, status in statuses.items():
        session.add(LibraryItem(kind=MOVIE, ref_id=movie_id, status=status))
    for movie_id in (1, 3, 4):
        library.upsert_watch(session, MOVIE, movie_id, datetime(2024, 5, 1))
    session.add(LibraryItem(kind=SHOW, ref_id=1, status=TO_WATCH))     # même id, série
    library.upsert_watch(session, EPISODE, 1, datetime(2024, 5, 1))
    session.flush()
    assert library.complete_watched_movies(session) == 1
    session.flush()
    assert {movie_id: library.get_library_item(session, MOVIE, movie_id).status
            for movie_id in statuses} == {1: COMPLETED, 2: TO_WATCH, 3: WATCHING, 4: STOPPED}
    assert library.get_library_item(session, SHOW, 1).status == TO_WATCH
    assert library.complete_watched_movies(session) == 0


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

def test_add_and_remove_rewatch(session):
    library.set_watch_date(session, MOVIE, 7, date(2024, 5, 1))
    watch = library.add_rewatch(session, MOVIE, 7, date(2025, 2, 3))
    assert watch.plays == 2
    assert watch.watched_at == local_day_to_utc(date(2024, 5, 1))
    assert watch.last_watched_at == local_day_to_utc(date(2025, 2, 3))
    # Revisionnage oublié, plus ancien : compté, mais le dernier visionnage ne recule pas.
    library.add_rewatch(session, MOVIE, 7, date(2024, 12, 1))
    assert watch.plays == 3 and watch.last_watched_at == local_day_to_utc(date(2025, 2, 3))
    # Aujourd'hui (ou None) : l'heure exacte.
    library.add_rewatch(session, MOVIE, 7)
    assert watch.plays == 4 and watch.last_watched_at > datetime(2026, 1, 1)

    library.remove_rewatch(session, MOVIE, 7)
    library.remove_rewatch(session, MOVIE, 7)
    assert watch.plays == 2 and watch.last_watched_at > datetime(2026, 1, 1)
    library.remove_rewatch(session, MOVIE, 7)
    assert watch.plays == 1 and watch.last_watched_at == watch.watched_at
    library.remove_rewatch(session, MOVIE, 7)   # minimum 1 : le film reste vu
    assert watch.plays == 1
    assert library.remove_rewatch(session, MOVIE, 99) is None


def test_add_rewatch_not_watched_is_a_first_watch(session):
    watch = library.add_rewatch(session, MOVIE, 8, date(2023, 3, 4))
    assert watch.plays == 1
    assert watch.watched_at == watch.last_watched_at == local_day_to_utc(date(2023, 3, 4))


def test_season_rewatch_only_watched_episodes(session, kingdom):
    library.mark_watched(session, EPISODE, ep(session, 1011).id)
    library.mark_watched(session, EPISODE, ep(session, 1012).id)
    library.mark_watched(session, EPISODE, ep(session, 1019).id)   # spécial
    library.mark_watched(session, EPISODE, ep(session, 1021).id)   # saison 2
    session.flush()

    assert library.add_season_rewatch(session, kingdom.id, [1], date(2025, 1, 1)) == 2
    session.flush()
    plays = {tvmaze_id: library.get_watch(session, EPISODE, ep(session, tvmaze_id).id).plays
             for tvmaze_id in (1011, 1012, 1019, 1021)}
    assert plays == {1011: 2, 1012: 2, 1019: 1, 1021: 1}
    assert library.get_watch(session, EPISODE, ep(session, 1013).id) is None   # pas vu
    assert library.add_season_rewatch(session, kingdom.id, [1, 2], include_specials=True) == 4

    session.flush()
    assert library.remove_season_rewatch(session, kingdom.id, [1, 2], include_specials=True) == 4
    session.flush()
    assert library.remove_season_rewatch(session, kingdom.id, [1, 2], include_specials=True) == 2
    session.flush()
    assert library.remove_season_rewatch(session, kingdom.id, [1, 2], include_specials=True) == 0


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


def test_auto_show_status():
    assert library.auto_show_status(0, 0) == TO_WATCH
    assert library.auto_show_status(0, 5) == TO_WATCH
    assert library.auto_show_status(2, 5) == WATCHING
    assert library.auto_show_status(7, 0) == COMPLETED


def test_refresh_show_statuses_repairs_library(session, kingdom):
    """Bibliothèque mal rangée : chaque série reprend le statut de ses épisodes vus."""
    library.ensure_library_item(session, SHOW, kingdom.id, WATCHING)   # en cours, rien vu
    assert library.refresh_show_statuses(session) == 1
    assert _status(session, kingdom) == TO_WATCH

    library.upsert_watch(session, EPISODE, ep(session, 1011).id)        # import : pas de calcul
    assert library.refresh_show_statuses(session) == 1
    assert _status(session, kingdom) == WATCHING

    # S02E03 n'a pas de date de diffusion et le spécial n'est pas vu : pas bloquant.
    for tvmaze_id in (1012, 1013, 1021, 1022):
        library.upsert_watch(session, EPISODE, ep(session, tvmaze_id).id)
    library.get_library_item(session, SHOW, kingdom.id).status = TO_WATCH   # vue, « à voir »
    assert library.refresh_show_statuses(session) == 1
    assert _status(session, kingdom) == COMPLETED
    assert library.refresh_show_statuses(session) == 0   # déjà d'accord


def test_completed_show_is_watching_again_when_an_episode_airs(session, kingdom):
    kingdom.status = "Running"
    session.add(kingdom)
    library.ensure_library_item(session, SHOW, kingdom.id, WATCHING)
    library.mark_season_watched(session, kingdom.id, 1)
    # Avant la saison 2 : tout ce qui est sorti est vu -> terminée, même si la série continue
    assert library.refresh_show_statuses(session, today=date(2020, 6, 1)) == 1
    assert _status(session, kingdom) == COMPLETED
    # La saison 2 sort : de nouveau en cours
    assert library.refresh_show_statuses(session) == 1
    assert _status(session, kingdom) == WATCHING


def test_refresh_show_statuses_keeps_manual_choices(session, kingdom):
    """« Plus tard » et « Arrêté » restent, sauf série finie entièrement vue."""
    library.set_status(session, SHOW, kingdom.id, STOPPED)
    library.mark_watched(session, EPISODE, ep(session, 1011).id)
    assert _status(session, kingdom) == STOPPED
    library.set_status(session, SHOW, kingdom.id, FOR_LATER)
    assert library.refresh_show_statuses(session) == 0
    assert _status(session, kingdom) == FOR_LATER
    library.mark_season_watched(session, kingdom.id, 1)
    library.mark_season_watched(session, kingdom.id, 2)
    assert _status(session, kingdom) == COMPLETED          # série finie (Ended), tout vu

    kingdom.status = "Running"
    session.add(kingdom)
    library.set_status(session, SHOW, kingdom.id, STOPPED)
    assert library.refresh_show_statuses(session) == 0     # série qui continue : choix gardé
    assert _status(session, kingdom) == STOPPED


def test_refresh_show_statuses_skips_unchecked_match(session, kingdom):
    """Correspondance TVMaze à vérifier : visionnages en attente, statut pas touché."""
    kingdom.match_status = MATCH_UNCERTAIN
    session.add(kingdom)
    library.set_status(session, SHOW, kingdom.id, WATCHING)
    assert library.refresh_show_statuses(session) == 0
    assert _status(session, kingdom) == WATCHING


def test_manual_show_undated_episodes_count_as_released(session):
    """Série saisie à la main : épisodes sans date = disponibles (sinon « Terminé » trop tôt)."""
    show = Show(title="Ma série", is_manual=True)
    session.add(show)
    session.flush()
    episodes = [Episode(show_id=show.id, season=1, number=n) for n in (1, 2)]
    session.add_all(episodes)
    session.flush()
    library.set_status(session, SHOW, show.id, TO_WATCH)
    library.mark_watched(session, EPISODE, episodes[0].id)
    assert _status(session, show) == WATCHING
    library.mark_watched(session, EPISODE, episodes[1].id)
    assert _status(session, show) == COMPLETED


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


def test_set_rating_changes_or_clears_favorite_character(session):
    rating = library.set_rating(session, MOVIE, 2, 4, favorite_character="  Neo ")
    assert rating.favorite_character == "Neo"
    # Non précisé : gardé ; nouvelle valeur : remplacée.
    assert library.set_rating(session, MOVIE, 2, 5).favorite_character == "Neo"
    assert library.set_rating(session, MOVIE, 2, 5, favorite_character="Trinity") is rating
    assert rating.favorite_character == "Trinity"
    # Effacé (vide) : la note reste ; plus rien du tout : la ligne disparaît.
    assert library.set_rating(session, MOVIE, 2, 5, favorite_character=" ").favorite_character \
        is None
    library.set_rating(session, MOVIE, 2, None, favorite_character="Morpheus")
    assert library.set_rating(session, MOVIE, 2, None, favorite_character=None) is None


def test_set_rating_value_keeps_the_rest_of_the_review(session):
    library.set_rating(session, EPISODE, 5, 2, note="Lent", emotions=["bored"],
                       favorite_character="Gus")
    rating = library.set_rating_value(session, EPISODE, 5, 4)
    assert (rating.rating, rating.note, rating.emotions, rating.favorite_character) == (
        4, "Lent", "bored", "Gus")
    assert library.set_rating_value(session, EPISODE, 5, None).note == "Lent"
    # Seulement une note : l'enlever supprime la ligne.
    library.set_rating_value(session, EPISODE, 6, 3)
    assert library.set_rating_value(session, EPISODE, 6, None) is None
    with pytest.raises(ValueError):
        library.set_rating_value(session, EPISODE, 6, 9)


def test_set_rating_rejects_invalid_value(session):
    with pytest.raises(ValueError):
        library.set_rating(session, SHOW, 1, 6)
    with pytest.raises(ValueError):
        library.set_rating(session, SHOW, 1, 0)


def test_half_ratings(session):
    assert library.RATING_VALUES == [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5]
    rating = library.set_rating(session, MOVIE, 7, 4.5)
    assert rating.rating == 4.5
    assert library.set_rating_value(session, MOVIE, 7, 2.5).rating == 2.5
    assert library.clean_rating(4.0) == 4 and isinstance(library.clean_rating(4.0), int)
    assert library.clean_rating(None) is None
    for wrong in (4.3, 0.5, 5.5, True, "4", float("nan")):
        with pytest.raises(ValueError, match="demi-point"):
            library.clean_rating(wrong)
    with pytest.raises(ValueError):
        library.set_rating(session, MOVIE, 7, 3.25)


def test_rating_display():
    assert library.format_rating(4.5) == "4,5" and library.format_rating(4.0) == "4"
    assert library.rating_stars(4.5) == "★★★★½" and library.rating_stars(1) == "★"
    assert library.rating_stars(3.0) == "★★★"


def test_half_rating_in_a_base_from_before_half_ratings(tmp_path):
    """Base d'avant les demi-notes (colonne INTEGER) : 4,5 est relu tel quel, sans migration."""
    engine = make_engine(tmp_path / "old.db")
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE "rating"'))
        conn.execute(text('CREATE TABLE "rating" (id INTEGER PRIMARY KEY, kind VARCHAR NOT NULL, '
                          'ref_id INTEGER NOT NULL, rating INTEGER, emotions VARCHAR, '
                          'favorite_character VARCHAR, note VARCHAR, UNIQUE (kind, ref_id))'))
    with Session(engine) as s:
        library.set_rating(s, MOVIE, 1, 4.5)
        library.set_rating(s, MOVIE, 2, 4)
        s.commit()
    with Session(engine) as s:
        assert library.get_rating(s, MOVIE, 1).rating == 4.5
        assert library.get_rating(s, MOVIE, 2).rating == 4


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