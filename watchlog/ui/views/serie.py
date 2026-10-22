"""Fiche d'une série : informations, statut, note, et liste des épisodes (cases « Vu »).

Performance : une série comme NCIS a environ 500 épisodes. Les épisodes ne sont affichés
(un tableau par saison) que pour les saisons dépliées.
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st
from sqlmodel import select

from watchlog.core import library, providers, stats
from watchlog.core.adding import tvmaze_status_label
from watchlog.core.dates import fmt_date, parse_date
from watchlog.core.models import (EPISODE, MATCH_OK, MATCH_REMAPPED, SHOW, Episode, Show,
                                  Watch)
from watchlog.core.queue import LOCAL_TZ, episode_code, local_date
from watchlog.ui import common

COL_SEEN = "Vu"
COL_UNTIL = "Vu jusqu'ici"
COL_DATE = "Vu le"
COL_RUNTIME = "Durée"
# Colonnes d'information (non modifiables), grisées pour les épisodes à venir
INFO_COLUMNS = ["Code", "Date", "Titre", COL_RUNTIME]
# Épisodes pas encore diffusés : texte grisé, contraste ≥ 4,5:1 dans chaque thème.
UPCOMING_STYLE = {"dark": "color: #9aa5b1", "light": "color: #5f6b7a"}
RATING_OPTIONS = [0, 1, 2, 3, 4, 5]  # 0 = pas de note
DATE_UNKNOWN, DATE_CHOSEN = "Date inconnue", "Choisir une date"
MIN_WATCH_DATE = date(1900, 1, 1)


def rating_label(value: int) -> str:
    return "Pas de note" if not value else f"{value} / 5"


# --- Enregistrement (fonctions appelées quand l'utilisateur change une valeur) ----------

def refresh_tables() -> None:
    """Change la clé des tableaux d'épisodes pour qu'ils repartent des données à jour."""
    st.session_state["serie_tables_version"] = st.session_state.get("serie_tables_version", 0) + 1


def save_status(show_id: int, key: str) -> None:
    with common.db() as session:
        library.set_status(session, SHOW, show_id, st.session_state[key])
        session.commit()


def save_favorite(show_id: int, key: str) -> None:
    with common.db() as session:
        library.set_favorite(session, SHOW, show_id, st.session_state[key])
        session.commit()


def save_rating(show_id: int, key: str) -> None:
    with common.db() as session:
        old = library.get_rating(session, SHOW, show_id)
        # On ne touche qu'à la note : commentaire et émotions éventuels sont gardés.
        library.set_rating(session, SHOW, show_id, st.session_state[key] or None,
                           note=old.note if old else None,
                           emotions=old.emotions if old else None)
        session.commit()


def add_to_library(show_id: int, key: str) -> None:
    save_status(show_id, key)
    st.session_state["serie_message"] = "Série ajoutée à la bibliothèque."


def remove_show(show_id: int) -> None:
    with common.db() as session:
        library.remove_from_library(session, SHOW, show_id)
        session.commit()
    st.session_state.pop(f"confirm_remove_{show_id}", None)
    st.session_state["serie_message"] = ("Série retirée de la bibliothèque. "
                                         "L'historique de visionnage est conservé.")


def mark_season(show_id: int, season: int, include_specials: bool) -> None:
    with common.db() as session:
        count = library.mark_season_watched(session, show_id, season, include_specials)
        session.commit()
    refresh_tables()
    st.session_state["serie_message"] = f"Saison {season} : {count} épisode(s) marqué(s) vu(s)."


def apply_bulk_date(show_id: int, prefix: str) -> None:
    """Change la date de visionnage de tous les épisodes vus des saisons choisies."""
    seasons = st.session_state[f"{prefix}_seasons"]
    if not seasons:
        st.session_state["serie_warning"] = "Choisis au moins une saison."
        return
    day = None
    if st.session_state[f"{prefix}_mode"] == DATE_CHOSEN:
        day = st.session_state.get(f"{prefix}_day")
        if day is None:
            st.session_state["serie_warning"] = "Choisis une date (ou « Date inconnue »)."
            return
    with common.db() as session:
        count = library.set_show_watch_dates(session, show_id, day, seasons)
        session.commit()
    refresh_tables()
    shown = fmt_date(day) if day else "date inconnue"
    st.session_state["serie_message"] = f"Date de visionnage de {count} épisode(s) : {shown}."


def apply_episode_changes(editor_key: str, episode_ids: list[int], include_specials: bool) -> None:
    """Applique les cases cochées / décochées dans le tableau d'une saison."""
    changes = st.session_state[editor_key].get("edited_rows", {})
    with common.db() as session:
        for row, values in changes.items():
            episode_id = episode_ids[int(row)]
            if values.get(COL_UNTIL):
                library.mark_watched_until(session, episode_id, include_specials)
            elif COL_SEEN in values:
                if values[COL_SEEN]:
                    library.mark_watched(session, EPISODE, episode_id)
                else:
                    library.unmark_watched(session, EPISODE, episode_id)
                    continue  # décoché : une date saisie sur la même ligne est ignorée
            if COL_DATE in values:
                # Date changée (vide : date inconnue) ; marque l'épisode vu au besoin.
                library.set_watch_date(session, EPISODE, episode_id, parse_date(values[COL_DATE]))
        session.commit()
    refresh_tables()


