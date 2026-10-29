"""Tests de l'import Bingers (faux clients, aucun appel réseau)."""

from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel, func, select

from conftest import SAMPLE_LIBRARY, FakeTMDB, FakeTVMaze
from watchlog.core.importer import (apply_absolute_mapping, apply_direct_mapping, confirm_match,
                                    discard_pending, import_bingers, preview_absolute_mapping,
                                    preview_direct_mapping, read_bingers_zip)
from watchlog.core.models import (COMPLETED, EPISODE, FOR_LATER, MATCH_NOT_FOUND,
                                  MATCH_NUMBERING, MATCH_OK, MATCH_REMAPPED, MATCH_UNCERTAIN,
                                  MOVIE, SHOW, STOPPED, TO_WATCH, WATCHING, ApiCache, CustomList,
                                  CustomListItem, Episode, LibraryItem, Movie, PendingWatch,
                                  Rating, Show, Watch)


def run_import(source, engine, tvmaze, tmdb, tmp_path, **kwargs):
    return import_bingers(source, engine, tvmaze, tmdb, posters_dir=tmp_path / "posters", **kwargs)


def count_rows(engine) -> dict[str, int]:
    """Nombre de lignes de chaque table (sauf le cache HTTP)."""
    with Session(engine) as s:
        return {table.name: s.exec(select(func.count()).select_from(table)).one()
                for table in SQLModel.metadata.sorted_tables if table.name != ApiCache.__tablename__}


def show_by_tvdb(s, tvdb_id) -> Show:
    return s.exec(select(Show).where(Show.tvdb_id == tvdb_id)).one()


def item(s, kind, ref_id) -> LibraryItem:
    return s.exec(select(LibraryItem).where(LibraryItem.kind == kind,
                                            LibraryItem.ref_id == ref_id)).one()


@pytest.fixture
def imported(sample_zip, engine, tvmaze, tmdb, tmp_path):
    return run_import(sample_zip, engine, tvmaze, tmdb, tmp_path)


# --- Lecture du zip ------------------------------------------------------------

def test_read_bingers_zip(sample_zip, make_bingers_zip):
    data = read_bingers_zip(sample_zip)
    assert set(data) == {"library", "watches", "ratings", "lists"}
    assert len(data["library"]) == 7
    assert data["library"][0]["tvdb_id"] == "1001"
    assert data["library"][4]["tvdb_id"] == ""      # film : pas d'id TVDB, chaîne vide

    only_library = make_bingers_zip(library=[{"type": "movie", "title": "X", "tmdb_id": "1"}])
    data = read_bingers_zip(only_library)
    assert data["lists"] == [] and data["watches"] == []


def test_read_bingers_zip_missing_file(tmp_path):
    import zipfile
    path = tmp_path / "partial.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("library.csv", "type,title\nshow,Test\n")
    data = read_bingers_zip(path)
    assert data["library"] == [{"type": "show", "title": "Test"}]
    assert data["watches"] == [] and data["ratings"] == [] and data["lists"] == []


# --- Import complet --------------------------------------------------------------

def test_import_counters(imported):
    report = imported
    assert report.shows == 4
    assert report.movies == 3
    assert report.episode_watches == 4      # Le Royaume S1E1-3 + S2E1
    assert report.movie_watches == 2
    assert report.ratings == 3              # la note d'épisode Lupin est ignorée
    assert report.ratings_skipped == 1
    assert report.lists == 1
    assert report.pending_watches == 7      # 1 spécial + 4 Lupin + 1 incertaine + 1 introuvable
    assert report.movies_without_metadata == ["Film Pas Vu", "Film Rare"]


def test_import_statuses_and_favorites(imported, engine):
    with Session(engine) as s:
        assert item(s, SHOW, show_by_tvdb(s, 1001).id).status == WATCHING
        assert item(s, SHOW, show_by_tvdb(s, 1001).id).favorite is True
        assert item(s, SHOW, show_by_tvdb(s, 2002).id).status == STOPPED
        assert item(s, SHOW, show_by_tvdb(s, 3003).id).status == FOR_LATER
        assert item(s, SHOW, show_by_tvdb(s, 4004).id).status == TO_WATCH  # following
        movies = {m.tmdb_id: m for m in s.exec(select(Movie))}
        assert item(s, MOVIE, movies[550].id).status == COMPLETED          # following + vu
        assert item(s, MOVIE, movies[550].id).favorite is True
        assert item(s, MOVIE, movies[551].id).status == TO_WATCH           # following, pas vu
        assert item(s, MOVIE, movies[552].id).status == COMPLETED


