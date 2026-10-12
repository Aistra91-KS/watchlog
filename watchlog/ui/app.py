"""Interface Watchlog (Streamlit) : point d'entrée et navigation entre les pages.

Lancement : python -m streamlit run watchlog/ui/app.py (ou Ctrl+Maj+B dans VS Code).
"""

import sys
from pathlib import Path

# Streamlit lance ce script depuis watchlog/ui : on ajoute la racine du projet
# au chemin de recherche pour pouvoir importer le paquet `watchlog`.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from watchlog.core.config import setup_logging  # noqa: E402
from watchlog.ui import common  # noqa: E402

setup_logging()
st.set_page_config(page_title="Watchlog", layout="wide")


@st.cache_resource
def startup_backup() -> None:
    """Sauvegarde automatique de la base, une fois par lancement de l'application."""
    try:
        from watchlog.core.backup import make_backup
    except ImportError:  # module pas encore disponible
        return
    make_backup()


startup_backup()

pages = [
    st.Page(common.PAGE_QUEUE, title="À regarder", default=True),
    st.Page(common.PAGE_LIBRARY, title="Bibliothèque"),
    st.Page(common.PAGE_CALENDAR, title="Calendrier"),
    st.Page(common.PAGE_ADD, title="Ajouter"),
    st.Page(common.PAGE_STATS, title="Statistiques"),
    st.Page(common.PAGE_SETTINGS, title="Paramètres"),
    # Fiches : accessibles depuis les autres pages (pas dans le menu).
    st.Page(common.PAGE_SHOW, title="Fiche série", url_path="serie"),
    st.Page(common.PAGE_MOVIE, title="Fiche film", url_path="film"),
]
page = st.navigation(pages, position="hidden")

# Menu latéral : les pages principales seulement.
with st.sidebar:
    st.markdown("### Watchlog")
    for p in pages[:6]:
        st.page_link(p)
    connected, _message = common.tmdb_status()
    st.caption("TMDB : connecté" if connected else "TMDB : non connecté (voir Paramètres)")

page.run()
common.footer()
