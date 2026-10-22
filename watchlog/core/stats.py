"""Statistiques : visionnages par mois, temps passé, séries terminées, meilleures notes.

Remarque : les dates de visionnage importées de Bingers sont parfois des dates
d'import (beaucoup d'épisodes le même jour). C'est normal, on les garde telles quelles.
"""

import calendar
import math
from datetime import date, datetime, timedelta

import pandas as pd
from sqlmodel import Session, func, select

from watchlog.core.models import (COMPLETED, EPISODE, MOVIE, SHOW, WATCHING, Episode, LibraryItem,
                                  Movie, Rating, Show, Watch)
from watchlog.core.queue import (DEFAULT_STATUSES, LOCAL_TZ, RELEASED, build_queue, episode_code,
                                 local_date)


def _month(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def last_months(months: int, today: date) -> list[str]:
    """Les `months` derniers mois, du plus ancien au mois en cours : ["2025-11", ..., "2026-10"]."""
    year, month = today.year, today.month
    result = []
    for _ in range(months):
        result.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(result))


def watched_per_month(session: Session, months: int = 12, today: date | None = None) -> pd.DataFrame:
    """Épisodes et films vus par mois (date du premier visionnage, heure française).

    Colonnes : mois ("2026-10"), episodes, films. Un mois sans visionnage vaut 0.
    """
    today = today or datetime.now(LOCAL_TZ).date()
    counts = {m: {"episodes": 0, "films": 0} for m in last_months(months, today)}
    for kind, watched_at, last_watched_at in session.exec(
            select(Watch.kind, Watch.watched_at, Watch.last_watched_at)):
        when = watched_at or last_watched_at
        if when is None:
            continue
        month = _month(local_date(when, None))  # UTC -> date française
        if month in counts:
            counts[month]["episodes" if kind == EPISODE else "films"] += 1
    return pd.DataFrame([{"mois": m, **c} for m, c in counts.items()],
                        columns=["mois", "episodes", "films"])


def time_spent(session: Session) -> dict:
    """Temps passé devant l'écran, en minutes : somme de durée × nombre de visionnages.

    Les éléments sans durée connue ne sont pas comptés dans le temps, mais à part.
    """
    result = {"episode_minutes": 0, "movie_minutes": 0, "total_minutes": 0,
              "episodes_without_runtime": 0, "movies_without_runtime": 0}
    episodes = session.exec(select(Episode.runtime, Watch.plays)
                            .join(Watch, (Watch.kind == EPISODE) & (Watch.ref_id == Episode.id)))
    for runtime, plays in episodes:
        if runtime:
            result["episode_minutes"] += runtime * (plays or 1)
        else:
            result["episodes_without_runtime"] += 1
    movies = session.exec(select(Movie.runtime, Watch.plays)
                          .join(Watch, (Watch.kind == MOVIE) & (Watch.ref_id == Movie.id)))
    for runtime, plays in movies:
        if runtime:
            result["movie_minutes"] += runtime * (plays or 1)
        else:
            result["movies_without_runtime"] += 1
    result["total_minutes"] = result["episode_minutes"] + result["movie_minutes"]
    return result


def format_duration(minutes: int) -> str:
    """312 h 40 min (ou 40 min si moins d'une heure)."""
    hours, rest = divmod(int(minutes), 60)
    if hours == 0:
        return f"{rest} min"
    return f"{hours:,} h {rest:02d} min".replace(",", " ")


def completed_shows(session: Session) -> list[dict]:
    """Séries au statut « Terminé », par ordre alphabétique.

    Chaque élément : {"show_id", "title", "episodes" (vus), "last_watched" (date ou None)}.
    """
    query = (select(Show)
             .join(LibraryItem, (LibraryItem.kind == SHOW) & (LibraryItem.ref_id == Show.id))
             .where(LibraryItem.status == COMPLETED))
    result = []
    for show in session.exec(query):
        count, last = session.exec(
            select(func.count(Watch.id), func.max(Watch.last_watched_at))
            .join(Episode, (Watch.kind == EPISODE) & (Watch.ref_id == Episode.id))
            .where(Episode.show_id == show.id)).one()
        result.append({"show_id": show.id, "title": show.title, "episodes": count,
                       "last_watched": local_date(last, None) if last else None})
    return sorted(result, key=lambda row: row["title"].lower())


