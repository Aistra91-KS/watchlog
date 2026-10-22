"""Actions de l'utilisateur sur sa bibliothèque : marquer vu, changer de statut...

Ces fonctions ne font pas de commit : l'appelant (UI, import) enregistre quand il veut.
"""

from datetime import date, datetime

from sqlmodel import Session, select

from watchlog.core.dates import LOCAL_TZ, local_day_to_utc, utcnow
from watchlog.core.models import (COMPLETED, EPISODE, SHOW, STATUSES, TO_WATCH, Episode,
                                  LibraryItem, Rating, Show, Watch)
from watchlog.core.tvmaze import ENDED


def _earliest(a: datetime | None, b: datetime | None) -> datetime | None:
    """La plus ancienne des deux dates (en ignorant les None)."""
    dates = [d for d in (a, b) if d is not None]
    return min(dates) if dates else None


def _latest(a: datetime | None, b: datetime | None) -> datetime | None:
    dates = [d for d in (a, b) if d is not None]
    return max(dates) if dates else None


# --- Visionnages -------------------------------------------------------------

def get_watch(session: Session, kind: str, ref_id: int) -> Watch | None:
    return session.exec(select(Watch).where(Watch.kind == kind, Watch.ref_id == ref_id)).first()


def upsert_watch(session: Session, kind: str, ref_id: int, watched_at: datetime | None = None,
                 last_watched_at: datetime | None = None, plays: int = 1) -> Watch:
    """Ajoute un visionnage, ou le fusionne avec celui qui existe déjà (jamais de doublon).

    Fusion : premier visionnage = le plus ancien, dernier = le plus récent, plays = le maximum.
    Relancer la même fusion ne change donc rien (import idempotent).
    """
    last_watched_at = last_watched_at or watched_at
    watch = get_watch(session, kind, ref_id)
    if watch is None:
        watch = Watch(kind=kind, ref_id=ref_id, watched_at=watched_at,
                      last_watched_at=last_watched_at, plays=max(plays, 1))
    else:
        watch.watched_at = _earliest(watch.watched_at, watched_at)
        watch.last_watched_at = _latest(watch.last_watched_at, last_watched_at)
        watch.plays = max(watch.plays, plays)
    session.add(watch)
    return watch


def mark_watched(session: Session, kind: str, ref_id: int, when: datetime | None = None) -> Watch:
    """Marque un épisode ou un film comme vu (ne fait rien s'il l'est déjà)."""
    watch = get_watch(session, kind, ref_id)
    if watch is not None:
        return watch
    when = when or utcnow()
    watch = Watch(kind=kind, ref_id=ref_id, watched_at=when, last_watched_at=when)
    session.add(watch)
    return watch


def set_watch_date(session: Session, kind: str, ref_id: int, day: date | None) -> Watch:
    """Change la date du (premier) visionnage, et marque vu si ce n'était pas le cas.

    day=None : vu, date inconnue. Vu une seule fois : le dernier visionnage suit la même date ;
    vu plusieurs fois, il n'est avancé que si la nouvelle date est plus récente.
    """
    when = local_day_to_utc(day) if day is not None else None
    watch = get_watch(session, kind, ref_id)
    if watch is None:
        watch = Watch(kind=kind, ref_id=ref_id, watched_at=when, last_watched_at=when)
    else:
        watch.watched_at = when
        if watch.plays <= 1 or watch.last_watched_at is None:
            watch.last_watched_at = when
        elif when is not None and watch.last_watched_at < when:
            watch.last_watched_at = when
    session.add(watch)
    return watch


def set_show_watch_dates(session: Session, show_id: int, day: date | None,
                         seasons: list[int] | None = None) -> int:
    """Change d'un coup la date des épisodes déjà vus d'une série (toutes ou certaines saisons).

    Pour une série vue il y a longtemps, cochée d'un bloc : sans ça, tout tombe à la date du
    jour et fausse les statistiques. day=None : date inconnue. Les épisodes pas vus ne sont
    pas touchés (spéciaux compris s'ils sont vus). Renvoie le nombre d'épisodes changés.
    Pas de commit.
    """
    query = (select(Episode.id)
             .join(Watch, (Watch.kind == EPISODE) & (Watch.ref_id == Episode.id))
             .where(Episode.show_id == show_id))
    if seasons is not None:
        query = query.where(Episode.season.in_(seasons))
    episode_ids = session.exec(query).all()
    for episode_id in episode_ids:
        set_watch_date(session, EPISODE, episode_id, day)
    return len(episode_ids)


def unmark_watched(session: Session, kind: str, ref_id: int) -> None:
    """Retire le visionnage d'un épisode ou d'un film."""
    watch = get_watch(session, kind, ref_id)
    if watch is not None:
        session.delete(watch)


def _rewatch_time(day: date | None) -> datetime:
    """Moment d'un revisionnage : l'heure exacte si c'est aujourd'hui (ou None), sinon midi."""
    if day is None or day == datetime.now(LOCAL_TZ).date():
        return utcnow()
    return local_day_to_utc(day)


