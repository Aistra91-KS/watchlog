"""Tests de la vue d'ensemble de la bibliothèque (watchlog/core/progress.py)."""

from datetime import date, datetime, timedelta

import pytest
from sqlmodel import Session

from helpers_db import add_episode, add_movie, add_show, watch
from watchlog.core import progress
from watchlog.core.models import (COMPLETED, EPISODE, MATCH_NUMBERING, MATCH_UNCERTAIN, MOVIE,
                                  SHOW, TO_WATCH, WATCHING, PendingWatch, Watch)

TODAY = date(2026, 10, 12)


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def overview(session, **kwargs):
    return progress.library_overview(session, today=TODAY, **kwargs)


def by_title(entries) -> dict:
    return {e.title: e for e in entries}


def label(entry) -> str:
    return progress.progress_label(entry, today=TODAY)


def test_show_progress_counters(session):
    show = add_show(session, "Série", original_title="The Show", premiered=date(2020, 1, 1))
    episodes = [add_episode(session, show, 1, n, airdate=date(2020, 1, n)) for n in range(1, 4)]
    s2 = [add_episode(session, show, 2, n, airdate=date(2021, 1, n)) for n in range(1, 3)]
    add_episode(session, show, 2, 3, airdate=TODAY + timedelta(days=7))   # à venir
    add_episode(session, show, 2, 4)                                     # sans date
    add_episode(session, show, 3, None, airdate=date(2022, 1, 1))        # spécial : ignoré
    for ep in episodes + s2[:1]:
        watch(session, ep)
    session.commit()

    [entry] = overview(session)
    assert entry.kind == SHOW and entry.ref_id == show.id
    assert entry.original_title == "The Show" and entry.year == 2020
    assert entry.total_regular == 7
    assert entry.last_watched_code == "S02E01"
    assert entry.remaining == 1           # S02E02
    assert entry.upcoming == 1            # S02E03
    assert label(entry) == "S02E01 · 1 restant"


def test_last_watched_is_highest_season_and_number(session):
    show = add_show(session, "Série")
    # Vu S09E09 puis S10E01 (dans le désordre) : le plus loin est S10E01.
    watch(session, add_episode(session, show, 10, 1, airdate=date(2025, 1, 1)))
    watch(session, add_episode(session, show, 9, 9, airdate=date(2024, 1, 1)))
    watch(session, add_episode(session, show, 9, 10, airdate=date(2024, 2, 1)))
    watch(session, add_episode(session, show, 11, None, airdate=date(2025, 6, 1)))  # spécial
    session.commit()
    [entry] = overview(session)
    assert entry.last_watched_code == "S10E01"
    assert label(entry) == "À jour"


def test_remaining_uses_paris_date(session):
    show = add_show(session, "Série US")
    # Diffusé le 12/10 à 23:30 UTC = 13/10 à 01:30 à Paris : pas encore sorti le 12/10.
    add_episode(session, show, 1, 1, airdate=TODAY, airstamp=datetime(2026, 10, 12, 23, 30))
    add_episode(session, show, 1, 2, airdate=TODAY, airstamp=datetime(2026, 10, 12, 20, 0))
    session.commit()
    [entry] = overview(session)
    assert (entry.remaining, entry.upcoming) == (1, 1)
    assert label(entry) == "Pas commencé · 1 restant"


def test_show_labels(session):
    fresh = add_show(session, "Pas commencée")
    for n in range(1, 13):
        add_episode(session, fresh, 1, n, airdate=date(2025, 1, n))
    upcoming_only = add_show(session, "Pas sortie")
    add_episode(session, upcoming_only, 1, 1, airdate=TODAY + timedelta(days=30))
    add_show(session, "Sans épisodes")
    broken = add_show(session, "Cassée", match_status=MATCH_NUMBERING)
    add_episode(session, broken, 1, 1, airdate=date(2025, 1, 1))
    many = add_show(session, "Longue")
    for n in range(1, 11):
        ep = add_episode(session, many, 1, n, airdate=date(2025, 1, n))
        if n <= 2:
            watch(session, ep)
    session.commit()

    entries = by_title(overview(session))
    assert label(entries["Pas commencée"]) == "Pas commencé · 12 restants"
    assert label(entries["Pas sortie"]) == "Pas commencé"
    assert entries["Pas sortie"].upcoming == 1
    assert label(entries["Sans épisodes"]) == "Pas commencé"
    assert entries["Sans épisodes"].total_regular == 0
    assert label(entries["Cassée"]) == "Correspondance à vérifier"
    assert label(entries["Longue"]) == "S01E02 · 8 restants"


