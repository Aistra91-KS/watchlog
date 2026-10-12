"""Configuration : chemins du projet, identifiants TMDB (lus dans .env) et logs."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
# WATCHLOG_DATA_DIR permet d'utiliser un autre dossier de données (tests, essais).
DATA_DIR = Path(os.getenv("WATCHLOG_DATA_DIR") or ROOT / "data")
DB_PATH = DATA_DIR / "watchlog.db"
POSTERS_DIR = DATA_DIR / "posters"
BACKUP_DIR = DATA_DIR / "backups"
LOG_PATH = DATA_DIR / "watchlog.log"

# Les identifiants ne sont lus QUE depuis .env (ou l'environnement). Jamais en dur.
load_dotenv(ROOT / ".env")


def tmdb_read_token() -> str:
    return os.getenv("TMDB_READ_TOKEN", "").strip()


def tmdb_api_key() -> str:
    return os.getenv("TMDB_API_KEY", "").strip()


class SecretFilter(logging.Filter):
    """Remplace les valeurs secrètes par *** dans tout ce qui part dans les logs.

    Ceinture et bretelles : le code ne logue jamais les identifiants,
    mais une URL ou une exception pourrait en contenir.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = [s for s in (tmdb_read_token(), tmdb_api_key()) if s]
        if secrets:
            message = record.getMessage()
            for secret in secrets:
                message = message.replace(secret, "***")
            record.msg, record.args = message, None
            record.exc_info = record.exc_text = None  # une trace pourrait contenir un secret
        return True


def setup_logging() -> None:
    """Logs simples dans data/watchlog.log (appelable plusieurs fois sans doublon)."""
    root = logging.getLogger()
    if any(getattr(h, "_watchlog", False) for h in root.handlers):
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler._watchlog = True
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s : %(message)s"))
    handler.addFilter(SecretFilter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # httpx logue chaque URL en INFO (avec api_key éventuelle) : on le fait taire.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
