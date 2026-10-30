// Statistiques (port de watchlog/core/stats.py, sans pandas) : à partir d'un tableau des
// visionnages, une ligne par épisode ou film vu.

import { addDays, daysBetween, localDate, parisDay, today as todayParis } from "./dates.js";
import { RATING_VALUES, ratingStars } from "./library.js";
import { buildQueue, RELEASED } from "./queue.js";
import {
  COMPLETED, EPISODE, MOVIE, SHOW, TO_WATCH, WATCHING, episodeCode, splitGenres,
} from "./model.js";

export const WEEKDAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
// Visionnages le même jour à partir desquels c'est un import (Bingers), pas un marathon.
export const BULK_THRESHOLD = 50;
// Rythme de référence : libellé -> jours glissants (aujourd'hui compris)
export const PACE_PERIODS = [["Au rythme de la semaine dernière", 7],
  ["Au rythme du mois dernier", 30], ["Au rythme de l'année dernière", 365]];
// Durée d'un élément sans durée connue, quand aucune durée du même type n'est connue
const DEFAULT_RUNTIMES = { [EPISODE]: 45, [MOVIE]: 110 };
const KIND_LABELS = { [SHOW]: "Série", [EPISODE]: "Épisode", [MOVIE]: "Film" };

const monthOf = (day) => day.slice(0, 7);

/** Un visionnage par ligne : {kind, ref, show, title, day, plays, runtime, releaseYear,
 *  genres, minutes}. `day` = jour (Paris) du premier visionnage, ou null. */
export function watchesFrame(store) {
  const rows = [];
  for (const w of store.watches.values()) {
    const when = w.watched_at || w.last_watched_at;
    const day = when ? parisDay(when) : null;
    const plays = w.plays || 1;
    if (w.kind === EPISODE) {
      const ep = store.get("episodes", w.ref);
      const show = ep && store.get("shows", ep.show);
      if (!show) continue;
      rows.push({ kind: EPISODE, ref: w.ref, show: show.key, title: show.title, day, plays,
        runtime: ep.runtime || null, releaseYear: null, genres: show.genres || null,
        minutes: (ep.runtime || 0) * plays });
    } else if (w.kind === MOVIE) {
      const movie = store.get("movies", w.ref);
      if (!movie) continue;
      const released = movie.release_date_original || movie.release_date_fr;
      rows.push({ kind: MOVIE, ref: w.ref, show: null, title: movie.title, day, plays,
        runtime: movie.runtime || null,
        releaseYear: released ? Number(released.slice(0, 4)) : movie.year || null,
        genres: movie.genres || null, minutes: (movie.runtime || 0) * plays });
    }
  }
  return rows;
}

/** Jours « d'import » : {jour: nombre} pour les jours d'au moins `threshold` visionnages. */
export function bulkDays(frame, threshold = BULK_THRESHOLD) {
  const counts = new Map();
  for (const r of frame) if (r.day) counts.set(r.day, (counts.get(r.day) || 0) + 1);
  return new Map([...counts].filter(([, n]) => n >= threshold).sort());
}

export function withoutDays(frame, days) {
  const skip = new Set(days);
  return skip.size ? frame.filter((r) => !skip.has(r.day)) : frame;
}

export function globalCounts(store) {
  const lib = [...store.library.values()];
  const watches = [...store.watches.values()];
  return {
    shows: lib.filter((l) => l.kind === SHOW).length,
    movies: lib.filter((l) => l.kind === MOVIE).length,
    completedShows: lib.filter((l) => l.kind === SHOW && l.status === COMPLETED).length,
    episodesWatched: watches.filter((w) => w.kind === EPISODE).length,
    moviesWatched: watches.filter((w) => w.kind === MOVIE).length,
    ratings: [...store.ratings.values()].filter((r) => r.rating != null).length,
  };
}

/** Temps passé : durée × visionnages ; les éléments sans durée sont comptés à part. */
export function timeSpent(frame) {
  const out = { episodeMinutes: 0, movieMinutes: 0, episodesWithoutRuntime: 0,
    moviesWithoutRuntime: 0 };
  for (const r of frame) {
    const isEp = r.kind === EPISODE;
    if (r.runtime) out[isEp ? "episodeMinutes" : "movieMinutes"] += r.minutes;
    else out[isEp ? "episodesWithoutRuntime" : "moviesWithoutRuntime"] += 1;
  }
  out.totalMinutes = out.episodeMinutes + out.movieMinutes;
  return out;
}

/** « 312 h 40 min » (ou « 40 min »). */
export function formatDuration(minutes) {
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (!h) return `${m} min`;
  return `${h.toLocaleString("fr-FR")} h ${String(m).padStart(2, "0")} min`;
}

