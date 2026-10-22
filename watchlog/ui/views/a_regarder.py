"""Page « À regarder » : épisodes et films non vus, dans une seule liste triée par date
(ou par dernier visionnage de la série, au choix)."""

from datetime import date, datetime

import streamlit as st

from watchlog.core import library, progress, queue
from watchlog.core.dates import LOCAL_TZ
from watchlog.core.models import EPISODE, MOVIE, TO_WATCH, WATCHING
from watchlog.core.queue import (AVAILABILITY_LABELS, NO_DATE, RELEASED, SORT_DATE, SORT_LABELS,
                                 UPCOMING)
from watchlog.ui import common

PAGE_SIZE = 50
TYPE_LABELS = {"all": "Tout", EPISODE: "Épisodes", MOVIE: "Films"}
# Largeurs des colonnes d'une carte : affiche, infos, boutons
COLUMNS = [0.5, 6, 1.4]
POSTER_WIDTH = 64    # largeur des affiches, en pixels
NEW_DAYS = 7         # badge « Nouveau » : sorti depuis moins de NEW_DAYS jours
# Titres des sections de la liste, dans l'ordre de tri de queue.build_queue
SECTION_TITLES = {RELEASED: ":material/play_circle: Disponibles",
                  UPCOMING: ":material/event_upcoming: À venir",
                  NO_DATE: ":material/help: Sans date"}


md = common.md_escape  # protège les caractères Markdown des titres


def fmt_runtime(minutes: int | None) -> str:
    """45 -> « 45 min », 112 -> « 1 h 52 »."""
    if not minutes:
        return ""
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d}"


def gray_if(text: str, gray: bool) -> str:
    return f":gray[{text}]" if gray and text else text


def relative_day(day: date | None, today: date) -> str:
    """« Aujourd'hui », « Hier », « Dans 3 jours »... au-delà d'une semaine, la date."""
    if day is None:
        return "Sans date"
    delta = (day - today).days
    names = {0: "Aujourd'hui", -1: "Hier", 1: "Demain"}
    if delta in names:
        return names[delta]
    if 1 < delta <= 7:
        return f"Dans {delta} jours"
    if -7 <= delta < -1:
        return f"Il y a {-delta} jours"
    return common.fmt_date(day)


def card_css() -> str:
    """Liseré à gauche des cartes (épisode bleu, film orange) ; affiches à venir pâlies."""
    colors = common.chart_colors()
    return (
        "<style>"
        f"[class*='st-key-aq_card_'][class*='_{EPISODE}_']{{border-left:4px solid "
        f"{colors['episodes']} !important}}"
        f"[class*='st-key-aq_card_'][class*='_{MOVIE}_']{{border-left:4px solid "
        f"{colors['films']} !important}}"
        "[class*='st-key-aq_card_up_'] img{opacity:0.45;filter:grayscale(60%)}"
        "[class*='st-key-aq_card_'] img{border-radius:4px}"
        "</style>")


# --- Actions -------------------------------------------------------------------

def mark_one(item: queue.QueueItem) -> None:
    with common.db() as session:
        library.mark_watched(session, item.kind, item.ref_id)
        session.commit()
    name = f"{item.title} {item.code}".strip()
    st.session_state["aq_last_action"] = {"text": f"« {name} » marqué vu.",
                                          "undo": (item.kind, item.ref_id)}
    st.rerun()


def mark_until(item: queue.QueueItem, include_specials: bool) -> None:
    with common.db() as session:
        count = library.mark_watched_until(session, item.ref_id, include_specials=include_specials)
        session.commit()
    plural = "s" if count > 1 else ""
    st.session_state["aq_last_action"] = {
        "text": f"{item.title} : {count} épisode{plural} marqué{plural} vu{plural} "
                f"jusqu'à {item.code}.", "undo": None}
    st.rerun()


