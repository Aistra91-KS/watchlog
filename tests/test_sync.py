"""Tests de la synchronisation (faux clients TVMaze/TMDB, aucun appel réseau)."""

from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from watchlog.core import settings
from watchlog.core.dates import utcnow
from watchlog.core.db import make_engine
from watchlog.core.http import ApiError
from watchlog.core.models import (MATCH_OK, MATCH_REMAPPED, MATCH_UNCERTAIN, MOVIE, SHOW,
                                  STOPPED, WATCHING, LibraryItem, Movie, Show)
from watchlog.core.sync import last_sync_at, show_needs_refresh, sync, sync_due

NOW = utcnow()


def timestamp(dt: datetime) -> int:
    """datetime UTC naïf -> horodatage Unix (format de /updates/shows)."""
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


# --- Faux clients ------------------------------------------------------------

class FakeTVMaze:
    def __init__(self, updates=None, down=False, broken_ids=()):
        self.updates = updates or {}
        self.down = down
        self.broken_ids = set(broken_ids)
        self.fetched: list[int] = []

    def updated_since(self, since="week"):
        assert since == "week"
        if self.down:
            raise ApiError("TVMaze indisponible : erreur réseau")
        return self.updates

    def get_show(self, tvmaze_id, force=False):
        self.fetched.append(tvmaze_id)
        if tvmaze_id in self.broken_ids:
            raise ApiError("TVMaze indisponible : erreur serveur (500)")
        episode = {"id": tvmaze_id * 100 + 1, "season": 1, "number": 1, "name": "Pilote",
                   "airdate": "2020-01-01", "airstamp": None, "runtime": 45}
        return {"id": tvmaze_id, "name": f"Série {tvmaze_id}", "status": "Running",
                "_embedded": {"episodes": [episode]}}

    def get_episodes_with_specials(self, tvmaze_id, status, force=False):
        return []

    def download(self, url, dest):
        return False


class FakeTMDB:
    def __init__(self, available=True):
        self.available = available
        self.fetched: list[int] = []

    def get_movie(self, tmdb_id, force=False):
        self.fetched.append(tmdb_id)
        return {"id": tmdb_id, "title": f"Film {tmdb_id} (à jour)", "release_date": "2030-05-01",
                "release_dates": {"results": []}}

    def download(self, url, dest):
        return False


# --- Données de test ---------------------------------------------------------

def add_show(session, tvmaze_id, last_synced_at, status=WATCHING, match=MATCH_OK):
    show = Show(title=f"Série {tvmaze_id}", tvmaze_id=tvmaze_id, match_status=match,
                last_synced_at=last_synced_at)
    session.add(show)
    session.flush()
    session.add(LibraryItem(kind=SHOW, ref_id=show.id, status=status))
    return show


def add_movie(session, tmdb_id, last_synced_at=None, release=None, in_library=True):
    movie = Movie(title=f"Film {tmdb_id}", tmdb_id=tmdb_id, last_synced_at=last_synced_at,
                  release_date_fr=release)
    session.add(movie)
    session.flush()
    if in_library:
        session.add(LibraryItem(kind=MOVIE, ref_id=movie.id))
    return movie


def make_db(tmp_path):
    return make_engine(tmp_path / "test.db")


# --- Tests -------------------------------------------------------------------

def test_show_needs_refresh_rules():
    yesterday = NOW - timedelta(days=1)
    assert show_needs_refresh(None, None, NOW)                       # jamais synchronisée
    assert show_needs_refresh(NOW - timedelta(days=8), None, NOW)   # trop ancienne
    assert not show_needs_refresh(yesterday, None, NOW)             # pas de changement
    assert show_needs_refresh(yesterday, timestamp(NOW - timedelta(hours=2)), NOW)
    assert not show_needs_refresh(yesterday, timestamp(NOW - timedelta(days=3)), NOW)


def test_sync_selects_only_changed_or_old_shows(tmp_path):
    engine = make_db(tmp_path)
    recent = NOW - timedelta(days=1)
    with Session(engine) as s:
        add_show(s, 1, recent)                                  # rien de neuf : ignorée
        add_show(s, 2, recent)                                  # modifiée depuis : rechargée
        add_show(s, 3, NOW - timedelta(days=10))                # trop ancienne : rechargée
        add_show(s, 4, None)                                    # jamais synchronisée
        add_show(s, 5, None, status=STOPPED)                    # arrêtée : ignorée
        add_show(s, 6, None, match=MATCH_UNCERTAIN)             # à confirmer : ignorée
        add_show(s, 7, recent, match=MATCH_REMAPPED)            # remappée, modifiée
        s.add(Show(title="Sans TVMaze", tvmaze_id=None))        # hors bibliothèque, sans id
        s.commit()

    tvmaze = FakeTVMaze(updates={
        2: timestamp(NOW - timedelta(hours=1)),
        7: timestamp(NOW - timedelta(hours=1)),
        1: timestamp(NOW - timedelta(days=3)),   # modifiée AVANT notre dernière synchro
        999: timestamp(NOW),                     # pas dans la bibliothèque
    })
    report = sync(engine, tvmaze, FakeTMDB(), download_posters=False)

    assert sorted(tvmaze.fetched) == [2, 3, 4, 7]
    assert report.shows_checked == 5
    assert report.shows_updated == 4
    assert report.errors == []
    with Session(engine) as s:
        show = s.exec(select(Show).where(Show.tvmaze_id == 4)).one()
        assert show.last_synced_at is not None
        assert show.title == "Série 4"