/** Les `months` derniers mois, du plus ancien au mois en cours : ["2025-11", …, "2026-10"]. */
export function lastMonths(months, today = todayParis()) {
  let [year, month] = today.split("-").map(Number);
  const out = [];
  for (let i = 0; i < months; i += 1) {
    out.push(`${year}-${String(month).padStart(2, "0")}`);
    month -= 1;
    if (!month) { year -= 1; month = 12; }
  }
  return out.reverse();
}

/** Par mois : [{month, episodes, movies}] (nombre ou heures selon `measure`). */
export function perMonth(frame, months = 12, today = todayParis(), measure = "count") {
  const totals = new Map(lastMonths(months, today).map((m) => [m, { month: m, episodes: 0, movies: 0 }]));
  for (const r of frame) {
    const row = r.day && totals.get(monthOf(r.day));
    if (!row) continue;
    row[r.kind === EPISODE ? "episodes" : "movies"] += measure === "hours" ? r.minutes / 60 : 1;
  }
  return [...totals.values()].map((r) => (measure === "hours"
    ? { ...r, episodes: Math.round(r.episodes * 10) / 10, movies: Math.round(r.movies * 10) / 10 } : r));
}

export function perYear(frame) {
  const dated = frame.filter((r) => r.day);
  if (!dated.length) return [];
  const years = dated.map((r) => Number(r.day.slice(0, 4)));
  const out = [];
  for (let y = Math.min(...years); y <= Math.max(...years); y += 1) {
    const inYear = dated.filter((r) => Number(r.day.slice(0, 4)) === y);
    out.push({ year: y, episodes: inYear.filter((r) => r.kind === EPISODE).length,
      movies: inYear.filter((r) => r.kind === MOVIE).length });
  }
  return out;
}

export function perWeekday(frame) {
  const counts = [0, 0, 0, 0, 0, 0, 0];
  for (const r of frame) {
    if (r.day) counts[(new Date(`${r.day}T12:00:00Z`).getUTCDay() + 6) % 7] += 1;
  }
  return WEEKDAYS.map((label, i) => ({ label, count: counts[i] }));
}

/** Visionnages par jour sur les `days` derniers jours (jours vides compris). */
export function dailyCounts(frame, days = 365, today = todayParis()) {
  const counts = new Map();
  for (const r of frame) if (r.day) counts.set(r.day, (counts.get(r.day) || 0) + 1);
  const start = addDays(today, -(days - 1));
  return Array.from({ length: days }, (_, i) => {
    const day = addDays(start, i);
    return { day, count: counts.get(day) || 0 };
  });
}

/** Jour le plus chargé, plus longue suite de jours consécutifs, mois record. */
export function records(frame) {
  const byDay = new Map();
  const byMonth = new Map();
  for (const r of frame) {
    if (!r.day) continue;
    byDay.set(r.day, (byDay.get(r.day) || 0) + 1);
    byMonth.set(monthOf(r.day), (byMonth.get(monthOf(r.day)) || 0) + 1);
  }
  if (!byDay.size) return { bestDay: null, streak: null, bestMonth: null };
  const best = (map) => [...map].reduce((a, b) => (b[1] > a[1] || (b[1] === a[1] && b[0] > a[0]) ? b : a));
  const days = [...byDay.keys()].sort();
  let streak = [1, days[0], days[0]];
  let start = days[0];
  for (let i = 1; i < days.length; i += 1) {
    if (daysBetween(days[i - 1], days[i]) !== 1) start = days[i];
    const length = daysBetween(start, days[i]) + 1;
    if (length > streak[0]) streak = [length, start, days[i]];
  }
  return { bestDay: best(byDay), streak, bestMonth: best(byMonth) };
}

export function topShowsByTime(frame, limit = 10) {
  const shows = new Map();
  for (const r of frame) {
    if (r.kind !== EPISODE) continue;
    const s = shows.get(r.show) || { show: r.show, title: r.title, minutes: 0, episodes: 0 };
    s.minutes += r.minutes;
    s.episodes += 1;
    shows.set(r.show, s);
  }
  return [...shows.values()].sort((a, b) => (b.minutes - a.minutes) || (b.episodes - a.episodes))
    .slice(0, limit).map((s) => ({ ...s, hours: Math.round(s.minutes / 6) / 10 }));
}

