"""Page « Calendrier » : sorties des séries et films suivis, en vue Mois, Semaine ou Liste.

- Mois / Semaine : toutes les sorties de la période (les déjà vues en grisé, avec ✓).
- Liste : les sorties non vues des 60 prochains jours, groupées par semaine.
La vue et la période sont gardées dans l'URL (?vue=mois&mois=2026-10) : un clic sur un titre
ouvre sa fiche, et le bouton « retour » du navigateur ramène au même endroit.
"""

import calendar as cal
import html
from datetime import date, datetime, timedelta
from itertools import groupby

import streamlit as st

from watchlog.core import queue
from watchlog.core.models import EPISODE, MOVIE
from watchlog.ui import common

DAYS = 60
MAX_PER_DAY = 4          # vue Mois : au-delà, « +N autres »
DAY_NAMES = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MONTH_NAMES = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
               "septembre", "octobre", "novembre", "décembre"]
VIEW_MONTH, VIEW_WEEK, VIEW_LIST = "Mois", "Semaine", "Liste"
VIEW_PARAMS = {VIEW_MONTH: "mois", VIEW_WEEK: "semaine", VIEW_LIST: "liste"}
TYPE_ALL, TYPE_SHOWS, TYPE_MOVIES = "Tout", "Séries", "Films"
MOVIE_COLOR = "#8e6bbf"   # liseré des films (les épisodes n'en ont pas)

CSS = """
<style>
.wl-grid {display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px;min-width:700px}
.wl-scroll {overflow-x:auto;padding-bottom:4px}
.wl-head {font-size:.8rem;opacity:.65;text-align:center;padding:2px 0;font-weight:600}
.wl-day {border:1px solid rgba(128,128,128,.25);border-radius:10px;padding:6px;
         background:rgba(128,128,128,.04);min-height:118px}
.wl-day.out .wl-num, .wl-day.out img {opacity:.5}
.wl-day.today {border:2px solid var(--wl-primary);background:rgba(128,128,128,.08)}
.wl-num {font-size:.8rem;font-weight:700;margin-bottom:4px;display:flex;justify-content:space-between}
.wl-num .tag {font-weight:600;color:var(--wl-primary)}
.wl-item {display:flex;gap:6px;align-items:center;margin:0 0 4px 0;padding:2px;border-radius:6px;
          text-decoration:none !important;color:inherit !important}
.wl-item:hover {background:rgba(128,128,128,.15)}
.wl-item img, .wl-item .ph {width:26px;height:39px;object-fit:cover;border-radius:3px;flex:none;
          background:rgba(128,128,128,.2)}
.wl-item .t {font-size:.74rem;line-height:1.15;font-weight:600;overflow:hidden;display:-webkit-box;
          -webkit-line-clamp:2;-webkit-box-orient:vertical}
.wl-item .c {font-size:.7rem;opacity:.7}
.wl-item.vu img {opacity:.45}
.wl-item.vu .t {opacity:.8}
.wl-item.film {border-left:3px solid %(movie)s;padding-left:4px}
.wl-more {font-size:.7rem;opacity:.7}
.wl-week .wl-day {min-height:260px}
.wl-week .wl-item {flex-direction:column;align-items:stretch;gap:3px;margin-bottom:8px}
.wl-week .wl-item img, .wl-week .wl-item .ph {width:100%%;height:auto;aspect-ratio:2/3}
.wl-cards {display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:10px;margin-bottom:1rem}
.wl-card {display:flex;gap:10px;padding:8px;border:1px solid rgba(128,128,128,.25);border-radius:10px;
          text-decoration:none !important;color:inherit !important;background:rgba(128,128,128,.04)}
.wl-card:hover {background:rgba(128,128,128,.12)}
.wl-card.film {border-left:4px solid %(movie)s}
.wl-card img, .wl-card .ph {width:60px;height:90px;object-fit:cover;border-radius:6px;flex:none;
          background:rgba(128,128,128,.2)}
.wl-card .d {font-size:.75rem;font-weight:700;color:var(--wl-primary);text-transform:uppercase}
.wl-card .t {font-weight:700;font-size:.95rem;line-height:1.2}
.wl-card .c {font-size:.8rem;opacity:.75}
</style>
"""

esc = html.escape


# --- Navigation (gardée dans l'URL) --------------------------------------------------------

def current_view() -> str:
    by_param = {v: k for k, v in VIEW_PARAMS.items()}
    return by_param.get(st.query_params.get("vue", ""), VIEW_MONTH)


def current_month(today: date) -> date:
    try:
        year, month = (int(x) for x in st.query_params.get("mois", "").split("-"))
        return date(year, month, 1)
    except ValueError:
        return today.replace(day=1)


def current_week(today: date) -> date:
    try:
        return queue.week_start(date.fromisoformat(st.query_params.get("semaine", "")))
    except ValueError:
        return queue.week_start(today)


