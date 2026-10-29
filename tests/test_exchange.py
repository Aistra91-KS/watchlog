"""Tests du fichier d'échange JSON avec l'app web (exchange.py)."""

import io
import json
from datetime import date, datetime

import pytest
from sqlmodel import Session, select

from tests.conftest import make_engine
from watchlog.core import settings
from watchlog.core.exchange import FORMAT, VERSION, export_data, import_data, read_json, write_json
from watchlog.core.models import (EPISODE, MOVIE, SHOW, STOPPED, TO_WATCH, WATCHING, CustomList,
                                  CustomListItem, DismissedSuggestion, Episode, LibraryItem, Movie,
                                  Rating, Show, ThemePreference, Watch)


def fill(s: Session) -> None:
    """Une série suivie (1 épisode vu sur 2), un film vu et noté, une liste, des réglages."""
    show = Show(title="Le Royaume", tvmaze_id=101, tvdb_id=9001, genres="Drame",
                premiered=date(2020, 1, 5))
    movie = Movie(title="Fight Club", tmdb_id=550, year=1999, runtime=139,
                  release_date_fr=date(1999, 11, 10))
    s.add_all([show, movie])
    s.flush()
    e1 = Episode(show_id=show.id, season=1, number=1, title="Le début", tvmaze_episode_id=1,
                 airdate=date(2020, 1, 5), runtime=50)
    e2 = Episode(show_id=show.id, season=1, number=2, title="La suite", tvmaze_episode_id=2,
                 airstamp=datetime(2020, 1, 12, 20, 0))
    s.add_all([e1, e2])
    s.flush()
    s.add_all([
        LibraryItem(kind=SHOW, ref_id=show.id, status=WATCHING, favorite=True,
                    added_at=datetime(2024, 1, 1, 10, 0)),
        LibraryItem(kind=MOVIE, ref_id=movie.id, status=TO_WATCH),
        Watch(kind=EPISODE, ref_id=e1.id, watched_at=datetime(2024, 2, 1, 21, 0),
              last_watched_at=datetime(2024, 2, 1, 21, 0)),
        Watch(kind=MOVIE, ref_id=movie.id, watched_at=datetime(2023, 5, 1, 20, 0),
              last_watched_at=datetime(2024, 5, 1, 20, 0), plays=2),
        Rating(kind=MOVIE, ref_id=movie.id, rating=5, emotions="thrilled"),
        Rating(kind=EPISODE, ref_id=e1.id, rating=4.5),   # demi-note
        DismissedSuggestion(kind=MOVIE, tmdb_id=777, title="Pas pour moi",
                            dismissed_at=datetime(2024, 3, 1)),
        ThemePreference(feature="k:4565", label="Dystopie", weight=1,
                        updated_at=datetime(2024, 3, 2)),
    ])
    custom = CustomList(name="Week-end", description="À voir vite")
    s.add(custom)
    s.flush()
    s.add_all([CustomListItem(list_id=custom.id, kind=SHOW, ref_id=show.id),
               CustomListItem(list_id=custom.id, kind=MOVIE, ref_id=movie.id)])
    settings.set_my_platforms(s, ["Netflix"])
    s.commit()


@pytest.fixture
def source(engine):
    with Session(engine) as s:
        fill(s)
    return engine


def test_export_format(source):
    with Session(source) as s:
        data = export_data(s)
    assert data["format"] == FORMAT and data["version"] == VERSION
    [show] = data["shows"]
    assert show["tvmaze_id"] == 101 and show["premiered"] == "2020-01-05"
    assert show["library"] == {"status": WATCHING, "favorite": True,
                               "added_at": "2024-01-01T10:00:00Z"}
    first, second = show["episodes"]
    assert first["watch"]["watched_at"] == "2024-02-01T21:00:00Z"
    assert first["rating"]["rating"] == 4.5   # demi-note
    assert second["watch"] is None and second["airstamp"] == "2020-01-12T20:00:00Z"
    [movie] = data["movies"]
    assert movie["watch"]["plays"] == 2 and movie["rating"]["emotions"] == "thrilled"
    assert data["lists"][0]["items"] == [{"kind": MOVIE, "tmdb_id": 550, "title": "Fight Club",
                                          "year": 1999},
                                         {"kind": SHOW, "tvmaze_id": 101, "title": "Le Royaume"}]
    assert data["settings"] == {"my_platforms": ["Netflix"]}
    json.dumps(data)  # sérialisable tel quel


def test_round_trip_into_empty_base(source, tmp_path):
    buffer = io.BytesIO()
    with Session(source) as s:
        assert write_json(s, buffer) == 2
    target = make_engine(tmp_path / "cible.db")
    with Session(target) as s:
        report = import_data(s, read_json(buffer.getvalue()))
        s.commit()
        assert (report.shows_added, report.movies_added, report.episodes_added) == (1, 1, 2)
        assert report.watches == 2 and not report.skipped
        again = export_data(s)
    with Session(source) as s:
        original = export_data(s)
    for data in (again, original):
        data.pop("exported_at")
    assert again == original