def add_rewatch(session: Session, kind: str, ref_id: int, day: date | None = None) -> Watch:
    """Ajoute un revisionnage (plays + 1) daté de `day` (None ou aujourd'hui : maintenant).

    Une seule ligne Watch par titre : on compte les visionnages au lieu de les dupliquer.
    Le dernier visionnage n'est avancé que si la date est plus récente (un revisionnage
    oublié, saisi après coup, ne le recule pas). Pas encore vu : simple visionnage à
    cette date. Pas de commit.
    """
    when = _rewatch_time(day)
    watch = get_watch(session, kind, ref_id)
    if watch is None:
        watch = Watch(kind=kind, ref_id=ref_id, watched_at=when, last_watched_at=when)
    else:
        watch.plays = (watch.plays or 1) + 1
        watch.last_watched_at = _latest(watch.last_watched_at, when)
    session.add(watch)
    return watch


def remove_rewatch(session: Session, kind: str, ref_id: int) -> Watch | None:
    """Retire un revisionnage (plays − 1, jamais moins de 1 : le titre reste vu).

    Revenu à un seul visionnage : le dernier visionnage redevient le premier.
    Renvoie None si le titre n'est pas vu. Pas de commit.
    """
    watch = get_watch(session, kind, ref_id)
    if watch is None or (watch.plays or 1) <= 1:
        return watch
    watch.plays -= 1
    if watch.plays == 1:
        watch.last_watched_at = watch.watched_at
    session.add(watch)
    return watch


def _watched_episodes(session: Session, show_id: int, seasons, include_specials: bool):
    query = (select(Episode).join(Watch, (Watch.kind == EPISODE) & (Watch.ref_id == Episode.id))
             .where(Episode.show_id == show_id, Episode.season.in_(list(seasons))))
    if not include_specials:
        query = query.where(Episode.is_special == False)  # noqa: E712 (syntaxe SQLAlchemy)
    return list(session.exec(query))


def add_season_rewatch(session: Session, show_id: int, seasons, day: date | None = None,
                       include_specials: bool = False) -> int:
    """Revisionnage des épisodes déjà vus des saisons données (les non vus sont ignorés).

    Renvoie le nombre d'épisodes concernés. Pas de commit.
    """
    episodes = _watched_episodes(session, show_id, seasons, include_specials)
    for episode in episodes:
        add_rewatch(session, EPISODE, episode.id, day)
    return len(episodes)


def remove_season_rewatch(session: Session, show_id: int, seasons,
                          include_specials: bool = False) -> int:
    """Retire un revisionnage aux épisodes revus des saisons données.

    Renvoie le nombre d'épisodes qui avaient un revisionnage à retirer. Pas de commit.
    """
    count = 0
    for episode in _watched_episodes(session, show_id, seasons, include_specials):
        watch = get_watch(session, EPISODE, episode.id)
        if watch is not None and (watch.plays or 1) > 1:
            remove_rewatch(session, EPISODE, episode.id)
            count += 1
    return count


def _mark_episodes(session: Session, episodes: list[Episode]) -> int:
    """Marque vus les épisodes donnés. Renvoie le nombre de nouveaux visionnages."""
    count = 0
    for episode in episodes:
        if get_watch(session, EPISODE, episode.id) is None:
            mark_watched(session, EPISODE, episode.id)
            count += 1
    session.flush()
    return count


def mark_season_watched(session: Session, show_id: int, season: int,
                        include_specials: bool = False) -> int:
    """Marque vue toute une saison. Renvoie le nombre d'épisodes nouvellement marqués."""
    query = select(Episode).where(Episode.show_id == show_id, Episode.season == season)
    if not include_specials:
        query = query.where(Episode.is_special == False)  # noqa: E712 (syntaxe SQLAlchemy)
    return _mark_episodes(session, list(session.exec(query)))


def mark_watched_until(session: Session, episode_id: int, include_specials: bool = False) -> int:
    """Marque vus tous les épisodes de la série jusqu'à celui-ci inclus.

    Ordre : (saison, numéro). Les spéciaux ne sont pris que si include_specials=True
    (ceux des saisons précédentes, ou de la même saison diffusés avant l'épisode choisi).
    Renvoie le nombre d'épisodes nouvellement marqués.
    """
    target = session.get(Episode, episode_id)
    if target is None:
        return 0
    episodes = session.exec(select(Episode).where(Episode.show_id == target.show_id)).all()
    to_mark = []
    for ep in episodes:
        if ep.id == target.id:
            to_mark.append(ep)
        elif not ep.is_special and not target.is_special:
            if (ep.season, ep.number) <= (target.season, target.number):
                to_mark.append(ep)
        elif include_specials or not ep.is_special:
            # Comparaison avec un spécial (pas de numéro) : saison, puis date de diffusion.
            if ep.season < target.season:
                to_mark.append(ep)
            elif (ep.season == target.season and ep.airdate and target.airdate
                  and ep.airdate <= target.airdate):
                to_mark.append(ep)
    return _mark_episodes(session, to_mark)


# --- Bibliothèque (statuts) --------------------------------------------------