def rating_title(session: Session, kind: str, ref_id: int) -> str | None:
    """Titre lisible d'un élément noté : série, film, ou « Série · S01E02 »."""
    if kind == SHOW:
        show = session.get(Show, ref_id)
        return show.title if show else None
    if kind == MOVIE:
        movie = session.get(Movie, ref_id)
        return movie.title if movie else None
    episode = session.get(Episode, ref_id)
    if episode is None:
        return None
    show = session.get(Show, episode.show_id)
    return f"{show.title if show else '?'} · {episode_code(episode.season, episode.number)}"


KIND_LABELS = {SHOW: "Série", EPISODE: "Épisode", MOVIE: "Film"}


def top_ratings(session: Session, limit: int = 10) -> list[dict]:
    """Meilleures notes (note non vide), de la plus haute à la plus basse.

    Chaque élément : {"title", "kind" (libellé), "rating"}.
    """
    rows = []
    for rating in session.exec(select(Rating).where(Rating.rating != None)):  # noqa: E711
        title = rating_title(session, rating.kind, rating.ref_id)
        if title is not None:
            rows.append({"title": title, "kind": KIND_LABELS.get(rating.kind, rating.kind),
                         "rating": rating.rating})
    rows.sort(key=lambda row: (-row["rating"], row["title"].lower()))
    return rows[:limit]


def global_counts(session: Session) -> dict:
    """Chiffres clés : séries et films dans la bibliothèque, épisodes et films vus."""
    def count(query) -> int:
        return session.exec(query).one()

    return {
        "shows": count(select(func.count(LibraryItem.id)).where(LibraryItem.kind == SHOW)),
        "movies": count(select(func.count(LibraryItem.id)).where(LibraryItem.kind == MOVIE)),
        "completed_shows": count(select(func.count(LibraryItem.id))
                                 .where(LibraryItem.kind == SHOW, LibraryItem.status == COMPLETED)),
        "episodes_watched": count(select(func.count(Watch.id)).where(Watch.kind == EPISODE)),
        "movies_watched": count(select(func.count(Watch.id)).where(Watch.kind == MOVIE)),
        "ratings": count(select(func.count(Rating.id)).where(Rating.rating != None)),  # noqa: E711
    }


# --- Tableau de tous les visionnages (base des statistiques détaillées) --------------

WEEKDAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def _watch_day(watch: Watch) -> date | None:
    when = watch.watched_at or watch.last_watched_at
    return local_date(when, None) if when else None


def watches_frame(session: Session) -> pd.DataFrame:
    """Un visionnage par ligne (épisodes et films).

    Colonnes : kind (episode / movie), ref_id, show_id, title (série ou film), day (date
    française du premier visionnage ou None), plays, runtime, release_year (films),
    genres (texte « Drame, Comédie »), minutes (durée × visionnages, 0 si inconnue).
    """
    rows = []
    episodes = session.exec(
        select(Watch, Episode.runtime, Show.id, Show.title, Show.genres)
        .join(Episode, (Watch.kind == EPISODE) & (Watch.ref_id == Episode.id))
        .join(Show, Show.id == Episode.show_id))
    for watch, runtime, show_id, title, genres in episodes:
        rows.append({"kind": EPISODE, "ref_id": watch.ref_id, "show_id": show_id, "title": title,
                     "day": _watch_day(watch), "plays": watch.plays or 1, "runtime": runtime,
                     "release_year": None, "genres": genres})
    movies = session.exec(select(Watch, Movie)
                          .join(Movie, (Watch.kind == MOVIE) & (Watch.ref_id == Movie.id)))
    for watch, movie in movies:
        released = movie.release_date_original or movie.release_date_fr
        rows.append({"kind": MOVIE, "ref_id": movie.id, "show_id": None, "title": movie.title,
                     "day": _watch_day(watch), "plays": watch.plays or 1, "runtime": movie.runtime,
                     "release_year": released.year if released else movie.year,
                     "genres": movie.genres})
    frame = pd.DataFrame(rows, columns=["kind", "ref_id", "show_id", "title", "day", "plays",
                                        "runtime", "release_year", "genres"])
    frame["minutes"] = (pd.to_numeric(frame["runtime"]).fillna(0).astype(int)
                        * pd.to_numeric(frame["plays"]).fillna(1).astype(int))
    return frame


