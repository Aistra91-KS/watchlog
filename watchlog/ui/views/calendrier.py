"""Page « Calendrier » : sorties non vues des 60 prochains jours, groupées par semaine."""

from datetime import datetime
from itertools import groupby

import streamlit as st

from watchlog.core import queue
from watchlog.core.models import EPISODE
from watchlog.ui import common

DAYS = 60
DAY_NAMES = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


md = common.md_escape  # protège les caractères Markdown des titres


def show_item(item: queue.QueueItem) -> None:
    title_col, detail_col = st.columns([2, 5], vertical_alignment="center")
    if title_col.button(md(item.title), key=f"cal_{item.kind}_{item.ref_id}", type="tertiary"):
        if item.kind == EPISODE:
            common.open_show(item.show_id)
        else:
            common.open_movie(item.ref_id)
    if item.kind == EPISODE:
        detail = item.code + (f" · {md(item.episode_title)}" if item.episode_title else "")
    else:
        detail = "Film"
    detail_col.markdown(detail)


st.title("Calendrier")
st.caption(f"Sorties non vues des {DAYS} prochains jours (séries et films suivis, "
           "hors séries arrêtées ou terminées).")

with common.db() as session:
    items = queue.calendar(session, DAYS)

if not items:
    st.info(f"Aucune sortie prévue dans les {DAYS} prochains jours.")
    st.stop()

today = datetime.now(queue.LOCAL_TZ).date()
# Les éléments sont déjà triés par date : on les regroupe par semaine, puis par jour.
for monday, week_items in groupby(items, key=lambda item: queue.week_start(item.release_date)):
    st.subheader(f"Semaine du lundi {common.fmt_date(monday)}")
    for day, day_items in groupby(week_items, key=lambda item: item.release_date):
        label = f"{DAY_NAMES[day.weekday()]} {common.fmt_date(day)}"
        if day == today:
            label += " · aujourd'hui"
        st.markdown(f"**{label}**")
        for item in day_items:
            show_item(item)
