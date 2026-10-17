"""Tests des exports (Excel « À regarder », CSV complet en zip)."""

import io
import zipfile
from datetime import date, datetime

import pandas as pd
import pytest
from sqlmodel import Session, select

from watchlog.core.export import QUEUE_COLUMNS, export_all_csv, export_queue_excel, queue_dataframe
from watchlog.core.models import (EPISODE, MOVIE, SHOW, TO_WATCH, WATCHING, ApiCache, Episode,
                                  LibraryItem, Movie, Show, Watch)
from watchlog.core.queue import build_queue

TODAY = date(2026, 10, 12)


@pytest.fixture
def session(engine):
    """Une série en cours (2 épisodes non vus, 1 vu) et un film à voir sans durée."""
    with Session(engine) as s:
        show = Show(title="Le Royaume", tvmaze_id=101)
        s.add(show)
        s.flush()
        s.add_all([
            Episode(show_id=show.id, season=1, number=1, title="Le début",
                    airdate=date(2026, 1, 5), runtime=50),
            Episode(show_id=show.id, season=1, number=2, title="La suite",
                    airdate=date(2026, 12, 1), runtime=50),
            Episode(show_id=show.id, season=1, number=3, title="Sans date"),
        ])
        movie = Movie(title="Fight Club", release_date_fr=date(2026, 3, 1))
        s.add(movie)
        s.flush()
        s.add_all([LibraryItem(kind=SHOW, ref_id=show.id, status=WATCHING),
                   LibraryItem(kind=MOVIE, ref_id=movie.id, status=TO_WATCH)])
        first = s.exec(select(Episode).where(Episode.number == 1)).one()
        s.add(Watch(kind=EPISODE, ref_id=first.id, watched_at=datetime(2026, 1, 6, 20)))
        s.add(ApiCache(url="/shows/101", response_json="{}", fetched_at=datetime(2026, 1, 1)))
        s.commit()
        yield s


def test_queue_dataframe_columns_and_order(session):
    frame = queue_dataframe(build_queue(session, today=TODAY))

    assert list(frame.columns) == QUEUE_COLUMNS
    assert frame["Série / Film"].tolist() == ["Fight Club", "Le Royaume", "Le Royaume"]
    film, upcoming, no_date = frame.to_dict(orient="records")
    assert film["Date"] == "01/03/2026"
    assert film["Type"] == "Film" and film["Épisode"] == "" and film["Statut"] == "À voir"
    assert film["Disponibilité"] == "Sorti" and pd.isna(film["Durée (min)"])
    assert upcoming["Épisode"] == "S01E02" and upcoming["Disponibilité"] == "À venir"
    assert upcoming["Titre de l'épisode"] == "La suite" and upcoming["Statut"] == "En cours"
    assert upcoming["Durée (min)"] == 50
    assert no_date["Date"] == "" and no_date["Disponibilité"] == "Sans date"


def test_queue_dataframe_empty():
    assert list(queue_dataframe([]).columns) == QUEUE_COLUMNS


def test_export_queue_excel_readable(session, tmp_path):
    dest = tmp_path / "a_regarder.xlsx"
    count = export_queue_excel(session, dest, today=TODAY)

    assert count == 3
    frame = pd.read_excel(dest, dtype=str)
    assert list(frame.columns) == QUEUE_COLUMNS
    assert frame["Épisode"].fillna("").tolist() == ["", "S01E02", "S01E03"]


def test_export_queue_excel_filters_and_stream(session):
    buffer = io.BytesIO()
    count = export_queue_excel(session, buffer, kinds=(MOVIE,), today=TODAY)
    assert count == 1
    buffer.seek(0)
    assert pd.read_excel(buffer)["Série / Film"].tolist() == ["Fight Club"]


def test_export_all_csv_without_api_cache(session, engine, tmp_path):
    dest = tmp_path / "base.zip"
    names = export_all_csv(engine, dest)

    assert "apicache.csv" not in names
    assert {"show.csv", "episode.csv", "movie.csv", "watch.csv", "libraryitem.csv"} <= set(names)
    with zipfile.ZipFile(dest) as archive:
        assert sorted(archive.namelist()) == sorted(names)
        raw = archive.read("show.csv")
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM : Excel lit bien les accents
    shows = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")
    assert shows["title"].tolist() == ["Le Royaume"]


def test_cli_export(engine, session, tmp_path, monkeypatch):
    """Commande `export` : chemins donnés, puis noms horodatés par défaut dans exports/."""
    import argparse

    import watchlog.__main__ as cli

    excel, csv = tmp_path / "out" / "a.xlsx", tmp_path / "out" / "base.zip"
    assert cli.cmd_export(argparse.Namespace(excel=str(excel), csv=str(csv)), engine, None, None) == 0
    assert excel.exists() and csv.exists()

    only_csv = tmp_path / "seul.zip"
    cli.cmd_export(argparse.Namespace(excel=None, csv=str(only_csv)), engine, None, None)
    assert only_csv.exists()

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    cli.cmd_export(argparse.Namespace(excel=None, csv=None), engine, None, None)
    created = sorted(p.name for p in (tmp_path / "exports").iterdir())
    assert [name.split("-2")[0] for name in created] == ["a-regarder", "watchlog", "watchlog-bingers"]

    bingers = tmp_path / "pour-bingers.zip"
    cli.cmd_export(argparse.Namespace(excel=None, csv=None, bingers=str(bingers)), engine, None, None)
    assert bingers.exists()