def _days(frame: pd.DataFrame) -> list[date]:
    return [d for d in frame["day"] if isinstance(d, date)]


def per_year(frame: pd.DataFrame) -> pd.DataFrame:
    """Épisodes et films vus par année (premier visionnage). Colonnes : annee, episodes, films."""
    dated = frame[frame["day"].map(lambda d: isinstance(d, date))]
    if dated.empty:
        return pd.DataFrame(columns=["annee", "episodes", "films"])
    years = dated["day"].map(lambda d: d.year)
    result = []
    for year in range(int(years.min()), int(years.max()) + 1):
        in_year = dated[years == year]
        result.append({"annee": year, "episodes": int((in_year["kind"] == EPISODE).sum()),
                       "films": int((in_year["kind"] == MOVIE).sum())})
    return pd.DataFrame(result)


def count_per_month(frame: pd.DataFrame, months: int = 12,
                    today: date | None = None) -> pd.DataFrame:
    """Épisodes et films vus par mois, à partir du tableau des visionnages.

    Colonnes : mois, episodes, films (comme watched_per_month, mais filtrable).
    """
    today = today or datetime.now(LOCAL_TZ).date()
    totals = {m: {"episodes": 0, "films": 0} for m in last_months(months, today)}
    for kind, day in frame[["kind", "day"]].itertuples(index=False):
        if isinstance(day, date) and _month(day) in totals:
            totals[_month(day)]["episodes" if kind == EPISODE else "films"] += 1
    return pd.DataFrame([{"mois": m, **v} for m, v in totals.items()],
                        columns=["mois", "episodes", "films"])


def hours_per_month(frame: pd.DataFrame, months: int = 12,
                    today: date | None = None) -> pd.DataFrame:
    """Heures passées par mois. Colonnes : mois, episodes, films (en heures)."""
    today = today or datetime.now(LOCAL_TZ).date()
    totals = {m: {"episodes": 0.0, "films": 0.0} for m in last_months(months, today)}
    for kind, day, minutes in frame[["kind", "day", "minutes"]].itertuples(index=False):
        if isinstance(day, date) and _month(day) in totals:
            totals[_month(day)]["episodes" if kind == EPISODE else "films"] += minutes / 60
    return pd.DataFrame([{"mois": m, "episodes": round(v["episodes"], 1),
                          "films": round(v["films"], 1)} for m, v in totals.items()],
                        columns=["mois", "episodes", "films"])


def per_weekday(frame: pd.DataFrame) -> pd.DataFrame:
    """Visionnages par jour de la semaine. Colonnes : jour (0 = lundi), libelle, visionnages."""
    counts = [0] * 7
    for day in _days(frame):
        counts[day.weekday()] += 1
    return pd.DataFrame({"jour": range(7), "libelle": WEEKDAYS, "visionnages": counts})


def daily_counts(frame: pd.DataFrame, days: int = 365, today: date | None = None) -> pd.DataFrame:
    """Visionnages par jour sur les `days` derniers jours (jours vides compris).

    Colonnes : day, visionnages.
    """
    today = today or datetime.now(LOCAL_TZ).date()
    start = today - timedelta(days=days - 1)
    counts: dict[date, int] = {}
    for day in _days(frame):
        counts[day] = counts.get(day, 0) + 1
    return pd.DataFrame([{"day": start + timedelta(days=n),
                          "visionnages": counts.get(start + timedelta(days=n), 0)}
                         for n in range(days)], columns=["day", "visionnages"])


