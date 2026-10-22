"""Petits utilitaires de dates.

Convention : en base, les datetimes sont en UTC « naïf » (sans fuseau),
car SQLite ne stocke pas le fuseau. Les dates (sans heure) sont des `date`.
"""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Paris")  # heure française, pour l'affichage et les saisies


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


def fmt_ago(when: datetime, now: datetime) -> str:
    """Temps écoulé lisible : « à l'instant », « il y a 5 min », « il y a 3 h », « il y a 2 jours »."""
    minutes = int((now - when).total_seconds() // 60)
    if minutes < 1:
        return "à l'instant"
    if minutes < 60:
        return f"il y a {minutes} min"
    if minutes < 24 * 60:
        return f"il y a {minutes // 60} h"
    days = minutes // (24 * 60)
    return f"il y a {days} jour{'s' if days > 1 else ''}"


def local_day_to_utc(day: date) -> datetime:
    """Jour saisi par l'utilisateur -> datetime UTC naïf (midi, heure française).

    Midi : la date affichée (reconvertie en heure française) reste toujours la même.
    """
    return datetime.combine(day, time(12), LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)