def test_import_show_metadata(imported, engine):
    with Session(engine) as s:
        show = show_by_tvdb(s, 1001)
        assert show.title == "Le Royaume"            # titre Bingers (FR)
        assert show.original_title == "The Kingdom"
        assert show.tvmaze_id == 101 and show.match_status == MATCH_OK
        assert show.network == "HBO" and show.status == "Ended"
        assert show.summary == "The Kingdom est une série de test."  # HTML retiré
        assert show.last_synced_at is not None
        episodes = s.exec(select(Episode).where(Episode.show_id == show.id)).all()
        assert len(episodes) == 7
        special = [e for e in episodes if e.is_special]
        assert len(special) == 1 and special[0].number is None and special[0].season == 1
        unaired = s.exec(select(Episode).where(Episode.tvmaze_episode_id == 1023)).one()
        assert unaired.airdate is None and unaired.airstamp is None
        first = s.exec(select(Episode).where(Episode.tvmaze_episode_id == 1011)).one()
        assert first.airstamp == datetime(2020, 1, 5, 20, 0)
        assert show_by_tvdb(s, 2002).network == "Netflix"   # webChannel


def test_import_watches_and_special_pending(imported, engine):
    with Session(engine) as s:
        show = show_by_tvdb(s, 1001)
        s2e1 = s.exec(select(Episode).where(Episode.tvmaze_episode_id == 1021)).one()
        watch = s.exec(select(Watch).where(Watch.kind == EPISODE, Watch.ref_id == s2e1.id)).one()
        assert watch.plays == 2
        pending = s.exec(select(PendingWatch).where(PendingWatch.show_id == show.id)).all()
        assert [(p.season, p.number, p.reason) for p in pending] == [(0, 1, "épisode spécial")]
        fight_club = s.exec(select(Movie).where(Movie.tmdb_id == 550)).one()
        movie_watch = s.exec(select(Watch).where(Watch.kind == MOVIE,
                                                 Watch.ref_id == fight_club.id)).one()
        assert movie_watch.watched_at == datetime(2020, 2, 2, 21, 0)
        assert movie_watch.last_watched_at == datetime(2023, 3, 3, 21, 0)
        assert movie_watch.plays == 2


def test_import_movie_metadata(imported, engine):
    with Session(engine) as s:
        movie = s.exec(select(Movie).where(Movie.tmdb_id == 550)).one()
        assert movie.release_date_fr.isoformat() == "1999-11-10"
        assert movie.release_date_original.isoformat() == "1999-10-15"
        assert movie.runtime == 139 and movie.last_synced_at is not None
        rare = s.exec(select(Movie).where(Movie.tmdb_id == 552)).one()
        assert rare.title == "Film Rare" and rare.original_title == "Rare Movie"
        assert rare.year == 1970 and rare.last_synced_at is None


def test_import_ratings_and_lists(imported, engine):
    with Session(engine) as s:
        ratings = {(r.kind, r.rating) for r in s.exec(select(Rating))}
        assert ratings == {(MOVIE, 5), (SHOW, 4), (EPISODE, 3)}
        movie_rating = s.exec(select(Rating).where(Rating.kind == MOVIE)).one()
        assert movie_rating.emotions == "Choqué" and movie_rating.favorite_character == "Tyler"
        custom = s.exec(select(CustomList)).one()
        assert custom.name == "Coups de cœur" and custom.description == "Les meilleurs"
        items = s.exec(select(CustomListItem)).all()
        assert {i.kind for i in items} == {MOVIE, SHOW}


def test_import_posters(imported, engine, tmp_path, tvmaze, tmdb):
    with Session(engine) as s:
        show = show_by_tvdb(s, 1001)
        assert show.poster_path == f"posters/show_{show.id}.jpg"
        assert (tmp_path / "posters" / f"show_{show.id}.jpg").exists()
        movie = s.exec(select(Movie).where(Movie.tmdb_id == 550)).one()
        assert movie.poster_path == f"posters/movie_{movie.id}.jpg"
        assert show_by_tvdb(s, 4004).poster_path is None   # pas d'affiche connue


