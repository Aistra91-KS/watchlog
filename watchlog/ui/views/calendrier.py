"""Page « Calendrier » : sorties des séries et films suivis, en vue Mois, Semaine ou Liste.

- Mois / Semaine : toutes les sorties de la période (les déjà vues en grisé, avec ✓).
- Liste : les sorties non vues des 60 prochains jours, groupées par semaine.
La vue et la période sont gardées dans l'URL (?vue=mois&mois=2026-10). Un clic sur un titre
ouvre sa fiche par un lien de page Streamlit (st.page_link) : pas de rechargement complet,
la session (filtres des autres pages...) est gardée. La grille est faite de colonnes et de
conteneurs Streamlit ; leur clé (st-key-...) sert de sélecteur CSS (liseré des films, grisé).
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

THUMB_WIDTH = 26        # vue Mois : vignette à côté du titre (px)
CARD_WIDTH = 280        # vue Liste : largeur d'une carte (px), elles passent à la ligne
CARD_POSTER = 60        # vue Liste : largeur de l'affiche (px)
# Préfixes des clés des conteneurs : la classe st-key-<clé> sert de sélecteur CSS.
DAY_KEY, ITEM_KEY, CARD_KEY = "cal_day", "cal_it", "cal_card"

# %(movie)s : couleur du liseré des films ; %% = « % » littéral.
CSS = """
<style>
.wl-head {font-size:.8rem;opacity:.65;text-align:center;font-weight:600}
.wl-num {font-size:.8rem;font-weight:700;display:flex;justify-content:space-between}
.wl-num .tag {font-weight:600;color:var(--wl-primary)}
.wl-num .wd {display:none}
.wl-sr {position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);
        white-space:nowrap}
.wl-ph {width:100%%;aspect-ratio:2/3;border-radius:3px;background:rgba(128,128,128,.2)}
.wl-card-date {font-size:.75rem;font-weight:700;color:var(--wl-primary);text-transform:uppercase}
[class*='st-key-cal_day_'] {min-height:118px;padding:6px !important;gap:4px !important;
        background:rgba(128,128,128,.04)}
[class*='st-key-cal_day_out_'] .wl-num, [class*='st-key-cal_day_out_'] img {opacity:.5}
[class*='st-key-cal_day_today_'] {border:2px solid var(--wl-primary) !important;
        background:rgba(128,128,128,.08)}
[class*='st-key-cal_week'] [class*='st-key-cal_day_'] {min-height:260px}
[class*='st-key-cal_it_'] {border-radius:6px;padding:2px}
[class*='st-key-cal_it_'] img {border-radius:3px}
[class*='st-key-cal_it_'] a p, [class*='st-key-cal_it_'] a span {font-size:.74rem;
        line-height:1.15}
[class*='st-key-cal_it_'] a {padding:0 2px !important;min-height:0 !important}
[class*='st-key-cal_it_'][class*='_vu_'] img {opacity:.45}
[class*='st-key-cal_it_film'] {border-left:3px solid %(movie)s;padding-left:4px}
[class*='st-key-cal_card_'] {background:rgba(128,128,128,.04)}
[class*='st-key-cal_card_film'] {border-left:4px solid %(movie)s !important}
[class*='st-key-cal_card_'] img {border-radius:6px}
@media (max-width: 640px) {
  .wl-head {display:none}
  .wl-num .wd {display:inline}
  [class*='st-key-cal_day_'] {min-height:0}
}
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


# --- Rendu (éléments Streamlit) -----------------------------------------------------------

def fiche_target(item: queue.QueueItem) -> tuple[str, dict]:
    """Page de la fiche et ses paramètres d'URL (comme common.open_show / open_movie)."""
    if item.kind == EPISODE:
        return common.PAGE_SHOW, {"id": item.show_id}
    return common.PAGE_MOVIE, {"id": item.ref_id}


def fiche_link(entry: queue.DayEntry, label: str) -> None:
    """Lien vers la fiche : navigation Streamlit, la session est gardée."""
    item = entry.first
    page, params = fiche_target(item)
    tooltip = "Déjà vu" if entry.watched else "Ouvrir la fiche"
    if item.kind == EPISODE and len(entry.items) == 1 and item.episode_title:
        tooltip += f" · {item.episode_title}"
    st.page_link(page, label=label, query_params=params, help=tooltip)


def poster(item: queue.QueueItem, width: int | None) -> None:
    """Affiche (fichier local d'abord, sinon en ligne) ou cadre gris ; décorative (alt vide)."""
    src = common.poster_file(item.poster_path) or item.poster_url
    if src:
        st.image(src, width=width or "stretch", alt="")
    else:
        style = f"width:{width}px" if width else ""
        st.markdown(f"<div class='wl-ph' style='{style}'></div>", unsafe_allow_html=True)


