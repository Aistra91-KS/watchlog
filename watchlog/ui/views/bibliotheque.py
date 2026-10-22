"""Page « Bibliothèque » : grille d'affiches des séries et films, avec leur progression."""

import streamlit as st

from watchlog.core import progress
from watchlog.core.models import MOVIE, SHOW
from watchlog.ui import common

PER_ROW = 6          # affiches par ligne
CHUNK = 48           # nombre d'éléments affichés en plus à chaque « Afficher plus »
TYPE_LABELS = {"all": "Tout", SHOW: "Séries", MOVIE: "Films"}
SORT_LABELS = {progress.SORT_TITLE: "Titre", progress.SORT_ADDED: "Derniers ajouts",
               progress.SORT_RECENT: "Vu récemment",
               progress.SORT_PAUSED: "En pause depuis le plus longtemps"}
# Tris où la date du dernier visionnage est affichée sur chaque carte
DATED_SORTS = (progress.SORT_RECENT, progress.SORT_PAUSED)


md = common.md_escape  # protège les caractères Markdown des titres


def show_card(entry: progress.LibraryEntry, dated: bool = False) -> None:
    common.show_poster(entry.poster_path)
    st.markdown(f"**{md(entry.title)}**")
    details = ["Série" if entry.kind == SHOW else "Film"]
    if entry.year:
        details.append(str(entry.year))
    details.append(common.status_label(entry.status))
    if entry.favorite:
        details.append("Favori")
    st.caption(" · ".join(details))
    st.caption(progress.progress_label(entry))
    if dated and (seen := progress.last_watched_label(entry)):
        st.caption(seen)
    if st.button("Ouvrir", key=f"lib_open_{entry.kind}_{entry.ref_id}", width="stretch"):
        if entry.kind == SHOW:
            common.open_show(entry.ref_id)
        else:
            common.open_movie(entry.ref_id)


st.title("Bibliothèque")

# Filtres (mémorisés pour la session, même en changeant de page : persist_state)
cols = st.columns([1, 2, 2, 1.6])
kind = cols[0].selectbox("Type", list(TYPE_LABELS), format_func=TYPE_LABELS.get,
                         key="lib_kind", persist_state="session")
statuses = cols[1].multiselect("Statut", common.status_options(), format_func=common.status_label,
                               placeholder="Tous les statuts", key="lib_statuses",
                               persist_state="session")
search = cols[2].text_input("Recherche", placeholder="Titre ou titre original",
                            key="lib_search", persist_state="session")
sort = cols[3].selectbox("Tri", list(SORT_LABELS), format_func=SORT_LABELS.get,
                         key="lib_sort", persist_state="session")
favorite_only = st.checkbox("Favoris seulement", key="lib_favorites", persist_state="session")

with common.db() as session:
    entries = progress.library_overview(
        session, kind=None if kind == "all" else kind, status=statuses or None, search=search,
        favorite_only=favorite_only, sort=sort)

if sort == progress.SORT_PAUSED:
    st.caption("Séries « En cours » seulement, celle délaissée depuis le plus longtemps "
               "en premier : de quoi choisir une série à reprendre.")
if not entries:
    st.info("Aucun élément ne correspond à ces filtres.")
    st.stop()

# Chargement par tranches : on revient à la première tranche quand les filtres changent.
filters = (kind, tuple(statuses), search, sort, favorite_only)
if st.session_state.get("lib_filters") != filters:
    st.session_state["lib_filters"] = filters
    st.session_state["lib_shown"] = CHUNK
shown = st.session_state.get("lib_shown", CHUNK)

plural = "s" if len(entries) > 1 else ""
st.caption(f"{len(entries)} élément{plural}"
           + (f" · {shown} affichés" if shown < len(entries) else ""))

visible = entries[:shown]
for start in range(0, len(visible), PER_ROW):
    for col, entry in zip(st.columns(PER_ROW), visible[start:start + PER_ROW]):
        with col:
            show_card(entry, dated=sort in DATED_SORTS)

if shown < len(entries):
    if st.button(f"Afficher {min(CHUNK, len(entries) - shown)} de plus", key="lib_more"):
        st.session_state["lib_shown"] = shown + CHUNK
        st.rerun()
