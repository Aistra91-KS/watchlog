"""Page « Statistiques » : chiffres clés, visionnages par mois, temps passé, notes."""

import altair as alt
import pandas as pd
import streamlit as st

from watchlog.core import stats
from watchlog.ui import common

st.title("Statistiques")

with common.db() as session:
    counts = stats.global_counts(session)
    per_month = stats.watched_per_month(session, months=12)
    spent = stats.time_spent(session)
    completed = stats.completed_shows(session)
    top = stats.top_ratings(session, limit=10)

# --- Chiffres clés -------------------------------------------------------------
cols = st.columns(5)
cols[0].metric("Séries suivies", counts["shows"])
cols[1].metric("Séries terminées", counts["completed_shows"])
cols[2].metric("Épisodes vus", counts["episodes_watched"])
cols[3].metric("Films suivis", counts["movies"])
cols[4].metric("Films vus", counts["movies_watched"])


# --- Visionnages par mois --------------------------------------------------------
def month_chart(frame: pd.DataFrame, column: str, label: str) -> alt.Chart:
    """Histogramme simple : un mois par barre, une seule couleur."""
    return (alt.Chart(frame)
            .mark_bar(color=common.primary_color())
            .encode(x=alt.X("libelle:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)),
                    y=alt.Y(f"{column}:Q", title=label, axis=alt.Axis(tickMinStep=1)),
                    tooltip=[alt.Tooltip("libelle:N", title="Mois"),
                             alt.Tooltip(f"{column}:Q", title=label)])
            .properties(height=260))


st.subheader("Vus par mois (12 derniers mois)")
st.caption("Date du premier visionnage. Les visionnages importés de Bingers portent parfois "
           "la date de l'import : beaucoup d'épisodes peuvent alors tomber le même mois.")
# "2026-10" -> "10/2026" pour l'affichage
per_month["libelle"] = per_month["mois"].str[5:] + "/" + per_month["mois"].str[:4]
left, right = st.columns(2)
left.altair_chart(month_chart(per_month, "episodes", "Épisodes"), width="stretch")
right.altair_chart(month_chart(per_month, "films", "Films"), width="stretch")


# --- Temps passé ---------------------------------------------------------------
def days(minutes: int) -> str:
    return f"{minutes / 1440:.1f}".replace(".", ",") + " jours"


st.subheader("Temps passé")
cols = st.columns(3)
for col, label, minutes in [(cols[0], "Total", spent["total_minutes"]),
                            (cols[1], "Épisodes", spent["episode_minutes"]),
                            (cols[2], "Films", spent["movie_minutes"])]:
    col.metric(label, stats.format_duration(minutes))
    col.caption(f"soit {days(minutes)}")
missing = []
if spent["episodes_without_runtime"]:
    missing.append(f"{spent['episodes_without_runtime']} épisode(s)")
if spent["movies_without_runtime"]:
    missing.append(f"{spent['movies_without_runtime']} film(s)")
if missing:
    st.caption(f"Durée inconnue, non comptée : {' et '.join(missing)}.")

# --- Séries terminées et meilleures notes --------------------------------------------
left, right = st.columns(2)
with left:
    st.subheader(f"Séries terminées ({len(completed)})")
    if completed:
        st.dataframe(
            pd.DataFrame([{"Série": row["title"], "Épisodes vus": row["episodes"],
                           "Dernier visionnage": common.fmt_date(row["last_watched"])}
                          for row in completed]),
            hide_index=True, width="stretch")
    else:
        st.write("Aucune série terminée pour l'instant.")

with right:
    st.subheader("Meilleures notes")
    if top:
        st.dataframe(
            pd.DataFrame([{"Titre": row["title"], "Type": row["kind"], "Note": f"{row['rating']} / 5"}
                          for row in top]),
            hide_index=True, width="stretch")
    else:
        st.write("Aucune note pour l'instant.")
