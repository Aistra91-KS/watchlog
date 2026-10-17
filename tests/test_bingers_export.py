"""Tests de l'export au format Bingers (réimportable dans l'application Bingers)."""

import csv
import io
import zipfile
from datetime import datetime

from sqlmodel import Session, select

from conftest import BINGERS_HEADERS, FakeTMDB, FakeTVMaze
from helpers_db import add_episode, add_movie, add_show
from watchlog.core import bingers_export
from watchlog.core.db import make_engine
from watchlog.core.importer import import_bingers
from watchlog.core.models import (EPISODE, FOR_LATER, MOVIE, SHOW, STOPPED, TO_WATCH, LibraryItem,
                                  Movie, Rating, Show, Watch)


def read_zip(data) -> dict[str, list[dict]]:
    archive = zipfile.ZipFile(data)
    return {name: list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig"))))
            for name in archive.namelist()}


def test_iso_format():
    assert bingers_export.iso(datetime(2026, 8, 13, 17, 2, 42, 346999)) == "2026-08-13T17:02:42.346Z"
    assert bingers_export.iso(None) == ""


def test_export_format_matches_bingers(engine):
    with Session(engine) as session:
        show = add_show(session, "Le Royaume", status=STOPPED, favorite=True, tvdb_id=1001,
                        tmdb_id=77, original_title="The Kingdom")
        e1, special = add_episode(session, show, 1, 1), add_episode(session, show, 0, None)
        movie = add_movie(session, "Fight Club", status=TO_WATCH, tmdb_id=550, year=1999,
                          added_at=datetime(2026, 1, 2, 3, 4, 5))
        add_movie(session, "Fait main", is_manual=True)
        add_show(session, "Série maison", is_manual=True)
        session.add(Watch(kind=EPISODE, ref_id=e1.id, watched_at=datetime(2024, 1, 10, 20),
                          last_watched_at=datetime(2024, 2, 1, 20), plays=2))
        session.add(Watch(kind=EPISODE, ref_id=special.id, watched_at=datetime(2024, 1, 10)))
        session.add(Watch(kind=MOVIE, ref_id=movie.id))   # vu, date inconnue
        session.add(Rating(kind=MOVIE, ref_id=movie.id, rating=5, emotions="scared"))
        session.commit()
        buffer = io.BytesIO()
        report = bingers_export.export_bingers_zip(session, buffer)

    raw = zipfile.ZipFile(buffer)
    assert sorted(raw.namelist()) == ["library.csv", "lists.csv", "ratings.csv", "watches.csv"]
    library_bytes = raw.read("library.csv")
    assert library_bytes.startswith(b"\xef\xbb\xbf") and b"\r\n" in library_bytes
    files = read_zip(buffer)
    for name, header in BINGERS_HEADERS.items():
        assert list(csv.reader(io.StringIO(raw.read(f"{name}.csv").decode("utf-8-sig"))))[0] == header

    by_title = {r["title"]: r for r in files["library.csv"]}
    assert set(by_title) == {"Le Royaume", "Fight Club"}
    kingdom, fight = by_title["Le Royaume"], by_title["Fight Club"]
    assert (kingdom["type"], kingdom["tvdb_id"], kingdom["tmdb_id"]) == ("show", "1001", "77")
    assert kingdom["list_status"] == "stopped" and kingdom["stopped_watching_at"]
    assert kingdom["favorite"] == "yes" and kingdom["original_title"] == "The Kingdom"
    assert (fight["list_status"], fight["year"], fight["favorite"]) == ("following", "1999", "no")
    assert fight["added_at"] == "2026-01-02T03:04:05.000Z"

    watches = files["watches.csv"]
    assert len(watches) == 2                       # l'épisode spécial n'est pas exporté
    episode = next(w for w in watches if w["type"] == "episode")
    assert (episode["season_number"], episode["episode_number"], episode["plays"]) == ("1", "1", "2")
    assert episode["first_watched_at"] == "2024-01-10T20:00:00.000Z"
    assert episode["last_watched_at"] == "2024-02-01T20:00:00.000Z"
    film = next(w for w in watches if w["type"] == "movie")
    assert film["first_watched_at"] == "2026-01-02T03:04:05.000Z"   # date inconnue -> ajout

    assert files["ratings.csv"] == [{"type": "movie", "title": "Fight Club", "tvdb_id": "",
                                     "tmdb_id": "550", "season_number": "", "episode_number": "",
                                     "rating": "5", "favorite_character": "", "emotions": "scared"}]
    assert sorted(report.skipped) == ["Fait main", "Série maison"]
    assert report.undated == 1 and report.episode_watches == 1 and report.movie_watches == 1


def test_for_later_status(engine):
    with Session(engine) as session:
        add_show(session, "Plus tard", status=FOR_LATER, tvdb_id=5)
        session.commit()
        buffer = io.BytesIO()
        bingers_export.export_bingers_zip(session, buffer)
    row = read_zip(buffer)["library.csv"][0]
    assert row["list_status"] == "for_later" and row["for_later_at"]


def test_round_trip_through_importer(engine, sample_zip, tmp_path):
    """Import Bingers -> export Bingers -> nouvel import : même bibliothèque, mêmes visionnages."""
    import_bingers(sample_zip, engine, FakeTVMaze(), FakeTMDB(), download_posters=False)
    exported = tmp_path / "watchlog-bingers.zip"
    with Session(engine) as session:
        bingers_export.export_bingers_zip(session, exported)

    other = make_engine(tmp_path / "other.db")
    import_bingers(exported, other, FakeTVMaze(), FakeTMDB(), download_posters=False)

    def snapshot(eng):
        with Session(eng) as s:
            library = set()
            for item in s.exec(select(LibraryItem)).all():
                obj = s.get(Show if item.kind == SHOW else Movie, item.ref_id)
                library.add((item.kind, obj.title, item.status, item.favorite))
            watches = len(s.exec(select(Watch)).all())
            ratings = {(r.kind, r.rating) for r in s.exec(select(Rating)).all()}
            return library, watches, ratings

    first, second = snapshot(engine), snapshot(other)
    assert second[0] == first[0]
    assert second[1] == first[1]
    assert {r for r in second[2] if r[0] != EPISODE} == {r for r in first[2] if r[0] != EPISODE}
