"""Fiche d'une série : informations, statut, épisodes (cases « Vu », notes) et avis.

Performance : une série comme NCIS a environ 500 épisodes. Les épisodes ne sont affichés
(un tableau par saison) que pour les saisons dépliées.
Cocher une case ne recrée pas le tableau (position de défilement gardée) : voir
`edits_match` et les deux sortes d'expanders de la boucle des saisons.
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st
from sqlmodel import select

from watchlog.core import library, providers, queue, stats
from watchlog.core.adding import tvmaze_status_label
from watchlog.core.dates import fmt_date, parse_date
from watchlog.core.models import (EPISODE, MATCH_OK, MATCH_REMAPPED, SHOW, Episode, Rating,
                                  Show, Watch)
from watchlog.core.queue import LOCAL_TZ, UPCOMING, episode_code, local_date
from watchlog.ui import avis, common

COL_SEEN = "Vu"
COL_UNTIL = "Vu jusqu'ici"
COL_DATE = "Vu le"
COL_RUNTIME = "Durée"
COL_PLAYS = "Visionnages"   # affichée seulement pour une saison dont un épisode a été revu
# Colonnes d'information (non modifiables), grisées pour les épisodes à venir
INFO_COLUMNS = ["Code", "Date", "Titre", COL_RUNTIME]
# Épisodes pas encore diffusés : texte grisé, contraste ≥ 4,5:1 dans chaque thème.
UPCOMING_STYLE = {"dark": "color: #9aa5b1", "light": "color: #5f6b7a"}
COL_RATING = "Note"
EPISODE_RATINGS = [1, 2, 3, 4, 5]   # colonne « Note » : vide = pas de note
DATE_UNKNOWN, DATE_CHOSEN = "Date inconnue", "Choisir une date"
MIN_WATCH_DATE = date(1900, 1, 1)


# --- Enregistrement (fonctions appelées quand l'utilisateur change une valeur) ----------

def save_status(show_id: int, key: str) -> None:
    with common.db() as session:
        library.set_status(session, SHOW, show_id, st.session_state[key])
        session.commit()


def save_favorite(show_id: int, key: str) -> None:
    with common.db() as session:
        library.set_favorite(session, SHOW, show_id, st.session_state[key])
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
    shown = fmt_date(day) if day else "date inconnue"
    st.session_state["serie_message"] = f"Date de visionnage de {count} épisode(s) : {shown}."


def apply_rewatch(show_id: int, prefix: str, add: bool, include_specials: bool) -> None:
    """Ajoute (ou retire) un revisionnage aux épisodes vus des saisons choisies."""
    seasons = st.session_state[f"{prefix}_seasons"]
    if not seasons:
        st.session_state["serie_warning"] = "Choisis au moins une saison."
        return
    with common.db() as session:
        if add:
            count = library.add_season_rewatch(session, show_id, seasons,
                                               st.session_state.get(f"{prefix}_day"),
                                               include_specials)
        else:
            count = library.remove_season_rewatch(session, show_id, seasons, include_specials)
        session.commit()
    action = "ajouté à" if add else "retiré de"
    st.session_state["serie_message"] = f"Revisionnage {action} {count} épisode(s)."


def apply_episode_changes(editor_key: str, episode_ids: list[int], include_specials: bool) -> None:
    """Applique les cases cochées / décochées, dates et notes du tableau d'une saison.

    Le tableau garde sa clé tant que ses modifications correspondent à la base (voir
    `edits_match`) : `edited_rows` cumule donc toutes les saisies depuis sa création.
    Seules les valeurs différentes de la base sont appliquées (rejouer une saisie déjà
    enregistrée ne change rien).
    """
    changes = st.session_state[editor_key].get("edited_rows", {})
    with common.db() as session:
        for row, values in changes.items():
            episode_id = episode_ids[int(row)]
            if COL_RATING in values:   # note de l'épisode : indépendante du visionnage
                value = cell_rating(values[COL_RATING])
                current = library.get_rating(session, EPISODE, episode_id)
                if value != (current.rating if current else None):
                    library.set_rating_value(session, EPISODE, episode_id, value)
            watch = library.get_watch(session, EPISODE, episode_id)
            if values.get(COL_UNTIL):
                library.mark_watched_until(session, episode_id, include_specials)
            elif COL_SEEN in values:
                if values[COL_SEEN] and watch is None:
                    library.mark_watched(session, EPISODE, episode_id)
                elif not values[COL_SEEN]:
                    if watch is not None:
                        library.unmark_watched(session, EPISODE, episode_id)
                    continue  # décoché : une date saisie sur la même ligne est ignorée
            if COL_DATE in values:
                # Date changée (vide : date inconnue) ; marque l'épisode vu au besoin.
                day = cell_date(values[COL_DATE])
                if watch is None or day != local_date(watch.watched_at, None):
                    library.set_watch_date(session, EPISODE, episode_id, day)
            session.flush()
        session.commit()


def mark_next(show_id: int, episode_id: int, label: str) -> None:
    """Bouton « Vu » du bloc Prochain épisode (avec « Annuler » ensuite)."""
    with common.db() as session:
        library.mark_watched(session, EPISODE, episode_id)
        session.commit()
    st.session_state[f"serie_last_seen_{show_id}"] = {"text": f"{label} marqué vu.",
                                                      "episode_id": episode_id}


def undo_next(show_id: int) -> None:
    action = st.session_state.pop(f"serie_last_seen_{show_id}", None)
    if action is None:
        return
    with common.db() as session:
        library.unmark_watched(session, EPISODE, action["episode_id"])
        session.commit()


def toggle_season(open_key: str, season: int, expander_key: str) -> None:
    """Retient qu'une saison a été dépliée (elle le reste après une mise à jour)."""
    if st.session_state.get(expander_key):
        st.session_state[open_key].add(season)


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


