"""Dernier calcul des suggestions, partagé par les pages Suggestions et Accueil."""

import time

import streamlit as st

from watchlog.core import suggestions
from watchlog.ui import common

MAX_AGE = 3600          # secondes : résultat gardé une heure (ou jusqu'à « Recalculer »)
MAX_AGE_INCOMPLETE = 120  # résultat incomplet (TMDB a mal répondu) : recalculé vite


@st.cache_resource
def shared() -> dict:
    """Dernier calcul, partagé entre les onglets du navigateur : {"at": horodatage, "result": ...}.

    Pas st.cache_data : le calcul affiche une barre de progression, ce que les fonctions
    en cache de Streamlit n'acceptent pas.
    """
    return {}


def load() -> suggestions.Result:
    """Profil de goûts, séries et films suggérés, avec leurs plateformes."""
    cache = shared()
    if cache.get("result"):
        max_age = MAX_AGE_INCOMPLETE if cache["result"].errors else MAX_AGE
        if time.time() - cache["at"] < max_age:
            return cache["result"]
    tmdb = common.tmdb_client()
    bar = st.progress(0.0, text="Analyse de tes goûts… La première fois, cela peut prendre "
                                "une à deux minutes.")

    def progress(done: int, total: int, label: str) -> None:
        bar.progress(min(done / total, 1.0) if total else 1.0, text=label)

    with common.db() as session:
        suggestions.resolve_show_tmdb_ids(session, tmdb)
        session.commit()
        result = suggestions.compute_all(session, tmdb, progress=progress)
    failed = suggestions.attach_availability(tmdb, result.items, progress)
    if failed:
        result.errors.append(f"Plateformes non lues pour {failed} suggestion(s) (TMDB) : les "
                             "filtres de plateformes (« Dispo en streaming », « Sur mes "
                             "plateformes »…) sont incomplets.")
    bar.empty()
    cache.update(at=time.time(), result=result)
    return result


def hidden() -> set[tuple[str, int]]:
    """Titres ajoutés ou écartés pendant la session : masqués sans tout recalculer."""
    return st.session_state.setdefault("sugg_hidden", set())


def recompute() -> None:
    shared().clear()
    hidden().clear()
