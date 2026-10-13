"""Plateformes où regarder un film ou une série en France (abonnement, location, achat).

Données JustWatch fournies par TMDB (/watch/providers). JustWatch doit être cité.
"""

from dataclasses import dataclass, field

from sqlmodel import Session

from watchlog.core.http import ApiError
from watchlog.core.models import MOVIE, SHOW, Show

REGION = "FR"
LOGO_BASE = "https://image.tmdb.org/t/p/w92"
ATTRIBUTION = "Disponibilités en France : JustWatch (via TMDB)."

# Types d'offre TMDB, dans l'ordre d'affichage
OFFER_LABELS = {
    "flatrate": "Abonnement",
    "free": "Gratuit",
    "ads": "Gratuit avec pub",
    "rent": "Location",
    "buy": "Achat",
}
TMDB_MEDIA = {SHOW: "tv", MOVIE: "movie"}


@dataclass
class Provider:
    name: str
    logo_url: str | None


@dataclass
class Availability:
    offers: dict[str, list[Provider]] = field(default_factory=dict)  # libellé -> plateformes
    link: str | None = None   # page TMDB « Où regarder » (liens vers chaque plateforme)

    @property
    def empty(self) -> bool:
        return not self.offers


def parse(region_data: dict) -> Availability:
    """Bloc d'un pays de la réponse TMDB -> Availability (plateformes triées par priorité)."""
    offers = {}
    for key, label in OFFER_LABELS.items():
        entries = sorted(region_data.get(key) or [], key=lambda e: e.get("display_priority", 999))
        providers = [Provider(e["provider_name"],
                              f"{LOGO_BASE}{e['logo_path']}" if e.get("logo_path") else None)
                     for e in entries if e.get("provider_name")]
        if providers:
            offers[label] = providers
    return Availability(offers=offers, link=region_data.get("link"))


def availability(tmdb, kind: str, tmdb_id: int | None) -> Availability | None:
    """Disponibilités en France. None si inconnu (pas d'id TMDB, TMDB indisponible)."""
    if tmdb_id is None:
        return None
    try:
        results = tmdb.watch_providers(TMDB_MEDIA[kind], tmdb_id)
    except ApiError:
        return None
    if not results and not tmdb.available:
        return None
    return parse(results.get(REGION) or {})


def show_tmdb_id(session: Session, tmdb, show: Show) -> int | None:
    """Id TMDB de la série ; s'il manque, on le cherche via l'id TVDB et on l'enregistre (sans commit)."""
    if show.tmdb_id is None and show.tvdb_id is not None:
        try:
            show.tmdb_id = tmdb.find_tv_by_tvdb(show.tvdb_id)
        except ApiError:
            return None
        if show.tmdb_id is not None:
            session.add(show)
    return show.tmdb_id