def undo_last(kind: str, ref_id: int) -> None:
    with common.db() as session:
        library.unmark_watched(session, kind, ref_id)
        session.commit()
    st.session_state.pop("aq_last_action", None)
    st.rerun()


# --- Affichage -------------------------------------------------------------------

def show_last_action() -> None:
    """Confirmation de la dernière action, avec « Annuler » pour un simple « Vu »."""
    action = st.session_state.get("aq_last_action")
    if not action:
        return
    text_col, undo_col = st.columns([6, 1], vertical_alignment="center")
    text_col.caption(md(action["text"]))
    if action["undo"] and undo_col.button("Annuler", key="aq_undo"):
        undo_last(*action["undo"])


def show_check_banner(count: int) -> None:
    if count == 0:
        return
    text_col, link_col = st.columns([4, 1], vertical_alignment="center")
    plural = "s" if count > 1 else ""
    text_col.caption(f"{count} série{plural} à vérifier (correspondance TVMaze ou "
                     "visionnages en attente).")
    link_col.page_link(common.PAGE_SETTINGS, label="Ouvrir les paramètres")


def show_summary(items: list[queue.QueueItem]) -> None:
    """Compteurs en tête de liste : ce qui est dispo, ce qui arrive, le temps à rattraper."""
    released = [item for item in items if item.availability == RELEASED]
    episodes = sum(1 for item in released if item.kind == EPISODE)
    minutes = sum(item.runtime or 0 for item in released)
    cols = st.columns(4)
    cols[0].metric("Disponibles", len(released), border=True)
    cols[1].metric("Épisodes / films dispo", f"{episodes} / {len(released) - episodes}",
                   border=True)
    cols[2].metric("À venir", len(items) - len(released), border=True)
    cols[3].metric("Temps à rattraper", fmt_runtime(minutes) or "—", border=True)


def show_row(item: queue.QueueItem, include_specials: bool, today: date) -> None:
    gray = item.availability != RELEASED     # à venir ou sans date : grisé, pas de bouton
    key = f"{item.kind}_{item.ref_id}"
    with st.container(border=True, key=f"aq_card_{'up' if gray else 'ok'}_{key}"):
        poster_col, info_col, action_col = st.columns(COLUMNS, vertical_alignment="center")
        with poster_col:
            common.show_poster(item.poster_path, width=POSTER_WIDTH, poster_url=item.poster_url)
        with info_col:
            title = gray_if(f"**{md(item.title)}**", gray)
            if st.button(title, key=f"aq_open_{key}", type="tertiary"):
                if item.kind == EPISODE:
                    common.open_show(item.show_id)
                else:
                    common.open_movie(item.ref_id)
            badges = [f":blue-badge[{item.code}]" if item.kind == EPISODE
                      else ":orange-badge[Film]"]
            if (not gray and item.release_date
                    and (today - item.release_date).days < NEW_DAYS):
                badges.append(":green-badge[:material/fiber_new: Nouveau]")
            if item.episode_title:
                badges.append(gray_if(md(item.episode_title), gray))
            st.markdown(" ".join(badges))
            details = [f":material/calendar_today: {relative_day(item.release_date, today)}"]
            if item.runtime:
                details.append(f":material/schedule: {fmt_runtime(item.runtime)}")
            st.caption(" · ".join(details))
        if gray:
            return
        with action_col:
            if st.button("Vu", key=f"aq_seen_{key}", icon=":material/check:", width="stretch"):
                mark_one(item)
            if item.kind == EPISODE and st.button(
                    "Vu jusqu'ici", key=f"aq_until_{key}", icon=":material/done_all:",
                    width="stretch", help="Marque vus tous les épisodes précédents"):
                mark_until(item, include_specials)


def show_pagination(total_pages: int) -> None:
    if total_pages <= 1:
        return
    prev_col, text_col, next_col = st.columns([1, 2, 1], vertical_alignment="center")
    page = st.session_state["aq_page"]
    if prev_col.button("Page précédente", disabled=page == 0, key="aq_prev"):
        st.session_state["aq_page"] = page - 1
        st.rerun()
    text_col.caption(f"Page {page + 1} sur {total_pages}")
    if next_col.button("Page suivante", disabled=page >= total_pages - 1, key="aq_next"):
        st.session_state["aq_page"] = page + 1
        st.rerun()