def test_import_without_posters(sample_zip, engine, tvmaze, tmdb, tmp_path):
    run_import(sample_zip, engine, tvmaze, tmdb, tmp_path, download_posters=False)
    assert tvmaze.downloads == [] and tmdb.downloads == []
    assert not (tmp_path / "posters").exists()


def test_import_progress(sample_zip, engine, tvmaze, tmdb, tmp_path):
    calls = []
    run_import(sample_zip, engine, tvmaze, tmdb, tmp_path,
               progress=lambda done, total, label: calls.append((done, total, label)))
    assert calls[0] == (0, 8, "Série : Le Royaume")
    assert calls[-1] == (8, 8, "Terminé")


def test_import_tmdb_unavailable(sample_zip, engine, tvmaze, tmp_path):
    report = run_import(sample_zip, engine, tvmaze, FakeTMDB(available=False), tmp_path)
    assert report.movies_without_metadata == ["Fight Club", "Film Pas Vu", "Film Rare"]
    with Session(engine) as s:
        movie = s.exec(select(Movie).where(Movie.tmdb_id == 550)).one()
        assert movie.title == "Fight Club" and movie.year == 1999
        assert movie.last_synced_at is None and movie.poster_url is None


# --- Idempotence ----------------------------------------------------------------

def test_import_twice_creates_no_duplicates(sample_zip, engine, tvmaze, tmdb, tmp_path):
    run_import(sample_zip, engine, tvmaze, tmdb, tmp_path)
    first = count_rows(engine)
    report = run_import(sample_zip, engine, tvmaze, tmdb, tmp_path)
    assert count_rows(engine) == first
    assert report.shows == 4 and report.pending_watches == 7
    assert first["watch"] == 6 and first["pendingwatch"] == 7 and first["libraryitem"] == 7


def test_reimport_does_not_overwrite_library(sample_zip, engine, tvmaze, tmdb, tmp_path):
    run_import(sample_zip, engine, tvmaze, tmdb, tmp_path)
    with Session(engine) as s:
        show = show_by_tvdb(s, 1001)
        show.title = "Mon titre à moi"
        library_item = item(s, SHOW, show.id)
        library_item.status, library_item.favorite = STOPPED, False
        s.add(show)
        s.add(library_item)
        s.commit()
    run_import(sample_zip, engine, tvmaze, tmdb, tmp_path)
    with Session(engine) as s:
        show = show_by_tvdb(s, 1001)
        assert show.title == "Mon titre à moi"
        assert item(s, SHOW, show.id).status == STOPPED
        assert item(s, SHOW, show.id).favorite is False


def test_reimport_merges_watches(make_bingers_zip, engine, tvmaze, tmdb, tmp_path):
    library = [{"type": "movie", "title": "Fight Club", "tmdb_id": "550", "list_status": "following"}]
    old = {"type": "movie", "title": "Fight Club", "tmdb_id": "550", "plays": "1",
           "first_watched_at": "2020-01-01T00:00:00.000Z", "last_watched_at": "2020-01-01T00:00:00.000Z"}
    new = {**old, "plays": "3", "first_watched_at": "2021-01-01T00:00:00.000Z",
           "last_watched_at": "2024-01-01T00:00:00.000Z"}
    run_import(make_bingers_zip(library=library, watches=[old]), engine, tvmaze, tmdb, tmp_path)
    run_import(make_bingers_zip(library=library, watches=[new]), engine, tvmaze, tmdb, tmp_path)
    with Session(engine) as s:
        watch = s.exec(select(Watch)).one()
        assert watch.watched_at == datetime(2020, 1, 1)
        assert watch.last_watched_at == datetime(2024, 1, 1)
        assert watch.plays == 3


# --- Séries à problème -----------------------------------------------------------

def test_lupin_numbering_mismatch(imported, engine):
    with Session(engine) as s:
        lupin = show_by_tvdb(s, 2002)
        assert lupin.match_status == MATCH_NUMBERING
        assert lupin.match_note == ("2 épisode(s) vu(s) absent(s) chez TVMaze (ex. S01E03) ; "
                                    "4 vus / 6 chez TVMaze")
        episode_ids = [e.id for e in s.exec(select(Episode).where(Episode.show_id == lupin.id))]
        assert len(episode_ids) == 6
        assert s.exec(select(Watch).where(Watch.ref_id.in_(episode_ids),
                                          Watch.kind == EPISODE)).all() == []
        pending = s.exec(select(PendingWatch).where(PendingWatch.show_id == lupin.id)).all()
        assert len(pending) == 4
        assert all(p.reason == "numérotation différente de TVMaze" for p in pending)
    assert any(line.startswith("Lupin : 2 épisode(s)") for line in imported.numbering)


