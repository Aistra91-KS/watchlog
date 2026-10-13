"""Lanceur Windows de Watchlog (raccourcis « Watchlog » créés par launcher/install.ps1).

- Si l'outil tourne déjà : ouvre simplement le navigateur.
- Sinon : démarre le serveur Streamlit en arrière-plan (sans fenêtre), attend qu'il
  réponde, puis ouvre http://localhost:8501.
- Avec --restart (bouton « Redémarrer » de l'interface) : attend l'arrêt de l'ancien serveur,
  le relance, sans ouvrir de nouvel onglet (l'onglet ouvert se reconnecte tout seul).
Pour arrêter l'outil : bouton « Arrêter Watchlog » dans le menu de l'interface.

Bibliothèque standard uniquement ; lancé avec le pythonw.exe du .venv (sans console).
"""

import ctypes
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

PORT = int(os.environ.get("WATCHLOG_PORT") or 8501)  # autre port : pour des essais
URL = f"http://localhost:{PORT}"
HEALTH = f"{URL}/_stcore/health"
STARTUP_TIMEOUT = 90  # secondes (premier lancement plus lent : compilation Python)


def project_root() -> Path:
    """Dossier du projet : le parent de launcher/."""
    return Path(__file__).resolve().parents[1]


def message(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "Watchlog", 0x40)


def is_running() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def wait_stopped(timeout: float = 20) -> None:
    """Attend que l'ancien serveur ait libéré le port (redémarrage)."""
    deadline = time.monotonic() + timeout
    while is_running() and time.monotonic() < deadline:
        time.sleep(0.3)


def main(argv: list[str]) -> int:
    root = project_root()
    restart = "--restart" in argv
    if restart:
        wait_stopped()
    elif is_running():
        webbrowser.open(URL)
        return 0

    python = root / ".venv" / "Scripts" / "pythonw.exe"
    if not python.exists():
        message(f"Environnement Python introuvable :\n{python}\n\n"
                "Si le projet a été déplacé, relance launcher\\install.ps1.")
        return 1

    log_dir = root / "data"
    log_dir.mkdir(exist_ok=True)
    log = open(log_dir / "serveur.log", "a", encoding="utf-8")
    subprocess.Popen(
        [str(python), "-m", "streamlit", "run", "watchlog/ui/app.py",
         "--server.port", str(PORT), "--server.headless", "true"],
        cwd=root, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)

    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if is_running():
            if not restart:
                webbrowser.open(URL)
            return 0
        time.sleep(0.5)
    message("Watchlog ne répond pas après le démarrage.\n"
            f"Détails dans {log_dir / 'serveur.log'}.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
