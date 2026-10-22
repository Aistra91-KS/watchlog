"""Plateformes où regarder un film ou une série en France (abonnement, location, achat).

Données JustWatch fournies par TMDB (/watch/providers). JustWatch doit être cité.
"""

import logging
from dataclasses import dataclass, field

from sqlmodel import Session

from watchlog.core.http import ApiError
from watchlog.core.models import MOVIE, SHOW, Show

REGION = "FR"
LOGO_BASE = "https://image.tmdb.org/t/p/w92"
ATTRIBUTION = "Disponibilités en France : JustWatch (via TMDB)."

log = logging.getLogger(__name__)

# Types d'offre TMDB, dans l'ordre d'affichage
OFFER_LABELS = {
    "flatrate": "Abonnement",
    "free": "Gratuit",
    "ads": "Gratuit avec pub",
    "rent": "Location",
    "buy": "Achat",
}
TMDB_MEDIA = {SHOW: "tv", MOVIE: "movie"}
# Offres qui permettent de regarder sans payer à l'unité : « dispo en streaming »,
# « sur mes plateformes » (la location et l'achat n'en font pas partie)
STREAMING_OFFERS = ("Abonnement", "Gratuit", "Gratuit avec pub")
UNKNOWN_PRIORITY = 999   # plateforme sans priorité d'affichage : en fin de liste


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


def streaming_names(data: Availability) -> list[str]:
    """Plateformes où le titre se regarde en abonnement ou gratuitement, sans doublon."""
    names = []
    for offer in STREAMING_OFFERS:
        names += [p.name for p in data.offers.get(offer, []) if p.name not in names]
    return names


def platform_choices(tmdb, saved: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Plateformes proposées pour « Mes plateformes » : films et séries en France.

    Triées par priorité d'affichage TMDB (les plus courantes d'abord) ; les plateformes déjà
    enregistrées absentes de la liste sont gardées à la fin (sinon le réglage les perdrait
    sans prévenir). ApiError si TMDB ne répond pas.
    """
    priorities: dict[str, int] = {}
    for media in TMDB_MEDIA.values():
        for entry in tmdb.provider_list(media, REGION):
            name = entry.get("provider_name")
            if not name:
                continue
            priority = (entry.get("display_priorities") or {}).get(
                REGION, entry.get("display_priority", UNKNOWN_PRIORITY))
            priorities[name] = min(priority, priorities.get(name, UNKNOWN_PRIORITY))
    names = sorted(priorities, key=lambda n: (priorities[n], n.casefold()))
    return names + [n for n in saved if n not in priorities]


def availability(tmdb, kind: str, tmdb_id: int | None,
                 raise_errors: bool = False) -> Availability | None:
    """Disponibilités en France. None si inconnu (pas d'id TMDB, TMDB indisponible).

    raise_errors=True : une panne de TMDB lève ApiError au lieu de renvoyer None
    (pour la compter et la signaler, au lieu d'afficher « pas disponible »).
    """
    if tmdb_id is None:
        return None
    try:
        results = tmdb.watch_providers(TMDB_MEDIA[kind], tmdb_id)
    except ApiError as exc:
        log.warning("Plateformes non lues pour %s %s : %s", kind, tmdb_id, exc)
        if raise_errors:
            raise
        return None
    if not results and not tmdb.available:
        return None
    return parse(results.get(REGION) or {})


def show_tmdb_id(session: Session, tmdb, show: Show, raise_errors: bool = False) -> int | None:
    """Id TMDB de la série ; s'il manque, on le cherche via l'id TVDB et on l'enregistre.

    Pas de commit. raise_errors=True : une panne de TMDB lève ApiError (à distinguer
    d'une série que TMDB ne connaît pas).
    """
    if show.tmdb_id is None and show.tvdb_id is not None:
        try:
            show.tmdb_id = tmdb.find_tv_by_tvdb(show.tvdb_id)
        except ApiError as exc:
            log.warning("Id TMDB introuvable pour « %s » (TVDB %s) : %s",
                        show.title, show.tvdb_id, exc)
            if raise_errors:
                raise
            return None
        if show.tmdb_id is not None:
            session.add(show)
    return show.tmdb_id
