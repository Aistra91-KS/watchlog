// Actions de l'utilisateur sur sa bibliothèque (port de watchlog/core/library.py) :
// marquer vu, statut, favori, note. Mêmes règles que l'app Python.

import { isoUtc, localDayToUtc, today as todayParis, localDate } from "./dates.js";
import {
  COMPLETED, ENDED, EPISODE, SHOW, STATUSES, TO_WATCH, refKey,
} from "./model.js";

function earliest(a, b) {
  if (!a) return b || null;
  if (!b) return a;
  return a < b ? a : b;
}

function latest(a, b) {
  if (!a) return b || null;
  if (!b) return a;
  return a > b ? a : b;
}

// --- Visionnages -------------------------------------------------------------

export function getWatch(store, kind, ref) {
  return store.get("watches", refKey(kind, ref)) || null;
}

/** Ajoute un visionnage ou le fusionne (premier = le plus ancien, dernier = le plus récent,
 *  plays = le maximum) : relancer la même fusion ne change rien. */
export function upsertWatch(store, kind, ref, watchedAt = null, lastWatchedAt = null, plays = 1) {
  const last = lastWatchedAt || watchedAt;
  const existing = getWatch(store, kind, ref);
  const next = existing
    ? {
      ...existing,
      watched_at: earliest(existing.watched_at, watchedAt),
      last_watched_at: latest(existing.last_watched_at, last),
      plays: Math.max(existing.plays || 1, plays || 1),
    }
    : {
      key: refKey(kind, ref), kind, ref, watched_at: watchedAt, last_watched_at: last,
      plays: Math.max(plays || 1, 1),
    };
  const changed = !existing || existing.watched_at !== next.watched_at
    || existing.last_watched_at !== next.last_watched_at || existing.plays !== next.plays;
  if (changed) store.put("watches", next);
  return { watch: next, changed };
}

/** Marque un épisode ou un film comme vu (rien si c'est déjà le cas). */
export function markWatched(store, kind, ref, when = null) {
  const existing = getWatch(store, kind, ref);
  if (existing) return existing;
  const at = when || isoUtc();
  return store.put("watches", {
    key: refKey(kind, ref), kind, ref, watched_at: at, last_watched_at: at, plays: 1,
  });
}

export function unmarkWatched(store, kind, ref) {
  return store.remove("watches", refKey(kind, ref));
}

/** Date de visionnage choisie par l'utilisateur (un jour, midi heure de Paris). */
export function setWatchDate(store, kind, ref, day) {
  const at = day ? localDayToUtc(day) : null;
  const existing = getWatch(store, kind, ref);
  return store.put("watches", {
    ...(existing || { key: refKey(kind, ref), kind, ref, plays: 1 }),
    watched_at: at, last_watched_at: existing && existing.plays > 1 ? existing.last_watched_at : at,
  });
}

/** Un visionnage de plus (revisionnage) : plays + 1, dernier visionnage = maintenant. */
export function addRewatch(store, kind, ref) {
  const existing = getWatch(store, kind, ref);
  if (!existing) return markWatched(store, kind, ref);
  return store.put("watches", {
    ...existing, plays: (existing.plays || 1) + 1, last_watched_at: isoUtc(),
  });
}

/** Retire un revisionnage (plays - 1, jamais sous 1). */
export function removeRewatch(store, kind, ref) {
  const existing = getWatch(store, kind, ref);
  if (!existing || (existing.plays || 1) <= 1) return existing;
  return store.put("watches", { ...existing, plays: existing.plays - 1 });
}

/** Épisode déjà diffusé (date française au plus tard aujourd'hui). */
export function isAired(ep, day = todayParis()) {
  const date = localDate(ep.airstamp, ep.airdate);
  return Boolean(date && date <= day);
}

function markEpisodes(store, episodes) {
  let count = 0;
  const now = isoUtc();
  for (const ep of episodes) {
    if (!getWatch(store, EPISODE, ep.key)) {
      markWatched(store, EPISODE, ep.key, now);
      count += 1;
    }
  }
  return count;
}

/** Toute une saison vue (épisodes déjà diffusés ; spéciaux si demandé). */
export function markSeasonWatched(store, showKey, season, includeSpecials = false, day) {
  const episodes = store.episodesOf(showKey).filter((ep) => ep.season === season
    && (includeSpecials || !ep.is_special) && isAired(ep, day));
  return markEpisodes(store, episodes);
}

