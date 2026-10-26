"""Ajouter une série ou un film : recherche unique TVMaze + TMDB, ou saisie à la main."""

from datetime import date

import streamlit as st

from watchlog.core import adding
from watchlog.core.http import ApiError
from watchlog.core.models import COMPLETED, MOVIE, SHOW, TO_WATCH, WATCHING
from watchlog.ui import common

SEARCH = "search"
MANUAL = "manual"
MIN_DATE, MAX_DATE = date(1880, 1, 1), date(2100, 12, 31)
# Badge de chaque résultat : séries en bleu, films en orange (couleurs de CLAUDE.md).
KIND_BADGES = {SHOW: ":blue-badge[Série]", MOVIE: ":orange-badge[Film]"}
# « J'en suis à S2E5 » : date des visionnages passés (inconnue par défaut, pour ne pas
# fausser les statistiques avec la date du jour).
UNKNOWN_DATE, CHOSEN_DATE = "Date inconnue", "Je choisis une date"
# Paramètre d'URL de la page Aperçu ouverte d'ici : son lien « Retour » revient à la recherche.
PREVIEW_ORIGIN = {"from": "ajouter"}


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


# --- « J'en suis à S2E5 » (série déjà commencée) -----------------------------------------

def start_progress(tab: str, show_id: int, title: str, status: str) -> None:
    """Après l'ajout d'une série : propose d'indiquer le dernier épisode vu."""
    st.session_state[f"add_progress_{tab}"] = (show_id, title, status == WATCHING)


def close_progress(tab: str) -> None:
    st.session_state.pop(f"add_progress_{tab}", None)


def save_progress(tab: str, show_id: int, title: str) -> None:
    """Bouton « Marquer vus » : tous les épisodes jusqu'au choix, à la date indiquée."""
    state = st.session_state
    season = state[f"progress_season_{show_id}"]
    number = state[f"progress_episode_{show_id}_{season}"]
    day = None
    if state[f"progress_date_mode_{show_id}"] == CHOSEN_DATE:
        day = state.get(f"progress_day_{show_id}")
        if day is None:
            state[f"add_error_{tab}"] = "Choisis la date, ou garde « Date inconnue »."
            return
    try:
        with common.db() as session:
            count = adding.mark_progress(session, show_id, season, number, day=day,
                                         status=state[f"progress_status_{show_id}"])
            session.commit()
    except ValueError as exc:
        state[f"add_error_{tab}"] = f"Progression non enregistrée : {exc}"
        return
    close_progress(tab)
    state[f"add_progress_done_{tab}"] = (
        f"« {common.md_escape(title)} » : {count} épisode(s) marqué(s) vu(s), "
        f"jusqu'à S{season}E{number}.")


def episode_label(number: int, title: str | None) -> str:
    return f"Épisode {number}" + (f" · {title}" if title else "")


def progress_panel(tab: str) -> None:
    """Choix du dernier épisode vu d'une série qu'on vient d'ajouter (« j'en suis à S2E5 »)."""
    done = st.session_state.pop(f"add_progress_done_{tab}", None)
    if done:
        st.success(done)
    progress = st.session_state.get(f"add_progress_{tab}")
    if not progress:
        return
    show_id, title, watching = progress
    with common.db() as session:
        choices = adding.episode_choices(session, show_id)
    if not choices:
        return  # aucun épisode diffusé connu : rien à proposer
    label = f"Tu as déjà commencé « {common.md_escape(title)} » ? Indique où tu en es"
    with st.expander(label, expanded=watching):
        st.caption("Tous les épisodes jusqu'à celui-ci inclus (hors spéciaux) seront "
                   "marqués vus.")
        col_season, col_episode = st.columns([1, 2])
        season = col_season.selectbox("Saison", list(choices),
                                      format_func=lambda s: f"Saison {s}",
                                      key=f"progress_season_{show_id}")
        episodes = dict(choices[season])
        col_episode.selectbox("Dernier épisode vu", list(episodes),
                              format_func=lambda n: episode_label(n, episodes[n]),
                              key=f"progress_episode_{show_id}_{season}")
        mode = st.radio("Date de ces visionnages", [UNKNOWN_DATE, CHOSEN_DATE],
                        horizontal=True, key=f"progress_date_mode_{show_id}",
                        help="Vus dans le passé : la date du jour fausserait les statistiques.")
        if mode == CHOSEN_DATE:
            st.date_input("Vus le", value=None, min_value=MIN_DATE, max_value=date.today(),
                          format="DD/MM/YYYY", key=f"progress_day_{show_id}")
        st.selectbox("Statut", common.status_options(),
                     index=common.status_options().index(WATCHING),
                     format_func=common.status_label, key=f"progress_status_{show_id}")
        col_save, col_close = st.columns(2)
        col_save.button("Marquer vus", type="primary", key=f"progress_save_{show_id}",
                        on_click=save_progress, args=(tab, show_id, title))
        col_close.button("Non merci", key=f"progress_close_{show_id}",
                         on_click=close_progress, args=(tab,))


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


def add_result(index: int, status_key: str) -> None:
    """Appelée par le bouton « Ajouter » d'un résultat de recherche (série ou film)."""
    result = st.session_state["add_results"][index]
    kind = result["kind"]
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
            common.download_poster(session, obj, client)
    except (ApiError, ValueError) as exc:
        st.session_state[f"add_error_{SEARCH}"] = f"Ajout impossible : {exc}"
        return
    result["in_library"], result["local_id"] = True, obj_id
    remember_success(SEARCH, kind, obj_id, title)
    if kind == SHOW:
        start_progress(SEARCH, obj_id, title, status)


