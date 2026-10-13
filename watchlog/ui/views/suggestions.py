"""Page « Suggestions » : séries et films à découvrir, d'après la bibliothèque (TMDB)."""

from datetime import date

import streamlit as st

from watchlog.core import suggestions
from watchlog.core.http import ApiError
from watchlog.core.models import COMPLETED, MOVIE, SHOW, TO_WATCH
from watchlog.ui import common

PAGE_SIZE = 15  # suggestions affichées, puis « Afficher plus » par tranche
TYPE_ALL, TYPE_SHOWS, TYPE_MOVIES = "Tout", "Séries", "Films"
TYPE_KINDS = {TYPE_ALL: (SHOW, MOVIE), TYPE_SHOWS: (SHOW,), TYPE_MOVIES: (MOVIE,)}
MIN_DATE = date(1900, 1, 1)


@st.cache_data(ttl=3600, show_spinner=False)
def load() -> tuple[list[suggestions.Suggestion], list[str]]:
    """Séries et films suggérés, avec leurs plateformes. Gardé une heure (ou jusqu'à « Recalculer »).

    Renvoie (suggestions, messages d'erreur éventuels).
    """
    tmdb = common.tmdb_client()
    items, errors = [], []
    with common.db() as session:
        suggestions.resolve_show_tmdb_ids(session, tmdb)
        session.commit()
        for kind, label in ((SHOW, "séries"), (MOVIE, "films")):
            try:
                items += suggestions.compute(session, tmdb, kind)
            except ApiError as exc:
                errors.append(f"Suggestions de {label} indisponibles : {exc}")
    suggestions.attach_availability(tmdb, items)
    return items, errors


def hidden() -> set[tuple[str, int]]:
    """Titres ajoutés ou écartés pendant la session : masqués sans tout recalculer."""
    return st.session_state.setdefault("sugg_hidden", set())


def add_suggestion(item: suggestions.Suggestion, status_key: str, date_key: str) -> None:
    try:
        obj_id, title = common.add_from_tmdb(item.kind, item.tmdb_id, st.session_state[status_key],
                                             title=item.title,
                                             watched_on=st.session_state.get(date_key))
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


def reset_filters() -> None:
    for key in ("sugg_genres", "sugg_platforms", "sugg_streaming", "sugg_years", "sugg_min_vote"):
        st.session_state.pop(key, None)
    st.session_state["sugg_count"] = PAGE_SIZE


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
                st.image(item.poster_url, width=100)
            else:
                common.show_poster(None)
        with col_text:
            title = common.md_escape(item.title)
            if item.year:
                title += f" ({item.year})"
            # Titre cliquable : page Aperçu (casting, bande-annonce, titres similaires...)
            st.page_link(common.PAGE_PREVIEW, label=f"**{title}**", icon=":material/open_in_new:",
                         query_params=common.preview_params(item.kind, item.tmdb_id),
                         help="Voir la fiche détaillée")
            badge = ":blue-badge[Série]" if item.kind == SHOW else ":violet-badge[Film]"
            st.markdown(f"{badge} :green-badge[{item.match} % pour toi]")
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
            status_key, date_key = f"sugg_status_{key}", f"sugg_date_{key}"
            col_status, col_add, col_dismiss = st.columns([2, 1, 1], vertical_alignment="bottom")
            col_status.selectbox("Statut", common.status_options(),
                                 index=common.status_options().index(TO_WATCH),
                                 format_func=common.status_label, key=status_key)
            col_add.button("Ajouter", key=f"sugg_add_{key}", on_click=add_suggestion,
                           args=(item, status_key, date_key))
            col_dismiss.button("Pas intéressé", key=f"sugg_dismiss_{key}",
                               on_click=dismiss_suggestion, args=(item,))
            if item.kind == MOVIE and st.session_state.get(status_key) == COMPLETED:
                st.date_input("Vu le (facultatif)", value=None, key=date_key, min_value=MIN_DATE,
                              max_value=date.today(), format="DD/MM/YYYY")


