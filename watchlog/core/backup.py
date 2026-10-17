"""Sauvegardes de la base SQLite (dossier data/backups).

La base est en mode WAL : une partie des données récentes peut se trouver dans le
fichier watchlog.db-wal. Copier seulement watchlog.db pourrait donc donner une copie
incomplète. On utilise l'API de sauvegarde de SQLite, qui produit une copie cohérente.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from watchlog.core.config import BACKUP_DIR, DB_PATH

log = logging.getLogger(__name__)

PREFIX = "watchlog-"          # nom : watchlog-AAAAMMJJ-HHMMSS.db
PATTERN = "watchlog-*.db"


def list_backups(backup_dir: Path = BACKUP_DIR) -> list[Path]:
    """Sauvegardes existantes, la plus récente en premier.

    Le nom contient la date (AAAAMMJJ-HHMMSS) : l'ordre alphabétique est l'ordre chronologique.
    """
    backup_dir = Path(backup_dir)
    if not backup_dir.is_dir():
        return []
    return sorted(backup_dir.glob(PATTERN), reverse=True)


def backup_date(path: Path) -> datetime | None:
    """Date lue dans le nom du fichier (None si le nom ne suit pas le format)."""
    try:
        return datetime.strptime(path.stem[len(PREFIX):], "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def make_backup(db_path: Path = DB_PATH, backup_dir: Path = BACKUP_DIR, keep: int = 5) -> Path | None:
    """Copie horodatée de la base, en ne gardant que les `keep` plus récentes.

    Renvoie le chemin de la copie, ou None si la base n'existe pas ou en cas d'erreur
    (une sauvegarde ratée ne doit jamais empêcher d'utiliser l'outil : on note dans le log).
    """
    db_path, backup_dir = Path(db_path), Path(backup_dir)
    if not db_path.is_file():
        return None
    dest = backup_dir / f"{PREFIX}{datetime.now():%Y%m%d-%H%M%S}.db"
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(db_path)
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        _remove_old(backup_dir, keep)
        log.info("Sauvegarde créée : %s", dest.name)
        return dest
    except (OSError, sqlite3.Error) as exc:
        log.error("Sauvegarde impossible : %s", exc)
        try:  # une copie partielle compterait parmi les `keep` et évincerait une bonne
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def _remove_old(backup_dir: Path, keep: int) -> None:
    """Supprime les sauvegardes au-delà des `keep` plus récentes."""
    for old in list_backups(backup_dir)[keep:]:
        try:
            old.unlink()
        except OSError as exc:
            log.warning("Ancienne sauvegarde non supprimée (%s) : %s", old.name, exc)