def test_import_is_idempotent(source):
    with Session(source) as s:
        data = export_data(s)
        report = import_data(s, data)
        s.commit()
        assert report.lines()[:4] == ["Séries ajoutées : 0", "Films ajoutés : 0",
                                      "Épisodes ajoutés : 0",
                                      "Visionnages ajoutés ou complétés : 0"]
        assert report.library_changes == report.ratings == report.lists == 0
        assert len(s.exec(select(Watch)).all()) == 2
        assert len(s.exec(select(CustomListItem)).all()) == 2


def test_import_merges_watches_and_file_wins_for_status(source):
    with Session(source) as s:
        data = export_data(s)
    show = data["shows"][0]
    show["library"]["status"] = STOPPED
    show["episodes"][1]["watch"] = {"watched_at": "2024-06-01T20:00:00Z",
                                    "last_watched_at": None, "plays": 1}
    show["episodes"][0]["watch"] = None          # absent du fichier : on ne supprime rien
    data["movies"][0]["rating"] = {"rating": 2, "emotions": None, "favorite_character": None,
                                   "note": "Déçu"}
    with Session(source) as s:
        report = import_data(s, data)
        s.commit()
        assert report.library_changes == 1 and report.watches == 1 and report.ratings == 1
        item = s.exec(select(LibraryItem).where(LibraryItem.kind == SHOW)).one()
        assert item.status == STOPPED
        assert len(s.exec(select(Watch).where(Watch.kind == EPISODE)).all()) == 2
        rating = s.exec(select(Rating).where(Rating.kind == MOVIE)).one()
        assert (rating.rating, rating.note) == (2, "Déçu")


def test_import_matches_manual_titles_and_fills_missing_fields(engine):
    data = {"format": FORMAT, "version": 1, "shows": [
        {"title": "Série perso", "is_manual": True, "episodes": [
            {"season": 1, "number": 1, "watch": {"watched_at": "2024-01-01T00:00:00Z"}}]},
        {"title": "Le Royaume", "tvmaze_id": 101, "summary_fr": "Résumé", "episodes": []}],
        "movies": [{"title": "Film perso", "year": 2001, "is_manual": True}]}
    with Session(engine) as s:
        s.add(Show(title="Le Royaume", tvmaze_id=101, summary_fr=None))
        s.commit()
        first = import_data(s, data)
        second = import_data(s, data)
        s.commit()
        assert (first.shows_added, first.movies_added, first.episodes_added) == (1, 1, 1)
        assert (second.shows_added, second.movies_added, second.episodes_added) == (0, 0, 0)
        royaume = s.exec(select(Show).where(Show.tvmaze_id == 101)).one()
        assert royaume.summary_fr == "Résumé"


def test_invalid_entries_are_reported_not_silent(engine):
    data = {"format": FORMAT, "version": 1,
            "shows": [{"title": "Mauvais statut", "tvmaze_id": 5,
                       "library": {"status": "regardé"}, "episodes": []},
                      {"tvmaze_id": 6}],
            "lists": [{"name": "L", "items": [{"kind": MOVIE, "tmdb_id": 999, "title": "Inconnu"}]}]}
    with Session(engine) as s:
        report = import_data(s, data)
    assert len(report.skipped) == 3
    assert any("Mauvais statut" in line for line in report.skipped)
    assert any("Inconnu" in line for line in report.lines())


def test_theme_preferences_most_recent_wins(source):
    data = {"format": FORMAT, "version": 1, "theme_preferences": [
        {"feature": "k:4565", "label": "Dystopie", "weight": -1, "updated_at": "2020-01-01T00:00:00Z"},
        {"feature": "g:Animation", "label": "Animation", "weight": -1,
         "updated_at": "2025-01-01T00:00:00Z"}]}
    with Session(source) as s:
        import_data(s, data)
        s.commit()
        assert s.get(ThemePreference, "k:4565").weight == 1      # locale plus récente : gardée
        assert s.get(ThemePreference, "g:Animation").weight == -1


def test_invalid_entry_is_never_half_imported(engine):
    """Note à 7 au 50e épisode : rien de la série n'est écrit (validation avant écriture)."""
    episodes = [{"season": 1, "number": n, "watch": {"watched_at": "2024-01-01T00:00:00Z"},
                 "rating": {"rating": 7} if n == 50 else None} for n in range(1, 51)]
    data = {"format": FORMAT, "version": 1,
            "shows": [{"title": "Note à 7", "tvmaze_id": 9, "library": {"status": WATCHING},
                       "episodes": episodes}],
            "lists": [None, {"name": ""}], "dismissed": [None],
            "theme_preferences": [{"feature": "k:1", "weight": 3}]}
    with Session(engine) as s:
        report = import_data(s, data)
        s.commit()
        assert s.exec(select(Show)).all() == [] and s.exec(select(Watch)).all() == []
    assert report.shows_added == 0
    assert "note invalide (S1E50)" in report.skipped[0]
    assert len(report.skipped) == 5


