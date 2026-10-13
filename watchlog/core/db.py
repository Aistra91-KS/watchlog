"""Connexion SQLite et création des tables."""

from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, event, inspect, text
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
    add_missing_columns(engine)
    return engine


def add_missing_columns(engine: Engine) -> list[str]:
    """Migration simple : ajoute aux tables existantes les colonnes nouvelles du modèle.

    create_all ne crée que les tables absentes. Une nouvelle colonne (toujours facultative,
    sans valeur par défaut) est ajoutée par ALTER TABLE ; les données ne sont pas touchées.
    Renvoie la liste « table.colonne » ajoutée.
    """
    added = []
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing or not column.nullable:
                    continue
                kind = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}'))
                added.append(f"{table.name}.{column.name}")
    return added


@lru_cache
def get_engine() -> Engine:
    """Moteur de la base principale (data/watchlog.db), créé une seule fois."""
    return make_engine()


def session(engine: Engine | None = None) -> Session:
    return Session(engine or get_engine())
