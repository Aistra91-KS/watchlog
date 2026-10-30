"""Formulaire « Mon avis » (note, commentaire, émotions, personnage préféré).

Partagé par les fiches film et série : même fonctionnement pour les deux.
"""

import streamlit as st

from watchlog.core import cast, library
from watchlog.core.models import SHOW, Movie, Show
from watchlog.ui import common

RATING_OPTIONS = [0, *library.RATING_VALUES]  # 0 = pas de note ; demi-notes comprises
PHOTO_WIDTH = 90   # photos des personnages (« Revoir les personnages »), en pixels


def rating_label(value: float) -> str:
    return "Pas de note" if not value else f"{library.format_rating(value)} / 5"


def emotion_label(code: str) -> str:
    return library.EMOTION_LABELS.get(code, code)


def characters(kind: str, ref_id: int) -> cast.Cast:
    """Personnages du titre (TVMaze / TMDB, réponses gardées en cache par les clients)."""
    with common.db() as session:
        obj = session.get(Show if kind == SHOW else Movie, ref_id)
        if obj is None:
            return cast.Cast()
        if kind == SHOW:
            return cast.show_cast(common.tvmaze_client(), common.tmdb_client(), obj)
        return cast.movie_cast(common.tmdb_client(), obj)


def character_field(found: cast.Cast, current: str) -> str | None:
    """« Personnage préféré » : liste « Personnage (Acteur) », photos pour s'en souvenir, et
    saisie libre (un nom absent de la liste reste possible)."""
    if not found.characters:
        value = st.text_input("Personnage préféré", value=current)
    else:
        names = [c.name for c in found.characters]
        labels = {c.name: c.label for c in found.characters}
        options = ["", *([current] if current and current not in names else []), *names]
        value = st.selectbox("Personnage préféré", options, index=options.index(current),
                             format_func=lambda name: labels.get(name, name) or "Aucun",
                             accept_new_options=True,
                             help="Choisis dans la liste (personnage et acteur entre "
                                  "parenthèses) ou tape un autre nom.")
        with_photo = [c for c in found.characters if c.photo_url]
        if with_photo:
            with st.expander("Revoir les personnages (photos)", icon=":material/photo_library:"):
                st.image([c.photo_url for c in with_photo],
                         caption=[c.label for c in with_photo], width=PHOTO_WIDTH)
    if found.errors:
        st.caption(f"Liste des personnages incomplète : {' et '.join(found.errors)} n'a pas "
                   "répondu.")
    return value


def review_form(kind: str, ref_id: int, message_key: str) -> None:
    """Affiche « Mon avis » ; « Enregistrer » remplace l'avis puis recharge la page.

    message_key : clé de session du message de confirmation affiché par la page.
    """
    with common.db() as session:
        rating = library.get_rating(session, kind, ref_id)
    found = characters(kind, ref_id)
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
        character = character_field(found, (rating.favorite_character or "") if rating else "")
        if st.form_submit_button("Enregistrer"):
            with common.db() as session:
                library.set_rating(session, kind, ref_id, value or None, note=note,
                                   emotions=emotions, favorite_character=character)
                session.commit()
            st.session_state[message_key] = "Avis enregistré."
            st.rerun()