def season_table(episodes: list[Episode], watched: dict[int, date | None], today,
                 plays: dict[int, int] | None = None,
                 ratings: dict[int, int] | None = None) -> tuple[pd.DataFrame, list]:
    """Tableau d'une saison + liste des épisodes « à venir » (lignes grisées).

    Colonne « Visionnages » seulement si un épisode de la saison a été revu.
    ratings : épisode -> note (1 à 5).
    """
    plays, ratings = plays or {}, ratings or {}
    with_plays = any(plays.get(ep.id, 1) > 1 for ep in episodes)
    rows, upcoming = [], []
    for i, ep in enumerate(episodes):
        day = local_date(ep.airstamp, ep.airdate)
        if day is not None and day > today:
            upcoming.append(i)
        row = {"Code": episode_code(ep.season, ep.number), "Date": fmt_date(day),
               "Titre": ep.display_title or "",
               COL_RUNTIME: f"{ep.runtime} min" if ep.runtime else "",
               COL_SEEN: ep.id in watched,
               COL_DATE: watched.get(ep.id), COL_RATING: ratings.get(ep.id),
               COL_UNTIL: False}
        if with_plays:
            row[COL_PLAYS] = plays.get(ep.id) if ep.id in watched else None
        rows.append(row)
    columns = [*INFO_COLUMNS, COL_SEEN, COL_DATE, COL_RATING]
    columns += [COL_PLAYS] if with_plays else []
    df = pd.DataFrame(rows, columns=[*columns, COL_UNTIL])
    df[COL_DATE] = pd.to_datetime(df[COL_DATE])   # type date : case vide (et non « None ») si pas vu
    df[COL_RATING] = df[COL_RATING].astype("Int64")   # entier, case vide si pas de note
    if with_plays:
        df[COL_PLAYS] = df[COL_PLAYS].astype("Int64")   # entier, case vide si pas vu
    return df, upcoming


def cell_date(value) -> date | None:
    """Valeur d'une case « Vu le » (saisie : texte ISO ; tableau : Timestamp / NaT) -> date."""
    if value is None or value is pd.NaT or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        return parse_date(value)
    if isinstance(value, datetime):   # pd.Timestamp compris
        return value.date()
    return value if isinstance(value, date) else None


def cell_rating(value) -> int | None:
    """Valeur d'une case « Note » (1 à 5, ou vide) -> int ou None."""
    if value is None or pd.isna(value):
        return None
    return int(value)


def edits_match(df: pd.DataFrame, edited_rows: dict) -> bool:
    """Vrai si chaque saisie en cours du tableau est déjà la valeur en base (dans `df`).

    Le tableau peut alors garder sa clé : il reste affiché tel quel (défilement compris) et
    reçoit seulement les nouvelles valeurs. Sinon (« Vu jusqu'ici » coché, épisode décoché
    ailleurs, date changée en bloc...), ses saisies contrediraient la base : on le recrée.
    """
    for row, values in edited_rows.items():
        row = int(row)
        if row >= len(df):
            return False
        for column, value in values.items():
            if column == COL_DATE:
                same = cell_date(value) == cell_date(df.at[row, COL_DATE])
            elif column == COL_RATING:
                same = cell_rating(value) == cell_rating(df.at[row, COL_RATING])
            elif column in (COL_SEEN, COL_UNTIL):
                same = bool(value) == bool(df.at[row, column])
            else:
                same = False   # colonne non modifiable : ne devrait pas arriver
            if not same:
                return False
    return True