def filters(items: list[suggestions.Suggestion]) -> dict:
    """Barre de filtres. Les choix sont gardés pendant la session (clés de widgets)."""
    col_type, col_sort, col_reset, col_recompute = st.columns(
        [3, 2, 1, 1], vertical_alignment="bottom")
    with col_type:
        kind_choice = st.segmented_control("Type", [TYPE_ALL, TYPE_SHOWS, TYPE_MOVIES],
                                           default=TYPE_ALL, key="sugg_type", required=True)
    with col_sort:
        sort = st.selectbox("Trier par", suggestions.SORTS, key="sugg_sort")
    col_reset.button("Réinitialiser", on_click=reset_filters, help="Retire tous les filtres.")
    col_recompute.button("Recalculer", on_click=recompute,
                         help="Les recommandations TMDB restent en cache 30 jours ; ce bouton "
                              "refait le calcul avec l'état actuel de la bibliothèque.")

    kinds = TYPE_KINDS[kind_choice or TYPE_ALL]
    pool = [s for s in items if s.kind in kinds]
    genres = sorted({g for s in pool for g in s.genres})
    platforms = sorted({p for s in pool for p in s.streaming}, key=str.casefold)
    years = [s.year for s in pool if s.year]

    col_genres, col_platforms = st.columns(2)
    chosen_genres = col_genres.multiselect("Genres", genres, key="sugg_genres",
                                           placeholder="Tous les genres")
    chosen_platforms = col_platforms.multiselect(
        "Plateformes (abonnement ou gratuit)", platforms, key="sugg_platforms",
        placeholder="Toutes les plateformes")
    col_years, col_vote, col_streaming = st.columns([3, 2, 2], vertical_alignment="bottom")
    year_range = None
    if years and min(years) < max(years):
        low, high = min(years), max(years)
        current = st.session_state.get("sugg_years")
        if current and not (low <= current[0] <= current[1] <= high):
            st.session_state.pop("sugg_years")  # bornes changées (type, recalcul) : on repart
        year_range = col_years.slider("Années de sortie", low, high, (low, high), key="sugg_years")
        if year_range == (low, high):
            year_range = None
    min_vote = col_vote.slider("Note TMDB minimale", 0.0, 9.0, 0.0, 0.5, key="sugg_min_vote")
    streaming_only = col_streaming.checkbox("Dispo en streaming uniquement", key="sugg_streaming",
                                            help="Sur une plateforme d'abonnement ou gratuite, en France.")
    return {"kinds": kinds, "genres": tuple(chosen_genres), "platforms": tuple(chosen_platforms),
            "streaming_only": streaming_only, "years": year_range, "min_vote": min_vote,
            "sort": sort}


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
with st.spinner("Calcul des suggestions… La première fois, cela peut prendre une minute."):
    all_items, load_errors = load()
for error in load_errors:
    st.error(error)
all_items = [s for s in all_items if (s.kind, s.tmdb_id) not in hidden()]

chosen = filters(all_items)
items = suggestions.filter_items(all_items, **chosen)
st.caption(f"{len(items)} suggestion(s)")
if not all_items:
    st.write("Aucune suggestion pour l'instant : ajoute des titres à ta bibliothèque.")
elif not items:
    st.write("Aucune suggestion avec ces filtres.")

count = st.session_state.get("sugg_count", PAGE_SIZE)
for item in items[:count]:
    show_card(item)
if len(items) > count:
    if st.button("Afficher plus"):
        st.session_state["sugg_count"] = count + PAGE_SIZE
        st.rerun()

with common.db() as session:
    removed_titles = [r.title or f"#{r.tmdb_id}" for r in suggestions.dismissed(session)]
if removed_titles:
    with st.expander(f"Titres écartés ({len(removed_titles)})"):
        st.write(common.md_escape(", ".join(removed_titles)))
        st.button("Tout réafficher", on_click=restore_dismissed)
