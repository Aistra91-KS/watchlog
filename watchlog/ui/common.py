"""Outils partagés par les pages Streamlit (clients API, affiches, navigation, mentions)."""

import os
import re
from pathlib import Path

import streamlit as st
from sqlmodel import Session

from watchlog.core import tmdb as tmdb_module
from watchlog.core import tvmaze as tvmaze_module
from watchlog.core.config import DATA_DIR
from watchlog.core.dates import fmt_date  # noqa: F401  (réexporté pour les pages)
from watchlog.core.db import get_engine
from watchlog.core.models import STATUS_LABELS, STATUSES

# Chemins des pages (relatifs à app.py), utilisés pour la navigation entre pages.
PAGE_QUEUE = "views/a_regarder.py"
PAGE_LIBRARY = "views/bibliotheque.py"
PAGE_SHOW = "views/serie.py"
PAGE_MOVIE = "views/film.py"
PAGE_ADD = "views/ajouter.py"
PAGE_CALENDAR = "views/calendrier.py"
PAGE_STATS = "views/statistiques.py"
PAGE_SETTINGS = "views/parametres.py"


def db() -> Session:
    """Nouvelle session sur la base. À utiliser avec `with db() as session:`."""
    return Session(get_engine())


@st.cache_resource
def clients():
    """Clients TVMaze et TMDB, créés une seule fois par lancement de l'application."""
    from watchlog.core.services import make_clients
    return make_clients(get_engine())


def tvmaze_client():
    return clients()[0]


def tmdb_client():
    return clients()[1]


@st.cache_data(ttl=600, show_spinner=False)
def tmdb_status() -> tuple[bool, str]:
    """Test de connexion TMDB (GET /authentication), gardé 10 minutes. Jamais la clé.

    WATCHLOG_OFFLINE=1 : aucun appel (pratique pour les essais).
    """
    if os.getenv("WATCHLOG_OFFLINE") == "1":
        return False, "TMDB non testé : mode hors ligne (WATCHLOG_OFFLINE=1)."
    return tmdb_client().check_connection()


def md_escape(text: str | None) -> str:
    """Protège les caractères Markdown d'un titre (ex. « *batteries not included »)."""
    return re.sub(r"([\\`*_\[\]$~|#<>])", r"\\\1", text or "")


def poster_file(poster_path: str | None) -> str | None:
    """Chemin local de l'affiche si elle a été téléchargée, sinon None."""
    if not poster_path:
        return None
    path = Path(DATA_DIR) / poster_path
    return str(path) if path.exists() else None


def show_poster(poster_path: str | None, width: int | None = None) -> None:
    """Affiche l'image, ou un cadre gris neutre s'il n'y en a pas."""
    path = poster_file(poster_path)
    if path:
        st.image(path, width=width or "stretch")
    else:
        st.markdown(
            "<div style='aspect-ratio:2/3;background:#e6e9ed;border-radius:4px;"
            "display:flex;align-items:center;justify-content:center;color:#7b8794;"
            "font-size:0.8rem'>Pas d'affiche</div>", unsafe_allow_html=True)


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def status_options() -> list[str]:
    """Statuts dans l'ordre, pour les listes déroulantes (format_func=status_label)."""
    return list(STATUSES)


def open_show(show_id: int) -> None:
    """Ouvre la fiche d'une série."""
    st.session_state["show_id"] = show_id
    st.switch_page(PAGE_SHOW, query_params={"id": show_id})


def open_movie(movie_id: int) -> None:
    """Ouvre la fiche d'un film."""
    st.session_state["movie_id"] = movie_id
    st.switch_page(PAGE_MOVIE, query_params={"id": movie_id})


def selected_id(key: str) -> int | None:
    """Id de la fiche à afficher : paramètre d'URL ?id=..., sinon dernier ouvert."""
    value = st.query_params.get("id") or st.session_state.get(key)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def footer() -> None:
    """Mentions obligatoires (licences TVMaze et TMDB), en bas de chaque page."""
    st.divider()
    st.caption(tvmaze_module.ATTRIBUTION)
    st.caption(tmdb_module.ATTRIBUTION)
