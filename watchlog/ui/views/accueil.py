"""Page « Accueil » : l'essentiel tout de suite (chiffres de la semaine, épisodes à suivre,
films à voir, prochaines sorties, derniers visionnages, suggestions)."""

from datetime import date, datetime, timedelta

import streamlit as st
from sqlmodel import select

from watchlog.core import home, library, progress, stats, suggestions
from watchlog.core.dates import LOCAL_TZ
from watchlog.core.models import EPISODE, MOVIE, SHOW, LibraryItem
from watchlog.core.queue import QueueItem
from watchlog.ui import common, suggestions_cache

md = common.md_escape
POSTER_WIDTH = 64        # affiches des cartes « À suivre », en pixels
GRID = 6                 # affiches par ligne (films à voir, suggestions)
SUGGESTIONS_SHOWN = 6    # suggestions affichées (les plus pertinentes)
UPCOMING_SHOWN = 12      # lignes de « Prochaines sorties » (la suite : page Calendrier)
NEW_DAYS = 7             # badge « Nouveau » : sorti depuis moins de NEW_DAYS jours
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]


def plural(count: int, word: str, plural_word: str | None = None) -> str:
    return f"{count} {word if count <= 1 else plural_word or word + 's'}"


def day_name(day: date, today: date) -> str:
    """« Aujourd'hui », « Demain », sinon « Samedi 17/10 »."""
    if day == today:
        return "Aujourd'hui"
    if day == today + timedelta(days=1):
        return "Demain"
    return f"{stats.WEEKDAYS[day.weekday()]} {day:%d/%m}"


def card_css() -> str:
    """Liseré à gauche des cartes « À suivre » (bleu : épisodes), comme À regarder."""
    return ("<style>[class*='st-key-home_next_']{border-left:4px solid "
            f"{common.chart_colors()['episodes']} !important}}"
            "[class*='st-key-home_next_'] img{border-radius:4px}</style>")


# --- Actions -------------------------------------------------------------------

def mark_seen(item: QueueItem) -> None:
    with common.db() as session:
        library.mark_watched(session, item.kind, item.ref_id)
        session.commit()
    name = f"{item.title} {item.code}".strip()
    st.session_state["home_last_action"] = {"text": f"« {name} » marqué vu.",
                                            "undo": (item.kind, item.ref_id)}


def undo_seen(kind: str, ref_id: int) -> None:
    with common.db() as session:
        library.unmark_watched(session, kind, ref_id)
        session.commit()
    st.session_state.pop("home_last_action", None)


# --- Blocs ----------------------------------------------------------------------

def show_last_action() -> None:
    """Confirmation du dernier « Vu », avec « Annuler »."""
    action = st.session_state.get("home_last_action")
    if not action:
        return
    text_col, undo_col = st.columns([6, 1], vertical_alignment="center")
    text_col.caption(md(action["text"]))
    undo_col.button("Annuler", key="home_undo", on_click=undo_seen, args=action["undo"])


def show_metrics(session, today: date) -> None:
    """Chiffres clés : activité récente, ce qui reste à voir, temps passé."""
    frame = stats.watches_frame(session)
    recent = home.activity(frame, today)
    remaining = stats.remaining_time(session, today=today)
    spent = stats.time_spent(session)
    counts = stats.global_counts(session)

    def period_help(period: home.Period, what: str) -> str:
        text = (f"{plural(period.episodes, 'épisode')} et {plural(period.movies, 'film')} vus "
                f"{what} (premier visionnage).")
        if period.ignored:
            text += (f" {period.ignored} visionnage(s) datés d'un jour d'import ne sont pas "
                     "comptés : leur date n'est pas celle du visionnage.")
        return text

    cols = st.columns(5)
    cols[0].metric("Vus ces 7 jours", recent.week.total,
                   common.fmt_runtime(recent.week.minutes) or None, delta_color="off",
                   delta_arrow="off", border=True, help=period_help(recent.week, "ces 7 jours"))
    cols[1].metric(f"Vus en {MONTHS_FR[today.month - 1]}", recent.month.total,
                   common.fmt_runtime(recent.month.minutes) or None, delta_color="off",
                   delta_arrow="off", border=True, help=period_help(recent.month, "ce mois-ci"))
    streak_note = None
    if recent.streak and not recent.watched_today:
        streak_note = "Un épisode aujourd'hui pour continuer"
    cols[2].metric("Jours d'affilée", recent.streak, streak_note, delta_color="off",
                   delta_arrow="off", border=True,
                   help="Jours consécutifs avec au moins un épisode ou un film vu, jusqu'à "
                        "aujourd'hui (ou hier, si rien n'est encore vu aujourd'hui).")
    estimated = (f" Durée inconnue pour {remaining['estimated']} élément(s) : estimée."
                 if remaining["estimated"] else "")
    cols[3].metric("À rattraper", common.fmt_runtime(remaining["total_minutes"]) or "—",
                   f"{remaining['episodes']} ép. · {plural(remaining['movies'], 'film')}",
                   delta_color="off", delta_arrow="off", border=True,
                   help="Épisodes et films sortis, pas encore vus (En cours et À voir), comme la "
                        "page À regarder." + estimated)
    cols[4].metric("Temps total", f"{spent['total_minutes'] / 1440:.1f} jours".replace(".", ","),
                   f"{counts['episodes_watched']:,} ép. · ".replace(",", " ")
                   + plural(counts["movies_watched"], "film"),
                   delta_color="off", delta_arrow="off", border=True,
                   help="Temps passé devant tes épisodes et films vus (revisionnages compris).")


