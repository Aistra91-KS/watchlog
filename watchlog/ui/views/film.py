"""Fiche d'un film : informations, statut, visionnage, note, commentaire et émotions."""

from datetime import date, datetime

import streamlit as st

from watchlog.core import library
from watchlog.core.dates import fmt_date
from watchlog.core.models import COMPLETED, MOVIE, TO_WATCH, Movie
from watchlog.core.queue import LOCAL_TZ, local_date
from watchlog.ui import avis, common

MIN_DATE = date(1900, 1, 1)


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


def set_watched(movie_id: int, watched: bool, day_key: str | None = None,
                unknown_key: str | None = None) -> None:
    """Marque vu / non vu. Un film vu passe en « Terminé » ; non vu, il revient en « À voir ».

    day_key : clé du champ « Date de visionnage » (aujourd'hui par défaut) ; unknown_key : clé
    de la case « Date inconnue », qui l'emporte sur le champ.
    """
    with common.db() as session:
        item = library.get_library_item(session, MOVIE, movie_id)
        if watched:
            day = st.session_state.get(day_key) if day_key else datetime.now(LOCAL_TZ).date()
            if unknown_key and st.session_state.get(unknown_key):
                day = None
            if day == datetime.now(LOCAL_TZ).date():
                library.mark_watched(session, MOVIE, movie_id)  # heure exacte
            else:
                library.set_watch_date(session, MOVIE, movie_id, day)  # None : date inconnue
            if item is not None:
                item.status = COMPLETED
        else:
            library.unmark_watched(session, MOVIE, movie_id)
            if item is not None and item.status == COMPLETED:
                item.status = TO_WATCH
        session.commit()


def save_watch_date(movie_id: int, key: str) -> None:
    """Nouvelle date de visionnage (champ vidé : date inconnue)."""
    with common.db() as session:
        library.set_watch_date(session, MOVIE, movie_id, st.session_state[key])
        session.commit()
    st.session_state["film_message"] = "Date de visionnage enregistrée."


def add_rewatch(movie_id: int, day_key: str) -> None:
    """Bouton « Revu » : un visionnage de plus, à la date choisie."""
    with common.db() as session:
        watch = library.add_rewatch(session, MOVIE, movie_id, st.session_state.get(day_key))
        plays = watch.plays
        session.commit()
    st.session_state["film_message"] = f"Revisionnage ajouté : vu {plays} fois."


def remove_rewatch(movie_id: int) -> None:
    with common.db() as session:
        watch = library.remove_rewatch(session, MOVIE, movie_id)
        plays = watch.plays if watch is not None else 0
        session.commit()
    st.session_state["film_message"] = (f"Revisionnage retiré : vu {plays} fois." if plays > 1
                                        else "Revisionnage retiré : vu une fois.")


def forget_watch_date(movie_id: int) -> None:
    """Vu, mais on ne sait plus quand : le film ne compte plus dans aucun mois des stats."""
    with common.db() as session:
        library.set_watch_date(session, MOVIE, movie_id, None)
        session.commit()
    st.session_state["film_message"] = "Date de visionnage : inconnue."


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
        lines.append(f"Durée : {common.fmt_runtime(movie.runtime)}")
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

    if not movie.is_manual and movie.tmdb_id is not None:
        st.markdown("**Où regarder**")
        common.show_availability(MOVIE, movie.tmdb_id)

    # Visionnage (la date se change directement dans le champ)
    today = datetime.now(LOCAL_TZ).date()
    if watch is not None:
        date_key = f"film_watch_date_{movie.id}"
        st.session_state[date_key] = local_date(watch.watched_at, None)
        col_date, col_unknown, col_button = st.columns([2, 1, 1], vertical_alignment="bottom")
        col_date.date_input("Vu le", key=date_key, min_value=MIN_DATE, max_value=today,
                            format="DD/MM/YYYY", on_change=save_watch_date,
                            args=(movie.id, date_key),
                            help="Date du premier visionnage (vide : date inconnue).")
        col_unknown.button("Date inconnue", key=f"film_forget_date_{movie.id}",
                           on_click=forget_watch_date, args=(movie.id,),
                           disabled=watch.watched_at is None,
                           help="Vu, mais tu ne sais plus quand : il ne compte dans aucun mois "
                                "des statistiques.")
        col_button.button("Marquer non vu", on_click=set_watched, args=(movie.id, False))
        if watch.watched_at is None:
            st.caption("Date de visionnage inconnue : choisis une date pour la renseigner.")
        # Revisionnage : un visionnage de plus (les statistiques comptent chaque visionnage).
        rewatch_key = f"film_rewatch_date_{movie.id}"
        col_date, col_button = st.columns([2, 1], vertical_alignment="bottom")
        col_date.date_input("Revu le", value=today, key=rewatch_key, min_value=MIN_DATE,
                            max_value=today, format="DD/MM/YYYY",
                            help="Date du revisionnage (aujourd'hui par défaut).")
        col_button.button("Revu", on_click=add_rewatch, args=(movie.id, rewatch_key))
        plays = watch.plays or 1
        if plays > 1:
            col_text, col_button = st.columns([2, 1], vertical_alignment="center")
            last = local_date(watch.last_watched_at, None)
            col_text.caption(f"Vu {plays} fois"
                             + (f" · dernier visionnage le {fmt_date(last)}" if last else ""))
            col_button.button("Retirer un revisionnage", on_click=remove_rewatch,
                              args=(movie.id,))
        else:
            st.caption("Vu 1 fois")
    else:
        day_key = f"film_new_watch_date_{movie.id}"
        unknown_key = f"film_new_watch_unknown_{movie.id}"
        col_date, col_unknown, col_button = st.columns([2, 1, 1], vertical_alignment="bottom")
        col_date.date_input("Date de visionnage", value=today, key=day_key, min_value=MIN_DATE,
                            max_value=today, format="DD/MM/YYYY",
                            disabled=st.session_state.get(unknown_key, False),
                            help="Pour un film vu il y a longtemps, choisis la date.")
        col_unknown.checkbox("Date inconnue", key=unknown_key,
                             help="Vu, mais tu ne sais plus quand.")
        col_button.button("Vu", type="primary", on_click=set_watched,
                          args=(movie.id, True, day_key, unknown_key))

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

# Note, commentaire, émotions, personnage préféré (même formulaire que la fiche série)
avis.review_form(MOVIE, movie.id, "film_message")

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