def grey_upcoming(df: pd.DataFrame, upcoming: list[int]):
    """Grise le texte des épisodes à venir (colonnes non modifiables seulement)."""
    style = UPCOMING_STYLE["dark" if common.is_dark_theme() else "light"]

    def style_row(row):
        color = style if row.name in upcoming else ""
        return [color if col in INFO_COLUMNS else "" for col in row.index]
    return df.style.apply(style_row, axis=1)


def show_next_episode(show_id: int, today: date) -> None:
    """Bloc « Prochain épisode » : le marquer vu sans chercher dans les saisons."""
    last_key = f"serie_last_seen_{show_id}"
    with common.db() as session:
        nxt = queue.next_episode(session, show_id, today=today)
    last = st.session_state.get(last_key)
    if nxt is None and not last:
        return  # tout est vu : rien à afficher
    with st.container(border=True):
        if nxt is not None and nxt.availability == UPCOMING:
            st.markdown(f"**Prochain épisode** : {nxt.code} le {fmt_date(nxt.release_date)}")
        elif nxt is not None:
            text_col, button_col = st.columns([6, 1], vertical_alignment="center")
            details = [f"**{nxt.code}**", common.md_escape(nxt.episode_title),
                       fmt_date(nxt.release_date), common.fmt_runtime(nxt.runtime)]
            text_col.markdown("Prochain épisode : " + " · ".join(d for d in details if d))
            button_col.button("Vu", type="primary", key=f"serie_next_seen_{show_id}",
                              on_click=mark_next, args=(show_id, nxt.ref_id, nxt.code))
        if last:
            text_col, undo_col = st.columns([6, 1], vertical_alignment="center")
            text_col.caption(last["text"])
            undo_col.button("Annuler", key=f"serie_next_undo_{show_id}", on_click=undo_next,
                            args=(show_id,))


# --- Page ------------------------------------------------------------------------------

show_id = common.selected_id("show_id")
with common.db() as session:
    show = session.get(Show, show_id) if show_id is not None else None
    if show is not None:
        item = library.get_library_item(session, SHOW, show.id)
        episodes = session.exec(select(Episode).where(Episode.show_id == show.id)).all()
        episode_ratings = dict(session.exec(
            select(Rating.ref_id, Rating.rating).join(Episode, Episode.id == Rating.ref_id)
            .where(Rating.kind == EPISODE, Episode.show_id == show.id,
                   Rating.rating.is_not(None))).all())
        # épisode vu -> date du premier visionnage (heure française), None si inconnue
        watched, plays = {}, {}   # plays : nombre de visionnages (revisionnages compris)
        for ref_id, when, count in session.exec(
                select(Watch.ref_id, Watch.watched_at, Watch.plays)
                .join(Episode, Episode.id == Watch.ref_id)
                .where(Watch.kind == EPISODE, Episode.show_id == show.id)).all():
            watched[ref_id] = local_date(when, None)
            plays[ref_id] = count or 1
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
    if show.display_summary:
        st.write(common.md_escape(show.display_summary))

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

show_next_episode(show.id, today)

# Correspondance TVMaze à vérifier
if show.match_status not in (MATCH_OK, MATCH_REMAPPED):
    st.info("La correspondance de cette série avec TVMaze est à vérifier : certains épisodes "
            "vus peuvent ne pas apparaître ci-dessous (ils sont en attente)."
            + (f"\n\nDétail : {common.md_escape(show.match_note)}" if show.match_note else ""))
    st.page_link(common.PAGE_SETTINGS, label="Résoudre dans Paramètres")

