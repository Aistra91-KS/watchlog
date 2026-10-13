"""Ajouter une série (TVMaze), un film (TMDB) ou un titre saisi à la main."""

from datetime import date

import streamlit as st

from watchlog.core import adding, catalog
from watchlog.core.http import ApiError
from watchlog.core.models import COMPLETED, MOVIE, SHOW, TO_WATCH
from watchlog.ui import common

MANUAL = "manual"
MIN_DATE, MAX_DATE = date(1880, 1, 1), date(2100, 12, 31)


def download_poster(session, obj, client) -> None:
    """Télécharge l'affiche ; un échec n'empêche pas l'ajout."""
    try:
        catalog.ensure_poster(session, obj, client)
        session.commit()
    except Exception:  # noqa: BLE001  (une affiche ratée n'est pas grave)
        session.rollback()


def remember_success(tab: str, kind: str, obj_id: int, title: str) -> None:
    """Message affiché en haut de l'onglet après l'ajout (avec lien vers la fiche)."""
    st.session_state[f"add_message_{tab}"] = (kind, obj_id, title)


def show_messages(tab: str) -> None:
    error = st.session_state.pop(f"add_error_{tab}", None)
    if error:
        st.error(error)
    success = st.session_state.pop(f"add_message_{tab}", None)
    if success:
        kind, obj_id, title = success
        st.success(f"« {common.md_escape(title)} » a été ajouté à la bibliothèque.")
        page = common.PAGE_SHOW if kind == SHOW else common.PAGE_MOVIE
        st.page_link(page, label="Ouvrir la fiche", query_params={"id": obj_id})


def default_status_index() -> int:
    return common.status_options().index(TO_WATCH)


# --- Ajout d'un résultat de recherche ----------------------------------------------------

def watched_on_key(status_key: str) -> str:
    """Clé du champ « Vu le » associé à une liste de statuts (films en « Terminé »)."""
    return status_key.replace("add_status_", "add_watched_on_", 1)


def watched_on_input(status_key: str, label: str = "Vu le (facultatif)") -> None:
    """Champ date pour un film « Terminé » : date du visionnage, pour les statistiques."""
    if st.session_state.get(status_key) == COMPLETED:
        st.date_input(label, value=None, key=watched_on_key(status_key), min_value=MIN_DATE,
                      max_value=date.today(), format="DD/MM/YYYY",
                      help="Vide : date inconnue (modifiable ensuite dans la fiche).")


def add_result(kind: str, index: int, status_key: str) -> None:
    """Appelée par le bouton « Ajouter » d'un résultat de recherche."""
    result = st.session_state[f"add_results_{kind}"][index]
    status = st.session_state[status_key]
    watched_on = st.session_state.get(watched_on_key(status_key))
    try:
        with common.db() as session:
            if kind == SHOW:
                client = common.tvmaze_client()
                obj = adding.add_show(session, client, result["id"], status)
            else:
                client = common.tmdb_client()
                obj = adding.add_movie(session, client, result["id"], status,
                                       watched_on=watched_on)
            session.commit()
            obj_id, title = obj.id, obj.title
            download_poster(session, obj, client)
    except (ApiError, ValueError) as exc:
        st.session_state[f"add_error_{kind}"] = f"Ajout impossible : {exc}"
        return
    result["in_library"], result["local_id"] = True, obj_id
    remember_success(kind, kind, obj_id, title)


def search_form(kind: str, label: str) -> None:
    """Formulaire de recherche ; les résultats sont gardés dans st.session_state."""
    with st.form(f"add_search_{kind}"):
        query = st.text_input(label)
        submitted = st.form_submit_button("Rechercher")
    if submitted:
        try:
            with common.db() as session:
                if kind == SHOW:
                    results = adding.search_shows(session, common.tvmaze_client(), query)
                else:
                    results = adding.search_movies(session, common.tmdb_client(), query)
        except ApiError as exc:
            st.error(f"Recherche impossible : {exc}")
            return
        st.session_state[f"add_results_{kind}"] = results
        st.session_state[f"add_query_{kind}"] = query.strip()