def test_movie_entries_and_labels(session):
    seen = add_movie(session, "Vu", release=date(2020, 5, 1), status=COMPLETED)
    watch(session, seen)
    add_movie(session, "Pas vu", release=date(2021, 1, 1), year=2021)
    add_movie(session, "Bientôt", release=date(2026, 12, 16))
    add_movie(session, "Sans date", year=1999)
    session.commit()

    entries = by_title(overview(session, kind=MOVIE))
    assert entries["Vu"].watched and label(entries["Vu"]) == "Vu"
    assert entries["Vu"].year == 2020 and entries["Vu"].release_date == date(2020, 5, 1)
    assert label(entries["Pas vu"]) == "Non vu"
    assert label(entries["Bientôt"]) == "Sortie le 16/12/2026"
    assert label(entries["Sans date"]) == "Non vu" and entries["Sans date"].year == 1999


def test_filters_kind_status_favorite(session):
    add_show(session, "Série en cours", status=WATCHING, favorite=True)
    add_show(session, "Série à voir", status=TO_WATCH)
    add_movie(session, "Film à voir", status=TO_WATCH, favorite=True)
    session.commit()

    assert [e.title for e in overview(session, kind=SHOW)] == ["Série à voir", "Série en cours"]
    assert [e.title for e in overview(session, status=TO_WATCH)] == ["Film à voir", "Série à voir"]
    assert len(overview(session, status=[WATCHING, TO_WATCH])) == 3
    assert [e.title for e in overview(session, favorite_only=True)] == ["Film à voir",
                                                                         "Série en cours"]


def test_search_ignores_accents_and_case(session):
    add_show(session, "Les Revenants", original_title="The Returned")
    add_movie(session, "L'Été meurtrier")
    add_movie(session, "Autre film")
    session.commit()

    assert [e.title for e in overview(session, search="ETE")] == ["L'Été meurtrier"]
    assert [e.title for e in overview(session, search="returned")] == ["Les Revenants"]
    assert len(overview(session, search="  ")) == 3


def test_sort_by_title_or_last_added(session):
    add_show(session, "Bravo", added_at=datetime(2024, 1, 1))
    add_movie(session, "alpha", added_at=datetime(2025, 1, 1))
    add_movie(session, "Éclair", added_at=None)
    add_show(session, "Charlie", added_at=datetime(2026, 1, 1))
    session.commit()

    assert [e.title for e in overview(session)] == ["alpha", "Bravo", "Charlie", "Éclair"]
    assert [e.title for e in overview(session, sort=progress.SORT_ADDED)] == [
        "Charlie", "alpha", "Bravo", "Éclair"]


def _seen(session, kind, ref_id, first, last=None):
    session.add(Watch(kind=kind, ref_id=ref_id, watched_at=first, last_watched_at=last))
    session.flush()


def _recent_library(session):
    """Bibliothèque avec des derniers visionnages variés (et une date inconnue)."""
    old = add_show(session, "Ancienne", status=WATCHING)
    _seen(session, EPISODE, add_episode(session, old, 1, 1).id, datetime(2025, 1, 1))
    new = add_show(session, "Récente", status=WATCHING)
    _seen(session, EPISODE, add_episode(session, new, 1, 1).id, datetime(2026, 9, 1))
    # Un spécial vu plus tard compte aussi comme dernier visionnage.
    _seen(session, EPISODE, add_episode(session, new, 0, None).id, datetime(2026, 10, 10))
    undated = add_show(session, "Sans date", status=WATCHING)
    _seen(session, EPISODE, add_episode(session, undated, 1, 1).id, None)
    add_show(session, "Pas commencée", status=WATCHING)
    done = add_show(session, "Finie", status=COMPLETED)
    _seen(session, EPISODE, add_episode(session, done, 1, 1).id, datetime(2024, 1, 1))
    movie = add_movie(session, "Film revu", status=COMPLETED)
    # Revu : c'est le dernier visionnage qui compte.
    _seen(session, MOVIE, movie.id, datetime(2020, 1, 1), datetime(2026, 10, 1))
    add_movie(session, "Film pas vu", status=TO_WATCH)
    session.commit()


