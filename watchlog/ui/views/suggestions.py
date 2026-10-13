"""Page « Suggestions » : séries et films à découvrir, d'après la bibliothèque (TMDB)."""

import streamlit as st

from watchlog.core import catalog, suggestions
from watchlog.core.http import ApiError
from watchlog.core.models import MOVIE, SHOW, TO_WATCH
from watchlog.ui import common

PAGE_SIZE = 15  # suggestions affichées, puis « Afficher plus » par tranche


@st.cache_data(ttl=3600, show_spinner=False)
def load(kind: str) -> list[suggestions.Suggestion]:
    """Calcul gardé une heure (et jusqu'au clic sur « Recalculer »)."""
    with common.db() as session:
        if kind == SHOW:
            suggestions.resolve_show_tmdb_ids(session, common.tmdb_client())
            session.commit()
        return suggestions.compute(session, common.tmdb_client(), kind)


def hidden() -> set[tuple[str, int]]:
    """Titres ajoutés ou écartés pendant la session : masqués sans tout recalculer."""
    return st.session_state.setdefault("sugg_hidden", set())


def add_suggestion(item: suggestions.Suggestion, status_key: str) -> None:
    status = st.session_state[status_key]
    try:
        with common.db() as session:
            if item.kind == SHOW:
                client = common.tvmaze_client()
                obj = suggestions.add_suggested_show(session, common.tmdb_client(), client,
                                                     item.tmdb_id, status, title=item.title)
            else:
                client = common.tmdb_client()
                obj = suggestions.add_suggested_movie(session, client, item.tmdb_id, status)
            session.commit()
            obj_id, title = obj.id, obj.title
            try:
                catalog.ensure_poster(session, obj, client)
                session.commit()
            except Exception:  # noqa: BLE001  (une affiche ratée n'est pas grave)
                session.rollback()
    except (ApiError, ValueError) as exc:
        st.session_state["sugg_error"] = f"Ajout impossible : {exc}"
        return
    hidden().add((item.kind, item.tmdb_id))
    st.session_state["sugg_message"] = (item.kind, obj_id, title)


def dismiss_suggestion(item: suggestions.Suggestion) -> None:
    with common.db() as session:
        suggestions.dismiss(session, item.kind, item.tmdb_id, item.title)
        session.commit()
    hidden().add((item.kind, item.tmdb_id))


def recompute() -> None:
    load.clear()
    hidden().clear()


def restore_dismissed() -> None:
    with common.db() as session:
        suggestions.undismiss_all(session)
        session.commit()
    recompute()


def show_messages() -> None:
    error = st.session_state.pop("sugg_error", None)
    if error:
        st.error(error)
    message = st.session_state.pop("sugg_message", None)
    if message:
        kind, obj_id, title = message
        st.success(f"« {common.md_escape(title)} » a été ajouté à la bibliothèque.")
        page = common.PAGE_SHOW if kind == SHOW else common.PAGE_MOVIE
        st.page_link(page, label="Ouvrir la fiche", query_params={"id": obj_id})


def show_card(item: suggestions.Suggestion) -> None:
    with st.container(border=True):
        col_image, col_text = st.columns([1, 7])
        with col_image:
            if item.poster_url:
                st.image(item.poster_url, width=90)
            else:
                common.show_poster(None)
        with col_text:
            title = common.md_escape(item.title)
            if item.year:
                title += f" ({item.year})"
            st.markdown(f"**{title}**")
            details = []
            if item.original_title and item.original_title != item.title:
                details.append(f"Titre original : {item.original_title}")
            if item.genres:
                details.append(", ".join(item.genres))
            if item.vote_average:
                details.append(f"Note TMDB : {item.vote_average:.1f}/10")
            if details:
                st.caption(common.md_escape(" · ".join(details)))
            if item.because:
                st.caption("Parce que tu as vu : " + common.md_escape(", ".join(item.because)))
            common.show_availability(item.kind, item.tmdb_id, compact=True)
            if item.overview:
                st.write(common.md_escape(item.overview))
            key = f"{item.kind}_{item.tmdb_id}"
            status_key = f"sugg_status_{key}"
            col_status, col_add, col_dismiss = st.columns([2, 1, 1], vertical_alignment="bottom")
            col_status.selectbox("Statut", common.status_options(),
                                 index=common.status_options().index(TO_WATCH),
                                 format_func=common.status_label, key=status_key)
            col_add.button("Ajouter", key=f"sugg_add_{key}", on_click=add_suggestion,
                           args=(item, status_key))
            col_dismiss.button("Pas intéressé", key=f"sugg_dismiss_{key}",
                               on_click=dismiss_suggestion, args=(item,))


def show_tab(kind: str) -> None:
    with st.spinner("Calcul des suggestions… La première fois, cela peut prendre une minute."):
        try:
            items = load(kind)
        except ApiError as exc:
            st.error(f"Suggestions indisponibles : {exc}")
            return
    items = [s for s in items if (s.kind, s.tmdb_id) not in hidden()]
    if not items:
        st.write("Aucune suggestion pour l'instant : ajoute des titres à ta bibliothèque.")
        return

    genres = sorted({g for s in items for g in s.genres})
    chosen = st.multiselect("Genres", genres, key=f"sugg_genres_{kind}",
                            placeholder="Tous les genres")
    if chosen:
        items = [s for s in items if set(chosen) & set(s.genres)]
        if not items:
            st.write("Aucune suggestion pour ces genres.")
            return

    count_key = f"sugg_count_{kind}"
    count = st.session_state.get(count_key, PAGE_SIZE)
    for item in items[:count]:
        show_card(item)
    if len(items) > count:
        if st.button("Afficher plus", key=f"sugg_more_{kind}"):
            st.session_state[count_key] = count + PAGE_SIZE
            st.rerun()


# --- Page ---------------------------------------------------------------------------------

st.title("Suggestions")
st.caption("D'après les recommandations TMDB des titres de ta bibliothèque. Les favoris et les "
           "titres bien notés comptent davantage ; les séries arrêtées et les titres mal notés "
           "éloignent les titres semblables. Plus tu notes, plus les suggestions sont justes.")

connected, tmdb_message = common.tmdb_status()
if not connected:
    st.info(f"{tmdb_message} Les suggestions ont besoin de TMDB.")
    st.stop()

show_messages()
st.button("Recalculer", on_click=recompute,
          help="Les recommandations TMDB restent en cache 30 jours ; ce bouton refait le calcul "
               "avec l'état actuel de la bibliothèque.")

# key + on_change : l'onglet choisi est gardé quand la page se recharge (après un ajout...),
# et seul l'onglet ouvert est calculé.
tab_show, tab_movie = st.tabs(["Séries", "Films"], key="sugg_tab", on_change="rerun")
if tab_show.open:
    with tab_show:
        show_tab(SHOW)
if tab_movie.open:
    with tab_movie:
        show_tab(MOVIE)

with common.db() as session:
    removed = suggestions.dismissed(session)
    removed_titles = [r.title or f"#{r.tmdb_id}" for r in removed]
if removed_titles:
    with st.expander(f"Titres écartés ({len(removed_titles)})"):
        st.write(common.md_escape(", ".join(removed_titles)))
        st.button("Tout réafficher", on_click=restore_dismissed)
