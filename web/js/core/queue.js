// Liste « À regarder » et calendrier (port de watchlog/core/queue.py).
// Épisodes et films non vus, triés par date de sortie ; à venir et sans date à la fin.

import { addDays, localDate, today as todayParis } from "./dates.js";
import { getWatch } from "./library.js";
import {
  COMPLETED, EPISODE, FOR_LATER, MATCH_OK, MATCH_REMAPPED, MOVIE, SHOW, TO_WATCH, WATCHING,
  episodeCode, episodeTitle, refKey,
} from "./model.js";

export const RELEASED = "released";
export const UPCOMING = "upcoming";
export const NO_DATE = "no_date";
export const AVAILABILITY_LABELS = { [RELEASED]: "Sorti", [UPCOMING]: "À venir", [NO_DATE]: "Sans date" };
export const DEFAULT_STATUSES = [WATCHING, TO_WATCH];
export const SORT_DATE = "date";
export const SORT_RECENT = "recent";
const AVAILABILITY_ORDER = { [RELEASED]: 0, [UPCOMING]: 1, [NO_DATE]: 2 };

export function availability(day, today) {
  if (!day) return NO_DATE;
  return day <= today ? RELEASED : UPCOMING;
}

function compareDefault(a, b) {
  return (Number(!a.releaseDate) - Number(!b.releaseDate))
    || (a.releaseDate || "").localeCompare(b.releaseDate || "")
    || a.title.toLowerCase().localeCompare(b.title.toLowerCase())
    || ((a.season || 0) - (b.season || 0))
    || ((a.number ?? 10_000) - (b.number ?? 10_000));
}

/** Date du dernier visionnage de chaque série (instant ISO le plus récent de ses épisodes). */
export function showActivity(store) {
  const activity = new Map();
  for (const w of store.watches.values()) {
    if (w.kind !== EPISODE) continue;
    const ep = store.get("episodes", w.ref);
    const when = w.last_watched_at || w.watched_at;
    if (!ep || !when) continue;
    if (!activity.has(ep.show) || activity.get(ep.show) < when) activity.set(ep.show, when);
  }
  return activity;
}

function episodeItems(store, statuses, showKey, includeSpecials, today, includeWatched = false) {
  const items = [];
  const shows = showKey ? [store.get("shows", showKey)].filter(Boolean) : store.shows.values();
  for (const show of shows) {
    // Numérotation non vérifiée (import Bingers) : tout semblerait non vu, on n'affiche rien.
    if (![MATCH_OK, MATCH_REMAPPED, undefined, null].includes(show.match_status)) continue;
    const lib = store.get("library", refKey(SHOW, show.key));
    if (statuses && (!lib || !statuses.includes(lib.status))) continue;
    for (const ep of store.episodesOf(show.key)) {
      if (!includeSpecials && ep.is_special) continue;
      const watched = Boolean(getWatch(store, EPISODE, ep.key));
      if (watched && !includeWatched) continue;
      const day = localDate(ep.airstamp, ep.airdate);
      items.push({
        kind: EPISODE, ref: ep.key, show: show.key, title: show.title,
        code: episodeCode(ep.season, ep.number), episodeTitle: episodeTitle(ep),
        releaseDate: day, availability: availability(day, today), status: lib?.status ?? null,
        runtime: ep.runtime ?? null, season: ep.season, number: ep.number,
        isSpecial: Boolean(ep.is_special), posterUrl: show.poster_url || null, watched,
      });
    }
  }
  return items;
}

function movieItems(store, statuses, today, includeWatched = false) {
  const items = [];
  for (const lib of store.library.values()) {
    if (lib.kind !== MOVIE || !statuses.includes(lib.status)) continue;
    const movie = store.get("movies", lib.ref);
    if (!movie) continue;
    const watched = Boolean(getWatch(store, MOVIE, movie.key));
    if (watched && !includeWatched) continue;
    const day = movie.release_date_fr || movie.release_date_original || null;
    items.push({
      kind: MOVIE, ref: movie.key, show: null, title: movie.title, code: "", episodeTitle: null,
      releaseDate: day, availability: availability(day, today), status: lib.status,
      runtime: movie.runtime ?? null, season: null, number: null, isSpecial: false,
      posterUrl: movie.poster_url || null, watched,
    });
  }
  return items;
}

function episodeRank(item) {
  return [item.season || 0, item.number ?? 10_000, item.releaseDate || "9999"];
}