def test_sort_recently_watched(session):
    _recent_library(session)
    entries = overview(session, sort=progress.SORT_RECENT)
    assert [e.title for e in entries] == [
        "Récente", "Film revu", "Ancienne", "Finie",
        "Film pas vu", "Pas commencée", "Sans date"]   # dates inconnues à la fin, par titre
    assert by_title(entries)["Récente"].last_watched == datetime(2026, 10, 10)


def test_sort_paused_longest_keeps_watching_shows_only(session):
    _recent_library(session)
    assert [e.title for e in overview(session, sort=progress.SORT_PAUSED)] == [
        "Ancienne", "Récente", "Pas commencée", "Sans date"]
    assert overview(session, sort=progress.SORT_PAUSED, kind=MOVIE) == []
    assert overview(session, sort=progress.SORT_PAUSED, status=[COMPLETED]) == []
    assert len(overview(session, sort=progress.SORT_PAUSED, status=[COMPLETED, WATCHING])) == 4


def test_last_watched_label(session):
    _recent_library(session)
    entries = by_title(overview(session))
    assert label_last(entries["Récente"]) == "Vu il y a 2 jours"
    assert label_last(entries["Film revu"]) == "Vu il y a 11 jours"
    assert label_last(entries["Ancienne"]) == "Vu il y a 1 an"
    assert label_last(entries["Finie"]) == "Vu il y a 2 ans"
    assert label_last(entries["Sans date"]) == "Date de visionnage inconnue"
    assert label_last(entries["Pas commencée"]) == ""
    assert label_last(entries["Film pas vu"]) == ""
    entry = entries["Récente"]
    entry.last_watched = datetime(2026, 7, 1)
    assert label_last(entry) == "Vu il y a 3 mois"
    entry.last_watched = datetime(2026, 10, 11, 12)
    assert label_last(entry) == "Vu hier"
    entry.last_watched = datetime(2026, 10, 11, 23)   # 01:00 le 12 à Paris
    assert label_last(entry) == "Vu aujourd'hui"


def test_import_day_counts_as_unknown_date(session, monkeypatch):
    """Dernier visionnage un jour d'import : date inconnue, à la fin des tris par date."""
    monkeypatch.setattr(progress.stats.count_bulk_days, "__defaults__", (3,))  # seuil : 3
    imported = add_show(session, "Importée")
    for n in range(1, 4):
        _seen(session, EPISODE, add_episode(session, imported, 1, n).id, datetime(2026, 8, 13, 9))
    real = add_show(session, "Datée")
    _seen(session, EPISODE, add_episode(session, real, 1, 1).id, datetime(2025, 1, 1))
    session.commit()

    entries = overview(session, sort=progress.SORT_PAUSED)
    assert [e.title for e in entries] == ["Datée", "Importée"]
    assert entries[1].last_watched is None and entries[1].import_day
    assert label_last(entries[1]) == "Date inconnue (import)"


def label_last(entry) -> str:
    return progress.last_watched_label(entry, today=TODAY)


def test_shows_to_check(session):
    add_show(session, "OK")
    add_show(session, "Incertaine", match_status=MATCH_UNCERTAIN)
    pending = add_show(session, "Avec attente")
    session.add(PendingWatch(show_id=pending.id, season=1, number=1))
    session.commit()
    assert progress.shows_to_check(session) == 2


def test_normalize():
    assert progress.normalize("L'Été À Paris") == "l'ete a paris"
    assert progress.normalize(None) == ""
