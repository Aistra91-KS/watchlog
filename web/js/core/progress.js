// Vue d'ensemble de la bibliothèque : progression de chaque série, état de chaque film
// (port de watchlog/core/progress.py).

import { daysBetween, fmtDate, localDate, parisDay, today as todayParis } from "./dates.js";
import { getWatch } from "./library.js";
import { showActivity } from "./queue.js";
import {
  EPISODE, MATCH_TO_CHECK, MOVIE, SHOW, WATCHING, episodeCode, normalize,
} from "./model.js";

export const SORT_TITLE = "title";
export const SORT_ADDED = "added";
export const SORT_RECENT = "recent";
export const SORT_PAUSED = "paused";
export const SORT_LABELS = {
  [SORT_TITLE]: "Titre", [SORT_ADDED]: "Derniers ajouts", [SORT_RECENT]: "Vu récemment",
  [SORT_PAUSED]: "En pause depuis le plus longtemps",
};

// Un « jour d'import » : au moins autant de visionnages datés du même jour (import Bingers).
export const BULK_DAY_THRESHOLD = 50;
const DAYS_PER_MONTH = 30;
const DAYS_PER_YEAR = 365;

/** Jours (à Paris) qui comptent au moins BULK_DAY_THRESHOLD visionnages : dates d'import. */
export function bulkDays(store) {
  const counts = new Map();
  for (const w of store.watches.values()) {
    const when = w.last_watched_at || w.watched_at;
    if (!when) continue;
    const day = parisDay(when);
    counts.set(day, (counts.get(day) || 0) + 1);
  }
  return new Set([...counts].filter(([, n]) => n >= BULK_DAY_THRESHOLD).map(([d]) => d));
}

function showCounters(store, showKey, today) {
  let total = 0; let remaining = 0; let upcoming = 0; let best = null;
  for (const ep of store.episodesOf(showKey)) {
    if (ep.is_special || ep.number == null) continue;
    total += 1;
    const watched = Boolean(getWatch(store, EPISODE, ep.key));
    if (watched) {
      if (!best || ep.season > best.season || (ep.season === best.season && ep.number > best.number)) {
        best = ep;
      }
      continue;
    }
    const day = localDate(ep.airstamp, ep.airdate);
    if (day && day <= today) remaining += 1;
    else if (day) upcoming += 1;
  }
  return {
    totalRegular: total, remaining, upcoming,
    lastWatchedCode: best ? episodeCode(best.season, best.number) : null,
  };
}

/** Séries et films de la bibliothèque avec leur progression (options de library_overview). */
export function libraryOverview(store, { kind = null, status = null, search = "",
  favoriteOnly = false, sort = SORT_TITLE, today = todayParis() } = {}) {
  let statuses = typeof status === "string" ? [status] : status;
  if (sort === SORT_PAUSED) {
    statuses = statuses ? statuses.filter((s) => s === WATCHING) : [WATCHING];
    if (kind === MOVIE || !statuses.length) return [];
    kind = SHOW;
  }
  const activity = showActivity(store);
  const entries = [];
  for (const lib of store.library.values()) {
    if (kind && lib.kind !== kind) continue;
    if (statuses && !statuses.includes(lib.status)) continue;
    if (favoriteOnly && !lib.favorite) continue;
    if (lib.kind === SHOW) {
      const show = store.get("shows", lib.ref);
      if (!show) continue;
      entries.push({
        kind: SHOW, ref: show.key, title: show.title, originalTitle: show.original_title,
        status: lib.status, favorite: Boolean(lib.favorite), posterUrl: show.poster_url,
        year: show.premiered ? Number(show.premiered.slice(0, 4)) : null,
        addedAt: lib.added_at, lastWatched: activity.get(show.key) || null,
        matchStatus: show.match_status || null, ...showCounters(store, show.key, today),
      });
    } else {
      const movie = store.get("movies", lib.ref);
      if (!movie) continue;
      const watch = getWatch(store, MOVIE, movie.key);
      const release = movie.release_date_fr || movie.release_date_original || null;
      entries.push({
        kind: MOVIE, ref: movie.key, title: movie.title, originalTitle: movie.original_title,
        status: lib.status, favorite: Boolean(lib.favorite), posterUrl: movie.poster_url,
        year: movie.year || (release ? Number(release.slice(0, 4)) : null),
        addedAt: lib.added_at, lastWatched: watch ? (watch.last_watched_at || watch.watched_at) : null,
        watched: Boolean(watch), releaseDate: release,
      });
    }
  }
  let result = entries;
  if (search && search.trim()) {
    const wanted = normalize(search.trim());
    result = result.filter((e) => normalize(e.title).includes(wanted)
      || normalize(e.originalTitle).includes(wanted));
  }
  const bulk = bulkDays(store);
  for (const e of result) {
    if (e.lastWatched && bulk.has(parisDay(e.lastWatched))) {
      e.lastWatched = null;
      e.importDay = true;
    }
  }
  result.sort((a, b) => normalize(a.title).localeCompare(normalize(b.title)));
  const desc = (field) => (a, b) => (b[field] || "").localeCompare(a[field] || "");
  if (sort === SORT_ADDED) result.sort(desc("addedAt"));
  else if (sort === SORT_RECENT) result.sort(desc("lastWatched"));
  else if (sort === SORT_PAUSED) {
    result.sort((a, b) => (Number(!a.lastWatched) - Number(!b.lastWatched))
      || (a.lastWatched || "").localeCompare(b.lastWatched || ""));
  }
  return result;
}

const plural = (n, word) => `${n} ${word}${n > 1 ? "s" : ""}`;

/** « Vu aujourd'hui », « Vu il y a 3 mois »... ; vide si rien n'a été vu. */
export function lastWatchedLabel(entry, today = todayParis()) {
  if (!entry.lastWatched) {
    if (entry.importDay) return "Date inconnue (import)";
    const seen = entry.kind === MOVIE ? entry.watched : entry.lastWatchedCode != null;
    return seen ? "Date de visionnage inconnue" : "";
  }
  const days = daysBetween(parisDay(entry.lastWatched), today);
  if (days <= 0) return "Vu aujourd'hui";
  if (days === 1) return "Vu hier";
  if (days < DAYS_PER_MONTH) return `Vu il y a ${days} jours`;
  if (days < DAYS_PER_YEAR) return `Vu il y a ${Math.floor(days / DAYS_PER_MONTH)} mois`;
  return `Vu il y a ${plural(Math.floor(days / DAYS_PER_YEAR), "an")}`;
}

/** « S09E09 · 87 restants », « À jour », « Vu », « Sortie le 01/12/2026 »... */
export function progressLabel(entry, today = todayParis()) {
  if (entry.kind === MOVIE) {
    if (entry.watched) return "Vu";
    if (entry.releaseDate && entry.releaseDate > today) return `Sortie le ${fmtDate(entry.releaseDate)}`;
    return "Non vu";
  }
  if (MATCH_TO_CHECK.includes(entry.matchStatus)) return "Correspondance à vérifier (app Python)";
  if (!entry.lastWatchedCode) {
    return entry.remaining ? `Pas commencé · ${plural(entry.remaining, "restant")}` : "Pas commencé";
  }
  if (entry.remaining === 0) return "À jour";
  return `${entry.lastWatchedCode} · ${plural(entry.remaining, "restant")}`;
}