def show_next(entry: home.NextUp, today: date) -> None:
    """Une carte « À suivre » : affiche, série, prochain épisode, bouton « Vu »."""
    item = entry.item
    with st.container(border=True, key=f"home_next_{item.ref_id}"):
        poster_col, info_col, action_col = st.columns([0.6, 6, 1.3], vertical_alignment="center")
        with poster_col:
            common.show_poster(item.poster_path, width=POSTER_WIDTH, poster_url=item.poster_url)
        with info_col:
            if st.button(f"**{md(item.title)}**", key=f"home_open_{item.ref_id}",
                         type="tertiary"):
                common.open_show(item.show_id)
            badges = [f":blue-badge[{item.code}]"]
            if item.release_date and (today - item.release_date).days < NEW_DAYS:
                badges.append(":green-badge[:material/fiber_new: Nouveau]")
            if item.episode_title:
                badges.append(md(item.episode_title))
            st.markdown(" ".join(badges))
            details = []
            if entry.last_watched:
                ago = progress.watched_ago(entry.last_watched, today)
                details.append(f":material/history: {ago}")
            else:
                details.append(":material/fiber_new: Pas commencée")
            details.append(plural(entry.remaining, "épisode restant", "épisodes restants"))
            if item.runtime:
                details.append(f":material/schedule: {common.fmt_runtime(item.runtime)}")
            st.caption(" · ".join(details))
        with action_col:
            st.button("Vu", key=f"home_seen_{item.ref_id}", icon=":material/check:",
                      width="stretch", on_click=mark_seen, args=(item,),
                      help=f"Marque {item.code} vu")


def show_upcoming(days: list[tuple[date, list]], today: date) -> None:
    st.subheader(":material/event_upcoming: Prochaines sorties")
    if not days:
        st.caption(f"Aucune sortie dans les {home.UPCOMING_DAYS} prochains jours.")
    shown = 0
    for day, entries in days:
        entries = entries[:UPCOMING_SHOWN - shown]
        if not entries:
            break
        shown += len(entries)
        st.markdown(f"**{day_name(day, today)}**")
        for entry in entries:
            item = entry.first
            badge = (f":blue-badge[{entry.label}]" if item.kind == EPISODE
                     else ":orange-badge[Film]")
            page, params = ((common.PAGE_SHOW, {"id": item.show_id}) if item.kind == EPISODE
                            else (common.PAGE_MOVIE, {"id": item.ref_id}))
            link_col, badge_col = st.columns([3, 2], vertical_alignment="center")
            link_col.page_link(page, label=md(item.title), query_params=params)
            badge_col.markdown(badge)
    hidden = sum(len(entries) for _day, entries in days) - shown
    if hidden > 0:
        st.caption(f"Et {plural(hidden, 'autre sortie', 'autres sorties')} cette semaine.")
    st.page_link(common.PAGE_CALENDAR, label="Ouvrir le calendrier",
                 icon=":material/arrow_forward:")


def show_recent(recent: list[home.RecentWatch], today: date) -> None:
    st.subheader(":material/history: Derniers visionnages")
    if not recent:
        st.caption("Rien de vu récemment (les dates d'import ne comptent pas).")
    for watch in recent:
        page = common.PAGE_SHOW if watch.kind == SHOW else common.PAGE_MOVIE
        label = md(watch.title) + (f" · {watch.code}" if watch.code else "")
        link_col, when_col = st.columns([3, 2], vertical_alignment="center")
        link_col.page_link(page, label=label, query_params={"id": watch.ref_id})
        when_col.caption(progress.watched_ago(watch.when, today))


