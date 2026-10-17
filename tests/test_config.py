"""Tests du filtre qui masque les secrets dans les logs (faux secrets uniquement)."""

import io
import logging
import sys

from watchlog.core.config import SecretFilter

FAKE_TOKEN = "fake-token-123456"
FAKE_KEY = "fake-key-abcdef"


def make_record(msg, *args, exc_info=None):
    return logging.LogRecord("test", logging.WARNING, __file__, 1, msg, args, exc_info)


def test_secret_filter_replaces_secrets(monkeypatch):
    monkeypatch.setenv("TMDB_READ_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("TMDB_API_KEY", FAKE_KEY)
    record = make_record("GET %s avec %s", f"/movie/1?api_key={FAKE_KEY}", FAKE_TOKEN)

    assert SecretFilter().filter(record) is True  # le message est gardé, mais nettoyé

    message = record.getMessage()
    assert message == "GET /movie/1?api_key=*** avec ***"
    assert FAKE_TOKEN not in message and FAKE_KEY not in message


def test_secret_filter_keeps_traceback_without_secret(monkeypatch):
    monkeypatch.setenv("TMDB_READ_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("TMDB_API_KEY", "")
    try:
        raise ValueError(f"erreur avec {FAKE_TOKEN}")
    except ValueError:
        record = make_record("plantage", exc_info=sys.exc_info())

    SecretFilter().filter(record)

    # La cause reste dans le journal (sinon log.exception n'apprend rien), secret masqué.
    assert record.exc_info is None
    assert "ValueError: erreur avec ***" in record.exc_text
    assert FAKE_TOKEN not in record.exc_text


def test_secret_filter_in_real_logger(monkeypatch):
    monkeypatch.setenv("TMDB_READ_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("TMDB_API_KEY", FAKE_KEY)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(SecretFilter())
    logger = logging.getLogger("watchlog.test_secret_filter")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning("Bearer %s refusé (clé %s)", FAKE_TOKEN, FAKE_KEY)
    finally:
        logger.removeHandler(handler)

    assert output.getvalue().strip() == "Bearer *** refusé (clé ***)"


def test_secret_filter_without_secret_keeps_message(monkeypatch):
    monkeypatch.setenv("TMDB_READ_TOKEN", "")
    monkeypatch.setenv("TMDB_API_KEY", "")
    record = make_record("rien de %s ici", "secret")

    SecretFilter().filter(record)

    assert record.getMessage() == "rien de secret ici"