def toggle_season(open_key: str, season: int, expander_key: str) -> None:
    """Retient quelles saisons sont dépliées (pour qu'elles le restent après une mise à jour)."""
    opened = st.session_state[open_key]
    if st.session_state.get(expander_key):
        opened.add(season)
    else:
        opened.discard(season)


# --- Préparation des données ------------------------------------------------------------

def episode_sort_key(ep: Episode):
    # Épisodes réguliers dans l'ordre, puis les spéciaux de la saison (par date).
    return (ep.number is None, ep.number or 0, ep.airdate or datetime.max.date())


def current_season(seasons: dict[int, list[Episode]], watched: dict[int, date | None], today) -> int | None:
    """Première saison qui a un épisode diffusé (ou sans date) pas encore vu."""
    for season, episodes in seasons.items():
        for ep in episodes:
            day = local_date(ep.airstamp, ep.airdate)
            if ep.id not in watched and (day is None or day <= today):
                return season
    return None


def season_table(episodes: list[Episode], watched: dict[int, date | None],
                 today) -> tuple[pd.DataFrame, list]:
    """Tableau d'une saison + liste des épisodes « à venir » (lignes grisées)."""
    rows, upcoming = [], []
    for i, ep in enumerate(episodes):
        day = local_date(ep.airstamp, ep.airdate)
        if day is not None and day > today:
            upcoming.append(i)
        rows.append({"Code": episode_code(ep.season, ep.number), "Date": fmt_date(day),
                     "Titre": ep.title or "",
                     COL_RUNTIME: f"{ep.runtime} min" if ep.runtime else "",
                     COL_SEEN: ep.id in watched,
                     COL_DATE: watched.get(ep.id), COL_UNTIL: False})
    df = pd.DataFrame(rows, columns=[*INFO_COLUMNS, COL_SEEN, COL_DATE, COL_UNTIL])
    df[COL_DATE] = pd.to_datetime(df[COL_DATE])   # type date : case vide (et non « None ») si pas vu
    return df, upcoming


def grey_upcoming(df: pd.DataFrame, upcoming: list[int]):
    """Grise le texte des épisodes à venir (colonnes non modifiables seulement)."""
    style = UPCOMING_STYLE["dark" if common.is_dark_theme() else "light"]

    def style_row(row):
        color = style if row.name in upcoming else ""
        return [color if col in INFO_COLUMNS else "" for col in row.index]
    return df.style.apply(style_row, axis=1)


# --- Page ------------------------------------------------------------------------------

show_id = common.selected_id("show_id")
with common.db() as session:
    show = session.get(Show, show_id) if show_id is not None else None
    if show is not None:
        item = library.get_library_item(session, SHOW, show.id)
        rating = library.get_rating(session, SHOW, show.id)
        episodes = session.exec(select(Episode).where(Episode.show_id == show.id)).all()
        # épisode vu -> date du premier visionnage (heure française), None si inconnue
        watched = {ref_id: local_date(when, None) for ref_id, when in session.exec(
            select(Watch.ref_id, Watch.watched_at).join(Episode, Episode.id == Watch.ref_id)
            .where(Watch.kind == EPISODE, Episode.show_id == show.id)).all()}
        durations = stats.show_time(session, show.id)

if show is None:
    st.info("Aucune série sélectionnée. Choisis une série dans la bibliothèque.")
    st.page_link(common.PAGE_LIBRARY, label="Aller à la bibliothèque")
    st.stop()

today = datetime.now(LOCAL_TZ).date()
message = st.session_state.pop("serie_message", None)
if message:
    st.success(message)
warning = st.session_state.pop("serie_warning", None)
if warning:
    st.warning(warning)

# En-tête : affiche + informations
col_poster, col_info = st.columns([1, 3])
with col_poster:
    if common.poster_file(show.poster_path) or not show.poster_url:
        common.show_poster(show.poster_path)
    else:
        st.image(show.poster_url, width="stretch")