/** Genres : un par film vu (kind=movie) ou par série regardée (kind=episode). */
export function genreCounts(frame, kind, limit = 10) {
  const seen = new Set();
  const counts = new Map();
  for (const r of frame) {
    if (r.kind !== kind) continue;
    if (kind === EPISODE) {
      if (seen.has(r.show)) continue;
      seen.add(r.show);
    }
    for (const g of splitGenres(r.genres)) counts.set(g, (counts.get(g) || 0) + 1);
  }
  return [...counts].sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0], "fr"))
    .slice(0, limit).map(([genre, count]) => ({ genre, count }));
}

export function movieDecades(frame) {
  const counts = new Map();
  for (const r of frame) {
    if (r.kind !== MOVIE || !r.releaseYear) continue;
    const decade = Math.floor(r.releaseYear / 10) * 10;
    counts.set(decade, (counts.get(decade) || 0) + 1);
  }
  return [...counts].sort((a, b) => a[0] - b[0]).map(([decade, count]) => ({
    decade, label: `${decade}s`, count,
  }));
}

export function movieRuntimeStats(frame) {
  const known = frame.filter((r) => r.kind === MOVIE && r.runtime > 0);
  if (!known.length) return null;
  const sorted = [...known].sort((a, b) => a.runtime - b.runtime);
  return {
    average: Math.round(known.reduce((s, r) => s + r.runtime, 0) / known.length),
    shortest: [sorted[0].title, sorted[0].runtime],
    longest: [sorted.at(-1).title, sorted.at(-1).runtime],
  };
}

/** Séries « En cours » : épisodes vus / diffusés (hors spéciaux), du plus avancé au moins. */
export function showProgress(store, today = todayParis()) {
  const rows = [];
  for (const lib of store.library.values()) {
    if (lib.kind !== SHOW || lib.status !== WATCHING) continue;
    const show = store.get("shows", lib.ref);
    if (!show) continue;
    const aired = store.episodesOf(show.key).filter((ep) => !ep.is_special
      && (localDate(ep.airstamp, ep.airdate) || "9999") <= today);
    if (!aired.length) continue;
    const seen = aired.filter((ep) => store.get("watches", `${EPISODE}:${ep.key}`)).length;
    rows.push({ show: show.key, title: show.title, seen, aired: aired.length,
      percent: Math.round((100 * seen) / aired.length) });
  }
  return rows.sort((a, b) => (b.percent - a.percent) || a.title.localeCompare(b.title, "fr"));
}

/** Nombre de notes par valeur (1 à 5). Une demi-note (4,5...) n'a sa ligne que si elle sert :
 *  sans demi-note, cinq barres. */
export function ratingDistribution(store) {
  const counts = new Map(RATING_VALUES.map((n) => [n, 0]));
  for (const r of store.ratings.values()) {
    if (counts.has(r.rating)) counts.set(r.rating, counts.get(r.rating) + 1);
  }
  return [...counts].filter(([rating, count]) => count || Number.isInteger(rating))
    .map(([rating, count]) => ({ rating, label: ratingStars(rating), count }));
}

function ratingTitle(store, rating) {
  if (rating.kind === SHOW) return store.get("shows", rating.ref)?.title ?? null;
  if (rating.kind === MOVIE) return store.get("movies", rating.ref)?.title ?? null;
  const ep = store.get("episodes", rating.ref);
  if (!ep) return null;
  return `${store.get("shows", ep.show)?.title ?? "?"} · ${episodeCode(ep.season, ep.number)}`;
}

export function topRatings(store, limit = 10) {
  return [...store.ratings.values()].filter((r) => r.rating != null)
    .map((r) => ({ title: ratingTitle(store, r), kind: KIND_LABELS[r.kind], rating: r.rating }))
    .filter((r) => r.title)
    .sort((a, b) => (b.rating - a.rating) || a.title.localeCompare(b.title, "fr"))
    .slice(0, limit);
}

/** Somme des durées ; une durée inconnue vaut la moyenne des durées connues du lot. */
export function estimatedMinutes(runtimes, kind) {
  const known = runtimes.filter((r) => r > 0);
  const missing = runtimes.length - known.length;
  const average = known.length ? known.reduce((s, r) => s + r, 0) / known.length : DEFAULT_RUNTIMES[kind];
  return [Math.round(known.reduce((s, r) => s + r, 0) + missing * average), missing];
}

/** Ce qui reste à voir (épisodes hors spéciaux et films déjà sortis, non vus), pour les
 *  statuts donnés (« En cours » et « À voir », plus « Plus tard » si demandé). */
