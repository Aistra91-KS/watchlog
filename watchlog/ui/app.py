"""Interface Watchlog (Streamlit) : point d'entrée et navigation entre les pages.

Lancement : python -m streamlit run watchlog/ui/app.py (ou Ctrl+Maj+B dans VS Code).
"""

import os
import subprocess
import sys
import threading
import time
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


@st.cache_resource
def server_started_at() -> float:
    """Heure de démarrage du serveur (première exécution de cette fonction dans le processus)."""
    return time.time()


def code_changed_since_start() -> bool:
    """Vrai si un fichier .py du projet a changé depuis le démarrage du serveur.

    Streamlit recharge les pages modifiées, mais pas toujours les modules qu'elles importent
    (watchlog/core...) : après une mise à jour, il faut redémarrer le serveur.
    """
    started = server_started_at()
    return any(path.stat().st_mtime > started for path in (ROOT / "watchlog").rglob("*.py"))


def restart_server() -> None:
    """Relance Watchlog : le lanceur attend l'arrêt de ce serveur, puis en démarre un nouveau."""
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    subprocess.Popen([str(pythonw if pythonw.exists() else python),
                      str(ROOT / "launcher" / "watchlog_launcher.py"), "--restart"],
                     cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                     | getattr(subprocess, "DETACHED_PROCESS", 0))
    st.session_state["restarting"] = True
    threading.Timer(1.5, os._exit, args=(0,)).start()


server_started_at()
st.set_page_config(page_title="Watchlog", page_icon=str(ROOT / "assets" / "watchlog.png"),
                   layout="wide")


@st.cache_resource
def startup_backup() -> None:
    """Sauvegarde automatique de la base, une fois par lancement de l'application."""
    from watchlog.core.backup import make_backup
    make_backup()


# Recharge la page quand le nouveau serveur répond (après la coupure de l'ancien).
RELOAD_WHEN_BACK = """<script>
(() => {
  let down = false;
  const started = Date.now();
  async function check() {
    try {
      const r = await fetch("/_stcore/health", {cache: "no-store"});
      if (r.ok && down) { window.location.reload(); return; }
      if (!r.ok) down = true;
    } catch (e) { down = true; }
    if (Date.now() - started < 120000) setTimeout(check, 700);
  }
  setTimeout(check, 500);
})();
</script>"""

if st.session_state.get("restarting"):
    st.info("Redémarrage de Watchlog… La page se recharge toute seule dans quelques secondes.")
    st.html(RELOAD_WHEN_BACK, unsafe_allow_javascript=True)
    st.stop()
if code_changed_since_start():
    st.warning("Watchlog a été mis à jour depuis son démarrage : redémarre-le pour charger "
               "la nouvelle version (sinon certaines pages peuvent afficher des erreurs).")
    st.button("Redémarrer Watchlog", type="primary", on_click=restart_server)
    st.stop()

startup_backup()


@st.cache_resource
def startup_genres() -> None:
    """Genres manquants (titres importés avant cette fonction), lus dans le cache des API."""
    from watchlog.core import catalog
    with common.db() as session:
        catalog.backfill_genres(session, *common.clients())
        session.commit()


startup_genres()


def stop_server() -> None:
    """Arrête Watchlog (utile quand il est lancé par le raccourci, sans fenêtre de terminal)."""
    st.session_state["stopping"] = True
    # Petit délai : le message « arrêté » a le temps d'arriver dans le navigateur.
    threading.Timer(1.5, os._exit, args=(0,)).start()

pages = [
    st.Page(common.PAGE_QUEUE, title="À regarder", default=True),
    st.Page(common.PAGE_LIBRARY, title="Bibliothèque"),
    st.Page(common.PAGE_CALENDAR, title="Calendrier"),
    st.Page(common.PAGE_SUGGESTIONS, title="Suggestions"),
    st.Page(common.PAGE_ADD, title="Ajouter"),
    st.Page(common.PAGE_STATS, title="Statistiques"),
    st.Page(common.PAGE_SETTINGS, title="Paramètres"),
    # Fiches : accessibles depuis les autres pages (pas dans le menu).
    st.Page(common.PAGE_SHOW, title="Fiche série", url_path="serie"),
    st.Page(common.PAGE_MOVIE, title="Fiche film", url_path="film"),
    st.Page(common.PAGE_PREVIEW, title="Aperçu", url_path="apercu"),
]
page = st.navigation(pages, position="hidden")

# Menu latéral : les pages principales seulement.
with st.sidebar:
    st.markdown("### Watchlog")
    for p in pages[:7]:
        st.page_link(p)
    connected, _message = common.tmdb_status()
    st.caption("TMDB : connecté" if connected else "TMDB : non connecté (voir Paramètres)")
    st.button("Arrêter Watchlog", on_click=stop_server, type="tertiary",
              help="Ferme l'outil. Pour le rouvrir : le raccourci Watchlog (Bureau ou menu Démarrer).")

if st.session_state.get("stopping"):
    st.info("Watchlog est arrêté. Tu peux fermer cet onglet.")
    st.stop()

page.run()
common.footer()