with col_info:
    st.title(common.md_escape(show.title))
    if show.original_title and show.original_title != show.title:
        st.caption(f"Titre original : {common.md_escape(show.original_title)}")
    details = []
    if show.status:
        details.append(tvmaze_status_label(show.status))
    if show.network:
        details.append(common.md_escape(show.network))
    if show.premiered:
        details.append(f"Première diffusion : {fmt_date(show.premiered)}")
    if show.is_manual:
        details.append("Saisie manuelle")
    if details:
        st.write(" · ".join(details))
    if show.summary:
        st.write(common.md_escape(show.summary))

    if not show.is_manual:
        tmdb_id = show.tmdb_id
        if tmdb_id is None:  # série ajoutée depuis TVMaze : id TMDB cherché une fois via TVDB
            with common.db() as session:
                tmdb_id = providers.show_tmdb_id(session, common.tmdb_client(),
                                                 session.get(Show, show.id))
                session.commit()
        if tmdb_id is not None:
            st.markdown("**Où regarder**")
            common.show_availability(SHOW, tmdb_id)

    regular = [ep for ep in episodes if not ep.is_special]
    if regular:
        seen = sum(1 for ep in regular if ep.id in watched)
        st.caption(f"{seen}/{len(regular)} épisodes vus (hors spéciaux)")
    if episodes:
        col_spent, col_left = st.columns(2)
        col_spent.metric("Temps passé", stats.format_duration(durations["spent_minutes"]),
                         border=True)
        left = durations["remaining_episodes"]
        col_left.metric("Temps restant", stats.format_duration(durations["remaining_minutes"]),
                        f"{left} épisode{'s' if left > 1 else ''} à voir", delta_color="off",
                        delta_arrow="off", border=True)
        notes = []
        if durations["upcoming_episodes"]:
            notes.append(f"{durations['upcoming_episodes']} épisode(s) pas encore diffusé(s) "
                         "non compté(s)")
        if durations["estimated"]:
            notes.append(f"durée inconnue pour {durations['estimated']} épisode(s) : "
                         "estimée à la moyenne de la série")
        if notes:
            st.caption(" · ".join(notes).capitalize())

# Correspondance TVMaze à vérifier
if show.match_status not in (MATCH_OK, MATCH_REMAPPED):
    st.info("La correspondance de cette série avec TVMaze est à vérifier : certains épisodes "
            "vus peuvent ne pas apparaître ci-dessous (ils sont en attente)."
            + (f"\n\nDétail : {common.md_escape(show.match_note)}" if show.match_note else ""))
    st.page_link(common.PAGE_SETTINGS, label="Résoudre dans Paramètres")

# Bibliothèque : statut, favori, note
status_key = f"serie_status_{show.id}"
if item is None:
    st.info("Cette série n'est pas dans ta bibliothèque.")
    col_status, col_button = st.columns([2, 1], vertical_alignment="bottom")
    with col_status:
        st.selectbox("Statut", common.status_options(), format_func=common.status_label,
                     key=status_key)
    with col_button:
        st.button("Ajouter à la bibliothèque", on_click=add_to_library,
                  args=(show.id, status_key))
else:
    # Les widgets reprennent toujours la valeur enregistrée en base.
    favorite_key, rating_key = f"serie_favorite_{show.id}", f"serie_rating_{show.id}"
    st.session_state[status_key] = item.status
    st.session_state[favorite_key] = item.favorite
    st.session_state[rating_key] = (rating.rating or 0) if rating else 0
    col_status, col_rating, col_favorite = st.columns([2, 2, 1], vertical_alignment="bottom")
    with col_status:
        st.selectbox("Statut", common.status_options(), format_func=common.status_label,
                     key=status_key, on_change=save_status, args=(show.id, status_key))
    with col_rating:
        st.selectbox("Note de la série", RATING_OPTIONS, format_func=rating_label,
                     key=rating_key, on_change=save_rating, args=(show.id, rating_key))
    with col_favorite:
        st.checkbox("Favori", key=favorite_key, on_change=save_favorite,
                    args=(show.id, favorite_key))

# Épisodes, saison par saison
st.subheader("Épisodes")
show_specials = st.checkbox("Afficher les épisodes spéciaux", key=f"serie_specials_{show.id}")
shown = [ep for ep in episodes if show_specials or not ep.is_special]