function firstPerShow(items) {
  const first = new Map();
  for (const item of items) {
    const best = first.get(item.show);
    if (!best) { first.set(item.show, item); continue; }
    const [a, b] = [episodeRank(item), episodeRank(best)];
    if (a[0] - b[0] < 0 || (a[0] === b[0] && (a[1] - b[1] < 0
      || (a[1] === b[1] && a[2] < b[2])))) first.set(item.show, item);
  }
  return [...first.values()];
}

/** Liste « À regarder » (mêmes options que queue.build_queue). */
export function buildQueue(store, {
  kinds = [EPISODE, MOVIE], statuses = DEFAULT_STATUSES,
  availabilities = [RELEASED, UPCOMING, NO_DATE], showKey = null, includeSpecials = false,
  nextOnly = false, today = todayParis(), sort = SORT_DATE,
} = {}) {
  let items = [];
  if (kinds.includes(EPISODE)) {
    let episodes = episodeItems(store, statuses, showKey, includeSpecials, today);
    if (nextOnly) episodes = firstPerShow(episodes);
    items.push(...episodes);
  }
  if (kinds.includes(MOVIE) && !showKey) items.push(...movieItems(store, statuses, today));
  items = items.filter((i) => availabilities.includes(i.availability));
  if (sort === SORT_RECENT) {
    const activity = showActivity(store);
    const key = (i) => {
      const when = i.kind === EPISODE ? activity.get(i.show) : null;
      return i.availability !== RELEASED || !when
        ? [AVAILABILITY_ORDER[i.availability], 1, "", ""] : [0, 0, when, i.show];
    };
    return items.sort((a, b) => {
      const [ka, kb] = [key(a), key(b)];
      return (ka[0] - kb[0]) || (ka[1] - kb[1]) || kb[2].localeCompare(ka[2])
        || ka[3].localeCompare(kb[3]) || compareDefault(a, b);
    });
  }
  return items.sort(compareDefault);
}

/** Prochain épisode non vu d'une série (hors spéciaux), quel que soit son statut. */
export function nextEpisode(store, showKey, today = todayParis()) {
  const items = firstPerShow(episodeItems(store, null, showKey, false, today));
  return items[0] || null;
}

/** Sorties non vues des `days` prochains jours, séries et films suivis (sauf arrêtés / finis). */
export function calendar(store, { days = 60, today = todayParis(), includeSpecials = false } = {}) {
  const end = addDays(today, days);
  return buildQueue(store, {
    statuses: [WATCHING, TO_WATCH, FOR_LATER], availabilities: [RELEASED, UPCOMING],
    includeSpecials, today,
  }).filter((i) => today <= i.releaseDate && i.releaseDate <= end);
}

/** Sorties entre deux jours, vues ou non (champ `watched`), pour la vue « Mois ». */
export function releasesBetween(store, start, end, { includeSpecials = false,
  today = todayParis() } = {}) {
  const statuses = [WATCHING, TO_WATCH, FOR_LATER, COMPLETED];
  const items = [...episodeItems(store, statuses, null, includeSpecials, today, true),
    ...movieItems(store, statuses, today, true)];
  return items.filter((i) => i.releaseDate && start <= i.releaseDate && i.releaseDate <= end)
    .sort(compareDefault);
}

/** Regroupe les épisodes d'une même série sortis le même jour ; films à part. */
export function groupDay(items) {
  const entries = [];
  const byShow = new Map();
  for (const item of items) {
    if (item.kind === EPISODE && byShow.has(item.show)) {
      byShow.get(item.show).items.push(item);
      continue;
    }
    const entry = { items: [item] };
    entries.push(entry);
    if (item.kind === EPISODE) byShow.set(item.show, entry);
  }
  return entries;
}

/** « S01E03 », « S01E01–E08 » (même saison, à la suite) ou « 8 épisodes ». */
export function dayEntryLabel(entry) {
  const first = entry.items[0];
  if (first.kind === MOVIE) return "Film";
  if (entry.items.length === 1) return first.code;
  const numbers = entry.items.map((i) => i.number);
  const seasons = new Set(entry.items.map((i) => i.season));
  const consecutive = numbers.every((n, i) => n != null && n === numbers[0] + i);
  if (seasons.size === 1 && consecutive) {
    return `${first.code}–E${String(numbers.at(-1)).padStart(2, "0")}`;
  }
  return `${entry.items.length} épisodes`;
}
