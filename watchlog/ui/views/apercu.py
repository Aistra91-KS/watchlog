"""Page « Aperçu » : infos détaillées d'un film ou d'une série TMDB (depuis les suggestions).

URL : /apercu?type=serie|film&id=<id TMDB>. Le titre n'a pas besoin d'être dans la bibliothèque.
"""

import html
from datetime import date

import streamlit as st

from watchlog.core import preview, suggestions
from watchlog.core.dates import fmt_date
from watchlog.core.http import ApiError
from watchlog.core.models import COMPLETED, MOVIE, SHOW, TO_WATCH
from watchlog.ui import common

MIN_DATE = date(1900, 1, 1)


def requested() -> tuple[str, int] | None:
    kind = {"serie": SHOW, "film": MOVIE}.get(st.query_params.get("type", ""))
    try:
        tmdb_id = int(st.query_params.get("id", ""))
    except ValueError:
        return None
    return (kind, tmdb_id) if kind else None


def add(item: preview.Preview, status_key: str, date_key: str) -> None:
    try:
        obj_id, title = common.add_from_tmdb(item.kind, item.tmdb_id, st.session_state[status_key],
                                             title=item.title,
                                             watched_on=st.session_state.get(date_key))
    except (ApiError, ValueError) as exc:
        st.session_state["apercu_error"] = f"Ajout impossible : {exc}"
        return
    st.session_state.setdefault("sugg_hidden", set()).add((item.kind, item.tmdb_id))
    st.session_state["apercu_message"] = f"« {title} » a été ajouté à la bibliothèque."


def dismiss(item: preview.Preview) -> None:
    with common.db() as session:
        suggestions.dismiss(session, item.kind, item.tmdb_id, item.title)
        session.commit()
    st.session_state.setdefault("sugg_hidden", set()).add((item.kind, item.tmdb_id))
    st.session_state["apercu_message"] = "Ce titre ne sera plus suggéré."


def open_other(kind: str, tmdb_id: int) -> None:
    """Ouvre un autre titre dans cette même page (un lien vers la page en cours est ignoré)."""
    st.query_params.from_dict(common.preview_params(kind, tmdb_id))


def cast_grid(people: list[preview.Person]) -> None:
    """Casting : photos rondes avec nom et rôle (HTML, s'adapte à la largeur)."""
    cells = []
    for person in people:
        photo = (f"<img src='{html.escape(person.photo_url)}' alt='' style='width:72px;height:72px;"
                 "object-fit:cover;border-radius:50%'>" if person.photo_url else
                 "<div style='width:72px;height:72px;border-radius:50%;margin:auto;"
                 "background:rgba(128,128,128,0.2)'></div>")
        role = (f"<div style='font-size:0.75rem;opacity:0.7'>{html.escape(person.role)}</div>"
                if person.role else "")
        cells.append(f"<div style='width:104px;text-align:center'>{photo}"
                     f"<div style='font-size:0.85rem;margin-top:4px'>{html.escape(person.name)}</div>"
                     f"{role}</div>")
    st.markdown("<div style='display:flex;flex-wrap:wrap;gap:12px'>" + "".join(cells) + "</div>",
                unsafe_allow_html=True)


# --- Page ------------------------------------------------------------------------------

target = requested()
if target is None:
    st.info("Aucun titre sélectionné. Ouvre un titre depuis la page Suggestions.")
    st.page_link(common.PAGE_SUGGESTIONS, label="Aller aux suggestions")
    st.stop()

kind, tmdb_id = target
try:
    item = preview.load(common.tmdb_client(), kind, tmdb_id)
except ApiError as exc:
    st.error(f"Informations indisponibles : {exc}")
    st.stop()
if item is None:
    st.info("Titre introuvable sur TMDB (ou TMDB non connecté).")
    st.stop()

st.page_link(common.PAGE_SUGGESTIONS, label="Retour aux suggestions", icon=":material/arrow_back:")
for key, show in (("apercu_error", st.error), ("apercu_message", st.success)):
    text = st.session_state.pop(key, None)
    if text:
        show(common.md_escape(text))