# Date de visionnage en bloc (série vue il y a longtemps, cochée d'un coup aujourd'hui)
watched_seasons = sorted({ep.season for ep in episodes if ep.id in watched})
if watched_seasons:
    with st.expander("Changer la date de visionnage de plusieurs épisodes"):
        prefix = f"serie_bulk_{show.id}"
        if f"{prefix}_seasons" in st.session_state:  # saison qui n'a plus d'épisode vu : retirée
            st.session_state[f"{prefix}_seasons"] = [
                s for s in st.session_state[f"{prefix}_seasons"] if s in watched_seasons]
        st.caption("S'applique aux épisodes déjà vus des saisons choisies (spéciaux compris). "
                   "« Date inconnue » : ils restent vus, mais sans date, et ne comptent dans "
                   "aucun mois des statistiques.")
        st.multiselect("Saisons", watched_seasons, default=watched_seasons,
                       format_func=lambda season: f"Saison {season}", key=f"{prefix}_seasons")
        mode = st.radio("Date", [DATE_UNKNOWN, DATE_CHOSEN], horizontal=True,
                        key=f"{prefix}_mode")
        if mode == DATE_CHOSEN:
            st.date_input("Vu le", value=None, format="DD/MM/YYYY", min_value=MIN_WATCH_DATE,
                          max_value=today, key=f"{prefix}_day")
        st.button("Appliquer", key=f"{prefix}_apply", on_click=apply_bulk_date,
                  args=(show.id, prefix))
seasons: dict[int, list[Episode]] = {}
for ep in sorted(shown, key=lambda e: (e.season, episode_sort_key(e))):
    seasons.setdefault(ep.season, []).append(ep)

if not seasons:
    st.write("Aucun épisode connu pour cette série.")

open_key = f"serie_open_seasons_{show.id}"
if open_key not in st.session_state:
    current = current_season(seasons, watched, today)
    st.session_state[open_key] = {current} if current is not None else set()
version = st.session_state.get("serie_tables_version", 0)

for season, season_episodes in seasons.items():
    seen = sum(1 for ep in season_episodes if ep.id in watched)
    label = f"Saison {season} · {seen}/{len(season_episodes)} vus"
    expander_key = f"serie_season_{show.id}_{season}"
    # `expanded` vient de notre mémoire des saisons ouvertes : ainsi la saison reste
    # dépliée même quand son libellé change (nombre d'épisodes vus).
    expander = st.expander(label, expanded=season in st.session_state[open_key],
                           key=expander_key, on_change=toggle_season,
                           args=(open_key, season, expander_key))
    if not expander.open:
        continue  # saison repliée : on ne construit pas le tableau
    with expander:
        df, upcoming = season_table(season_episodes, watched, today)
        editor_key = f"serie_table_{show.id}_{season}_{version}"
        st.data_editor(
            grey_upcoming(df, upcoming), key=editor_key, hide_index=True,
            disabled=INFO_COLUMNS,
            height=min(36 * (len(df) + 1) + 3, 900),
            column_config={
                "Code": st.column_config.TextColumn(width="small"),
                "Date": st.column_config.TextColumn(width="small"),
                COL_RUNTIME: st.column_config.TextColumn(
                    width="small", help="Durée de l'épisode (vide : inconnue)"),
                COL_SEEN: st.column_config.CheckboxColumn(width="small"),
                COL_DATE: st.column_config.DateColumn(
                    width="small", format="DD/MM/YYYY", min_value=MIN_WATCH_DATE,
                    max_value=today,
                    help="Date de visionnage, modifiable (vide : date inconnue)"),
                COL_UNTIL: st.column_config.CheckboxColumn(
                    width="small",
                    help="Coche pour marquer vus tous les épisodes jusqu'à celui-ci inclus"),
            },
            on_change=apply_episode_changes,
            args=(editor_key, [ep.id for ep in season_episodes], show_specials))
        if upcoming:
            st.caption("Les épisodes en gris ne sont pas encore diffusés.")
        if seen < len(season_episodes):
            st.button("Marquer la saison vue", key=f"serie_mark_{show.id}_{season}",
                      on_click=mark_season, args=(show.id, season, show_specials))

# Retrait de la bibliothèque (avec confirmation)
if item is not None:
    st.divider()
    confirm_key = f"confirm_remove_{show.id}"
    if st.session_state.get(confirm_key):
        st.warning(f"Retirer « {common.md_escape(show.title)} » de la bibliothèque ? "
                   "L'historique de visionnage et les notes sont conservés.")
        col_yes, col_no = st.columns(2)
        col_yes.button("Confirmer le retrait", on_click=remove_show, args=(show.id,))
        col_no.button("Annuler", on_click=st.session_state.pop, args=(confirm_key, None))
    elif st.button("Retirer de la bibliothèque"):
        st.session_state[confirm_key] = True
        st.rerun()