def entry_key(prefix: str, entry: queue.DayEntry, suffix: str) -> str:
    """Clé du conteneur d'un titre : type (film / ep) et « vu » lisibles par le CSS."""
    kind = "film" if entry.first.kind == MOVIE else "ep"
    return f"{prefix}_{kind}_{'vu' if entry.watched else 'nv'}_{suffix}"


def grid_entry(entry: queue.DayEntry, key: str, big: bool) -> None:
    """Un titre dans une case : vignette + lien (Mois), grande affiche + lien (Semaine)."""
    check = "✓ " if entry.watched else ""
    label = f"{check}{common.md_escape(entry.first.title)} · {entry.label}"
    with st.container(key=entry_key(ITEM_KEY, entry, key), horizontal=not big, gap="small",
                      vertical_alignment="center"):
        poster(entry.first, None if big else THUMB_WIDTH)
        fiche_link(entry, label)


def day_cell(day: date, items: list[queue.QueueItem], today: date, in_period: bool,
             limit: int | None, big: bool) -> None:
    entries = queue.group_day(items)
    state = "today" if day == today else ("in" if in_period else "out")
    with st.container(border=True, key=f"{DAY_KEY}_{state}_{day:%Y%m%d}"):
        tag = "<span class='tag'>aujourd'hui</span>" if day == today else ""
        # Date complète pour les lecteurs d'écran : la case ne montre que le numéro du jour
        # (et le jour de la semaine sur petit écran, quand les colonnes s'empilent).
        label = f"{DAY_NAMES[day.weekday()]} {day.day} {MONTH_NAMES[day.month - 1]} {day.year}"
        st.markdown(f"<div class='wl-num'><span class='wl-sr'>{esc(label)}</span>"
                    f"<span aria-hidden='true'><span class='wd'>{DAY_NAMES[day.weekday()][:3]}. "
                    f"</span>{day.day}</span>{tag}</div>", unsafe_allow_html=True)
        shown = entries if limit is None else entries[:limit]
        for n, entry in enumerate(shown):
            grid_entry(entry, f"{day:%Y%m%d}_{n}", big)
        if limit is not None and len(entries) > limit:
            st.caption(f"+{len(entries) - limit} autre(s)")


def grid(days: list[date], by_day: dict, today: date, in_period, limit, big=False) -> None:
    """Grille de 7 colonnes (lundi → dimanche), une rangée de colonnes par semaine."""
    for col, name in zip(st.columns(7), DAY_NAMES):
        col.markdown(f"<div class='wl-head' aria-hidden='true'>{name[:3]}.</div>",
                     unsafe_allow_html=True)
    for start in range(0, len(days), 7):
        for col, day in zip(st.columns(7), days[start:start + 7]):
            with col:
                day_cell(day, by_day.get(day, []), today, in_period(day), limit, big)


def card(entry: queue.DayEntry, today: date, key: str) -> None:
    """Vue Liste : carte affiche + date + titre (lien vers la fiche) + épisode."""
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
    with st.container(border=True, key=entry_key(CARD_KEY, entry, key), width=CARD_WIDTH,
                      horizontal=True, gap="small"):
        poster(item, CARD_POSTER)
        with st.container(gap=None):
            st.markdown(f"<div class='wl-card-date'>{esc(when)}</div>", unsafe_allow_html=True)
            fiche_link(entry, f"**{common.md_escape(item.title)}**")
            st.caption(common.md_escape(detail))


# --- Page ---------------------------------------------------------------------------------

st.title("Calendrier")
today = datetime.now(queue.LOCAL_TZ).date()
st.markdown(f"<style>:root{{--wl-primary:{common.primary_color()}}}</style>"
            + CSS % {"movie": common.chart_colors()["films"]}, unsafe_allow_html=True)

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
        # Cartes de largeur fixe qui passent à la ligne (comme une grille « auto-fill »).
        with st.container(horizontal=True, wrap=True, gap="small"):
            for day, day_items in groupby(week_items, key=lambda i: i.release_date):
                for n, entry in enumerate(queue.group_day(list(day_items))):
                    card(entry, today, f"{day:%Y%m%d}_{n}")
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
        with st.container(key="cal_week"):   # cases plus hautes (CSS)
            grid(days, by_day, today, lambda d: True, None, big=True)
    in_period = [i for i in items if start <= i.release_date <= last]
    seen = sum(1 for i in in_period if i.watched)
    st.caption(f"{len(in_period)} sortie(s) sur la période, dont {seen} déjà vue(s). "
               "Liseré orange : film. Clic sur un titre : sa fiche.")