if item.backdrop_url:
    st.markdown(
        f"<div style='height:220px;border-radius:12px;background-image:linear-gradient("
        f"to bottom, rgba(0,0,0,0) 40%, rgba(0,0,0,0.55)), url({html.escape(item.backdrop_url)});"
        f"background-size:cover;background-position:center 25%;margin-bottom:1rem'></div>",
        unsafe_allow_html=True)

col_poster, col_info = st.columns([1, 3])
with col_poster:
    if item.poster_url:
        st.image(item.poster_url, width="stretch")
    else:
        common.show_poster(None)
with col_info:
    title = common.md_escape(item.title)
    st.title(f"{title} ({item.year})" if item.year else title)
    if item.original_title and item.original_title != item.title:
        st.caption(f"Titre original : {common.md_escape(item.original_title)}")
    if item.tagline:
        st.markdown(f"*{common.md_escape(item.tagline)}*")

    badges = [":blue-badge[Série]" if kind == SHOW else ":orange-badge[Film]"]
    badges += [f":gray-badge[{common.md_escape(g)}]" for g in item.genres]
    if item.vote_count:
        badges.append(f":orange-badge[★ {item.vote_average:.1f}/10 · {item.vote_count} votes]")
    st.markdown(" ".join(badges))

    facts = []
    if item.released:
        label = "Première diffusion" if kind == SHOW else "Sortie"
        facts.append(f"{label} : {fmt_date(item.released)}")
    if kind == SHOW:
        if item.seasons:
            facts.append(f"{item.seasons} saison(s), {item.episodes or '?'} épisodes")
        if item.runtime:
            facts.append(f"~{common.fmt_runtime(item.runtime)} par épisode")
        if item.networks:
            facts.append(", ".join(item.networks))
    elif item.runtime:
        facts.append(f"Durée : {common.fmt_runtime(item.runtime)}")
    if item.status:
        facts.append(item.status)
    if facts:
        st.write(common.md_escape(" · ".join(facts)))
    if item.makers:
        label = "Création" if kind == SHOW else "Réalisation"
        st.write(f"**{label}** : {common.md_escape(', '.join(item.makers))}")
    if item.overview:
        st.write(common.md_escape(item.overview))

    st.markdown("**Où regarder**")
    common.show_availability(kind, tmdb_id)

    with common.db() as session:
        local_id = preview.library_id(session, kind, tmdb_id)
    if local_id is not None:
        st.success("Déjà dans ta bibliothèque.")
        page = common.PAGE_SHOW if kind == SHOW else common.PAGE_MOVIE
        st.page_link(page, label="Ouvrir la fiche", icon=":material/open_in_new:",
                     query_params={"id": local_id})
    else:
        status_key, date_key = f"apercu_status_{kind}_{tmdb_id}", f"apercu_date_{kind}_{tmdb_id}"
        col_status, col_add, col_dismiss = st.columns([2, 1, 1], vertical_alignment="bottom")
        col_status.selectbox("Statut", common.status_options(),
                             index=common.status_options().index(TO_WATCH),
                             format_func=common.status_label, key=status_key)
        col_add.button("Ajouter", type="primary", on_click=add, args=(item, status_key, date_key))
        col_dismiss.button("Pas intéressé", on_click=dismiss, args=(item,))
        if kind == MOVIE and st.session_state.get(status_key) == COMPLETED:
            st.date_input("Vu le (facultatif)", value=None, key=date_key, min_value=MIN_DATE,
                          max_value=date.today(), format="DD/MM/YYYY")

if item.trailer_url:
    st.subheader("Bande-annonce")
    col_video, _ = st.columns([3, 1])
    col_video.video(item.trailer_url)

if item.cast:
    st.subheader("Casting")
    cast_grid(item.cast)

similar = preview.similar(common.tmdb_client(), kind, tmdb_id)
if similar:
    st.subheader("Titres similaires")
    cols = st.columns(len(similar))
    for col, other in zip(cols, similar):
        with col:
            if other["poster_url"]:
                st.image(other["poster_url"], width="stretch")
            label = other["title"] + (f" ({other['year']})" if other["year"] else "")
            st.button(label, key=f"apercu_other_{other['id']}", type="tertiary",
                      on_click=open_other, args=(kind, other["id"]))
