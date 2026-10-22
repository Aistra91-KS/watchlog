"""Outils partagés par les pages Streamlit (clients API, affiches, navigation, mentions)."""

import base64
import html
import logging
import os
import re
import threading
from pathlib import Path

import streamlit as st
from sqlmodel import Session

from watchlog.core import providers
from watchlog.core import sync as sync_module
from watchlog.core import tmdb as tmdb_module
from watchlog.core import tvmaze as tvmaze_module
from watchlog.core.config import DATA_DIR
from watchlog.core.dates import fmt_date  # noqa: F401  (réexporté pour les pages)
from watchlog.core.db import get_engine
from watchlog.core.models import STATUS_LABELS, STATUSES

log = logging.getLogger(__name__)

# Chemins des pages (relatifs à app.py), utilisés pour la navigation entre pages.
PAGE_QUEUE = "views/a_regarder.py"
PAGE_LIBRARY = "views/bibliotheque.py"
PAGE_SHOW = "views/serie.py"
PAGE_MOVIE = "views/film.py"
PAGE_ADD = "views/ajouter.py"
PAGE_CALENDAR = "views/calendrier.py"
PAGE_STATS = "views/statistiques.py"
PAGE_SUGGESTIONS = "views/suggestions.py"
PAGE_PREVIEW = "views/apercu.py"
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


@st.cache_resource
def auto_sync() -> dict:
    """Synchro automatique, lancée une fois par démarrage si la dernière date de plus de 24 h.

    Elle tourne dans un thread à part : les pages restent utilisables pendant ce temps.
    Renvoie son état, partagé par toutes les pages : {"running": bool, "error": message ou None}.
    WATCHLOG_OFFLINE=1 : jamais lancée (essais).
    """
    state = {"running": False, "error": None}
    if os.getenv("WATCHLOG_OFFLINE") == "1":
        return state
    with db() as session:
        due = sync_module.sync_due(session)
    if due:
        state["running"] = True
        tvmaze, tmdb = clients()   # lus ici : le thread ne doit appeler aucune fonction Streamlit
        threading.Thread(target=_run_auto_sync, args=(state, tvmaze, tmdb), daemon=True,
                         name="watchlog-auto-sync").start()
    return state


def _run_auto_sync(state: dict, tvmaze, tmdb) -> None:
    """Corps du thread de synchro (le rapport est gardé en base par sync)."""
    try:
        sync_module.sync(get_engine(), tvmaze, tmdb)
    except Exception as exc:  # noqa: BLE001  (montré dans le menu, détail dans le journal)
        log.exception("Synchro automatique interrompue")
        state["error"] = str(exc)
    finally:
        state["running"] = False


def fmt_runtime(minutes: int | None) -> str:
    """Durée lisible, la même partout : 45 -> « 45 min », 112 -> « 1 h 52 », 120 -> « 2 h »."""
    if not minutes:
        return ""
    hours, rest = divmod(minutes, 60)
    if hours == 0:
        return f"{rest} min"
    return f"{hours} h {rest:02d}" if rest else f"{hours} h"


def md_escape(text: str | None) -> str:
    """Protège les caractères Markdown d'un titre (ex. « *batteries not included »)."""
    return re.sub(r"([\\`*_\[\]$~|#<>])", r"\\\1", text or "")


def poster_file(poster_path: str | None) -> str | None:
    """Chemin local de l'affiche si elle a été téléchargée, sinon None."""
    if not poster_path:
        return None
    path = Path(DATA_DIR) / poster_path
    return str(path) if path.exists() else None


def poster_src(poster_path: str | None, poster_url: str | None) -> str | None:
    """Source d'image pour du HTML : l'affiche en ligne, sinon le fichier local (en base64)."""
    if poster_url:
        return poster_url
    path = poster_file(poster_path)
    if path:
        mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()
    return None


def show_poster(poster_path: str | None, width: int | None = None,
                poster_url: str | None = None) -> None:
    """Affiche l'image (locale, sinon en ligne), ou un cadre gris neutre s'il n'y en a pas."""
    path = poster_file(poster_path) or poster_url
    if path:
        st.image(path, width=width or "stretch")
    else:
        # Fond gris semi-transparent, texte de la couleur du thème : lisible en clair et sombre.
        st.markdown(
            "<div style='aspect-ratio:2/3;background:rgba(128,128,128,0.15);border-radius:4px;"
            "display:flex;align-items:center;justify-content:center;color:inherit;opacity:0.8;"
            "font-size:0.8rem'>Pas d'affiche</div>", unsafe_allow_html=True)


