"""Tests des durées réelles des épisodes (TMDB prioritaire sur le créneau TVMaze)."""

from datetime import date, timedelta

from sqlmodel import Session, select

from watchlog.core import catalog
from watchlog.core.dates import utcnow
from watchlog.core.models import (MATCH_NUMBERING, MATCH_OK, SHOW, STOPPED, WATCHING, Episode,
                                  LibraryItem, Show)
from watchlog.core.sync import shows_for_runtimes, sync
from watchlog.core.tmdb import season_ttl


class SeasonTMDB:
    """Faux TMDB : saisons {(tmdb_id, saison): [épisodes TMDB]}, None = saison inconnue."""

    def __init__(self, seasons, available=True):
        self.seasons = seasons
        self.available = available
        self.fetched: list[tuple[int, int]] = []

    def tv_season(self, tmdb_id, season):
        self.fetched.append((tmdb_id, season))
        episodes = self.seasons.get((tmdb_id, season))
        return None if episodes is None else {"episodes": episodes}

    def download(self, url, dest):
        return False


def tmdb_ep(number, air_date, runtime):
    return {"episode_number": number, "air_date": air_date, "runtime": runtime}


def add_show(session, tmdb_id=500, status=WATCHING, match=MATCH_OK, checked_at=None):
    show = Show(title=f"Série {tmdb_id}", tvmaze_id=tmdb_id, tmdb_id=tmdb_id, match_status=match,
                runtimes_checked_at=checked_at, last_synced_at=utcnow())
    session.add(show)
    session.flush()
    session.add(LibraryItem(kind=SHOW, ref_id=show.id, status=status))
    return show


def add_episode(session, show, season, number, airdate, runtime=60):
    episode = Episode(show_id=show.id, season=season, number=number, airdate=airdate,
                      runtime=runtime, is_special=number is None)
    session.add(episode)
    session.flush()
    return episode


def test_apply_tmdb_runtimes_replaces_slot_duration(engine):
    with Session(engine) as s:
        show = add_show(s)
        e1 = add_episode(s, show, 4, 16, date(2010, 1, 18))
        e2 = add_episode(s, show, 4, 17, date(2010, 1, 25))
        tmdb = SeasonTMDB({(500, 4): [tmdb_ep(16, "2010-01-18", 42),
                                      tmdb_ep(17, "2010-01-26", 43)]})  # écart d'un jour : ok

        result = catalog.apply_tmdb_runtimes(s, tmdb, show)

        assert (result.updated, result.unmatched, result.missing_seasons) == (2, 0, 0)
        assert (e1.runtime, e1.tmdb_runtime) == (42, 42)
        assert e2.runtime == 43
        assert show.runtimes_checked_at is not None


def test_apply_tmdb_runtimes_keeps_tvmaze_when_unsure(engine):
    """Date différente (autre numérotation), durée ou date absente, spécial : rien ne change."""
    with Session(engine) as s:
        show = add_show(s)
        shifted = add_episode(s, show, 1, 1, date(2020, 1, 1))
        no_runtime = add_episode(s, show, 1, 2, date(2020, 1, 8))
        no_date = add_episode(s, show, 1, 3, None)
        special = add_episode(s, show, 1, None, date(2020, 1, 1))
        missing = add_episode(s, show, 2, 1, date(2021, 1, 1))
        tmdb = SeasonTMDB({(500, 1): [tmdb_ep(1, "2020-03-01", 40), tmdb_ep(2, "2020-01-08", None),
                                      tmdb_ep(3, "2020-01-15", 40)]})

        result = catalog.apply_tmdb_runtimes(s, tmdb, show)

        assert (result.updated, result.unmatched, result.missing_seasons) == (0, 2, 1)
        for episode in (shifted, no_runtime, no_date, special, missing):
            assert episode.runtime == 60 and episode.tmdb_runtime is None
        assert sorted(tmdb.fetched) == [(500, 1), (500, 2)]   # pas de saison 0 / spéciaux


def test_tvmaze_refresh_keeps_tmdb_runtime(engine):
    """Une resynchro TVMaze ne remet pas la durée du créneau sur un épisode corrigé."""
    with Session(engine) as s:
        show = add_show(s)
        episode = Episode(show_id=show.id, season=1, number=1, airdate=date(2020, 1, 1),
                          runtime=42, tmdb_runtime=42, tvmaze_episode_id=9001)
        s.add(episode)
        s.flush()
        data = {"id": show.tvmaze_id, "name": show.title, "status": "Ended"}
        tvmaze_ep = {"id": 9001, "season": 1, "number": 1, "airdate": "2020-01-01",
                     "runtime": 60}

        catalog.upsert_show_from_tvmaze(s, data, [tvmaze_ep])

        assert episode.runtime == 42


def test_shows_for_runtimes_selection(engine):
    with Session(engine) as s:
        never = add_show(s, 1)
        stopped = add_show(s, 2, status=STOPPED)                     # arrêtée : comprise
        checked = add_show(s, 3, checked_at=utcnow())                # déjà faite : ignorée
        refreshed = add_show(s, 4, checked_at=utcnow())              # rechargée : refaite
        add_show(s, 5, match=MATCH_NUMBERING)                        # pas vérifiée : ignorée
        s.add(Show(title="Hors bibliothèque", tmdb_id=6))
        s.commit()

        shows = shows_for_runtimes(s, refreshed_tvmaze_ids=[4])

        assert {show.id for show in shows} == {never.id, stopped.id, refreshed.id}
        assert checked not in shows


def test_sync_applies_tmdb_runtimes_once(engine):
    with Session(engine) as s:
        show = add_show(s)
        add_episode(s, show, 1, 1, date(2020, 1, 1))
        s.commit()

    class QuietTVMaze:
        def updated_since(self, since="week"):
            return {}

    tmdb = SeasonTMDB({(500, 1): [tmdb_ep(1, "2020-01-01", 41)]})
    report = sync(engine, QuietTVMaze(), tmdb, download_posters=False)

    assert report.runtimes_updated == 1
    assert report.errors == []
    assert any("1 épisodes corrigés" in line for line in report.lines())
    with Session(engine) as s:
        assert s.exec(select(Episode)).one().runtime == 41

    # Deuxième synchro : série déjà vérifiée et pas rechargée, TMDB n'est pas rappelé.
    tmdb.fetched.clear()
    sync(engine, QuietTVMaze(), tmdb, download_posters=False)
    assert tmdb.fetched == []


def test_sync_runtimes_skipped_when_tmdb_down(engine):
    with Session(engine) as s:
        show = add_show(s)
        add_episode(s, show, 1, 1, date(2020, 1, 1))
        s.commit()

    class QuietTVMaze:
        def updated_since(self, since="week"):
            return {}

    tmdb = SeasonTMDB({}, available=False)
    report = sync(engine, QuietTVMaze(), tmdb, download_posters=False)

    assert tmdb.fetched == []
    assert report.tmdb_down
    with Session(engine) as s:
        assert s.exec(select(Show)).one().runtimes_checked_at is None   # refait plus tard


def test_season_ttl():
    past = (date.today() - timedelta(days=30)).isoformat()
    future = (date.today() + timedelta(days=30)).isoformat()
    assert season_ttl({"episodes": [tmdb_ep(1, past, 42)]}) == timedelta(days=30)
    assert season_ttl({"episodes": [tmdb_ep(1, past, 42), tmdb_ep(2, future, None)]}) \
        == timedelta(days=1)
    assert season_ttl({"episodes": [tmdb_ep(1, past, None)]}) == timedelta(days=1)
    assert season_ttl({"episodes": []}) == timedelta(days=1)
