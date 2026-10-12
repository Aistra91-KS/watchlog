"""Petits utilitaires de dates.

Convention : en base, les datetimes sont en UTC « naïf » (sans fuseau),
car SQLite ne stocke pas le fuseau. Les dates (sans heure) sont des `date`.
"""

from datetime import date, datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_datetime_utc(value: str | None) -> datetime | None:
    """'2026-08-13T17:02:42.346Z' -> datetime UTC naïf. Vide ou invalide -> None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_date(value: str | None) -> date | None:
    """'2024-05-17' (ou un datetime ISO) -> date. Vide ou invalide -> None."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def fmt_date(value: date | datetime | None) -> str:
    """Format français jj/mm/aaaa, chaîne vide si pas de date."""
    return value.strftime("%d/%m/%Y") if value else ""