@pytest.mark.parametrize("value", [4.3, 0.5, "4", True])
def test_rating_must_be_a_half_step_between_1_and_5(engine, value):
    data = {"format": FORMAT, "version": 1, "movies": [
        {"title": "Mal noté", "tmdb_id": 1, "rating": {"rating": value}}]}
    with Session(engine) as s:
        report = import_data(s, data)
    assert report.movies_added == 0 and "note invalide (titre)" in report.skipped[0]


def test_types_checked_and_manual_specials_idempotent(engine):
    special = {"season": 1, "number": None, "title": "Spécial Noël", "is_special": True,
               "watch": {"watched_at": "2024-12-25T20:00:00Z", "plays": 1}}
    data = {"format": FORMAT, "version": 1, "shows": [
        {"title": "Perso", "is_manual": True, "episodes": [special]},
        {"title": "Genres en nombre", "tvmaze_id": 3, "genres": 42, "episodes": []},
        {"title": "Mauvaise date", "tvmaze_id": 4,
         "episodes": [{"season": 1, "number": 1, "watch": {"watched_at": "hier soir"}}]},
        {"title": 12}],
        "lists": [{"name": "L", "items": [{"kind": "constructor", "title": "x"}, "pas un objet"]}]}
    with Session(engine) as s:
        first = import_data(s, data)
        second = import_data(s, data)
        s.commit()
    assert (first.episodes_added, second.episodes_added) == (1, 0)
    assert len(first.skipped) == 5          # genres, date, titre, 2 éléments de liste


@pytest.mark.parametrize("raw, message", [
    (b"pas du json", "illisible"),
    (b'{"format": "autre"}', "format inconnu"),
    (b'{"format": "watchlog", "version": 99}', "non prise en charge"),
])
def test_read_json_rejects_bad_files(raw, message):
    with pytest.raises(ValueError, match=message):
        read_json(raw)


def test_cli_export_and_import_json(source, tmp_path, monkeypatch):
    import argparse

    from watchlog import __main__ as cli

    monkeypatch.setattr(cli, "backup_first", lambda: None)
    dest = tmp_path / "web.json"
    args = argparse.Namespace(excel=None, csv=None, bingers=None, json=str(dest))
    assert cli.cmd_export(args, source, None, None) == 0
    assert read_json(dest)["shows"][0]["title"] == "Le Royaume"
    target = make_engine(tmp_path / "cible.db")
    assert cli.cmd_import(argparse.Namespace(zip=str(dest), no_posters=True), target,
                          None, None) == 0
    with Session(target) as s:
        assert s.exec(select(Show)).one().title == "Le Royaume"
    bad = tmp_path / "mauvais.json"
    bad.write_text("{}", encoding="utf-8")
    assert cli.cmd_import(argparse.Namespace(zip=str(bad), no_posters=True), target,
                          None, None) == 1


# --- Contrat avec l'app web (web/js/core/exchange.js) -------------------------------

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
PYTHON_FIXTURE = ROOT / "web" / "tests" / "fixtures" / "python-export.json"
WEB_FIXTURE = ROOT / "tests" / "fixtures" / "web-export.json"


def test_contract_python_fixture_is_up_to_date(source):
    """La fixture lue par les tests web doit refléter le format Python actuel.

    Après un changement de format voulu : relancer ce test avec WATCHLOG_REGEN_FIXTURES=1
    (réécrit la fixture), puis `node web/tests/tools/web-export-fixture.mjs`.
    """
    with Session(source) as s:
        data = export_data(s)
    if __import__("os").getenv("WATCHLOG_REGEN_FIXTURES"):
        data["exported_at"] = "2026-10-25T12:00:00Z"
        PYTHON_FIXTURE.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                                  encoding="utf-8")
    fixture = read_json(PYTHON_FIXTURE)
    for d in (data, fixture):
        d.pop("exported_at")
    assert data == fixture


def test_contract_file_written_by_web_app(source):
    """Un fichier écrit par l'app web (statut changé, épisode vu, film saisi à la main)."""
    data = read_json(WEB_FIXTURE)
    assert data["app"] == "web"
    with Session(source) as s:
        report = import_data(s, data)
        s.commit()
        assert not report.skipped
        assert (report.movies_added, report.library_changes) == (1, 2)
        assert report.watches == 2
        item = s.exec(select(LibraryItem).where(LibraryItem.kind == SHOW)).one()
        assert item.status == STOPPED
        manual = s.exec(select(Movie).where(Movie.title == "Film du téléphone")).one()
        assert manual.is_manual and manual.tmdb_id is None
        assert len(s.exec(select(Watch).where(Watch.kind == EPISODE)).all()) == 2