def show_movies(movies: list[QueueItem], total: int) -> None:
    st.subheader(f":material/movie: Films à voir :gray[({total})]")
    if not movies:
        st.caption("Aucun film sorti dans ta liste « En cours » ou « À voir ».")
        return
    st.caption("Les derniers ajoutés à ta bibliothèque d'abord.")
    cols = st.columns(GRID)
    for col, movie in zip(cols, movies):
        with col:
            common.show_poster(movie.poster_path, poster_url=movie.poster_url)
            if st.button(f"**{md(movie.title)}**", key=f"home_movie_{movie.ref_id}",
                         type="tertiary"):
                common.open_movie(movie.ref_id)
            details = [str(movie.release_date.year)] if movie.release_date else []
            if movie.runtime:
                details.append(common.fmt_runtime(movie.runtime))
            st.caption(" · ".join(details))
            st.button("Vu", key=f"home_movie_seen_{movie.ref_id}", icon=":material/check:",
                      on_click=mark_seen, args=(movie,))


def show_suggestions() -> None:
    st.subheader(":material/recommend: Suggestions pour toi")
    connected, tmdb_message = common.tmdb_status()
    if not connected:
        st.info(f"{tmdb_message} Les suggestions ont besoin de TMDB.")
        return
    result = suggestions_cache.load()
    if result.errors:
        st.caption(":orange[Suggestions incomplètes (TMDB a mal répondu) : détail sur la page "
                   "Suggestions.]")
    hidden = suggestions_cache.hidden()
    with common.db() as session:
        known = {kind: suggestions.excluded_ids(session, kind) for kind in (SHOW, MOVIE)}
    # Ajoutés depuis le calcul (ici ou ailleurs) : plus des suggestions.
    items = [s for s in result.items
             if (s.kind, s.tmdb_id) not in hidden and s.tmdb_id not in known[s.kind]]
    if not items:
        st.caption("Aucune suggestion pour l'instant : ajoute et note des titres.")
        return
    cols = st.columns(GRID)
    for col, item in zip(cols, items[:SUGGESTIONS_SHOWN]):
        with col:
            if item.poster_url:
                st.image(item.poster_url, width="stretch")
            else:
                common.show_poster(None)
            title = md(item.title) + (f" ({item.year})" if item.year else "")
            st.page_link(common.PAGE_PREVIEW, label=f"**{title}**",
                         query_params=common.preview_params(item.kind, item.tmdb_id),
                         help="Voir la fiche détaillée")
            kind_badge = ":blue-badge[Série]" if item.kind == SHOW else ":orange-badge[Film]"
            st.markdown(f"{kind_badge} :green-badge[{item.match} % pour toi]")
            if item.streaming:
                st.caption(md(", ".join(item.streaming)))
    st.page_link(common.PAGE_SUGGESTIONS, label="Toutes les suggestions et leurs filtres",
                 icon=":material/arrow_forward:")


# --- Page ----------------------------------------------------------------------

today = datetime.now(LOCAL_TZ).date()
st.title("Accueil")
st.caption(f"{stats.WEEKDAYS[today.weekday()]} {today.day} {MONTHS_FR[today.month - 1]} "
           f"{today.year}")

with common.db() as session:
    empty = session.exec(select(LibraryItem.id)).first() is None
    to_check = progress.shows_to_check(session)
if empty:
    st.info("Ta bibliothèque est vide : ajoute une série ou un film, ou importe ton historique "
            "Bingers (Paramètres).")
    col_add, col_settings = st.columns(2)
    col_add.page_link(common.PAGE_ADD, label="Ajouter une série ou un film", icon=":material/add:")
    col_settings.page_link(common.PAGE_SETTINGS, label="Importer depuis Bingers",
                           icon=":material/upload:")
    st.stop()

if to_check:
    text_col, link_col = st.columns([4, 1], vertical_alignment="center")
    text_col.caption(f"{plural(to_check, 'série')} à vérifier (correspondance TVMaze ou "
                     "visionnages en attente).")
    link_col.page_link(common.PAGE_SETTINGS, label="Ouvrir les paramètres")
show_last_action()

with common.db() as session:
    show_metrics(session, today)
    next_up, next_total = home.continue_watching(session, today=today)
    coming = home.upcoming(session, today=today)
    recent = home.recent_watches(session)
    movies, movies_total = home.movies_to_watch(session, today=today)

st.markdown(card_css(), unsafe_allow_html=True)
main_col, side_col = st.columns([3, 2], gap="large")
with main_col:
    st.subheader(f":material/play_circle: À suivre :gray[({next_total})]")
    if next_up:
        st.caption("Le prochain épisode de chaque série, celles regardées récemment d'abord.")
        for entry in next_up:
            show_next(entry, today)
    else:
        st.caption("Aucun épisode sorti à voir dans tes séries « En cours » ou « À voir ».")
    st.page_link(common.PAGE_QUEUE, label="Tout voir dans À regarder",
                 icon=":material/arrow_forward:")
with side_col:
    show_upcoming(coming, today)
    show_recent(recent, today)

show_movies(movies, movies_total)
show_suggestions()   # en dernier : le premier calcul peut prendre une à deux minutes
