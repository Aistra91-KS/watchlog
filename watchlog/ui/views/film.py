"""Fiche d'un film : informations, statut, visionnage, note, commentaire et émotions."""

import streamlit as st

from watchlog.core import library
from watchlog.core.dates import fmt_date
from watchlog.core.models import COMPLETED, MOVIE, TO_WATCH, Movie
from watchlog.core.queue import local_date
from watchlog.ui import common

RATING_OPTIONS = [0, 1, 2, 3, 4, 5]  # 0 = pas de note


def rating_label(value: int) -> str:
    return "Pas de note" if not value else f"{value} / 5"


def fmt_runtime(minutes: int | None) -> str:
    """112 -> « 1 h 52 », 45 -> « 45 min », 120 -> « 2 h »."""
    if not minutes:
        return ""
    hours, rest = divmod(minutes, 60)
    if hours == 0:
        return f"{rest} min"
    return f"{hours} h {rest:02d}" if rest else f"{hours} h"


def emotion_label(code: str) -> str:
    return library.EMOTION_LABELS.get(code, code)


# --- Enregistrement (fonctions appelées quand l'utilisateur agit) ---------------------

def save_status(movie_id: int, key: str) -> None:
    with common.db() as session:
        library.set_status(session, MOVIE, movie_id, st.session_state[key])
        session.commit()


def save_favorite(movie_id: int, key: str) -> None:
    with common.db() as session:
        library.set_favorite(session, MOVIE, movie_id, st.session_state[key])
        session.commit()


def add_to_library(movie_id: int, key: str) -> None:
    save_status(movie_id, key)
    st.session_state["film_message"] = "Film ajouté à la bibliothèque."


def set_watched(movie_id: int, watched: bool) -> None:
    """Marque vu / non vu. Un film vu passe en « Terminé » ; non vu, il revient en « À voir »."""
    with common.db() as session:
        item = library.get_library_item(session, MOVIE, movie_id)
        if watched:
            library.mark_watched(session, MOVIE, movie_id)
            if item is not None:
                item.status = COMPLETED
        else:
            library.unmark_watched(session, MOVIE, movie_id)
            if item is not None and item.status == COMPLETED:
                item.status = TO_WATCH
        session.commit()


def remove_movie(movie_id: int) -> None:
    with common.db() as session:
        library.remove_from_library(session, MOVIE, movie_id)
        session.commit()
    st.session_state.pop(f"confirm_remove_movie_{movie_id}", None)
    st.session_state["film_message"] = ("Film retiré de la bibliothèque. "
                                        "L'historique de visionnage est conservé.")


# --- Page ------------------------------------------------------------------------------

movie_id = common.selected_id("movie_id")
with common.db() as session:
    movie = session.get(Movie, movie_id) if movie_id is not None else None
    if movie is not None:
        item = library.get_library_item(session, MOVIE, movie.id)
        rating = library.get_rating(session, MOVIE, movie.id)
        watch = library.get_watch(session, MOVIE, movie.id)

if movie is None:
    st.info("Aucun film sélectionné. Choisis un film dans la bibliothèque.")
    st.page_link(common.PAGE_LIBRARY, label="Aller à la bibliothèque")
    st.stop()

message = st.session_state.pop("film_message", None)
if message:
    st.success(message)

col_poster, col_info = st.columns([1, 3])
with col_poster:
    if common.poster_file(movie.poster_path) or not movie.poster_url:
        common.show_poster(movie.poster_path)
    else:
        st.image(movie.poster_url, width="stretch")
