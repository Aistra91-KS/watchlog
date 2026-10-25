// Ajout d'une série ou d'un film (recherche TVMaze / TMDB, ou saisie manuelle) et
// synchronisation de la bibliothèque. Port simplifié de adding.py et sync.py.

import { applyTmdbSeason, upsertMovieFromTmdb, upsertShowFromTvmaze } from "./catalog.js";
import { isoUtc } from "./dates.js";
import { ensureLibraryItem, markWatchedUntil, refreshCompleted, setStatus } from "./library.js";
import { MOVIE, SHOW, episodeKey, newId } from "./model.js";

const DAY_MS = 86_400_000;
// Au-delà, /updates/shows?since=month ne couvre plus l'écart : on relit toutes les séries.
const FULL_SYNC_AFTER_DAYS = 28;
// Réglage : séries / films dont la dernière mise à jour a échoué, relus à la synchro suivante
// (sinon leur changement pourrait sortir de la fenêtre « updates » et ne plus jamais être lu).
const RETRY = "sync_retry";

/** Textes français et durées réelles (TMDB) d'une série. Erreurs comptées, jamais bloquantes. */
export async function enrichShowFromTmdb(store, tmdb, key) {
  const show = store.get("shows", key);
  const result = { ok: false, errors: [] };
  if (!show || !tmdb) return result;
  try {
    if (!(await tmdb.available())) return result;   // pas de clé TMDB : rien à compléter
    let tmdbId = show.tmdb_id;
    if (!tmdbId && show.tvdb_id) tmdbId = await tmdb.findTvByTvdb(show.tvdb_id);
    if (!tmdbId) return result;
    const details = await tmdb.details("tv", tmdbId);
    store.put("shows", { ...store.get("shows", key), tmdb_id: tmdbId,
      summary_fr: (details?.overview || "").trim() || null });
    const seasons = [...new Set(store.episodesOf(key).filter((e) => e.number != null && e.season > 0)
      .map((e) => e.season))];
    for (const season of seasons) {
      const data = await tmdb.tvSeason(tmdbId, season);
      if (data) applyTmdbSeason(store, key, season, data);
    }
    result.ok = true;
  } catch (error) {
    console.warn(`TMDB : textes français de « ${show.title} » non lus`, error);
    result.errors.push(`${show.title} : ${error.message}`);
  }
  return result;
}

/** Ajoute une série TVMaze (fiche + épisodes), avec un statut et éventuellement « j'en suis à ». */
export async function addShow(store, tvmaze, tmdb, tvmazeId, { status = "to_watch",
  untilEpisode = null } = {}) {
  const data = await tvmaze.getShow(tvmazeId);
  if (!data) throw new Error("Série introuvable sur TVMaze.");
  const episodes = await tvmaze.getEpisodesWithSpecials(tvmazeId, data.status);
  const show = upsertShowFromTvmaze(store, data, episodes);
  const enrich = await enrichShowFromTmdb(store, tmdb, show.key);
  ensureLibraryItem(store, SHOW, show.key, status);
  setStatus(store, SHOW, show.key, status);
  if (untilEpisode) {
    const { season, number } = untilEpisode;
    const ep = store.episodesOf(show.key).find((e) => e.season === season && e.number === number);
    if (ep) markWatchedUntil(store, ep.key);
  }
  return { show, warnings: enrich.errors };
}

/** Ajoute une série suggérée (id TMDB) : fiche TVMaze retrouvée via TVDB, sinon IMDb.
 *  `title` : titre français TMDB, gardé si TVMaze n'a que le titre original. */
export async function addSuggestedShow(store, tvmaze, tmdb, tmdbId, { status = "to_watch",
  title = null } = {}) {
  const ids = await tmdb.tvExternalIds(tmdbId);
  let data = ids.tvdb_id ? await tvmaze.lookupTvdb(ids.tvdb_id) : null;
  if (!data && ids.imdb_id) data = await tvmaze.lookupImdb(ids.imdb_id);
  if (!data) throw new Error("Série introuvable sur TVMaze : cherche-la dans la page Ajouter.");
  const { show, warnings } = await addShow(store, tvmaze, tmdb, data.id, { status });
  const current = store.get("shows", show.key);
  store.put("shows", { ...current, tmdb_id: current.tmdb_id || tmdbId,
    title: title && current.title === current.original_title ? title : current.title });
  return { show: store.get("shows", show.key), warnings };
}

/** Ajoute un film TMDB. */
export async function addMovie(store, tmdb, tmdbId, { status = "to_watch" } = {}) {
  const data = await tmdb.getMovie(tmdbId);
  if (!data) throw new Error("Film introuvable sur TMDB (ou TMDB non connecté).");
  const movie = upsertMovieFromTmdb(store, data);
  ensureLibraryItem(store, MOVIE, movie.key, status);
  setStatus(store, MOVIE, movie.key, status);
  return movie;
}

/** Série saisie à la main (hors TVMaze) : nombre d'épisodes par saison. */
export function addManualShow(store, { title, seasons = [], status = "to_watch" }) {
  const name = (title || "").trim();
  if (!name) throw new Error("Titre obligatoire.");
  const key = `manual:${newId()}`;
  store.put("shows", { key, title: name, is_manual: true, match_status: "ok", synced_at: null });
  seasons.forEach((count, i) => {
    for (let n = 1; n <= count; n += 1) {
      const ep = { show: key, season: i + 1, number: n, title: null, title_fr: null,
        airdate: null, airstamp: null, runtime: null, is_special: false, tvmaze_episode_id: null };
      store.put("episodes", { ...ep, key: episodeKey(key, ep) });
    }
  });
  ensureLibraryItem(store, SHOW, key, status);
  return store.get("shows", key);
}