/** « J'en suis là » : tous les épisodes jusqu'à celui-ci compris (ordre saison / numéro). */
export function markWatchedUntil(store, episodeKey, includeSpecials = false) {
  const target = store.get("episodes", episodeKey);
  if (!target) return 0;
  const episodes = [];
  for (const ep of store.episodesOf(target.show)) {
    if (ep.is_special && !includeSpecials && ep.key !== target.key) continue;
    episodes.push(ep);
    if (ep.key === target.key) break;
  }
  return markEpisodes(store, episodes);
}

/** Retire les visionnages d'une saison. */
export function unmarkSeason(store, showKey, season) {
  let count = 0;
  for (const ep of store.episodesOf(showKey)) {
    if (ep.season === season && unmarkWatched(store, EPISODE, ep.key)) count += 1;
  }
  return count;
}

// --- Bibliothèque (statuts) --------------------------------------------------

export function getLibraryItem(store, kind, ref) {
  return store.get("library", refKey(kind, ref)) || null;
}

/** Ajoute à la bibliothèque ; un élément existant n'est PAS modifié. */
export function ensureLibraryItem(store, kind, ref, status = TO_WATCH, favorite = false,
  addedAt = null) {
  const existing = getLibraryItem(store, kind, ref);
  if (existing) return existing;
  return store.put("library", {
    key: refKey(kind, ref), kind, ref, status, favorite, added_at: addedAt || isoUtc(),
  });
}

export function setStatus(store, kind, ref, status) {
  if (!STATUSES.includes(status)) throw new Error(`Statut inconnu : ${status}`);
  const item = ensureLibraryItem(store, kind, ref, status);
  if (item.status === status) return item;
  return store.put("library", { ...item, status });
}

export function setFavorite(store, kind, ref, favorite) {
  const item = ensureLibraryItem(store, kind, ref, TO_WATCH);
  return store.put("library", { ...item, favorite: Boolean(favorite) });
}

/** Retire de la bibliothèque : la fiche, l'historique et les notes sont gardés. */
export function removeFromLibrary(store, kind, ref) {
  return store.remove("library", refKey(kind, ref));
}

/** Passe en « Terminé » les séries finies (TVMaze : Ended) dont tous les épisodes réguliers
 *  diffusés sont vus. Renvoie le nombre de séries changées. */
export function refreshCompleted(store, day = todayParis()) {
  let count = 0;
  for (const item of store.library.values()) {
    if (item.kind !== SHOW || item.status === COMPLETED) continue;
    const show = store.get("shows", item.ref);
    if (!show || show.status !== ENDED) continue;
    const aired = store.episodesOf(item.ref).filter((ep) => !ep.is_special && ep.airdate
      && ep.airdate <= day);
    if (aired.length && aired.every((ep) => getWatch(store, EPISODE, ep.key))) {
      store.put("library", { ...item, status: COMPLETED });
      count += 1;
    }
  }
  return count;
}

// --- Notes -------------------------------------------------------------------

export function getRating(store, kind, ref) {
  return store.get("ratings", refKey(kind, ref)) || null;
}

function cleanText(text) {
  return text && String(text).trim() ? String(text).trim() : null;
}

/** Note (1 à 5 ou null), commentaire, émotions (liste de codes), personnage préféré.
 *  Tout vide : la note est supprimée. */
export function setRating(store, kind, ref, { rating = null, note = null, emotions = [],
  favoriteCharacter } = {}) {
  if (rating != null && !(rating >= 1 && rating <= 5)) {
    throw new Error(`Note invalide : ${rating} (attendu : 1 à 5)`);
  }
  const existing = getRating(store, kind, ref);
  const character = favoriteCharacter === undefined
    ? existing?.favorite_character ?? null : cleanText(favoriteCharacter);
  const emotionsText = emotions && emotions.length ? emotions.join(", ") : null;
  const text = cleanText(note);
  if (rating == null && !text && !emotionsText && !character) {
    store.remove("ratings", refKey(kind, ref));
    return null;
  }
  return store.put("ratings", {
    key: refKey(kind, ref), kind, ref, rating, note: text, emotions: emotionsText,
    favorite_character: character,
  });
}

export function splitEmotions(emotions) {
  return (emotions || "").split(",").map((e) => e.trim()).filter(Boolean);
}
