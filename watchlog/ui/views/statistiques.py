"""Page « Statistiques » : chiffres clés, activité, séries, films et notes, en graphiques.

Couleurs : épisodes / séries en bleu, films en orange, sur tous les graphiques
(palette vérifiée pour le daltonisme, en thème clair et sombre : voir common.CHART_COLORS).
"""

from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from watchlog.core import stats
from watchlog.core.models import EPISODE, FOR_LATER, MOVIE, TO_WATCH, WATCHING
from watchlog.core.queue import LOCAL_TZ
from watchlog.ui import common

COLORS = common.chart_colors()
SERIES_DOMAIN = ["Épisodes", "Films"]
SERIES_RANGE = [COLORS["episodes"], COLORS["films"]]
MONTHS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.",
             "nov.", "déc."]
today = datetime.now(LOCAL_TZ).date()


def hours(minutes: int) -> str:
    """Durée courte pour une tuile : « 2 711 h » (ou « 45 min »)."""
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60:,} h".replace(",", " ")


def month_label(month: str) -> str:
    """« 2026-10 » -> « oct. 26 »."""
    return f"{MONTHS_FR[int(month[5:]) - 1]} {month[2:4]}"


def bars(frame: pd.DataFrame, x: str, y: str, color: str, x_title=None, y_title=None,
         horizontal: bool = False, height: int = 260, tooltip=None, sort=None) -> alt.Chart:
    """Barres fines à bouts arrondis (4 px), une seule couleur, infobulle au survol."""
    radius = ({"cornerRadiusTopRight": 4, "cornerRadiusBottomRight": 4} if horizontal else
              {"cornerRadiusTopLeft": 4, "cornerRadiusTopRight": 4})
    mark = alt.Chart(frame).mark_bar(color=color, **radius)
    category = alt.Y(f"{x}:N", sort=sort, title=x_title) if horizontal else \
        alt.X(f"{x}:N", sort=sort, title=x_title, axis=alt.Axis(labelAngle=0))
    value = alt.X(f"{y}:Q", title=y_title) if horizontal else \
        alt.Y(f"{y}:Q", title=y_title, axis=alt.Axis(tickMinStep=1))
    encoding = {"y": category, "x": value} if horizontal else {"x": category, "y": value}
    return mark.encode(**encoding, tooltip=tooltip or [x, y]).properties(height=height)


def two_series(frame: pd.DataFrame, x: str, x_title: str, y_title: str, stacked: bool,
               height: int = 280, x_sort=None) -> alt.Chart:
    """Épisodes et films sur un même axe : empilés (même total) ou côte à côte (comparaison)."""
    long = frame.melt(id_vars=[x], value_vars=["episodes", "films"], var_name="type",
                      value_name="valeur")
    long["type"] = long["type"].map({"episodes": "Épisodes", "films": "Films"})
    color = alt.Color("type:N", title=None, scale=alt.Scale(domain=SERIES_DOMAIN, range=SERIES_RANGE),
                      legend=alt.Legend(orient="top"))
    base = alt.Chart(long).mark_bar(
        cornerRadiusTopLeft=4 if not stacked else 0, cornerRadiusTopRight=4 if not stacked else 0,
        stroke=COLORS["surface"], strokeWidth=2 if stacked else 0)
    encoding = {"x": alt.X(f"{x}:N", sort=x_sort, title=x_title, axis=alt.Axis(labelAngle=0)),
                "y": alt.Y("valeur:Q", title=y_title, stack="zero" if stacked else None),
                "color": color,
                "tooltip": [alt.Tooltip(f"{x}:N", title=x_title or ""),
                            alt.Tooltip("type:N", title="Type"),
                            alt.Tooltip("valeur:Q", title=y_title)]}
    if not stacked:
        encoding["xOffset"] = alt.XOffset("type:N", sort=SERIES_DOMAIN)
    return base.encode(**encoding).properties(height=height)