def is_dark_theme() -> bool:
    """Vrai si l'utilisateur affiche le thème sombre (menu ⋮ > Settings ou système)."""
    try:
        return st.context.theme.type == "dark"
    except AttributeError:  # thème pas encore connu au tout premier affichage
        return False


# Couleurs des graphiques (palette validée daltonisme + contraste, clair et sombre).
# La couleur suit l'entité : épisodes / séries = bleu, films = orange, partout.
CHART_COLORS = {
    "light": {"episodes": "#2a78d6", "films": "#eb6834", "surface": "#ffffff",
              "empty": "#ebedf0",
              # 1er cran assez foncé pour qu'un jour à 1 visionnage se distingue d'un jour vide
              "ramp": ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b", "#06203f"]},
    "dark": {"episodes": "#3987e5", "films": "#d95926", "surface": "#14181d",
             "empty": "#232a32",
             "ramp": ["#184f95", "#256abf", "#3987e5", "#6da7ec", "#b7d3f6"]},
}


def chart_colors() -> dict:
    """Couleurs des graphiques pour le thème affiché."""
    return CHART_COLORS["dark" if is_dark_theme() else "light"]


def primary_color() -> str:
    """Couleur principale du thème affiché (.streamlit/config.toml), pour les graphiques."""
    return "#6fa3d6" if is_dark_theme() else "#2f5d8a"


def show_availability(kind: str, tmdb_id: int | None, compact: bool = False) -> None:
    """Plateformes où regarder en France : logos par type d'offre (abonnement, location...).

    compact=True (cartes de suggestions) : une seule ligne, sans titre ni lien.
    Rien n'est affiché si TMDB est indisponible ou si le titre n'a pas d'id TMDB.
    """
    data = providers.availability(tmdb_client(), kind, tmdb_id)
    if data is None:
        return
    if data.empty:
        st.caption("Pas disponible en streaming, location ou achat en France pour l'instant.")
        return
    size = 24 if compact else 32
    parts = []
    for label, items in data.offers.items():
        logos = "".join(
            f"<img src='{html.escape(p.logo_url)}' alt='{html.escape(p.name)}' "
            f"title='{html.escape(p.name)}' style='height:{size}px;border-radius:6px;"
            f"margin:0 4px 4px 0;vertical-align:middle'>"
            if p.logo_url else f"<span style='margin-right:6px'>{html.escape(p.name)}</span>"
            for p in items)
        parts.append(f"<span style='margin-right:14px;white-space:nowrap'>"
                     f"<span style='font-size:0.85rem;opacity:0.75;margin-right:6px'>"
                     f"{html.escape(label)}</span>{logos}</span>")
    separator = "" if compact else "<br>"
    st.markdown(separator.join(parts), unsafe_allow_html=True)
    if not compact and data.link:  # source JustWatch citée en bas de page (footer)
        st.caption(f"[Voir les offres et les prix]({data.link})")


def add_from_tmdb(kind: str, tmdb_id: int, status: str, title: str | None = None,
                  watched_on=None) -> tuple[int, str]:
    """Ajoute à la bibliothèque un titre connu par son id TMDB (suggestion, aperçu).

    Série : fiche TVMaze retrouvée via TVDB / IMDb. Renvoie (id en base, titre).
    ApiError / ValueError remontent à l'appelant (message à afficher).
    """
    from watchlog.core import suggestions
    from watchlog.core.models import SHOW

    with db() as session:
        if kind == SHOW:
            client = tvmaze_client()
            obj = suggestions.add_suggested_show(session, tmdb_client(), client, tmdb_id,
                                                 status, title=title)
        else:
            client = tmdb_client()
            obj = suggestions.add_suggested_movie(session, client, tmdb_id, status,
                                                  watched_on=watched_on)
        session.commit()
        obj_id, obj_title = obj.id, obj.title
        download_poster(session, obj, client)
    return obj_id, obj_title


def download_poster(session, obj, client) -> None:
    """Télécharge l'affiche d'un titre ajouté ; un échec n'empêche pas l'ajout (mais est noté)."""
    from watchlog.core import catalog
    try:
        catalog.ensure_poster(session, obj, client)
        session.commit()
    except Exception as exc:  # noqa: BLE001  (une affiche ratée n'est pas grave)
        session.rollback()
        log.warning("Affiche non téléchargée pour %s : %s", obj.title, exc)


def preview_params(kind: str, tmdb_id: int) -> dict:
    """Paramètres d'URL de la page Aperçu (?type=serie|film&id=<id TMDB>)."""
    from watchlog.core.models import SHOW
    return {"type": "serie" if kind == SHOW else "film", "id": tmdb_id}


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
    st.caption(providers.ATTRIBUTION)
