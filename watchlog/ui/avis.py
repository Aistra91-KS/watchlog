"""Formulaire « Mon avis » (note, commentaire, émotions, personnage préféré).

Partagé par les fiches film et série : même fonctionnement pour les deux.
"""

import streamlit as st

from watchlog.core import library
from watchlog.ui import common

RATING_OPTIONS = [0, 1, 2, 3, 4, 5]  # 0 = pas de note


def rating_label(value: int) -> str:
    return "Pas de note" if not value else f"{value} / 5"


def emotion_label(code: str) -> str:
    return library.EMOTION_LABELS.get(code, code)


def review_form(kind: str, ref_id: int, message_key: str) -> None:
    """Affiche « Mon avis » ; « Enregistrer » remplace l'avis puis recharge la page.

    message_key : clé de session du message de confirmation affiché par la page.
    """
    with common.db() as session:
        rating = library.get_rating(session, kind, ref_id)
    st.subheader("Mon avis")
    current_emotions = library.split_emotions(rating.emotions if rating else None)
    # Émotions inconnues (autre code Bingers) : gardées dans la liste pour ne pas les perdre.
    emotion_options = list(library.EMOTION_LABELS) + [
        e for e in current_emotions if e not in library.EMOTION_LABELS]
    with st.form(f"avis_{kind}_{ref_id}"):
        value = st.selectbox("Note", RATING_OPTIONS, format_func=rating_label,
                             index=RATING_OPTIONS.index((rating.rating or 0) if rating else 0))
        note = st.text_area("Commentaire", value=(rating.note or "") if rating else "")
        emotions = st.multiselect("Émotions", emotion_options, default=current_emotions,
                                  format_func=emotion_label)
        character = st.text_input("Personnage préféré",
                                  value=(rating.favorite_character or "") if rating else "")
        if st.form_submit_button("Enregistrer"):
            with common.db() as session:
                library.set_rating(session, kind, ref_id, value or None, note=note,
                                   emotions=emotions, favorite_character=character)
                session.commit()
            st.session_state[message_key] = "Avis enregistré."
            st.rerun()
