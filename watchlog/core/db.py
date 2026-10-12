"""Connexion SQLite et création des tables."""

from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

from watchlog.core import models  # noqa: F401  (enregistre les tables)
from watchlog.core.config import DB_PATH


def make_engine(db_path: Path = DB_PATH) -> Engine:
    """Crée (si besoin) la base et ses tables, et renvoie le moteur SQLAlchemy."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")  # lectures (UI) pendant une écriture (sync)
        cur.close()

    SQLModel.metadata.create_all(engine)
    return engine


@lru_cache
def get_engine() -> Engine:
    """Moteur de la base principale (data/watchlog.db), créé une seule fois."""
    return make_engine()


def session(engine: Engine | None = None) -> Session:
    return Session(engine or get_engine())