def records(frame: pd.DataFrame) -> dict:
    """Records : jour le plus chargé, plus longue série de jours consécutifs, mois record.

    {"best_day": (date, nombre) | None, "streak": (jours, début, fin) | None,
     "best_month": ("2026-10", nombre) | None}
    """
    days = _days(frame)
    if not days:
        return {"best_day": None, "streak": None, "best_month": None}
    by_day: dict[date, int] = {}
    by_month: dict[str, int] = {}
    for day in days:
        by_day[day] = by_day.get(day, 0) + 1
        by_month[_month(day)] = by_month.get(_month(day), 0) + 1
    best_day = max(by_day.items(), key=lambda kv: (kv[1], kv[0]))
    best_month = max(by_month.items(), key=lambda kv: (kv[1], kv[0]))
    unique = sorted(by_day)
    best = (1, unique[0], unique[0])
    start = prev = unique[0]
    for day in unique[1:]:
        if day - prev != timedelta(days=1):
            start = day
        prev = day
        if (day - start).days + 1 > best[0]:
            best = ((day - start).days + 1, start, day)
    return {"best_day": best_day, "streak": best, "best_month": best_month}


def top_shows_by_time(frame: pd.DataFrame, limit: int = 10) -> pd.DataFrame:
    """Séries où tu as passé le plus de temps. Colonnes : title, heures, episodes."""
    episodes = frame[frame["kind"] == EPISODE]
    if episodes.empty:
        return pd.DataFrame(columns=["title", "heures", "episodes"])
    grouped = (episodes.groupby(["show_id", "title"])
               .agg(minutes=("minutes", "sum"), episodes=("ref_id", "count")).reset_index())
    grouped["heures"] = (grouped["minutes"] / 60).round(1)
    grouped = grouped.sort_values(["minutes", "episodes"], ascending=False).head(limit)
    return grouped[["title", "heures", "episodes"]].reset_index(drop=True)


