"""Tests des sauvegardes de la base."""

import sqlite3

from sqlmodel import Session

from watchlog.core.backup import backup_date, list_backups, make_backup
from watchlog.core.db import make_engine
from watchlog.core.models import Show


def test_backup_is_readable_copy(tmp_path):
    db_path = tmp_path / "watchlog.db"
    engine = make_engine(db_path)  # base en WAL, comme la vraie
    with Session(engine) as session:
        session.add(Show(title="Le Royaume"))
        session.commit()

    dest = make_backup(db_path, tmp_path / "backups")

    assert dest is not None and dest.exists()
    assert dest.name.startswith("watchlog-") and dest.suffix == ".db"
    assert backup_date(dest) is not None
    with sqlite3.connect(dest) as copy:
        assert copy.execute("SELECT title FROM show").fetchall() == [("Le Royaume",)]
    engine.dispose()


def test_rotation_keeps_five_most_recent(tmp_path):
    db_path = tmp_path / "watchlog.db"
    sqlite3.connect(db_path).close()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    # Six anciennes sauvegardes (fausses), plus un fichier étranger à ne pas toucher.
    old_names = [f"watchlog-2025010{i}-120000.db" for i in range(1, 7)]
    for name in old_names:
        (backup_dir / name).write_bytes(b"")
    (backup_dir / "autre.txt").write_text("à garder")

    dest = make_backup(db_path, backup_dir, keep=5)

    remaining = [p.name for p in list_backups(backup_dir)]
    assert len(remaining) == 5
    assert remaining[0] == dest.name                       # la nouvelle est la plus récente
    assert remaining[1:] == sorted(old_names, reverse=True)[:4]
    assert (backup_dir / "autre.txt").exists()


def test_missing_database_gives_none(tmp_path):
    assert make_backup(tmp_path / "absente.db", tmp_path / "backups") is None
    assert list_backups(tmp_path / "backups") == []


def test_error_does_not_raise(tmp_path):
    db_path = tmp_path / "watchlog.db"
    sqlite3.connect(db_path).close()
    blocker = tmp_path / "pas_un_dossier"
    blocker.write_text("fichier")  # impossible de créer un dossier à cet endroit
    assert make_backup(db_path, blocker / "backups") is None


def test_failed_backup_leaves_no_partial_file(tmp_path, monkeypatch):
    db_path = tmp_path / "watchlog.db"
    make_engine(db_path)
    backups = tmp_path / "backups"
    real_connect = sqlite3.connect

    class Conn:
        def __init__(self, path):
            self.inner = real_connect(path)

        def backup(self, target, *args, **kwargs):
            raise sqlite3.OperationalError("disque plein (simulé)")

        def close(self):
            self.inner.close()

    monkeypatch.setattr(sqlite3, "connect", Conn)

    assert make_backup(db_path, backups) is None
    assert list(backups.iterdir()) == []   # pas de copie partielle comptée parmi les 5