# Bibliothèque : statut, favori (la note est dans « Mon avis », en bas de la fiche)
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
    favorite_key = f"serie_favorite_{show.id}"
    st.session_state[status_key] = item.status
    st.session_state[favorite_key] = item.favorite
    col_status, col_favorite = st.columns([2, 1], vertical_alignment="bottom")
    with col_status:
        st.selectbox("Statut", common.status_options(), format_func=common.status_label,
                     key=status_key, on_change=save_status, args=(show.id, status_key))
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
        # Valeur par défaut seulement au premier affichage (sinon Streamlit avertit : valeur
        # donnée à la fois par `default` et par st.session_state).
        st.multiselect("Saisons", watched_seasons,
                       default=None if f"{prefix}_seasons" in st.session_state else watched_seasons,
                       format_func=lambda season: f"Saison {season}", key=f"{prefix}_seasons")
        mode = st.radio("Date", [DATE_UNKNOWN, DATE_CHOSEN], horizontal=True,
                        key=f"{prefix}_mode")
        if mode == DATE_CHOSEN:
            st.date_input("Vu le", value=None, format="DD/MM/YYYY", min_value=MIN_WATCH_DATE,
                          max_value=today, key=f"{prefix}_day")
        st.button("Appliquer", key=f"{prefix}_apply", on_click=apply_bulk_date,
                  args=(show.id, prefix))

    # Revisionnage : un visionnage de plus pour les épisodes vus des saisons choisies
    with st.expander("Revisionnage"):
        prefix = f"serie_rewatch_{show.id}"
        if f"{prefix}_seasons" in st.session_state:  # saison qui n'a plus d'épisode vu : retirée
            st.session_state[f"{prefix}_seasons"] = [
                s for s in st.session_state[f"{prefix}_seasons"] if s in watched_seasons]
        rewatched = sum(1 for count in plays.values() if count > 1)
        st.caption("Ajoute un visionnage aux épisodes déjà vus des saisons choisies (les "
                   "statistiques comptent chaque visionnage). "
                   + (f"{rewatched} épisode(s) revu(s) pour l'instant." if rewatched
                      else "Aucun épisode revu pour l'instant."))
        st.multiselect("Saisons", watched_seasons, format_func=lambda season: f"Saison {season}",
                       key=f"{prefix}_seasons")
        st.date_input("Revu le", value=today, format="DD/MM/YYYY", min_value=MIN_WATCH_DATE,
                      max_value=today, key=f"{prefix}_day")
        col_add, col_remove = st.columns(2)
        col_add.button("Ajouter un revisionnage", key=f"{prefix}_add", on_click=apply_rewatch,
                       args=(show.id, prefix, True, show_specials))
        col_remove.button("Retirer un revisionnage", key=f"{prefix}_remove",
                          on_click=apply_rewatch, args=(show.id, prefix, False, show_specials),
                          disabled=not rewatched)
seasons: dict[int, list[Episode]] = {}
for ep in sorted(shown, key=lambda e: (e.season, episode_sort_key(e))):
    seasons.setdefault(ep.season, []).append(ep)

if not seasons:
    st.write("Aucun épisode connu pour cette série.")

open_key = f"serie_open_seasons_{show.id}"
if open_key not in st.session_state:
    current = current_season(seasons, watched, today)
    st.session_state[open_key] = {current} if current is not None else set()
for season, season_episodes in seasons.items():
    seen = sum(1 for ep in season_episodes if ep.id in watched)
    label = f"Saison {season} · {seen}/{len(season_episodes)} vus"
    if season in st.session_state[open_key]:
        # Saison ouverte : expander sans état, dont l'identité ne dépend que de la clé.
        # Le libellé peut changer (une case cochée) sans que le navigateur recrée le bloc
        # et le tableau qu'il contient (sinon : défilement perdu). Revers : on ne sait plus
        # quand il est replié, son tableau reste construit (saisons ouvertes une fois).
        expander = st.expander(label, expanded=True, key=f"serie_season_open_{show.id}_{season}")
    else:
        # Saison jamais ouverte : expander avec état, pour savoir quand on l'ouvre (le tableau
        # n'est construit qu'à ce moment : une série comme NCIS a ~500 épisodes).
        expander_key = f"serie_season_{show.id}_{season}"
        expander = st.expander(label, expanded=False, key=expander_key, on_change=toggle_season,
                               args=(open_key, season, expander_key))
        if not expander.open:
            continue
    with expander:
        df, upcoming = season_table(season_episodes, watched, today, plays, episode_ratings)
        # Le tableau garde la même clé tant que ses saisies correspondent à la base : une case
        # cochée ne le recrée pas. Sinon, nouvelle clé (tableau repris des données à jour).
        version_key = f"serie_table_version_{show.id}_{season}"
        version = st.session_state.get(version_key, 0)
        editor_key = f"serie_table_{show.id}_{season}_{version}"
        editor_state = st.session_state.get(editor_key)
        if editor_state and not edits_match(df, editor_state.get("edited_rows", {})):
            version += 1
            st.session_state[version_key] = version
            editor_key = f"serie_table_{show.id}_{season}_{version}"
        st.data_editor(
            grey_upcoming(df, upcoming), key=editor_key, hide_index=True,
            disabled=[*INFO_COLUMNS, COL_PLAYS] if COL_PLAYS in df else INFO_COLUMNS,
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
                COL_RATING: st.column_config.SelectboxColumn(
                    width="small", options=EPISODE_RATINGS,
                    help="Ta note de l'épisode, de 1 à 5 (vide : pas de note)"),
                COL_PLAYS: st.column_config.NumberColumn(
                    width="small", help="Nombre de visionnages (revisionnages compris)"),
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

# Note, commentaire, émotions, personnage préféré (même formulaire que la fiche film)
avis.review_form(SHOW, show.id, "serie_message")

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