def test_too_many_watches_is_mismatch(make_bingers_zip, engine, tmdb, tmp_path):
    # Tous les numéros existent chez TVMaze... sauf qu'il y en a trop : S1E1, S1E2, S2E1, S2E2, S3E1, S3E2, S3E3
    watches = [{"type": "episode", "title": "Lupin", "tvdb_id": "2002", "season_number": str(se),
                "episode_number": str(n)} for se, n in [(1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (3, 2), (3, 3)]]
    report = run_import(make_bingers_zip(watches=watches), engine, FakeTVMaze(), tmdb, tmp_path)
    assert report.numbering == ["Lupin : 1 épisode(s) vu(s) absent(s) chez TVMaze (ex. S03E03) ; "
                                "7 vus / 6 chez TVMaze"]


def test_uncertain_show(imported, engine):
    with Session(engine) as s:
        hidden = show_by_tvdb(s, 3003)
        assert hidden.match_status == MATCH_UNCERTAIN
        assert hidden.candidate_tvmaze_id == 303
        assert hidden.tvmaze_id is None
        assert hidden.title == "Le Caché"
        assert "The Hidden One" in hidden.match_note
        assert s.exec(select(Episode).where(Episode.show_id == hidden.id)).all() == []
        pending = s.exec(select(PendingWatch).where(PendingWatch.show_id == hidden.id)).one()
        assert "confirmer" in pending.reason
    assert len(imported.uncertain) == 1


def test_not_found_show(imported, engine):
    with Session(engine) as s:
        ghost = show_by_tvdb(s, 4004)
        assert ghost.match_status == MATCH_NOT_FOUND
        assert ghost.title == "Série Fantôme" and ghost.original_title == "Nowhere Show"
        pending = s.exec(select(PendingWatch).where(PendingWatch.show_id == ghost.id)).one()
        assert pending.reason == "série non trouvée sur TVMaze"
    assert imported.not_found[0].startswith("Série Fantôme : Introuvable sur TVMaze")


def test_tvmaze_down_continues(sample_zip, engine, tmdb, tmp_path):
    report = run_import(sample_zip, engine, FakeTVMaze(fail=True), tmdb, tmp_path)
    assert report.shows == 4 and report.movies == 3
    assert report.episode_watches == 0
    assert report.pending_watches == 5 + 4 + 1 + 1
    with Session(engine) as s:
        show = show_by_tvdb(s, 1001)
        assert show.match_status == MATCH_NOT_FOUND
        assert "TVMaze indisponible" in show.match_note
        assert s.exec(select(func.count()).select_from(Movie)).one() == 3

    # TVMaze revenu : un nouvel import rattache la série, sans doublon.
    run_import(sample_zip, engine, FakeTVMaze(), tmdb, tmp_path)
    with Session(engine) as s:
        show = show_by_tvdb(s, 1001)
        assert show.match_status == MATCH_OK and show.tvmaze_id == 101
        assert s.exec(select(func.count()).select_from(Show)).one() == 4
        pending = s.exec(select(PendingWatch).where(PendingWatch.show_id == show.id)).all()
        assert [(p.season, p.number) for p in pending] == [(0, 1)]


# --- Résolution manuelle -----------------------------------------------------------

def test_confirm_match(imported, engine, tvmaze):
    with Session(engine) as s:
        show_id = show_by_tvdb(s, 3003).id
    message = confirm_match(engine, tvmaze, show_id, 303)
    assert "1 visionnage(s) appliqué(s)" in message
    with Session(engine) as s:
        show = s.get(Show, show_id)
        assert show.match_status == MATCH_OK and show.tvmaze_id == 303
        assert show.candidate_tvmaze_id is None
        assert show.title == "Le Caché"               # titre Bingers conservé
        assert s.exec(select(PendingWatch).where(PendingWatch.show_id == show_id)).all() == []
        pilot = s.exec(select(Episode).where(Episode.tvmaze_episode_id == 3031)).one()
        assert s.exec(select(Watch).where(Watch.kind == EPISODE, Watch.ref_id == pilot.id)).one()


def test_confirm_match_refusals(imported, engine, tvmaze):
    with Session(engine) as s:
        ghost_id = show_by_tvdb(s, 4004).id
    assert "déjà rattachée" in confirm_match(engine, tvmaze, ghost_id, 202)  # fiche de Lupin
    assert "Aucune série TVMaze" in confirm_match(engine, tvmaze, ghost_id, 999)
    with Session(engine) as s:
        assert s.get(Show, ghost_id).match_status == MATCH_NOT_FOUND


def test_confirm_match_with_bad_numbering(make_bingers_zip, engine, tvmaze, tmdb, tmp_path):
    watches = [{"type": "episode", "title": "Série Fantôme", "tvdb_id": "4004",
                "season_number": "5", "episode_number": "1"}]
    run_import(make_bingers_zip(watches=watches), engine, tvmaze, tmdb, tmp_path)
    with Session(engine) as s:
        ghost_id = show_by_tvdb(s, 4004).id
    message = confirm_match(engine, tvmaze, ghost_id, 303)
    assert "la numérotation diffère" in message
    with Session(engine) as s:
        assert s.get(Show, ghost_id).match_status == MATCH_NUMBERING
        assert s.exec(select(Watch)).all() == []
        assert s.exec(select(PendingWatch)).one().reason == "numérotation différente de TVMaze"


def test_confirm_match_api_down(imported, engine):
    with Session(engine) as s:
        show_id = show_by_tvdb(s, 3003).id
    assert "indisponible" in confirm_match(engine, FakeTVMaze(fail=True), show_id, 303)
    with Session(engine) as s:
        assert s.get(Show, show_id).match_status == MATCH_UNCERTAIN


def test_preview_and_apply_absolute_mapping(imported, engine, sample_zip, tvmaze, tmdb, tmp_path):
    with Session(engine) as s:
        lupin_id = show_by_tvdb(s, 2002).id
        preview = preview_absolute_mapping(s, lupin_id)
        pairs = [((p.season, p.number), (e.season, e.number)) for p, e in preview]
        assert pairs == [((1, 1), (1, 1)), ((1, 2), (1, 2)), ((1, 3), (2, 1)), ((1, 4), (2, 2))]

    message = apply_absolute_mapping(engine, lupin_id)
    assert "4 visionnage(s) recalé(s)" in message
    with Session(engine) as s:
        lupin = s.get(Show, lupin_id)
        assert lupin.match_status == MATCH_REMAPPED
        assert s.exec(select(PendingWatch).where(PendingWatch.show_id == lupin_id)).all() == []
        watched = s.exec(select(Episode).join(Watch, Watch.ref_id == Episode.id)
                         .where(Watch.kind == EPISODE, Episode.show_id == lupin_id)).all()
        assert sorted(e.tvmaze_episode_id for e in watched) == [2021, 2022, 2031, 2032]

    # Réimport : la série recalée n'est pas re-traitée, et c'est signalé.
    before = count_rows(engine)
    report = run_import(sample_zip, engine, tvmaze, tmdb, tmp_path)
    assert report.remapped_skipped == ["Lupin"]
    assert count_rows(engine) == before
    assert any("recalées à la main" in line for line in report.lines())


def test_apply_absolute_mapping_more_pending_than_episodes(make_bingers_zip, engine, tvmaze, tmdb, tmp_path):
    watches = [{"type": "episode", "title": "Lupin", "tvdb_id": "2002", "season_number": "1",
                "episode_number": str(n)} for n in range(1, 9)]
    run_import(make_bingers_zip(watches=watches), engine, tvmaze, tmdb, tmp_path)
    with Session(engine) as s:
        lupin_id = show_by_tvdb(s, 2002).id
        preview = preview_absolute_mapping(s, lupin_id)
        assert [e is None for _, e in preview] == [False] * 6 + [True] * 2
    message = apply_absolute_mapping(engine, lupin_id)
    assert "6 visionnage(s) recalé(s), 2 sans épisode" in message
    with Session(engine) as s:
        assert len(s.exec(select(PendingWatch)).all()) == 2


def test_apply_absolute_mapping_without_episodes(imported, engine):
    with Session(engine) as s:
        ghost_id = show_by_tvdb(s, 4004).id
    assert "aucun épisode TVMaze" in apply_absolute_mapping(engine, ghost_id)


def test_preview_and_apply_direct_mapping(imported, engine):
    # Numéros identiques : S01E01-E02 existent chez TVMaze, S01E03-E04 non (restent en attente).
    with Session(engine) as s:
        lupin_id = show_by_tvdb(s, 2002).id
        preview = preview_direct_mapping(s, lupin_id)
        pairs = [((p.season, p.number), (e.season, e.number) if e else None) for p, e in preview]
        assert pairs == [((1, 1), (1, 1)), ((1, 2), (1, 2)), ((1, 3), None), ((1, 4), None)]

    message = apply_direct_mapping(engine, lupin_id)
    assert "2 visionnage(s) recalé(s), 2 sans épisode" in message
    with Session(engine) as s:
        assert s.get(Show, lupin_id).match_status == MATCH_REMAPPED
        left = s.exec(select(PendingWatch).where(PendingWatch.show_id == lupin_id)).all()
        assert sorted((p.season, p.number) for p in left) == [(1, 3), (1, 4)]


def test_discard_pending(imported, engine):
    with Session(engine) as s:
        lupin_id = show_by_tvdb(s, 2002).id
    assert "4 visionnage(s) en attente ignoré(s)" in discard_pending(engine, lupin_id)
    with Session(engine) as s:
        assert s.exec(select(PendingWatch).where(PendingWatch.show_id == lupin_id)).all() == []
        assert s.get(Show, lupin_id).match_status == MATCH_REMAPPED


# --- Rapport ----------------------------------------------------------------------

def test_report_lines(imported):
    lines = imported.lines()
    assert lines[:7] == ["Séries : 4", "Films : 3", "Épisodes vus : 4", "Films vus : 2",
                         "Notes : 3", "Listes : 1", "Visionnages en attente : 7"]
    text = "\n".join(lines)
    assert "Séries introuvables sur TVMaze (1) :" in text
    assert "Séries à confirmer (1) :" in text
    assert "Numérotation incohérente (visionnages en attente) (1) :" in text
    assert "Films sans métadonnées TMDB (2) :" in text
    assert "  - Film Rare" in lines


# --- Rapport fidèle (pas de lignes perdues en silence) ----------------------------

def test_import_rejects_zip_without_bingers_files(engine, tmp_path, tvmaze, tmdb):
    import zipfile
    other = tmp_path / "autre.zip"
    with zipfile.ZipFile(other, "w") as archive:
        archive.writestr("show.csv", "id,title\n1,X\n")   # ex. export « base complète »
    with pytest.raises(ValueError, match="pas un export Bingers"):
        import_bingers(other, engine, tvmaze, tmdb, download_posters=False)


def test_import_report_counts_kept_ratings_and_skipped_watches(engine, make_bingers_zip,
                                                               tvmaze, tmdb):
    source = make_bingers_zip(
        library=SAMPLE_LIBRARY[:1],
        watches=[{"type": "episode", "title": "Le Royaume", "tvdb_id": "1001",
                  "season_number": "", "episode_number": "", "plays": "1"}],
        ratings=[{"type": "show", "title": "Le Royaume", "tvdb_id": "1001", "rating": "4"}])

    first = import_bingers(source, engine, tvmaze, tmdb, download_posters=False)
    again = import_bingers(source, engine, tvmaze, tmdb, download_posters=False)

    assert (first.ratings, first.ratings_kept, first.watches_skipped) == (1, 0, 1)
    assert (again.ratings, again.ratings_kept) == (0, 1)   # note déjà là : gardée, pas « reprise »
    assert any("numéro d'épisode manquant" in line for line in first.lines())


def test_import_half_and_invalid_ratings(engine, make_bingers_zip, tvmaze, tmdb):
    """Demi-note reprise telle quelle ; note hors de 1 à 5 ignorée, comptée et signalée."""
    source = make_bingers_zip(
        library=SAMPLE_LIBRARY[:2],
        ratings=[{"type": "show", "title": "Le Royaume", "tvdb_id": "1001", "rating": "4.5"},
                 {"type": "show", "title": "Lupin", "tvdb_id": "2002", "rating": "7",
                  "emotions": "scared"}])

    report = import_bingers(source, engine, tvmaze, tmdb, download_posters=False)

    with Session(engine) as s:
        ratings = {s.get(Show, r.ref_id).title: (r.rating, r.emotions)
                   for r in s.exec(select(Rating)).all()}
    assert ratings == {"Le Royaume": (4.5, None), "Lupin": (None, "scared")}
    assert (report.ratings, report.ratings_invalid) == (2, 1)
    assert "Notes illisibles (ignorées) : 1" in report.lines()