def genre_counts(frame: pd.DataFrame, kind: str, limit: int = 10) -> pd.DataFrame:
    """Genres les plus regardés : un par film vu (kind=movie) ou par série regardée (episode).

    Colonnes : genre, titres.
    """
    rows = frame[frame["kind"] == kind]
    if kind == EPISODE:
        rows = rows.drop_duplicates("show_id")
    counts: dict[str, int] = {}
    for genres in rows["genres"]:
        # Genres inconnus : pandas met NaN (un float « vrai ») là où la base a None.
        if not isinstance(genres, str):
            continue
        for genre in [g.strip() for g in genres.split(",") if g.strip()]:
            counts[genre] = counts.get(genre, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return pd.DataFrame(ordered, columns=["genre", "titres"])


def movie_decades(frame: pd.DataFrame) -> pd.DataFrame:
    """Films vus par décennie de sortie. Colonnes : decennie (1990), libelle (« 1990s »), films."""
    years = [int(y) for y in frame.loc[frame["kind"] == MOVIE, "release_year"] if pd.notna(y)]
    if not years:
        return pd.DataFrame(columns=["decennie", "libelle", "films"])
    counts: dict[int, int] = {}
    for year in years:
        counts[year // 10 * 10] = counts.get(year // 10 * 10, 0) + 1
    decades = sorted(counts)
    return pd.DataFrame({"decennie": decades, "libelle": [f"{d}s" for d in decades],
                         "films": [counts[d] for d in decades]})


def movie_runtime_stats(frame: pd.DataFrame) -> dict | None:
    """Durées des films vus : {"average", "longest": (titre, min), "shortest": (titre, min)}."""
    movies = frame[frame["kind"] == MOVIE]
    known = [(title, int(rt)) for title, rt in movies[["title", "runtime"]].itertuples(index=False)
             if pd.notna(rt) and rt > 0]
    if not known:
        return None
    return {"average": round(sum(rt for _t, rt in known) / len(known)),
            "longest": max(known, key=lambda tr: tr[1]),
            "shortest": min(known, key=lambda tr: tr[1])}


def watched_movies(session: Session) -> list[dict]:
    """Films vus, du plus récent au plus ancien (date inconnue à la fin).

    Chaque élément : {"movie_id", "title", "watched" (date ou None), "plays", "rating",
    "runtime", "year", "genres"}.
    """
    ratings = {r.ref_id: r.rating for r in session.exec(
        select(Rating).where(Rating.kind == MOVIE, Rating.rating != None))}  # noqa: E711
    rows = []
    for watch, movie in session.exec(select(Watch, Movie).join(
            Movie, (Watch.kind == MOVIE) & (Watch.ref_id == Movie.id))):
        released = movie.release_date_original or movie.release_date_fr
        rows.append({"movie_id": movie.id, "title": movie.title, "watched": _watch_day(watch),
                     "plays": watch.plays or 1, "rating": ratings.get(movie.id),
                     "runtime": movie.runtime,
                     "year": released.year if released else movie.year, "genres": movie.genres})
    rows.sort(key=lambda r: (r["watched"] is None, -(r["watched"] or date.min).toordinal(),
                             r["title"].lower()))
    return rows


def show_progress(session: Session, today: date | None = None) -> pd.DataFrame:
    """Avancement des séries « En cours » : épisodes vus / épisodes déjà diffusés (hors spéciaux).

    Colonnes : show_id, title, vus, diffuses, pourcentage — du plus avancé au moins avancé.
    """
    today = today or datetime.now(LOCAL_TZ).date()
    watched = set(session.exec(select(Watch.ref_id).where(Watch.kind == EPISODE)).all())
    rows = []
    query = (select(Show)
             .join(LibraryItem, (LibraryItem.kind == SHOW) & (LibraryItem.ref_id == Show.id))
             .where(LibraryItem.status == WATCHING))
    for show in session.exec(query):
        episodes = session.exec(select(Episode).where(Episode.show_id == show.id,
                                                      Episode.is_special == False))  # noqa: E712
        aired = [ep for ep in episodes if (local_date(ep.airstamp, ep.airdate) or date.max) <= today]
        if not aired:
            continue
        seen = sum(1 for ep in aired if ep.id in watched)
        rows.append({"show_id": show.id, "title": show.title, "vus": seen,
                     "diffuses": len(aired), "pourcentage": round(100 * seen / len(aired))})
    frame = pd.DataFrame(rows, columns=["show_id", "title", "vus", "diffuses", "pourcentage"])
    return frame.sort_values(["pourcentage", "title"], ascending=[False, True]).reset_index(drop=True)


def rating_distribution(session: Session) -> pd.DataFrame:
    """Nombre de notes par valeur (1 à 5), tous types confondus. Colonnes : note, nombre."""
    counts = {n: 0 for n in range(1, 6)}
    for value in session.exec(select(Rating.rating).where(Rating.rating != None)):  # noqa: E711
        if value in counts:
            counts[value] += 1
    return pd.DataFrame({"note": list(counts), "nombre": list(counts.values())})


# --- Jours d'import massif ----------------------------------------------------------

BULK_THRESHOLD = 50  # visionnages le même jour au-delà desquels c'est un import, pas un binge


def bulk_days(frame: pd.DataFrame, threshold: int = BULK_THRESHOLD) -> dict[date, int]:
    """Jours « d'import » : un import (Bingers...) date souvent tout l'historique au jour de l'import.

    Renvoie {jour: nombre de visionnages} pour les jours d'au moins `threshold` visionnages.
    """
    counts: dict[date, int] = {}
    for day in _days(frame):
        counts[day] = counts.get(day, 0) + 1
    return {day: n for day, n in sorted(counts.items()) if n >= threshold}


def without_days(frame: pd.DataFrame, days) -> pd.DataFrame:
    """Le tableau sans les visionnages de ces jours (pour les graphiques basés sur les dates)."""
    days = set(days)
    if not days:
        return frame
    return frame[~frame["day"].map(lambda d: d in days)].reset_index(drop=True)


# --- Temps restant pour finir la liste ------------------------------------------------

# Périodes de référence du rythme : libellé -> jours glissants (aujourd'hui compris)
PACE_PERIODS = {"Au rythme de la semaine dernière": 7, "Au rythme du mois dernier": 30,
                "Au rythme de l'année dernière": 365}
# Durée (minutes) d'un élément sans durée connue, quand aucune durée du même type n'est connue
DEFAULT_RUNTIMES = {EPISODE: 45, MOVIE: 110}


def _estimated_minutes(runtimes: list, kind: str) -> tuple[int, int]:
    """Somme des durées ; une durée inconnue vaut la moyenne des durées connues du lot.

    Renvoie (minutes, nombre de durées estimées) : l'estimation doit pouvoir être affichée.
    """
    known = [int(r) for r in runtimes if pd.notna(r) and r > 0]
    missing = len(runtimes) - len(known)
    average = sum(known) / len(known) if known else DEFAULT_RUNTIMES[kind]
    return round(sum(known) + missing * average), missing


def remaining_time(session: Session, statuses=DEFAULT_STATUSES,
                   today: date | None = None) -> dict:
    """Ce qui reste à voir : épisodes (hors spéciaux) et films déjà sortis, pas encore vus.

    Mêmes éléments que « À regarder » filtré sur « Sorti » : une sortie à venir ne compte pas.
    {"episodes", "movies", "episode_minutes", "movie_minutes", "total_minutes", "estimated"}
    """
    items = build_queue(session, statuses=statuses, availabilities=(RELEASED,), today=today)
    result = {"estimated": 0}
    for kind, name in ((EPISODE, "episode"), (MOVIE, "movie")):
        runtimes = [item.runtime for item in items if item.kind == kind]
        minutes, estimated = _estimated_minutes(runtimes, kind)
        result[f"{name}s"] = len(runtimes)
        result[f"{name}_minutes"] = minutes
        result["estimated"] += estimated
    result["total_minutes"] = result["episode_minutes"] + result["movie_minutes"]
    return result


def watch_pace(frame: pd.DataFrame, days: int, bulk=(), today: date | None = None) -> dict:
    """Rythme de visionnage des `days` derniers jours (aujourd'hui compris), en minutes par jour.

    Un revisionnage compte pour une durée : il n'avance pas la liste. Les jours d'import
    (`bulk`) sont retirés, visionnages et jours. La période commence au plus tôt au premier
    jour d'import : avant, l'historique importé est daté du jour de l'import, donc vide, et
    ces jours vides feraient croire à un rythme bien plus lent.
    {"start", "days" (jours comptés), "watches", "minutes", "per_day", "estimated"}
    """
    today = today or datetime.now(LOCAL_TZ).date()
    bulk = set(bulk)
    start = today - timedelta(days=days - 1)
    if bulk:
        start = max(start, min(bulk))
    counted = sum(1 for n in range((today - start).days + 1)
                  if start + timedelta(days=n) not in bulk)
    # astype(bool) : sur un frame vide, le masque serait de type object et pandas le lirait
    # comme une liste de colonnes (frame sans colonne, KeyError plus bas).
    window = frame[frame["day"].map(
        lambda d: isinstance(d, date) and start <= d <= today and d not in bulk).astype(bool)]
    minutes = estimated = 0
    for kind in (EPISODE, MOVIE):
        kind_minutes, kind_estimated = _estimated_minutes(
            list(window.loc[window["kind"] == kind, "runtime"]), kind)
        minutes += kind_minutes
        estimated += kind_estimated
    return {"start": start, "days": counted, "watches": len(window), "minutes": minutes,
            "per_day": minutes / counted if counted else 0.0, "estimated": estimated}


def finish_estimate(remaining_minutes: int, per_day: float,
                    today: date | None = None) -> dict | None:
    """Jours nécessaires pour tout voir à ce rythme, et date de fin. None si le rythme est nul.

    La liste grossit avec les nouvelles sorties : c'est un minimum, pas une promesse.
    """
    if per_day <= 0:
        return None
    today = today or datetime.now(LOCAL_TZ).date()
    days = math.ceil(remaining_minutes / per_day)
    return {"days": days, "date": today + timedelta(days=days)}


def _add_months(day: date, months: int) -> date:
    """Même jour `months` mois plus tard (31 janvier + 1 mois = 28 ou 29 février)."""
    year, month = divmod(day.month - 1 + months, 12)
    year, month = day.year + year, month + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def format_span(start: date, end: date) -> str:
    """Écart exact sur le calendrier : « 3 mois et 25 jours », « 1 an, 2 mois et 5 jours ».

    Mois réels (pas des mois de 30 jours) : la durée tombe pile sur la date de fin affichée.
    """
    months = (end.year - start.year) * 12 + end.month - start.month
    if _add_months(start, months) > end:
        months -= 1
    days = (end - _add_months(start, months)).days
    years, months = divmod(months, 12)
    parts = [f"{years} an{'s' if years > 1 else ''}" if years else "",
             f"{months} mois" if months else "",
             f"{days} jour{'s' if days > 1 else ''}" if days else ""]
    parts = [part for part in parts if part]
    if not parts:
        return "0 jour"
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} et {parts[-1]}"