with col_info:
    st.title(common.md_escape(movie.title))
    if movie.original_title and movie.original_title != movie.title:
        st.caption(f"Titre original : {common.md_escape(movie.original_title)}")
    lines = []
    if movie.release_date_fr:
        lines.append(f"Sortie en France : {fmt_date(movie.release_date_fr)}")
    if movie.release_date_original and movie.release_date_original != movie.release_date_fr:
        lines.append(f"Sortie d'origine : {fmt_date(movie.release_date_original)}")
    if not lines and movie.year:
        lines.append(f"Année : {movie.year}")
    if movie.runtime:
        lines.append(f"Durée : {fmt_runtime(movie.runtime)}")
    if movie.is_manual:
        lines.append("Saisie manuelle")
    if lines:
        st.write(" · ".join(lines))
    if movie.overview:
        st.write(common.md_escape(movie.overview))

    if not movie.is_manual and movie.last_synced_at is None:
        if movie.tmdb_id is None:
            st.info("Ce film n'a pas d'identifiant TMDB : seules les informations "
                    "de l'import sont connues (pas de résumé ni d'affiche).")
        else:
            st.info("Ce film n'a pas encore de métadonnées TMDB (TMDB non connecté ou "
                    "indisponible). Elles seront récupérées à la prochaine synchronisation "
                    "quand TMDB sera connecté.")

    # Visionnage
    if watch is not None:
        day = local_date(watch.watched_at, None)
        text = f"Vu le {fmt_date(day)}" if day else "Vu (date inconnue)"
        if watch.plays > 1:
            text += f" · vu {watch.plays} fois"
        st.write(text)
        st.button("Marquer non vu", on_click=set_watched, args=(movie.id, False))
    else:
        st.button("Vu", type="primary", on_click=set_watched, args=(movie.id, True))

# Bibliothèque : statut et favori
status_key = f"film_status_{movie.id}"
if item is None:
    st.info("Ce film n'est pas dans ta bibliothèque.")
    col_status, col_button = st.columns([2, 1], vertical_alignment="bottom")
    with col_status:
        st.selectbox("Statut", common.status_options(), format_func=common.status_label,
                     key=status_key)
    with col_button:
        st.button("Ajouter à la bibliothèque", on_click=add_to_library,
                  args=(movie.id, status_key))
else:
    favorite_key = f"film_favorite_{movie.id}"
    # Les widgets reprennent toujours la valeur enregistrée en base.
    st.session_state[status_key] = item.status
    st.session_state[favorite_key] = item.favorite
    col_status, col_favorite = st.columns([2, 1], vertical_alignment="bottom")
    with col_status:
        st.selectbox("Statut", common.status_options(), format_func=common.status_label,
                     key=status_key, on_change=save_status, args=(movie.id, status_key))
    with col_favorite:
        st.checkbox("Favori", key=favorite_key, on_change=save_favorite,
                    args=(movie.id, favorite_key))

# Note, commentaire, émotions
st.subheader("Mon avis")
current_emotions = library.split_emotions(rating.emotions if rating else None)
emotion_options = list(library.EMOTION_LABELS) + [
    e for e in current_emotions if e not in library.EMOTION_LABELS]
if rating and rating.favorite_character:
    st.caption(f"Personnage préféré : {common.md_escape(rating.favorite_character)}")
with st.form(f"film_avis_{movie.id}"):
    value = st.selectbox("Note", RATING_OPTIONS, format_func=rating_label,
                         index=RATING_OPTIONS.index((rating.rating or 0) if rating else 0))
    note = st.text_area("Commentaire", value=(rating.note or "") if rating else "")
    emotions = st.multiselect("Émotions", emotion_options, default=current_emotions,
                              format_func=emotion_label)
    if st.form_submit_button("Enregistrer"):
        with common.db() as session:
            library.set_rating(session, MOVIE, movie.id, value or None, note=note,
                               emotions=emotions)
            session.commit()
        st.session_state["film_message"] = "Avis enregistré."
        st.rerun()

# Retrait de la bibliothèque (avec confirmation)
if item is not None:
    st.divider()
    confirm_key = f"confirm_remove_movie_{movie.id}"
    if st.session_state.get(confirm_key):
        st.warning(f"Retirer « {common.md_escape(movie.title)} » de la bibliothèque ? "
                   "L'historique de visionnage et l'avis sont conservés.")
        col_yes, col_no = st.columns(2)
        col_yes.button("Confirmer le retrait", on_click=remove_movie, args=(movie.id,))
        col_no.button("Annuler", on_click=st.session_state.pop, args=(confirm_key, None))
    elif st.button("Retirer de la bibliothèque"):
        st.session_state[confirm_key] = True
        st.rerun()
