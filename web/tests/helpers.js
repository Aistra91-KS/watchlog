// Petites fonctions pour remplir un Store de test (équivalent de tests/helpers_db.py).

import { Store } from "../js/core/store.js";
import { SHOW, MOVIE, WATCHING, episodeKey, refKey } from "../js/core/model.js";

export function makeStore() {
  return new Store();
}

/** Série TVMaze dans la bibliothèque, avec le statut donné. */
export function addShow(store, title, { id = 1, status = WATCHING, favorite = false,
  addedAt = null, ...fields } = {}) {
  const key = `tvmaze:${id}`;
  store.put("shows", { key, tvmaze_id: id, title, match_status: "ok", ...fields });
  if (status) {
    store.put("library", { key: refKey(SHOW, key), kind: SHOW, ref: key, status, favorite,
      added_at: addedAt });
  }
  return key;
}

/** Épisode (number = null : spécial). */
export function addEpisode(store, show, season, number, { airdate = null, airstamp = null,
  runtime = 45, title = null, id = null } = {}) {
  const ep = { show, season, number, airdate, airstamp, runtime, title: title || `Épisode ${season}x${number}`,
    title_fr: null, is_special: number == null, tvmaze_episode_id: id };
  const key = episodeKey(show, ep);
  store.put("episodes", { ...ep, key });
  return key;
}

export function addMovie(store, title, { id = 1, release = null, status = WATCHING,
  favorite = false, runtime = 120, ...fields } = {}) {
  const key = `tmdb:${id}`;
  store.put("movies", { key, tmdb_id: id, title, release_date_fr: release, runtime, ...fields });
  if (status) {
    store.put("library", { key: refKey(MOVIE, key), kind: MOVIE, ref: key, status, favorite,
      added_at: null });
  }
  return key;
}

export function watch(store, kind, ref, when = "2024-01-01T20:00:00Z", plays = 1) {
  store.put("watches", { key: refKey(kind, ref), kind, ref, watched_at: when,
    last_watched_at: when, plays });
}