/** Film saisi à la main (hors TMDB). */
export function addManualMovie(store, { title, year = null, runtime = null, status = "to_watch" }) {
  const name = (title || "").trim();
  if (!name) throw new Error("Titre obligatoire.");
  const key = `manual:${newId()}`;
  store.put("movies", { key, title: name, year: year || null, runtime: runtime || null,
    is_manual: true, synced_at: null });
  ensureLibraryItem(store, MOVIE, key, status);
  return store.get("movies", key);
}

/** Met à jour séries (TVMaze) et films à venir (TMDB) de la bibliothèque.
 *  Renvoie {shows, movies, errors, tvmazeDown}. Une panne est comptée, jamais avalée. */
export async function syncLibrary(store, tvmaze, tmdb, { progress = () => {}, now = Date.now() } = {}) {
  const report = { shows: 0, movies: 0, errors: [], tvmazeDown: false };
  // Repère : dernière synchro web, sinon la plus ancienne synchro des séries (fichier du PC).
  const lastSync = Date.parse(store.setting("last_sync_at", "")) || oldestShowSync(store);
  const ageDays = (now - lastSync) / DAY_MS;
  let updates = null;
  if (lastSync && ageDays < FULL_SYNC_AFTER_DAYS) {
    try {
      updates = await tvmaze.updatedSince(ageDays < 6 ? "week" : "month");
    } catch (error) {
      report.tvmazeDown = true;
      report.errors.push(`TVMaze : ${error.message}`);
    }
  }
  const retry = new Set(store.setting(RETRY, []));
  const failed = new Set();
  const shows = [];
  for (const lib of store.library.values()) {
    if (lib.kind !== SHOW) continue;
    const show = store.get("shows", lib.ref);
    if (!show?.tvmaze_id) continue;
    const synced = Date.parse(show.synced_at || "") || 0;
    // Sans liste des mises à jour (première synchro, ou trop ancienne) : tout est relu.
    const changed = updates ? (updates.get(show.tvmaze_id) || 0) * 1000 > synced : true;
    if (changed || !synced || retry.has(show.key)) shows.push(show);
  }
  const movies = [...store.library.values()].filter((l) => l.kind === MOVIE)
    .map((l) => store.get("movies", l.ref))
    .filter((m) => m?.tmdb_id && (retry.has(m.key)
      || ((!m.release_date_fr || m.release_date_fr > isoUtc(now).slice(0, 10))
        && now - (Date.parse(m.synced_at || "") || 0) > DAY_MS)));
  const total = report.tvmazeDown ? movies.length : shows.length + movies.length;
  let done = 0;
  if (!report.tvmazeDown) {
    for (const show of shows) {
      progress(done, total, show.title);
      try {
        const data = await tvmaze.getShow(show.tvmaze_id, true);
        if (!data) {
          console.warn(`Synchro : « ${show.title} » introuvable sur TVMaze (${show.tvmaze_id})`);
          report.errors.push(`${show.title} : introuvable sur TVMaze (retirée ?)`);
        } else {
          const episodes = await tvmaze.getEpisodesWithSpecials(show.tvmaze_id, data.status, true);
          upsertShowFromTvmaze(store, data, episodes);
          const enrich = await enrichShowFromTmdb(store, tmdb, show.key);
          report.errors.push(...enrich.errors);
          if (enrich.errors.length) failed.add(show.key);   // textes français à relire
          report.shows += 1;
        }
      } catch (error) {
        console.warn(`Synchro : « ${show.title} » non mise à jour`, error);
        report.errors.push(`${show.title} : ${error.message}`);
        failed.add(show.key);
      }
      done += 1;
    }
  }
  let tmdbReady = false;
  if (tmdb && movies.length) {
    try {
      tmdbReady = await tmdb.available();
    } catch (error) {
      report.errors.push(`TMDB : ${error.message} Films non mis à jour.`);
      for (const movie of movies) failed.add(movie.key);
    }
  }
  if (tmdbReady) {
    for (const movie of movies) {
      progress(done, total, movie.title);
      try {
        const data = await tmdb.getMovie(movie.tmdb_id);
        if (!data) {
          console.warn(`Synchro : « ${movie.title} » introuvable sur TMDB (${movie.tmdb_id})`);
          report.errors.push(`${movie.title} : introuvable sur TMDB`);
        } else {
          upsertMovieFromTmdb(store, data);
          report.movies += 1;
        }
      } catch (error) {
        console.warn(`Synchro : « ${movie.title} » non mis à jour`, error);
        report.errors.push(`${movie.title} : ${error.message}`);
        failed.add(movie.key);
      }
      done += 1;
    }
  }
  progress(total, total, "");
  refreshCompleted(store);
  if (!report.tvmazeDown) {
    store.setSetting("last_sync_at", isoUtc(now));
    // Les titres en échec sont relus la prochaine fois ; ceux qui ont réussi sortent de la liste.
    store.setSetting(RETRY, [...failed]);
  }
  return report;
}

function oldestShowSync(store) {
  let oldest = Infinity;
  for (const lib of store.library.values()) {
    const show = lib.kind === SHOW ? store.get("shows", lib.ref) : null;
    if (!show?.tvmaze_id) continue;
    oldest = Math.min(oldest, Date.parse(show.synced_at || "") || 0);
  }
  return Number.isFinite(oldest) ? oldest : 0;
}

export function syncReportLines(report) {
  const lines = [`Séries mises à jour : ${report.shows}`, `Films mis à jour : ${report.movies}`];
  if (report.tvmazeDown) lines.push("TVMaze ne répond pas : séries non mises à jour.");
  if (report.errors.length) {
    lines.push(`Problèmes : ${report.errors.length}`, ...report.errors.map((e) => `  - ${e}`));
  }
  return lines;
}
