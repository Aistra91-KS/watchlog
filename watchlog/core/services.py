"""Création des clients d'API, partagée par la ligne de commande et l'interface."""

from sqlalchemy import Engine

from watchlog.core.db import get_engine
from watchlog.core.tmdb import TMDBClient
from watchlog.core.tvmaze import TVMazeClient


def make_clients(engine: Engine | None = None) -> tuple[TVMazeClient, TMDBClient]:
    """Renvoie (client TVMaze, client TMDB) branchés sur la base (pour le cache)."""
    engine = engine or get_engine()
    return TVMazeClient(engine), TMDBClient(engine)
