"""Réglages et états gardés en base (table Setting), d'un lancement à l'autre.

Chaque valeur est stockée en JSON : nombre, texte, liste ou dictionnaire.
Pas de commit : l'appelant enregistre.
"""

import json
import logging

from sqlmodel import Session

from watchlog.core.models import Setting

log = logging.getLogger(__name__)

# Clés connues
LAST_SYNC_AT = "last_sync_at"          # fin de la dernière synchro où TVMaze a répondu (ISO, UTC)
LAST_SYNC_REPORT = "last_sync_report"  # {"at": ISO UTC, "lines": [...], "problems": n}


def get_value(session: Session, key: str, default=None):
    """Valeur enregistrée sous `key`, sinon `default` (valeur illisible : journalisée)."""
    row = session.get(Setting, key)
    if row is None or row.value is None:
        return default
    try:
        return json.loads(row.value)
    except ValueError:
        log.warning("Réglage %s illisible en base, valeur par défaut utilisée : %r", key, row.value)
        return default


def set_value(session: Session, key: str, value) -> None:
    """Enregistre `value` (sérialisable en JSON) sous `key`. Pas de commit."""
    row = session.get(Setting, key) or Setting(key=key)
    row.value = json.dumps(value, ensure_ascii=False)
    session.add(row)
