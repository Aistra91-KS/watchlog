"""Tests des réglages gardés en base (watchlog/core/settings.py)."""

from datetime import datetime

from sqlmodel import Session

from watchlog.core import settings
from watchlog.core.dates import fmt_ago
from watchlog.core.models import Setting


def test_set_and_get_values(engine):
    with Session(engine) as s:
        assert settings.get_value(s, "absent", default=[]) == []
        settings.set_value(s, "liste", ["Netflix", "Canal+"])
        settings.set_value(s, "liste", ["Disney Plus"])          # remplace
        settings.set_value(s, "rapport", {"lignes": ["é"], "n": 2})
        s.commit()
    with Session(engine) as s:
        assert settings.get_value(s, "liste") == ["Disney Plus"]
        assert settings.get_value(s, "rapport") == {"lignes": ["é"], "n": 2}


def test_unreadable_value_falls_back_to_default(engine, caplog):
    with Session(engine) as s:
        s.add(Setting(key="cassé", value="{pas du json"))
        s.commit()
        assert settings.get_value(s, "cassé", default=0) == 0
    assert "illisible" in caplog.text


def test_fmt_ago():
    now = datetime(2026, 10, 22, 12, 0)
    assert fmt_ago(datetime(2026, 10, 22, 11, 59, 40), now) == "à l'instant"
    assert fmt_ago(datetime(2026, 10, 22, 11, 55), now) == "il y a 5 min"
    assert fmt_ago(datetime(2026, 10, 22, 9, 10), now) == "il y a 2 h"
    assert fmt_ago(datetime(2026, 10, 21, 11, 0), now) == "il y a 1 jour"
    assert fmt_ago(datetime(2026, 10, 18, 12, 0), now) == "il y a 4 jours"