def open_show_preview(index: int) -> None:
    """Bouton « Voir la fiche » d'une série : son id TMDB est cherché (par l'id TVDB).

    La page Aperçu est ouverte au rendu suivant (pas de changement de page dans un callback).
    """
    result = st.session_state["add_results"][index]
    try:
        tmdb_id = common.tmdb_client().find_tv_by_tvdb(result["tvdb_id"])
    except ApiError as exc:
        st.session_state[f"add_error_{SEARCH}"] = f"Fiche indisponible : {exc}"
        return
    if tmdb_id is None:
        st.session_state[f"add_error_{SEARCH}"] = (
            f"Pas de fiche détaillée pour « {common.md_escape(result['title'])} » : "
            "série introuvable sur TMDB.")
        return
    st.session_state["add_open_preview"] = {**common.preview_params(SHOW, tmdb_id),
                                            **PREVIEW_ORIGIN}


def preview_link(index: int, result: dict) -> None:
    """Lien vers la page Aperçu (casting, bande-annonce, plateformes) d'un titre pas ajouté.

    L'Aperçu lit TMDB : rien si TMDB n'est pas connecté, ni pour une série sans id TVDB.
    """
    if not common.tmdb_status()[0]:
        return
    if result["kind"] == MOVIE:
        st.page_link(common.PAGE_PREVIEW, label="Voir la fiche", icon=":material/info:",
                     query_params={**common.preview_params(MOVIE, result["id"]),
                                   **PREVIEW_ORIGIN})
    elif result["tvdb_id"]:
        st.button("Voir la fiche", icon=":material/info:", type="tertiary",
                  key=f"add_preview_{result['id']}", on_click=open_show_preview, args=(index,))


def search_form() -> None:
    """Recherche unique (séries TVMaze + films TMDB) ; résultats gardés dans session_state."""
    with st.form("add_search"):
        query = st.text_input("Titre d'une série ou d'un film")
        submitted = st.form_submit_button("Rechercher")
    if submitted:
        connected, tmdb_message = common.tmdb_status()
        with common.db() as session:
            outcome = adding.search_all(session, common.tvmaze_client(),
                                        common.tmdb_client() if connected else None, query)
        errors = dict(outcome.errors)
        if not connected and MOVIE in errors:
            errors[MOVIE] += f" ({tmdb_message})"
        st.session_state["add_results"] = outcome.results
        st.session_state["add_search_errors"] = errors
        st.session_state["add_query"] = query.strip()


def show_result(index: int, result: dict) -> None:
    """Un résultat : affiche, badge Série / Film, détails, statut et bouton « Ajouter »."""
    kind = result["kind"]
    with st.container(border=True):
        col_image, col_text = st.columns([1, 7])
        with col_image:
            if result["poster_url"]:
                st.image(result["poster_url"], width=80)
        with col_text:
            title = common.md_escape(result["title"])
            if result["year"]:
                title += f" ({result['year']})"
            st.markdown(f"{KIND_BADGES[kind]} **{title}**")
            details = []
            if result["original_title"] and result["original_title"] != result["title"]:
                details.append(f"Titre original : {result['original_title']}")
            details += [d for d in (result["network"], result["status"]) if d]
            if details:
                st.caption(common.md_escape(" · ".join(details)))
            if result["summary"]:
                st.write(common.md_escape(result["summary"]))
            if not result["in_library"]:
                preview_link(index, result)
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
                                  on_click=add_result, args=(index, status_key))
                if kind == MOVIE:
                    watched_on_input(status_key)


def show_results() -> None:
    results = st.session_state.get("add_results")
    if results is None:
        return
    for message in st.session_state.get("add_search_errors", {}).values():
        st.warning(message)
    if not results:
        query = common.md_escape(st.session_state.get("add_query", ""))
        st.write(f"Aucun résultat pour « {query} ».")
        return
    with common.db() as session:  # titre ajouté depuis l'Aperçu entre-temps
        adding.refresh_library_state(session, results)
    shows = sum(1 for r in results if r["kind"] == SHOW)
    st.caption(f"{shows} série(s) et {len(results) - shows} film(s)")
    for index, result in enumerate(results):
        show_result(index, result)


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
                start_progress(MANUAL, show.id, show.title, status)
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

# « Voir la fiche » d'une série (callback open_show_preview) : on change de page ici.
preview_params = st.session_state.pop("add_open_preview", None)
if preview_params:
    st.switch_page(common.PAGE_PREVIEW, query_params=preview_params)

st.title("Ajouter")
# key + on_change : l'onglet choisi est gardé quand la page se recharge (après un ajout).
tab_search, tab_manual = st.tabs(["Rechercher", "Saisie manuelle"],
                                 key="add_tab", on_change="rerun")

with tab_search:
    show_messages(SEARCH)
    progress_panel(SEARCH)
    st.caption("Séries cherchées sur TVMaze, films sur TMDB.")
    search_form()
    show_results()

with tab_manual:
    show_messages(MANUAL)
    progress_panel(MANUAL)
    st.caption("Pour un titre absent de TVMaze ou de TMDB.")
    choice = st.radio("Type", ["Série", "Film"], horizontal=True, key="add_manual_kind")
    if choice == "Série":
        manual_show_form()
    else:
        manual_movie_form()