# --- Page ----------------------------------------------------------------------

st.title("À regarder")

# Filtres (mémorisés pour la session, même en changeant de page : persist_state)
row1 = st.columns([1, 2, 2, 2])
kind = row1[0].selectbox("Type", list(TYPE_LABELS), format_func=TYPE_LABELS.get,
                         key="aq_kind", persist_state="session")
availabilities = row1[2].multiselect(
    "Disponibilité", [RELEASED, UPCOMING, NO_DATE], default=[RELEASED, UPCOMING, NO_DATE],
    format_func=AVAILABILITY_LABELS.get, key="aq_availability", persist_state="session")
statuses = row1[3].multiselect(
    "Statut", common.status_options(), default=[WATCHING, TO_WATCH],
    format_func=common.status_label, key="aq_statuses", persist_state="session")
row2 = st.columns([2, 2, 2], vertical_alignment="bottom")
next_only = row2[0].checkbox("Par série : prochain épisode seulement", key="aq_next_only",
                             persist_state="session")
include_specials = row2[1].checkbox("Inclure les épisodes spéciaux", key="aq_specials",
                                    persist_state="session")
sort = row2[2].selectbox("Trier par", list(SORT_LABELS), format_func=SORT_LABELS.get,
                         index=list(SORT_LABELS).index(SORT_DATE), key="aq_sort",
                         persist_state="session",
                         help="« Dernier visionnage » : les séries regardées le plus récemment "
                              "en premier (parmi les épisodes disponibles).")

kinds = (EPISODE, MOVIE) if kind == "all" else (kind,)
with common.db() as session:
    items = queue.build_queue(session, kinds=kinds, statuses=statuses,
                              availabilities=availabilities, include_specials=include_specials,
                              next_only=next_only, sort=sort)
    to_check = progress.shows_to_check(session)

# Choix d'une série : seulement celles présentes dans la liste
show_titles = {item.show_id: item.title for item in items if item.kind == EPISODE}
show_ids = [None] + sorted(show_titles, key=lambda i: progress.normalize(show_titles[i]))
if st.session_state.get("aq_show") not in show_ids:
    st.session_state["aq_show"] = None   # série choisie avant mais plus dans la liste
show_id = row1[1].selectbox(
    "Série", show_ids, format_func=lambda i: "Toutes les séries" if i is None else show_titles[i],
    key="aq_show", persist_state="session", disabled=kind == MOVIE)
if show_id is not None and kind != MOVIE:
    items = [item for item in items if item.show_id == show_id]

show_check_banner(to_check)
show_last_action()

if not statuses or not availabilities:
    st.info("Choisissez au moins un statut et une disponibilité.")
    st.stop()
if not items:
    st.info("Rien à regarder avec ces filtres.")
    st.stop()

st.markdown(card_css(), unsafe_allow_html=True)
show_summary(items)

# Pagination : on revient à la première page quand les filtres changent.
filters = (kind, tuple(availabilities), tuple(statuses), next_only, include_specials, show_id,
           sort)
if st.session_state.get("aq_filters") != filters:
    st.session_state["aq_filters"] = filters
    st.session_state["aq_page"] = 0
total_pages = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
page = min(st.session_state.get("aq_page", 0), total_pages - 1)
st.session_state["aq_page"] = page

today = datetime.now(LOCAL_TZ).date()
section = None
for item in items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
    if item.availability != section:   # liste triée : chaque section d'un seul bloc
        section = item.availability
        count = sum(1 for i in items if i.availability == section)
        st.subheader(f"{SECTION_TITLES[section]} :gray[({count})]")
    show_row(item, include_specials, today)
show_pagination(total_pages)