def get_library_item(session: Session, kind: str, ref_id: int) -> LibraryItem | None:
    return session.exec(select(LibraryItem).where(LibraryItem.kind == kind,
                                                  LibraryItem.ref_id == ref_id)).first()


def ensure_library_item(session: Session, kind: str, ref_id: int, status: str,
                        favorite: bool = False, added_at: datetime | None = None) -> LibraryItem:
    """Ajoute la série/le film à la bibliothèque. Un élément existant n'est PAS modifié."""
    item = get_library_item(session, kind, ref_id)
    if item is None:
        item = LibraryItem(kind=kind, ref_id=ref_id, status=status, favorite=favorite,
                           added_at=added_at or utcnow())
        session.add(item)
    return item


def set_status(session: Session, kind: str, ref_id: int, status: str) -> LibraryItem:
    """Change le statut (ajoute l'élément à la bibliothèque s'il n'y est pas)."""
    if status not in STATUSES:
        raise ValueError(f"Statut inconnu : {status}")
    item = ensure_library_item(session, kind, ref_id, status)
    item.status = status
    session.add(item)
    return item


def set_favorite(session: Session, kind: str, ref_id: int, favorite: bool) -> LibraryItem:
    """Ajoute ou retire des favoris (ajoute l'élément à la bibliothèque s'il n'y est pas)."""
    item = ensure_library_item(session, kind, ref_id, TO_WATCH)
    item.favorite = bool(favorite)
    session.add(item)
    return item


def remove_from_library(session: Session, kind: str, ref_id: int) -> bool:
    """Retire une série ou un film de la bibliothèque.

    Seule la ligne LibraryItem est supprimée : la fiche, les épisodes, l'historique
    de visionnage et les notes sont conservés (on peut ré-ajouter l'élément plus tard).
    Renvoie True si l'élément était dans la bibliothèque.
    """
    item = get_library_item(session, kind, ref_id)
    if item is None:
        return False
    session.delete(item)
    return True


# --- Notes -------------------------------------------------------------------

# Émotions proposées (codes Bingers -> libellés). Les codes inconnus sont conservés tels quels.
EMOTION_LABELS = {
    "touched": "Ému", "thrilled": "Captivé", "scared": "Effrayé", "amused": "Amusé",
    "sad": "Triste", "shocked": "Choqué", "inspired": "Inspiré", "bored": "Ennuyé",
}


def split_emotions(emotions: str | None) -> list[str]:
    """"scared, touched" -> ["scared", "touched"] (chaîne vide ou None -> [])."""
    if not emotions:
        return []
    return [e.strip() for e in emotions.split(",") if e.strip()]


def get_rating(session: Session, kind: str, ref_id: int) -> Rating | None:
    return session.exec(select(Rating).where(Rating.kind == kind, Rating.ref_id == ref_id)).first()


def set_rating(session: Session, kind: str, ref_id: int, rating: int | None,
               note: str | None = None, emotions=None) -> Rating | None:
    """Enregistre la note (1 à 5, ou None), le commentaire libre et les émotions.

    Les trois valeurs remplacent les anciennes (None = vide). `emotions` : liste de
    codes ou chaîne « a, b ». Le personnage préféré (import Bingers) est conservé.
    Si tout est vide, la ligne est supprimée et la fonction renvoie None.
    """
    if rating is not None and not 1 <= rating <= 5:
        raise ValueError(f"Note invalide : {rating} (attendu : 1 à 5)")
    if isinstance(emotions, str):
        emotions = split_emotions(emotions)
    emotions_text = ", ".join(emotions) if emotions else None
    note = note.strip() if note and note.strip() else None

    existing = get_rating(session, kind, ref_id)
    favorite_character = existing.favorite_character if existing else None
    if rating is None and note is None and emotions_text is None and not favorite_character:
        if existing is not None:
            session.delete(existing)
        return None
    if existing is None:
        existing = Rating(kind=kind, ref_id=ref_id)
    existing.rating = rating
    existing.note = note
    existing.emotions = emotions_text
    session.add(existing)
    return existing


def refresh_completed(session: Session, today: date | None = None) -> int:
    """Passe en « Terminé » les séries finies (TVMaze : Ended) dont tout est vu.

    « Tout » = tous les épisodes réguliers (hors spéciaux) déjà diffusés.
    Renvoie le nombre de séries passées en « Terminé ».
    """
    today = today or date.today()
    query = (select(LibraryItem, Show)
             .join(Show, Show.id == LibraryItem.ref_id)
             .where(LibraryItem.kind == SHOW, LibraryItem.status != COMPLETED,
                    Show.status == ENDED))
    count = 0
    for item, show in session.exec(query).all():
        aired = session.exec(select(Episode).where(
            Episode.show_id == show.id, Episode.is_special == False,  # noqa: E712
            Episode.airdate.is_not(None), Episode.airdate <= today)).all()
        if not aired:
            continue
        watched_ids = set(session.exec(select(Watch.ref_id).where(
            Watch.kind == EPISODE, Watch.ref_id.in_([ep.id for ep in aired]))).all())
        if len(watched_ids) == len(aired):
            item.status = COMPLETED
            session.add(item)
            count += 1
    return count