def heatmap(daily: pd.DataFrame) -> alt.Chart:
    """Calendrier des 12 derniers mois : une case par jour, plus foncée = plus de visionnages."""
    daily = daily.copy()
    first_monday = daily["day"].iloc[0] - timedelta(days=daily["day"].iloc[0].weekday())
    daily["semaine"] = daily["day"].map(lambda d: (d - first_monday).days // 7)
    daily["jour"] = daily["day"].map(lambda d: stats.WEEKDAYS[d.weekday()][:3] + ".")
    daily["date"] = daily["day"].map(common.fmt_date)
    month_starts = daily[daily["day"].map(lambda d: d.day == 1)]
    labels = {int(r.semaine): MONTHS_FR[r.day.month - 1] for r in month_starts.itertuples()}
    peak = max(int(daily["visionnages"].max()), 1)
    cells = alt.Chart(daily).mark_rect(cornerRadius=2, stroke=COLORS["surface"], strokeWidth=2).encode(
        x=alt.X("semaine:O", title=None, axis=alt.Axis(
            labelExpr="{" + ", ".join(f"'{k}': '{v}'" for k, v in labels.items()) + "}[datum.value] || ''",
            labelAngle=0, ticks=False, domain=False)),
        y=alt.Y("jour:O", title=None, sort=[stats.WEEKDAYS[i][:3] + "." for i in range(7)],
                axis=alt.Axis(ticks=False, domain=False)),
        color=alt.condition(
            "datum.visionnages == 0", alt.value(COLORS["empty"]),
            alt.Color("visionnages:Q", title="Visionnages",
                      scale=alt.Scale(type="quantize", domain=[1, peak], range=COLORS["ramp"]),
                      legend=alt.Legend(orient="bottom", direction="horizontal"))),
        tooltip=[alt.Tooltip("date:N", title="Jour"), alt.Tooltip("visionnages:Q", title="Visionnages")])
    return cells.properties(height=alt.Step(17))


def open_selected(event, ids: list[int], opener) -> None:
    """Ligne cliquée dans un tableau -> ouvre la fiche correspondante."""
    rows = event.selection.rows if event and event.selection else []
    if rows:
        opener(ids[rows[0]])


# --- Données ------------------------------------------------------------------------------

st.title("Statistiques")
with common.db() as session:
    counts = stats.global_counts(session)
    frame = stats.watches_frame(session)
    spent = stats.time_spent(session)
    completed = stats.completed_shows(session)
    top = stats.top_ratings(session, limit=10)
    movies = stats.watched_movies(session)
    progress = stats.show_progress(session)
    notes = stats.rating_distribution(session)

# --- Chiffres clés ----------------------------------------------------------------------
cols = st.columns(5)
cols[0].metric("Séries suivies", counts["shows"])
cols[1].metric("Séries terminées", counts["completed_shows"])
cols[2].metric("Épisodes vus", f"{counts['episodes_watched']:,}".replace(",", " "))
cols[3].metric("Films suivis", counts["movies"])
cols[4].metric("Films vus", counts["movies_watched"])

cols = st.columns(5)
cols[0].metric("Temps total", f"{spent['total_minutes'] / 1440:.1f} jours".replace(".", ","),
               help=stats.format_duration(spent["total_minutes"]))
cols[1].metric("Temps en épisodes", hours(spent["episode_minutes"]),
               help=stats.format_duration(spent["episode_minutes"]))
cols[2].metric("Temps en films", hours(spent["movie_minutes"]),
               help=stats.format_duration(spent["movie_minutes"]))
rated = notes["nombre"].sum()
average = (notes["note"] * notes["nombre"]).sum() / rated if rated else None
cols[3].metric("Note moyenne", f"{average:.1f} / 5".replace(".", ",") if average else "—",
               help=f"Sur {rated} note(s)")
this_year = frame[frame["day"].map(lambda d: getattr(d, "year", None) == today.year)]
cols[4].metric(f"Vus en {today.year}", f"{len(this_year):,}".replace(",", " "),
               help="Épisodes et films (premier visionnage)")
missing = []
if spent["episodes_without_runtime"]:
    missing.append(f"{spent['episodes_without_runtime']} épisode(s)")
if spent["movies_without_runtime"]:
    missing.append(f"{spent['movies_without_runtime']} film(s)")
if missing:
    st.caption(f"Temps : durée inconnue, non comptée pour {' et '.join(missing)}.")

# --- Temps restant pour finir la liste ----------------------------------------------------
st.subheader("Pour finir ta liste")
with_later = st.toggle("Compter aussi « Plus tard »", key="stats_finish_later")
with common.db() as session:
    remaining = stats.remaining_time(
        session, statuses=(WATCHING, TO_WATCH, FOR_LATER) if with_later else (WATCHING, TO_WATCH))
bulk_finish = stats.bulk_days(frame)
cols = st.columns(len(stats.PACE_PERIODS) + 1)
cols[0].metric("Reste à voir", hours(remaining["total_minutes"]),
               help=f"{remaining['episodes']} épisode(s) ({hours(remaining['episode_minutes'])}) et "
                    f"{remaining['movies']} film(s) ({hours(remaining['movie_minutes'])}) "
                    "déjà sortis, pas encore vus.")
estimated = remaining["estimated"]
for col, (title, days) in zip(cols[1:], stats.PACE_PERIODS.items()):
    pace = stats.watch_pace(frame, days, bulk_finish, today)
    estimated += pace["estimated"]
    finish = stats.finish_estimate(remaining["total_minutes"], pace["per_day"], today)
    detail = (f"{stats.format_duration(round(pace['per_day']))} par jour : {pace['watches']} "
              f"visionnage(s) en {pace['days']} jour(s), depuis le {common.fmt_date(pace['start'])}.")
    if pace["start"] > today - timedelta(days=days - 1):
        detail += " Ton historique n'est daté que depuis le premier import."
    skipped = (today - pace["start"]).days + 1 - pace["days"]
    if skipped:
        detail += f" {skipped} jour(s) d'import non compté(s)."
    if finish is None:
        col.metric(title, "—", "rien vu sur la période", delta_color="off", delta_arrow="off",
                   help=detail)
    elif finish["days"] == 0:
        col.metric(title, "Liste finie", delta_color="off", help=detail)
    else:
        col.metric(title, stats.format_span(today, finish["date"]),
                   f"fin vers le {common.fmt_date(finish['date'])}", delta_color="off",
                   delta_arrow="off", help=detail)
note = ("En cours et À voir" + (", Plus tard" if with_later else "") + " ; hors épisodes spéciaux. "
        "Rythme : nouveaux visionnages (un revisionnage ne fait pas avancer la liste), jours "
        "d'import exclus. Les prochaines sorties allongeront la liste.")
if estimated:
    note += f" Durée inconnue estimée (moyenne) pour {estimated} élément(s)."
st.caption(note)

tab_activity, tab_shows, tab_movies, tab_notes = st.tabs(
    ["Activité", "Séries", "Films", "Notes"], key="stats_tab", on_change="rerun")

# --- Activité -----------------------------------------------------------------------------
if tab_activity.open:
    with tab_activity:
        bulk = stats.bulk_days(frame)
        dated = frame
        if bulk:
            details = ", ".join(f"{common.fmt_date(d)} ({n} visionnages)" for d, n in bulk.items())
            ignore = st.toggle("Ignorer les jours d'import", value=True, key="stats_ignore_bulk",
                               help=f"Jours où un import a daté d'un coup tout l'historique : {details}. "
                                    "Ils écrasent les graphiques basés sur les dates.")
            if ignore:
                dated = stats.without_days(frame, bulk)
                st.caption(f"{sum(bulk.values())} visionnages datés du jour de l'import "
                           f"({', '.join(common.fmt_date(d) for d in bulk)}) ne sont pas comptés ci-dessous.")

        rec = stats.records(dated)
        cols = st.columns(4)
        if rec["best_day"]:
            day, n = rec["best_day"]
            cols[0].metric("Jour record (visionnages)", n, common.fmt_date(day),
                           delta_color="off", delta_arrow="off")
        if rec["streak"]:
            length, start, end = rec["streak"]
            cols[1].metric("Jours d'affilée (record)", length,
                           f"{common.fmt_date(start)} → {common.fmt_date(end)}",
                           delta_color="off", delta_arrow="off")
        if rec["best_month"]:
            month, n = rec["best_month"]
            cols[2].metric("Mois record (visionnages)", n, month_label(month),
                           delta_color="off", delta_arrow="off")
        weekdays = stats.per_weekday(dated)
        if weekdays["visionnages"].sum():
            best = weekdays.loc[weekdays["visionnages"].idxmax()]
            cols[3].metric("Jour préféré", best["libelle"], f"{best['visionnages']} visionnage(s)",
                           delta_color="off", delta_arrow="off")

        st.subheader("Les 12 derniers mois, jour par jour")
        st.altair_chart(heatmap(stats.daily_counts(dated, 365, today)), width="stretch")

        st.subheader("Vus par mois")
        st.caption("Date du premier visionnage.")
        monthly = stats.count_per_month(dated, 12, today)
        monthly = monthly.assign(libelle=monthly["mois"].map(month_label))
        st.altair_chart(two_series(monthly[["libelle", "episodes", "films"]], "libelle", None,
                                   "Visionnages", stacked=False), width="stretch")

        st.subheader("Heures passées par mois")
        hours = stats.hours_per_month(dated, 12, today)
        hours = hours.assign(libelle=hours["mois"].map(month_label))
        st.altair_chart(two_series(hours[["libelle", "episodes", "films"]], "libelle", None, "Heures",
                                   stacked=True), width="stretch")

        left, right = st.columns(2)
        with left:
            st.subheader("Par jour de la semaine")
            st.altair_chart(bars(weekdays, "libelle", "visionnages", COLORS["episodes"],
                                 y_title="Visionnages", sort=stats.WEEKDAYS,
                                 tooltip=[alt.Tooltip("libelle:N", title="Jour"),
                                          alt.Tooltip("visionnages:Q", title="Visionnages")]),
                            width="stretch")
        with right:
            st.subheader("Par année")
            yearly = stats.per_year(dated)
            if yearly.empty:
                st.write("Aucun visionnage daté.")
            else:
                yearly["annee"] = yearly["annee"].astype(str)
                st.altair_chart(two_series(yearly, "annee", None, "Visionnages", stacked=False),
                                width="stretch")

# --- Séries -------------------------------------------------------------------------------
if tab_shows.open:
    with tab_shows:
        left, right = st.columns(2)
        with left:
            st.subheader("Où passe ton temps")
            top_time = stats.top_shows_by_time(frame, 10)
            if top_time.empty:
                st.write("Aucun épisode vu.")
            else:
                st.altair_chart(bars(top_time, "title", "heures", COLORS["episodes"], y_title="Heures",
                                     horizontal=True, height=320, sort="-x",
                                     tooltip=[alt.Tooltip("title:N", title="Série"),
                                              alt.Tooltip("heures:Q", title="Heures"),
                                              alt.Tooltip("episodes:Q", title="Épisodes vus")]),
                                width="stretch")
        with right:
            st.subheader("Genres des séries regardées")
            genres = stats.genre_counts(frame, EPISODE, 10)
            if genres.empty:
                st.write("Genres pas encore connus (ils arrivent avec la synchronisation).")
            else:
                st.altair_chart(bars(genres, "genre", "titres", COLORS["episodes"], y_title="Séries",
                                     horizontal=True, height=320, sort="-x",
                                     tooltip=[alt.Tooltip("genre:N", title="Genre"),
                                              alt.Tooltip("titres:Q", title="Séries")]),
                                width="stretch")

        st.subheader(f"Séries en cours ({len(progress)})")
        if progress.empty:
            st.write("Aucune série en cours.")
        else:
            event = st.dataframe(
                progress.assign(reste=progress["diffuses"] - progress["vus"]).rename(
                    columns={"title": "Série", "vus": "Vus", "diffuses": "Diffusés",
                             "pourcentage": "Avancement", "reste": "À voir"})[
                    ["Série", "Avancement", "Vus", "Diffusés", "À voir"]],
                hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                key="stats_progress",
                column_config={"Avancement": st.column_config.ProgressColumn(
                    format="%d %%", min_value=0, max_value=100)})
            st.caption("Épisodes vus sur épisodes déjà diffusés (hors spéciaux). "
                       "Clic sur une ligne : la fiche de la série.")
            open_selected(event, progress["show_id"].tolist(), common.open_show)

        st.subheader(f"Séries terminées ({len(completed)})")
        if completed:
            event = st.dataframe(
                pd.DataFrame([{"Série": row["title"], "Épisodes vus": row["episodes"],
                               "Dernier visionnage": row["last_watched"]} for row in completed]),
                hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                key="stats_completed",
                column_config={"Dernier visionnage": st.column_config.DateColumn(format="DD/MM/YYYY")})
            st.caption("Clic sur une ligne : la fiche de la série.")
            open_selected(event, [row["show_id"] for row in completed], common.open_show)
        else:
            st.write("Aucune série terminée pour l'instant.")

# --- Films --------------------------------------------------------------------------------
if tab_movies.open:
    with tab_movies:
        runtimes = stats.movie_runtime_stats(frame)
        cols = st.columns(4)
        cols[0].metric("Films vus", len(movies))
        if runtimes:
            cols[1].metric("Durée moyenne", stats.format_duration(runtimes["average"]))
            cols[2].metric("Le plus long", stats.format_duration(runtimes["longest"][1]),
                           runtimes["longest"][0], delta_color="off", delta_arrow="off")
            cols[3].metric("Le plus court", stats.format_duration(runtimes["shortest"][1]),
                           runtimes["shortest"][0], delta_color="off", delta_arrow="off")

        left, right = st.columns(2)
        with left:
            st.subheader("Genres des films vus")
            genres = stats.genre_counts(frame, MOVIE, 10)
            if genres.empty:
                st.write("Genres pas encore connus (ils arrivent avec la synchronisation).")
            else:
                st.altair_chart(bars(genres, "genre", "titres", COLORS["films"], y_title="Films",
                                     horizontal=True, height=320, sort="-x",
                                     tooltip=[alt.Tooltip("genre:N", title="Genre"),
                                              alt.Tooltip("titres:Q", title="Films")]),
                                width="stretch")
        with right:
            st.subheader("Par décennie de sortie")
            decades = stats.movie_decades(frame)
            if decades.empty:
                st.write("Années de sortie inconnues.")
            else:
                st.altair_chart(bars(decades, "libelle", "films", COLORS["films"], y_title="Films",
                                     height=320, sort=None,
                                     tooltip=[alt.Tooltip("libelle:N", title="Décennie"),
                                              alt.Tooltip("films:Q", title="Films")]),
                                width="stretch")

        st.subheader(f"Films vus ({len(movies)})")
        if movies:
            col_search, col_sort = st.columns([3, 1])
            query = col_search.text_input("Rechercher un film", key="stats_movie_search",
                                          placeholder="Titre ou genre")
            order = col_sort.selectbox("Trier par", ["Date de visionnage", "Titre", "Note", "Durée",
                                                     "Année de sortie"], key="stats_movie_sort")
            shown = [m for m in movies if query.lower() in
                     f"{m['title']} {m['genres'] or ''}".lower()]
            keys = {"Titre": lambda m: m["title"].lower(),
                    "Note": lambda m: -(m["rating"] or 0),
                    "Durée": lambda m: -(m["runtime"] or 0),
                    "Année de sortie": lambda m: -(m["year"] or 0)}
            if order in keys:
                shown = sorted(shown, key=keys[order])
            event = st.dataframe(
                pd.DataFrame([{"Film": m["title"], "Vu le": m["watched"],
                               "Note": "★" * m["rating"] if m["rating"] else "",
                               "Durée": stats.format_duration(m["runtime"]) if m["runtime"] else "",
                               "Sortie": str(m["year"]) if m["year"] else "",
                               "Genres": m["genres"] or "",
                               "Vu": f"{m['plays']} fois" if m["plays"] > 1 else ""} for m in shown],
                             columns=["Film", "Vu le", "Note", "Durée", "Sortie", "Genres", "Vu"]),
                hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                key="stats_movies", height=420,
                column_config={"Vu le": st.column_config.DateColumn(format="DD/MM/YYYY")})
            st.caption("Date inconnue : vide (modifiable dans la fiche). "
                       "Clic sur une ligne : la fiche du film.")
            open_selected(event, [m["movie_id"] for m in shown], common.open_movie)
        else:
            st.write("Aucun film vu pour l'instant.")

# --- Notes --------------------------------------------------------------------------------
if tab_notes.open:
    with tab_notes:
        left, right = st.columns(2)
        with left:
            st.subheader("Répartition des notes")
            if rated:
                notes["libelle"] = notes["note"].map(lambda n: "★" * n)
                st.altair_chart(bars(notes, "libelle", "nombre", COLORS["episodes"], y_title="Notes",
                                     sort=None, tooltip=[alt.Tooltip("libelle:N", title="Note"),
                                                         alt.Tooltip("nombre:Q", title="Nombre")]),
                                width="stretch")
            else:
                st.write("Aucune note pour l'instant.")
            st.caption("Noter tes séries et films affine aussi les suggestions.")
        with right:
            st.subheader("Meilleures notes")
            if top:
                st.dataframe(
                    pd.DataFrame([{"Titre": row["title"], "Type": row["kind"],
                                   "Note": "★" * row["rating"]} for row in top]),
                    hide_index=True, width="stretch")
            else:
                st.write("Aucune note pour l'instant.")