def show_results(kind: str) -> None:
    results = st.session_state.get(f"add_results_{kind}")
    if results is None:
        return
    if not results:
        query = common.md_escape(st.session_state.get(f"add_query_{kind}", ""))
        st.write(f"Aucun résultat pour « {query} ».")
        return
    for index, result in enumerate(results):
        with st.container(border=True):
            col_image, col_text = st.columns([1, 7])
            with col_image:
                if result["poster_url"]:
                    st.image(result["poster_url"], width=80)
            with col_text:
                title = common.md_escape(result["title"])
                if result["year"]:
                    title += f" ({result['year']})"
                st.markdown(f"**{title}**")
                details = []
                if result["original_title"] and result["original_title"] != result["title"]:
                    details.append(f"Titre original : {result['original_title']}")
                details += [d for d in (result["network"], result["status"]) if d]
                if details:
                    st.caption(common.md_escape(" · ".join(details)))
                if result["summary"]:
                    st.write(common.md_escape(result["summary"]))
                if result["in_library"]:
                    st.write("Déjà dans la bibliothèque")
                    page = common.PAGE_SHOW if kind == SHOW else common.PAGE_MOVIE
                    st.page_link(page, label="Ouvrir la fiche",
                                 query_params={"id": result["local_id"]})
                else:
                    status_key = f"add_status_{kind}_{result['id']}"
                    col_status, col_button = st.columns([2, 1], vertical_alignment="bottom")
                    col_status.selectbox("Statut", common.status_options(),
                                         index=default_status_index(),
                                         format_func=common.status_label, key=status_key)
                    col_button.button("Ajouter", key=f"add_button_{kind}_{result['id']}",
                                      on_click=add_result, args=(kind, index, status_key))
                    if kind == MOVIE:
                        watched_on_input(status_key)


# --- Saisie manuelle ----------------------------------------------------------------------

def manual_show_form() -> None:
    with st.form("add_manual_show", clear_on_submit=False):
        title = st.text_input("Titre")
        original_title = st.text_input("Titre original (facultatif)")
        counts = st.text_input("Nombre d'épisodes par saison",
                               placeholder="10, 8, 12",
                               help="Un nombre par saison, séparés par des virgules.")
        premiered = st.date_input("Première diffusion (facultatif)", value=None,
                                  min_value=MIN_DATE, max_value=MAX_DATE, format="DD/MM/YYYY")
        status = st.selectbox("Statut", common.status_options(), index=default_status_index(),
                              format_func=common.status_label)
        submitted = st.form_submit_button("Ajouter la série")
    if submitted:
        try:
            episodes = adding.parse_episode_counts(counts)
            with common.db() as session:
                show = adding.add_manual_show(session, title, episodes, status,
                                              original_title=original_title or None,
                                              premiered=premiered)
                session.commit()
                remember_success(MANUAL, SHOW, show.id, show.title)
        except ValueError as exc:
            st.session_state[f"add_error_{MANUAL}"] = str(exc)
        st.rerun()


def manual_movie_form() -> None:
    with st.form("add_manual_movie"):
        title = st.text_input("Titre")
        year = st.number_input("Année (facultatif)", min_value=1880, max_value=2100,
                               value=None, step=1)
        release_date = st.date_input("Date de sortie en France (facultatif)", value=None,
                                     min_value=MIN_DATE, max_value=MAX_DATE,
                                     format="DD/MM/YYYY")
        runtime = st.number_input("Durée en minutes (facultatif)", min_value=1,
                                  max_value=1000, value=None, step=1)
        overview = st.text_area("Résumé (facultatif)")
        status = st.selectbox("Statut", common.status_options(), index=default_status_index(),
                              format_func=common.status_label)
        watched_on = st.date_input("Vu le (si statut « Terminé », facultatif)", value=None,
                                   min_value=MIN_DATE, max_value=date.today(),
                                   format="DD/MM/YYYY")
        submitted = st.form_submit_button("Ajouter le film")
    if submitted:
        try:
            with common.db() as session:
                movie = adding.add_manual_movie(
                    session, title, status, year=int(year) if year else None,
                    release_date=release_date, runtime=int(runtime) if runtime else None,
                    overview=overview, watched_on=watched_on)
                session.commit()
                remember_success(MANUAL, MOVIE, movie.id, movie.title)
        except ValueError as exc:
            st.session_state[f"add_error_{MANUAL}"] = str(exc)
        st.rerun()


# --- Page ---------------------------------------------------------------------------------

st.title("Ajouter")
# key + on_change : l'onglet choisi est gardé quand la page se recharge (après un ajout).
tab_show, tab_movie, tab_manual = st.tabs(["Série", "Film", "Saisie manuelle"],
                                          key="add_tab", on_change="rerun")

with tab_show:
    show_messages(SHOW)
    search_form(SHOW, "Titre de la série")
    show_results(SHOW)

with tab_movie:
    show_messages(MOVIE)
    connected, tmdb_message = common.tmdb_status()
    if not connected:
        st.info(f"{tmdb_message} La recherche de films est indisponible ; "
                "tu peux ajouter un film dans l'onglet « Saisie manuelle ».")
    else:
        search_form(MOVIE, "Titre du film")
        show_results(MOVIE)

with tab_manual:
    show_messages(MANUAL)
    st.caption("Pour un titre absent de TVMaze ou de TMDB.")
    choice = st.radio("Type", ["Série", "Film"], horizontal=True, key="add_manual_kind")
    if choice == "Série":
        manual_show_form()
    else:
        manual_movie_form()
