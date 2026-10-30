// Accueil : l'essentiel tout de suite (port de watchlog/core/home.py). Activité récente,
// épisodes à suivre, films à voir, prochaines sorties, derniers visionnages. Mêmes règles que
// À regarder, Calendrier, Bibliothèque et Statistiques ; jours d'import (Bingers) ignorés.

import { addDays, parisDay, today as todayParis } from "./dates.js";
import { EPISODE, MOVIE, SHOW, episodeCode, refKey } from "./model.js";
import { bulkDays as importDays } from "./progress.js";
import {
  DEFAULT_STATUSES, RELEASED, buildQueue, calendar, compareDefault, groupDay, showActivity,
} from "./queue.js";
import { bulkDays, estimatedMinutes } from "./stats.js";

// Taille de chaque bloc (la page complète est à un clic : « Tout voir »)
export const CONTINUE_LIMIT = 6;   // séries de « À suivre »
export const MOVIES_LIMIT = 6;     // « Films à voir »
export const RECENT_LIMIT = 6;     // « Derniers visionnages » (titres différents)
export const UPCOMING_DAYS = 7;    // « Prochaines sorties » : aujourd'hui et les 7 jours suivants
export const WEEK_DAYS = 7;        // « Vus ces 7 jours » : aujourd'hui et les 6 jours précédents
// Statuts suivis : ceux de la liste « À regarder » par défaut (en cours, à voir)
const STATUSES = DEFAULT_STATUSES;

// --- Activité récente ------------------------------------------------------------------

/** Visionnages d'une période (premier visionnage), jours d'import exclus :
 *  {episodes, movies, total, minutes, ignored}. */
function period(frame, start, end, bulk) {
  const window = frame.filter((r) => r.day && start <= r.day && r.day <= end);
  const kept = window.filter((r) => !bulk.has(r.day));
  let minutes = 0;
  for (const kind of [EPISODE, MOVIE]) {
    minutes += estimatedMinutes(kept.filter((r) => r.kind === kind).map((r) => r.runtime), kind)[0];
  }
  const episodes = kept.filter((r) => r.kind === EPISODE).length;
  const movies = kept.length - episodes;
  return { episodes, movies, total: kept.length, minutes, ignored: window.length - kept.length };
}

/** Jours d'affilée avec au moins un visionnage, jusqu'à aujourd'hui ; rien aujourd'hui mais
 *  vu hier : la série tient encore et compte jusqu'à hier. Sinon 0. */
export function currentStreak(days, today) {
  const seen = new Set(days);
  let day = seen.has(today) ? today : addDays(today, -1);
  let count = 0;
  while (seen.has(day)) {
    count += 1;
    day = addDays(day, -1);
  }
  return count;
}

/** Semaine, mois en cours et jours d'affilée, d'après le tableau des visionnages
 *  (stats.watchesFrame). Un revisionnage ne compte pas : seul le premier est daté. */
export function activity(frame, today = todayParis()) {
  const bulk = new Set(bulkDays(frame).keys());
  const days = frame.map((r) => r.day).filter((d) => d && !bulk.has(d));
  return {
    week: period(frame, addDays(today, -(WEEK_DAYS - 1)), today, bulk),
    month: period(frame, `${today.slice(0, 8)}01`, today, bulk),
    streak: currentStreak(days, today), watchedToday: days.includes(today),
  };
}

// --- À suivre, films à voir, sorties ---------------------------------------------------

/** Dernier visionnage de chaque série, sans ceux datés d'un jour d'import. */
function lastWatched(store) {
  const bulk = importDays(store);
  return new Map([...showActivity(store)].filter(([, when]) => !bulk.has(parisDay(when))));
}

/** « À suivre » : le prochain épisode sorti (hors spéciaux) de chaque série en cours ou à
 *  voir ; séries regardées le plus récemment d'abord, puis les autres par date de sortie.
 *  {entries: [{item, remaining, lastWatched}], total}. */
export function continueWatching(store, { today = todayParis(), limit = CONTINUE_LIMIT } = {}) {
  const items = buildQueue(store, { kinds: [EPISODE], statuses: STATUSES,
    availabilities: [RELEASED], today });
  const byShow = new Map();
  for (const item of items) {
    if (!byShow.has(item.show)) byShow.set(item.show, []);
    byShow.get(item.show).push(item);
  }
  const last = lastWatched(store);
  const entries = [...byShow].map(([show, episodes]) => ({
    item: episodes.reduce((a, b) => ((b.season - a.season || b.number - a.number) < 0 ? b : a)),
    remaining: episodes.length, lastWatched: last.get(show) || null,
  }));
  entries.sort((a, b) => (Number(!a.lastWatched) - Number(!b.lastWatched))
    || (b.lastWatched || "").localeCompare(a.lastWatched || "") || compareDefault(a.item, b.item));
  return { entries: entries.slice(0, limit), total: entries.length };
}

/** Films sortis pas encore vus (en cours ou à voir), les derniers ajoutés d'abord (à
 *  égalité, le plus récent d'abord). {items, total}. */
export function moviesToWatch(store, { today = todayParis(), limit = MOVIES_LIMIT } = {}) {
  const items = buildQueue(store, { kinds: [MOVIE], statuses: STATUSES, availabilities: [RELEASED],
    today });
  const added = (i) => store.get("library", refKey(MOVIE, i.ref))?.added_at || "";
  items.sort((a, b) => added(b).localeCompare(added(a))
    || (b.releaseDate || "").localeCompare(a.releaseDate || ""));
  return { items: items.slice(0, limit), total: items.length };
}

/** Sorties non vues d'aujourd'hui aux `days` jours suivants (titres du calendrier), par jour :
 *  [[jour, entrées de groupDay]]. */
export function upcoming(store, { today = todayParis(), days = UPCOMING_DAYS } = {}) {
  const byDay = new Map();
  for (const item of calendar(store, { days, today })) {
    if (!byDay.has(item.releaseDate)) byDay.set(item.releaseDate, []);
    byDay.get(item.releaseDate).push(item);
  }
  return [...byDay].sort((a, b) => a[0].localeCompare(b[0])).map(([day, list]) => [day, groupDay(list)]);
}

// --- Derniers visionnages ----------------------------------------------------------------

/** Les `limit` titres vus le plus récemment (revisionnages compris), un par titre :
 *  [{kind (SHOW / MOVIE), ref, title, code, when, posterUrl}]. Jours d'import ignorés. */
export function recentWatches(store, { limit = RECENT_LIMIT } = {}) {
  const bulk = importDays(store);
  const found = new Map();
  for (const w of store.watches.values()) {
    const when = w.last_watched_at || w.watched_at;
    if (!when || bulk.has(parisDay(when))) continue;
    let entry;
    if (w.kind === EPISODE) {
      const ep = store.get("episodes", w.ref);
      const show = ep && store.get("shows", ep.show);
      if (!show) continue;
      entry = { kind: SHOW, ref: show.key, title: show.title, code: episodeCode(ep.season, ep.number),
        when, posterUrl: show.poster_url || null };
    } else {
      const movie = store.get("movies", w.ref);
      if (!movie) continue;
      entry = { kind: MOVIE, ref: movie.key, title: movie.title, when, code: "",
        posterUrl: movie.poster_url || null };
    }
    const key = refKey(entry.kind, entry.ref);
    if (!found.has(key) || found.get(key).when < when) found.set(key, entry);
  }
  return [...found.values()].sort((a, b) => b.when.localeCompare(a.when)).slice(0, limit);
}
