"""Tables de la base SQLite (SQLModel).

`kind` + `ref_id` : référence vers une ligne d'une autre table selon le type
(ex. kind="movie", ref_id=movie.id). Pas de clé étrangère SQL possible dans ce cas,
c'est le code qui garantit la cohérence.
"""

from datetime import date

from pydantic import NaiveDatetime
from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel

# Les datetimes sont stockés en UTC « naïf » (voir dates.py). Avec un simple
# `datetime`, SQLModel (>= 0.0.4x) exige un fuseau horaire et refuse l'écriture :
# on annote donc les colonnes avec NaiveDatetime.

# Valeurs possibles de `kind`
SHOW, EPISODE, MOVIE = "show", "episode", "movie"

# Statuts de la bibliothèque
WATCHING, TO_WATCH, FOR_LATER, STOPPED, COMPLETED = (
    "watching", "to_watch", "for_later", "stopped", "completed")
STATUSES = [WATCHING, TO_WATCH, FOR_LATER, STOPPED, COMPLETED]
STATUS_LABELS = {
    WATCHING: "En cours", TO_WATCH: "À voir", FOR_LATER: "Plus tard",
    STOPPED: "Arrêté", COMPLETED: "Terminé",
}

# État de la correspondance d'une série avec TVMaze
MATCH_OK = "ok"                      # trouvée via tvdb_id ou confirmée à la main
MATCH_UNCERTAIN = "uncertain"        # trouvée par recherche de titre : à confirmer
MATCH_NOT_FOUND = "not_found"        # rien trouvé
MATCH_NUMBERING = "numbering_mismatch"  # trouvée, mais numérotation des épisodes vus incohérente
MATCH_REMAPPED = "remapped"          # incohérence résolue par correspondance manuelle validée


class Show(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    tvmaze_id: int | None = Field(default=None, unique=True, index=True)
    tvdb_id: int | None = Field(default=None, index=True)
    tmdb_id: int | None = None
    imdb_id: str | None = None
    title: str                                # titre FR (Bingers) si dispo
    original_title: str | None = None
    status: str | None = None                 # TVMaze : Running, Ended, To Be Determined...
    network: str | None = None
    premiered: date | None = None
    poster_url: str | None = None
    poster_path: str | None = None            # chemin local, relatif à data/
    summary: str | None = None
    genres: str | None = None                 # « Drame, Comédie » (traduits en français)
    match_status: str = MATCH_OK
    match_note: str | None = None             # explication lisible (rapport d'import)
    candidate_tvmaze_id: int | None = None    # proposition à confirmer si match incertain
    is_manual: bool = False                   # saisie manuelle hors TVMaze
    last_synced_at: NaiveDatetime | None = None


class Episode(SQLModel, table=True):
    __table_args__ = (Index("ix_episode_show_season_number", "show_id", "season", "number"),)

    id: int | None = Field(default=None, primary_key=True)
    show_id: int = Field(foreign_key="show.id", index=True)
    season: int
    number: int | None = None                 # None = épisode spécial
    title: str | None = None
    airdate: date | None = Field(default=None, index=True)
    airstamp: NaiveDatetime | None = None          # UTC
    runtime: int | None = None                # minutes
    is_special: bool = False
    tvmaze_episode_id: int | None = Field(default=None, unique=True)


class Movie(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    tmdb_id: int | None = Field(default=None, unique=True, index=True)
    imdb_id: str | None = None
    title: str
    original_title: str | None = None
    year: int | None = None                   # année Bingers, utile si pas de métadonnées
    release_date_fr: date | None = None
    release_date_original: date | None = None
    runtime: int | None = None
    poster_url: str | None = None
    poster_path: str | None = None
    overview: str | None = None
    genres: str | None = None                 # « Drame, Comédie » (TMDB, en français)
    is_manual: bool = False
    last_synced_at: NaiveDatetime | None = None    # None = jamais récupéré depuis TMDB


class LibraryItem(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("kind", "ref_id"),)

    id: int | None = Field(default=None, primary_key=True)
    kind: str                                 # show / movie
    ref_id: int
    status: str = TO_WATCH
    favorite: bool = False
    added_at: NaiveDatetime | None = None


class Watch(SQLModel, table=True):
    # Une seule ligne par épisode/film : revoir incrémente `plays` (pas de doublon).
    __table_args__ = (UniqueConstraint("kind", "ref_id"),)

    id: int | None = Field(default=None, primary_key=True)
    kind: str                                 # episode / movie
    ref_id: int
    watched_at: NaiveDatetime | None = None        # premier visionnage
    last_watched_at: NaiveDatetime | None = None
    plays: int = 1


class Rating(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("kind", "ref_id"),)

    id: int | None = Field(default=None, primary_key=True)
    kind: str                                 # show / episode / movie
    ref_id: int
    rating: int | None = None                 # 1 à 5 (None : seulement une émotion)
    emotions: str | None = None
    favorite_character: str | None = None
    note: str | None = None


class CustomList(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    description: str | None = None


class CustomListItem(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("list_id", "kind", "ref_id"),)

    id: int | None = Field(default=None, primary_key=True)
    list_id: int = Field(foreign_key="customlist.id", index=True)
    kind: str
    ref_id: int


class PendingWatch(SQLModel, table=True):
    """Visionnage Bingers pas encore rattaché à un épisode TVMaze.

    Cas : série non trouvée / incertaine, ou numérotation des saisons incohérente.
    On garde le numéro d'origine (Bingers/TVDB) et on l'applique après confirmation.
    """

    __table_args__ = (UniqueConstraint("show_id", "season", "number"),)

    id: int | None = Field(default=None, primary_key=True)
    show_id: int = Field(foreign_key="show.id", index=True)
    season: int
    number: int
    watched_at: NaiveDatetime | None = None
    last_watched_at: NaiveDatetime | None = None
    plays: int = 1
    reason: str = ""


class DismissedSuggestion(SQLModel, table=True):
    """Titre écarté de la page Suggestions (« Pas intéressé ») : il n'y revient plus."""

    __table_args__ = (UniqueConstraint("kind", "tmdb_id"),)

    id: int | None = Field(default=None, primary_key=True)
    kind: str                                 # show / movie
    tmdb_id: int
    title: str | None = None                  # pour la liste des titres écartés
    dismissed_at: NaiveDatetime | None = None


class ThemePreference(SQLModel, table=True):
    """Thème que l'utilisateur veut voir plus (+1) ou moins (-1) dans les suggestions."""

    feature: str = Field(primary_key=True)    # « k:4565 » (mot-clé TMDB) ou « g:Animation »
    label: str                                # libellé affiché (« Dystopie »)
    weight: int                               # 1 = plus de ça, -1 = moins de ça
    updated_at: NaiveDatetime | None = None


class ApiCache(SQLModel, table=True):
    url: str = Field(primary_key=True)        # chemin + paramètres publics (jamais la clé)
    response_json: str
    fetched_at: NaiveDatetime
