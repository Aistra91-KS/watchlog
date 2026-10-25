"""Données partagées avec l'app web : web/js/core/taste-data.js est généré depuis taste.py.

Ainsi une traduction de mot-clé ajoutée côté Python (KEYWORDS_FR) n'est pas oubliée côté web.
Après un changement de taste.py : WATCHLOG_REGEN_FIXTURES=1 pytest tests/test_web_sync.py
"""

import json
import os
from pathlib import Path

from watchlog.core import taste

TASTE_DATA_JS = Path(__file__).resolve().parents[1] / "web" / "js" / "core" / "taste-data.js"


def taste_data_js() -> str:
    """Contenu attendu de taste-data.js (constantes de taste.py en JSON)."""
    def dump(value) -> str:
        return json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True)

    split = {str(k): list(v) for k, v in taste.TV_GENRE_SPLIT.items()}
    return (
        "// Fichier GÉNÉRÉ depuis watchlog/core/taste.py par tests/test_web_sync.py : ne pas\n"
        "// modifier à la main (WATCHLOG_REGEN_FIXTURES=1 pytest tests/test_web_sync.py).\n\n"
        f"export const IGNORED_KEYWORDS = new Set({dump(sorted(taste.IGNORED_KEYWORDS))});\n\n"
        f"export const KEYWORDS_FR = {dump(taste.KEYWORDS_FR)};\n\n"
        f"export const TV_GENRE_SPLIT = {dump(split)};\n\n"
        f"export const GENRE_ALIASES = {dump(taste.GENRE_ALIASES)};\n\n"
        f"export const GENRE_FACTOR = {taste.GENRE_FACTOR};\n"
        f"export const NEGATIVE_FACTOR = {taste.NEGATIVE_FACTOR};\n"
        f"export const MIN_SUPPORT = {taste.MIN_SUPPORT};\n"
        f"export const BOOST = {taste.BOOST};\n"
    )


def test_taste_data_js_is_in_sync():
    expected = taste_data_js()
    if os.getenv("WATCHLOG_REGEN_FIXTURES"):
        TASTE_DATA_JS.write_text(expected, encoding="utf-8", newline="\n")
    assert TASTE_DATA_JS.read_text(encoding="utf-8").replace("\r\n", "\n") == expected, (
        "web/js/core/taste-data.js n'est plus à jour : relancer ce test avec "
        "WATCHLOG_REGEN_FIXTURES=1")