def test_sync_tvmaze_down_skips_shows_but_not_movies(tmp_path):
    engine = make_db(tmp_path)
    with Session(engine) as s:
        add_show(s, 1, None)
        add_movie(s, 10)
        s.commit()

    tvmaze, tmdb = FakeTVMaze(down=True), FakeTMDB()
    report = sync(engine, tvmaze, tmdb, download_posters=False)

    assert report.tvmaze_down
    assert tvmaze.fetched == []
    assert tmdb.fetched == [10]
    assert "TVMaze indisponible : séries non mises à jour." in report.lines()


def test_sync_movies_selection(tmp_path):
    engine = make_db(tmp_path)
    today = date.today()
    with Session(engine) as s:
        add_movie(s, 10, NOW, release=today - timedelta(days=30))   # sorti et à jour : ignoré
        add_movie(s, 11, NOW, release=today + timedelta(days=30))   # pas encore sorti
        add_movie(s, 12, NOW, release=None)                         # aucune date connue
        add_movie(s, 13, None, release=today - timedelta(days=30))  # jamais synchronisé
        add_movie(s, 14, None, in_library=False)                    # hors bibliothèque
        s.commit()

    tmdb = FakeTMDB()
    report = sync(engine, FakeTVMaze(), tmdb, download_posters=False)

    assert sorted(tmdb.fetched) == [11, 12, 13]
    assert report.movies_checked == 3
    assert report.movies_updated == 3
    with Session(engine) as s:
        movie = s.exec(select(Movie).where(Movie.tmdb_id == 13)).one()
        assert movie.release_date_original == date(2030, 5, 1)
        assert movie.last_synced_at is not None


def test_sync_tmdb_not_connected(tmp_path):
    engine = make_db(tmp_path)
    with Session(engine) as s:
        add_movie(s, 10)
        s.commit()

    tmdb = FakeTMDB(available=False)
    report = sync(engine, FakeTVMaze(), tmdb, download_posters=False)

    assert report.tmdb_down
    assert tmdb.fetched == []
    assert "TMDB non connecté : films non mis à jour." in report.lines()


def test_sync_error_on_one_show_does_not_stop_the_others(tmp_path):
    engine = make_db(tmp_path)
    with Session(engine) as s:
        add_show(s, 1, None)
        add_show(s, 2, None)
        add_show(s, 3, None)
        s.commit()

    progress_calls = []
    tvmaze = FakeTVMaze(broken_ids={2})
    report = sync(engine, tvmaze, FakeTMDB(), download_posters=False,
                  progress=lambda done, total, label: progress_calls.append((done, total)))

    assert sorted(tvmaze.fetched) == [1, 2, 3]
    assert report.shows_updated == 2
    assert len(report.errors) == 1 and "Série 2" in report.errors[0]
    assert progress_calls[-1] == (3, 3)
    with Session(engine) as s:
        synced = {sh.tvmaze_id: sh.last_synced_at for sh in s.exec(select(Show))}
        assert synced[1] is not None and synced[3] is not None
        assert synced[2] is None


# --- Date de la dernière synchro et synchro automatique --------------------------

def test_sync_remembers_date_and_report(tmp_path):
    engine = make_db(tmp_path)
    with Session(engine) as s:
        assert sync_due(s)                       # jamais synchronisé
    sync(engine, FakeTVMaze(), FakeTMDB(), download_posters=False)

    with Session(engine) as s:
        last = last_sync_at(s)
        assert last is not None and utcnow() - last < timedelta(minutes=1)
        assert not sync_due(s)
        report = settings.get_value(s, settings.LAST_SYNC_REPORT)
        assert report["problems"] == 0 and "Aucune erreur." in report["lines"]


def test_sync_tvmaze_down_keeps_old_date_but_reports_problem(tmp_path):
    engine = make_db(tmp_path)
    old = NOW - timedelta(days=2)
    with Session(engine) as s:
        settings.set_value(s, settings.LAST_SYNC_AT, old.isoformat())
        s.commit()
    sync(engine, FakeTVMaze(down=True), FakeTMDB(), download_posters=False)

    with Session(engine) as s:
        assert last_sync_at(s) == old            # la prochaine ouverture réessaiera
        assert sync_due(s)
        assert settings.get_value(s, settings.LAST_SYNC_REPORT)["problems"] == 1


def test_sync_due_after_24_hours(tmp_path):
    engine = make_db(tmp_path)
    with Session(engine) as s:
        settings.set_value(s, settings.LAST_SYNC_AT, (NOW - timedelta(hours=23)).isoformat())
        s.commit()
        assert not sync_due(s, now=NOW)
        assert sync_due(s, now=NOW + timedelta(hours=2))