export function remainingTime(store, today = todayParis(), statuses = [WATCHING, TO_WATCH]) {
  const items = buildQueue(store, { statuses, availabilities: [RELEASED], today });
  const out = { estimated: 0 };
  for (const [kind, name] of [[EPISODE, "episode"], [MOVIE, "movie"]]) {
    const runtimes = items.filter((i) => i.kind === kind).map((i) => i.runtime);
    const [minutes, estimated] = estimatedMinutes(runtimes, kind);
    out[`${name}s`] = runtimes.length;
    out[`${name}Minutes`] = minutes;
    out.estimated += estimated;
  }
  out.totalMinutes = out.episodeMinutes + out.movieMinutes;
  return out;
}

/** Rythme des `days` derniers jours, en minutes par jour, sans les jours d'import ; la
 *  période commence au plus tôt au premier jour d'import (avant, l'historique est vide). */
export function watchPace(frame, days, bulk = [], today = todayParis()) {
  const skip = new Set(bulk);
  let start = addDays(today, -(days - 1));
  if (skip.size) {
    const first = [...skip].sort()[0];
    if (first > start) start = first;
  }
  let counted = 0;
  for (let d = start; d <= today; d = addDays(d, 1)) if (!skip.has(d)) counted += 1;
  const window = frame.filter((r) => r.day && start <= r.day && r.day <= today && !skip.has(r.day));
  let minutes = 0;
  let estimated = 0;
  for (const kind of [EPISODE, MOVIE]) {
    const [m, e] = estimatedMinutes(window.filter((r) => r.kind === kind).map((r) => r.runtime), kind);
    minutes += m;
    estimated += e;
  }
  return { start, days: counted, watches: window.length, minutes, perDay: counted ? minutes / counted : 0,
    estimated };
}

/** Jours pour tout voir à ce rythme (un minimum : la liste grossit). null si rythme nul. */
export function finishEstimate(remainingMinutes, perDay, today = todayParis()) {
  if (perDay <= 0) return null;
  const days = Math.ceil(remainingMinutes / perDay);
  return { days, date: addDays(today, days) };
}

/** Temps passé sur une série et temps restant pour la finir (stats.show_time). Passé :
 *  épisodes vus (spéciaux compris) × visionnages ; restant : épisodes diffusés pas vus, hors
 *  spéciaux. Une durée inconnue vaut la moyenne des durées connues de la série. */
export function showTime(store, showKey, today = todayParis()) {
  const episodes = store.episodesOf(showKey);
  const known = episodes.map((ep) => ep.runtime).filter((r) => r > 0);
  const average = known.length ? known.reduce((a, b) => a + b, 0) / known.length
    : DEFAULT_RUNTIMES[EPISODE];
  let spent = 0;
  let remaining = 0;
  const out = { remainingEpisodes: 0, upcomingEpisodes: 0, estimated: 0 };
  for (const ep of episodes) {
    const runtime = ep.runtime || average;
    const w = store.get("watches", `${EPISODE}:${ep.key}`);
    if (w) {
      spent += runtime * (w.plays || 1);
    } else if (ep.is_special) {
      continue;
    } else {
      const day = localDate(ep.airstamp, ep.airdate);
      if (!day || day > today) {   // à venir ou sans date : pas encore regardable
        out.upcomingEpisodes += 1;
        continue;
      }
      remaining += runtime;
      out.remainingEpisodes += 1;
    }
    if (!ep.runtime) out.estimated += 1;
  }
  return { ...out, spentMinutes: Math.round(spent), remainingMinutes: Math.round(remaining) };
}

/** Même jour `months` mois plus tard (31 janvier + 1 mois = 28 ou 29 février). */
function addMonths(day, months) {
  const [y, m, d] = day.split("-").map(Number);
  const index = m - 1 + months;
  const year = y + Math.floor(index / 12);
  const month = ((index % 12) + 12) % 12 + 1;
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return `${year}-${String(month).padStart(2, "0")}-${String(Math.min(d, last)).padStart(2, "0")}`;
}

/** Écart exact sur le calendrier (stats.format_span) : « 3 mois et 25 jours »,
 *  « 1 an, 2 mois et 5 jours ». Mois réels : la durée tombe pile sur la date de fin. */
export function formatSpan(start, end) {
  const [ys, ms] = start.split("-").map(Number);
  const [ye, me] = end.split("-").map(Number);
  let months = (ye - ys) * 12 + me - ms;
  if (addMonths(start, months) > end) months -= 1;
  const days = daysBetween(addMonths(start, months), end);
  const years = Math.floor(months / 12);
  months %= 12;
  const parts = [years ? `${years} an${years > 1 ? "s" : ""}` : "", months ? `${months} mois` : "",
    days ? `${days} jour${days > 1 ? "s" : ""}` : ""].filter(Boolean);
  if (!parts.length) return "0 jour";
  return parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} et ${parts.at(-1)}`;
}