def set_view() -> None:
    st.query_params["vue"] = VIEW_PARAMS[st.session_state["cal_view"] or VIEW_MONTH]


def shift(view: str, start: date, step: int) -> None:
    """Période précédente (-1), suivante (+1) ou actuelle (0)."""
    today = datetime.now(queue.LOCAL_TZ).date()
    if view == VIEW_MONTH:
        if step == 0:
            target = today.replace(day=1)
        else:
            month = start.month - 1 + step
            target = date(start.year + month // 12, month % 12 + 1, 1)
        st.query_params["mois"] = f"{target:%Y-%m}"
    else:
        target = queue.week_start(today) if step == 0 else start + timedelta(weeks=step)
        st.query_params["semaine"] = target.isoformat()


# --- Rendu HTML ---------------------------------------------------------------------------

def fiche_url(item: queue.QueueItem) -> str:
    return f"serie?id={item.show_id}" if item.kind == EPISODE else f"film?id={item.ref_id}"


def thumb(item: queue.QueueItem) -> str:
    src = common.poster_src(item.poster_path, item.poster_url)
    return f"<img src='{esc(src)}' alt='' loading='lazy'>" if src else "<div class='ph'></div>"


def entry_html(entry: queue.DayEntry) -> str:
    item = entry.first
    classes = ["wl-item"] + (["vu"] if entry.watched else []) + (["film"] if item.kind == MOVIE else [])
    check = "✓ " if entry.watched else ""
    tooltip = item.title + (f" · {item.episode_title}" if len(entry.items) == 1 and item.episode_title else "")
    return (f"<a class='{' '.join(classes)}' href='{fiche_url(item)}' target='_self' "
            f"title='{esc(tooltip)}'>{thumb(item)}<div><div class='t'>{esc(item.title)}</div>"
            f"<div class='c'>{check}{esc(entry.label)}</div></div></a>")


def day_cell(day: date, items: list[queue.QueueItem], today: date, in_period: bool,
             limit: int | None) -> str:
    entries = queue.group_day(items)
    classes = ["wl-day"] + ([] if in_period else ["out"]) + (["today"] if day == today else [])
    tag = "<span class='tag'>aujourd'hui</span>" if day == today else ""
    shown = entries if limit is None else entries[:limit]
    body = "".join(entry_html(e) for e in shown)
    if limit is not None and len(entries) > limit:
        body += f"<div class='wl-more'>+{len(entries) - limit} autre(s)</div>"
    # Date complète pour les lecteurs d'écran : la case seule ne montre que le numéro du jour.
    label = f"{DAY_NAMES[day.weekday()]} {day.day} {MONTH_NAMES[day.month - 1]} {day.year}"
    return (f"<div class='{' '.join(classes)}' role='group' aria-label='{esc(label)}'>"
            f"<div class='wl-num'><span aria-hidden='true'>{day.day}</span>{tag}</div>"
            f"{body}</div>")


def grid(days: list[date], by_day: dict, today: date, in_period, limit, extra_class="") -> None:
    head = "".join(f"<div class='wl-head' aria-hidden='true'>{name[:3]}.</div>"
                   for name in DAY_NAMES)
    cells = "".join(day_cell(d, by_day.get(d, []), today, in_period(d), limit) for d in days)
    st.markdown(f"<div class='wl-scroll {extra_class}'><div class='wl-grid'>{head}{cells}</div></div>",
                unsafe_allow_html=True)


def card_html(entry: queue.DayEntry, today: date) -> str:
    item = entry.first
    day = item.release_date
    if day == today:
        when = "Aujourd'hui"
    elif day == today + timedelta(days=1):
        when = "Demain"
    else:
        when = f"{DAY_NAMES[day.weekday()][:3]}. {day:%d/%m}"
    detail = entry.label
    if item.kind == EPISODE and len(entry.items) == 1 and item.episode_title:
        detail += f" · {item.episode_title}"
    classes = "wl-card film" if item.kind == MOVIE else "wl-card"
    return (f"<a class='{classes}' href='{fiche_url(item)}' target='_self'>{thumb(item)}"
            f"<div><div class='d'>{esc(when)}</div><div class='t'>{esc(item.title)}</div>"
            f"<div class='c'>{esc(detail)}</div></div></a>")


# --- Page ---------------------------------------------------------------------------------

st.title("Calendrier")
today = datetime.now(queue.LOCAL_TZ).date()
st.markdown(f"<style>:root{{--wl-primary:{common.primary_color()}}}</style>"
            + CSS % {"movie": MOVIE_COLOR}, unsafe_allow_html=True)

with common.db() as session:
    upcoming = queue.calendar(session, DAYS)

# Résumé en chiffres (sorties non vues à venir)
week_end = today + timedelta(days=6)
col_today, col_week, col_movies, col_next = st.columns(4)
col_today.metric("Aujourd'hui", sum(1 for i in upcoming if i.release_date == today))
col_week.metric("7 prochains jours", sum(1 for i in upcoming if i.release_date <= week_end))
col_movies.metric(f"Films ({DAYS} j)", sum(1 for i in upcoming if i.kind == MOVIE))
later = [i for i in upcoming if i.release_date > today]
if later:
    nxt = later[0]
    days_left = (nxt.release_date - today).days
    col_next.metric("Prochaine sortie", f"J-{days_left}", nxt.title, delta_color="off",
                    delta_arrow="off")
else:
    col_next.metric("Prochaine sortie", "—")

view = current_view()
col_view, col_type, col_options = st.columns([2, 2, 3], vertical_alignment="bottom")
with col_view:
    st.session_state["cal_view"] = view
    st.segmented_control("Vue", [VIEW_MONTH, VIEW_WEEK, VIEW_LIST], key="cal_view",
                         on_change=set_view, required=True)
with col_type:
    kind_choice = st.segmented_control("Type", [TYPE_ALL, TYPE_SHOWS, TYPE_MOVIES],
                                       default=TYPE_ALL, key="cal_type", required=True)
with col_options:
    col_seen, col_specials = st.columns(2)
    show_seen = view != VIEW_LIST and col_seen.checkbox("Épisodes et films vus", value=True,
                                                         key="cal_seen")
    specials = col_specials.checkbox("Épisodes spéciaux", key="cal_specials")


def keep(item: queue.QueueItem) -> bool:
    if kind_choice == TYPE_SHOWS and item.kind != EPISODE:
        return False
    if kind_choice == TYPE_MOVIES and item.kind != MOVIE:
        return False
    return show_seen or not item.watched


if view == VIEW_LIST:
    st.caption(f"Sorties non vues des {DAYS} prochains jours (séries et films suivis, "
               "hors séries arrêtées ou terminées). Clic sur un titre : sa fiche.")
    if specials:
        with common.db() as session:
            upcoming = queue.calendar(session, DAYS, include_specials=True)
    items = [i for i in upcoming if keep(i)]
    if not items:
        st.info(f"Aucune sortie prévue dans les {DAYS} prochains jours.")
    for monday, week_items in groupby(items, key=lambda i: queue.week_start(i.release_date)):
        label = "Cette semaine" if monday == queue.week_start(today) else \
            f"Semaine du lundi {common.fmt_date(monday)}"
        st.subheader(label)
        cards = []
        for _day, day_items in groupby(week_items, key=lambda i: i.release_date):
            cards += [card_html(e, today) for e in queue.group_day(list(day_items))]
        st.markdown(f"<div class='wl-cards'>{''.join(cards)}</div>", unsafe_allow_html=True)
else:
    if view == VIEW_MONTH:
        start = current_month(today)
        last = start.replace(day=cal.monthrange(start.year, start.month)[1])
        first_shown = queue.week_start(start)
        last_shown = queue.week_start(last) + timedelta(days=6)
        title = f"{MONTH_NAMES[start.month - 1].capitalize()} {start.year}"
    else:
        start = current_week(today)
        last = start + timedelta(days=6)
        first_shown, last_shown = start, last
        title = f"Semaine du {common.fmt_date(start)} au {common.fmt_date(last)}"

    col_prev, col_title, col_now, col_next_btn = st.columns([1, 4, 1, 1],
                                                            vertical_alignment="center")
    # Libellés en toutes lettres : un glyphe seul est mal lu par les lecteurs d'écran.
    col_prev.button("Précédent", icon=":material/chevron_left:", key="cal_prev",
                    on_click=shift, args=(view, start, -1), help="Période précédente",
                    width="stretch")
    col_title.markdown(f"### {title}")
    col_now.button("Aujourd'hui", key="cal_today", on_click=shift, args=(view, start, 0),
                   width="stretch")
    col_next_btn.button("Suivant", icon=":material/chevron_right:", key="cal_next",
                        on_click=shift, args=(view, start, 1), help="Période suivante",
                        width="stretch")

    with common.db() as session:
        items = queue.releases_between(session, first_shown, last_shown,
                                       include_specials=specials, today=today)
    items = [i for i in items if keep(i)]
    by_day: dict[date, list] = {}
    for item in items:
        by_day.setdefault(item.release_date, []).append(item)
    days = [first_shown + timedelta(days=n) for n in range((last_shown - first_shown).days + 1)]
    if view == VIEW_MONTH:
        grid(days, by_day, today, lambda d: d.month == start.month, MAX_PER_DAY)
    else:
        grid(days, by_day, today, lambda d: True, None, extra_class="wl-week")
    in_period = [i for i in items if start <= i.release_date <= last]
    seen = sum(1 for i in in_period if i.watched)
    st.caption(f"{len(in_period)} sortie(s) sur la période, dont {seen} déjà vue(s). "
               "Liseré violet : film. Clic sur un titre : sa fiche.")
