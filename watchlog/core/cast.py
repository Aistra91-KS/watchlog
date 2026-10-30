"""Personnages d'un titre, pour retrouver un nom oublié (« Personnage préféré » de Mon avis).

Séries : distribution principale TVMaze (photo du personnage quand elle existe), complétée par
les rôles récurrents TMDB (toute la série, les plus présents d'abord). Films : casting TMDB.
Une source qui ne répond pas est journalisée et nommée dans `Cast.errors` : la liste est
alors incomplète, et l'interface le dit.
"""

import logging
import re
from dataclasses import dataclass, field

from watchlog.core.http import ApiError
from watchlog.core.models import Movie, Show
from watchlog.core.preview import PROFILE_BASE

log = logging.getLogger(__name__)

MAX_CHARACTERS = 24   # personnages proposés, les plus présents d'abord
# Rôles qui ne sont pas des personnages (l'acteur joue son propre rôle), en minuscules
SELF_ROLES = {"self", "himself", "herself", "themselves", "lui-même", "elle-même"}
# Mentions TMDB en fin de rôle, retirées : « Rick Sanchez (voice) » -> « Rick Sanchez »
ROLE_NOTES = re.compile(r"(\s*\((voice|uncredited|archive footage|voix)\))+\s*$", re.IGNORECASE)


@dataclass
class Character:
    name: str                  # nom du personnage : c'est lui qui est enregistré
    actor: str | None
    photo_url: str | None      # photo du personnage (TVMaze), sinon de l'acteur

    @property
    def label(self) -> str:
        """« Walter White (Bryan Cranston) »."""
        return f"{self.name} ({self.actor})" if self.actor else self.name


@dataclass
class Cast:
    characters: list[Character] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)   # sources qui n'ont pas répondu


def clean_role(role: str | None) -> str | None:
    """Nom du personnage sans les mentions « (voice) »... ; None si ce n'est pas un personnage."""
    name = ROLE_NOTES.sub("", (role or "").strip()).strip()
    return name if name and name.casefold() not in SELF_ROLES else None


def _merge(characters: list[Character]) -> list[Character]:
    """Un personnage par nom (le premier vu) ; une photo manquante est prise sur un doublon."""
    kept: dict[str, Character] = {}
    for character in characters:
        key = character.name.casefold()
        if key not in kept:
            kept[key] = character
        elif kept[key].photo_url is None and character.photo_url:
            kept[key].photo_url = character.photo_url
    return list(kept.values())[:MAX_CHARACTERS]


def _profile(path: str | None) -> str | None:
    return f"{PROFILE_BASE}{path}" if path else None


def _tmdb_cast(cast: list[dict]) -> list[Character]:
    """Casting TMDB d'un film (ordre du générique)."""
    result = []
    for person in sorted(cast, key=lambda p: p.get("order", 999)):
        name = clean_role(person.get("character"))
        if name:
            result.append(Character(name, person.get("name") or None,
                                    _profile(person.get("profile_path"))))
    return result


def _tmdb_aggregate(cast: list[dict]) -> list[Character]:
    """Casting TMDB de toute une série : les rôles les plus présents (épisodes) d'abord."""
    roles = []
    for person in cast:
        for role in person.get("roles") or []:
            name = clean_role(role.get("character"))
            if name:
                roles.append((role.get("episode_count") or 0, person.get("order", 999),
                              Character(name, person.get("name") or None,
                                        _profile(person.get("profile_path")))))
    roles.sort(key=lambda r: (-r[0], r[1]))
    return [character for _count, _order, character in roles]


def _tvmaze_cast(entries: list[dict]) -> list[Character]:
    result = []
    for entry in entries:
        if entry.get("self"):
            continue
        person, role = entry.get("person") or {}, entry.get("character") or {}
        name = clean_role(role.get("name"))
        if name:
            photo = (role.get("image") or {}).get("medium") or \
                (person.get("image") or {}).get("medium")
            result.append(Character(name, person.get("name") or None, photo or None))
    return result


def show_cast(tvmaze, tmdb, show: Show) -> Cast:
    """Personnages d'une série : TVMaze (principaux), puis TMDB (récurrents)."""
    cast = Cast()
    characters: list[Character] = []
    if show.tvmaze_id:
        try:
            characters += _tvmaze_cast(tvmaze.get_cast(show.tvmaze_id))
        except ApiError as exc:
            log.warning("Casting TVMaze indisponible pour « %s » : %s", show.title, exc)
            cast.errors.append("TVMaze")
    if show.tmdb_id:
        try:
            data = tmdb.tv_aggregate_credits(show.tmdb_id)   # None : TMDB non connecté
        except ApiError as exc:
            log.warning("Casting TMDB indisponible pour « %s » : %s", show.title, exc)
            cast.errors.append("TMDB")
        else:
            characters += _tmdb_aggregate((data or {}).get("cast") or [])
    cast.characters = _merge(characters)
    return cast


def movie_cast(tmdb, movie: Movie) -> Cast:
    """Personnages d'un film (casting TMDB, même fiche en cache que la page Aperçu)."""
    cast = Cast()
    if not movie.tmdb_id:
        return cast
    try:
        data = tmdb.details("movie", movie.tmdb_id)   # None : TMDB non connecté
    except ApiError as exc:
        log.warning("Casting TMDB indisponible pour « %s » : %s", movie.title, exc)
        cast.errors.append("TMDB")
        return cast
    cast.characters = _merge(_tmdb_cast(((data or {}).get("credits") or {}).get("cast") or []))
    return cast
