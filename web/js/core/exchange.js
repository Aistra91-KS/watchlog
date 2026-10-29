// Fichier d'échange JSON avec l'app Python (port de watchlog/core/exchange.py, même format).
// Fusion à l'import : visionnages additionnés (jamais supprimés), titres manquants créés,
// pour un titre déjà présent le fichier fait référence pour statut, favori et note.
// La clé TMDB n'est jamais dans le fichier (elle n'est pas dans le Store).

import { isoUtc } from "./dates.js";
import { cleanRating, upsertWatch } from "./library.js";
import {
  EPISODE, MOVIE, SHOW, STATUSES, episodeKey, movieKey, newId, refKey, showKey,
} from "./model.js";

export const FORMAT = "watchlog";
export const VERSION = 1;

const SHOW_FIELDS = ["tvmaze_id", "tvdb_id", "tmdb_id", "imdb_id", "title", "original_title",
  "status", "network", "poster_url", "summary", "summary_fr", "genres", "is_manual",
  "match_status"];
const EPISODE_FIELDS = ["season", "number", "title", "title_fr", "runtime", "is_special",
  "tvmaze_episode_id"];
const MOVIE_FIELDS = ["tmdb_id", "imdb_id", "title", "original_title", "year", "runtime",
  "poster_url", "overview", "genres", "is_manual"];

const pick = (obj, names) => Object.fromEntries(names.map((n) => [n, obj[n] ?? null]));
const empty = (v) => v === null || v === undefined || v === "";

// --- Export ------------------------------------------------------------------

function libraryOf(store, kind, ref) {
  const item = store.get("library", refKey(kind, ref));
  return item ? { status: item.status, favorite: Boolean(item.favorite),
    added_at: item.added_at || null } : null;
}

function watchOf(store, kind, ref) {
  const w = store.get("watches", refKey(kind, ref));
  return w ? { watched_at: w.watched_at || null, last_watched_at: w.last_watched_at || null,
    plays: w.plays || 1 } : null;
}

function ratingOf(store, kind, ref) {
  const r = store.get("ratings", refKey(kind, ref));
  return r ? { rating: r.rating ?? null, emotions: r.emotions ?? null,
    favorite_character: r.favorite_character ?? null, note: r.note ?? null } : null;
}

function refOf(entry) {
  const out = {};
  for (const k of ["tvmaze_id", "tmdb_id", "title", "year"]) {
    if (entry[k] != null) out[k] = entry[k];
  }
  return out;
}

const byTitle = (a, b) => (a.title || "").localeCompare(b.title || "");

/** Toute la bibliothèque au format d'échange (objet prêt pour JSON.stringify). */
export function exportData(store, now = new Date()) {
  const refs = new Map();
  const shows = [...store.shows.values()].sort(byTitle).map((show) => {
    const entry = { ...pick(show, SHOW_FIELDS), premiered: show.premiered || null,
      synced_at: show.synced_at || null };
    entry.is_manual = Boolean(show.is_manual);
    entry.library = libraryOf(store, SHOW, show.key);
    entry.rating = ratingOf(store, SHOW, show.key);
    entry.episodes = store.episodesOf(show.key).map((ep) => ({
      ...pick(ep, EPISODE_FIELDS), is_special: Boolean(ep.is_special),
      airdate: ep.airdate || null, airstamp: ep.airstamp || null,
      watch: watchOf(store, EPISODE, ep.key), rating: ratingOf(store, EPISODE, ep.key),
    }));
    refs.set(refKey(SHOW, show.key), refOf(entry));
    return entry;
  });
  const movies = [...store.movies.values()].sort(byTitle).map((movie) => {
    const entry = {
      ...pick(movie, MOVIE_FIELDS), is_manual: Boolean(movie.is_manual),
      release_date_fr: movie.release_date_fr || null,
      release_date_original: movie.release_date_original || null,
      synced_at: movie.synced_at || null,
      library: libraryOf(store, MOVIE, movie.key), watch: watchOf(store, MOVIE, movie.key),
      rating: ratingOf(store, MOVIE, movie.key),
    };
    refs.set(refKey(MOVIE, movie.key), refOf(entry));
    return entry;
  });
  const lists = [...store.lists.values()].sort((a, b) => a.name.localeCompare(b.name))
    .map((list) => ({
      name: list.name, description: list.description ?? null,
      items: list.items.map((i) => refs.has(refKey(i.kind, i.ref))
        && { kind: i.kind, ...refs.get(refKey(i.kind, i.ref)) }).filter(Boolean)
        .sort((a, b) => a.kind.localeCompare(b.kind) || (a.title || "").localeCompare(b.title || "")),
    }));
  return {
    format: FORMAT, version: VERSION, app: "web", exported_at: isoUtc(now),
    shows, movies, lists,
    dismissed: [...store.dismissed.values()].map((d) => ({
      kind: d.kind, tmdb_id: d.tmdb_id, title: d.title ?? null, dismissed_at: d.dismissed_at ?? null,
    })),
    theme_preferences: [...store.themes.values()].map((t) => ({
      feature: t.feature, label: t.label, weight: t.weight, updated_at: t.updated_at ?? null,
    })),
    settings: { my_platforms: store.setting("my_platforms", []) },
  };
}

// --- Import ------------------------------------------------------------------

export class ExchangeReport {
  constructor() {
    Object.assign(this, {
      showsAdded: 0, moviesAdded: 0, episodesAdded: 0, watches: 0, libraryChanges: 0,
      ratings: 0, lists: 0, skipped: [],
    });
  }

  lines() {
    const lines = [
      `Séries ajoutées : ${this.showsAdded}`,
      `Films ajoutés : ${this.moviesAdded}`,
      `Épisodes ajoutés : ${this.episodesAdded}`,
      `Visionnages ajoutés ou complétés : ${this.watches}`,
      `Statuts / favoris mis à jour : ${this.libraryChanges}`,
      `Notes mises à jour : ${this.ratings}`,
      `Listes : ${this.lists}`,
    ];
    if (this.skipped.length) {
      lines.push(`Entrées ignorées : ${this.skipped.length}`);
      lines.push(...this.skipped.map((s) => `  - ${s}`));
    }
    return lines;
  }
}

/** Lit et vérifie un fichier d'échange (texte). Erreur au message clair sinon. */
export function readJson(text) {
  let data;
  try {
    data = JSON.parse(String(text).replace(/^﻿/, ""));
  } catch {
    throw new Error("Fichier illisible : ce n'est pas un JSON Watchlog.");
  }
  if (!data || typeof data !== "object" || data.format !== FORMAT) {
    throw new Error("Ce fichier n'est pas un export Watchlog (format inconnu).");
  }
  if (!Number.isInteger(data.version) || data.version > VERSION) {
    throw new Error(`Version de fichier ${JSON.stringify(data.version)} non prise en charge `
      + `(attendu : ${VERSION} au plus). Recharge la page pour avoir la dernière version.`);
  }
  return data;
}

function findShow(store, entry) {
  if (entry.tvmaze_id) {
    const show = store.get("shows", `tvmaze:${entry.tvmaze_id}`);
    if (show) return show;
  }
  for (const show of store.shows.values()) {
    if (entry.tvdb_id && show.tvdb_id === entry.tvdb_id) return show;
    if (!entry.tvmaze_id && !show.tvmaze_id && show.title === entry.title) return show;
  }
  return null;
}

function findMovie(store, entry) {
  if (entry.tmdb_id) return store.get("movies", `tmdb:${entry.tmdb_id}`) || null;
  for (const movie of store.movies.values()) {
    if (!movie.tmdb_id && movie.title === entry.title && (movie.year ?? null) === (entry.year ?? null)) {
      return movie;
    }
  }
  return null;
}

function fill(record, entry, names) {
  const next = { ...record };
  for (const name of names) {
    if (empty(next[name]) && !empty(entry[name])) next[name] = entry[name];
  }
  return next;
}

function importLibrary(store, kind, ref, value, report) {
  if (!value) return;
  if (!STATUSES.includes(value.status)) throw new Error(`statut inconnu : ${JSON.stringify(value.status)}`);
  const favorite = Boolean(value.favorite);
  const item = store.get("library", refKey(kind, ref));
  if (item && item.status === value.status && Boolean(item.favorite) === favorite) return;
  store.put("library", item ? { ...item, status: value.status, favorite } : {
    key: refKey(kind, ref), kind, ref, status: value.status, favorite,
    added_at: normInstant(value.added_at),
  });
  report.libraryChanges += 1;
}

function importWatch(store, kind, ref, value, report) {
  if (!value) return;
  const { changed } = upsertWatch(store, kind, ref, normInstant(value.watched_at),
    normInstant(value.last_watched_at), Number(value.plays) || 1);
  if (changed) report.watches += 1;
}

function importRating(store, kind, ref, value, report) {
  if (!value) return;
  try {
    cleanRating(value.rating);
  } catch {
    throw new Error(`note invalide : ${JSON.stringify(value.rating)}`);
  }
  const fields = {
    rating: value.rating ?? null, emotions: value.emotions ?? null,
    favorite_character: value.favorite_character ?? null, note: value.note ?? null,
  };
  const row = store.get("ratings", refKey(kind, ref));
  if (row && Object.entries(fields).every(([k, v]) => (row[k] ?? null) === v)) return;
  store.put("ratings", { key: refKey(kind, ref), kind, ref, ...fields });
  report.ratings += 1;
}

const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
// Champs typés : une valeur d'un autre type ferait planter l'affichage à chaque ouverture.
const TEXT_FIELDS = ["title", "original_title", "status", "network", "poster_url", "summary",
  "summary_fr", "genres", "overview", "imdb_id", "match_status"];
const INT_FIELDS = ["tvmaze_id", "tvdb_id", "tmdb_id", "year", "runtime"];
const DAY_FIELDS = ["premiered", "release_date_fr", "release_date_original"];

function checkTypes(obj, where, { text = [], ints = [], days = [], instants = [] }) {
  for (const f of text) {
    if (obj[f] != null && typeof obj[f] !== "string") throw new Error(`${f} invalide (${where})`);
  }
  for (const f of ints) {
    if (obj[f] != null && !Number.isInteger(obj[f])) throw new Error(`${f} invalide (${where})`);
  }
  for (const f of days) {
    if (obj[f] != null && !(typeof obj[f] === "string" && DAY_RE.test(obj[f])
      && Number.isFinite(Date.parse(obj[f])))) throw new Error(`date ${f} invalide (${where})`);
  }
  for (const f of instants) {
    if (obj[f] != null && !(typeof obj[f] === "string" && Number.isFinite(Date.parse(obj[f])))) {
      throw new Error(`date ${f} invalide (${where})`);
    }
  }
}

/** Instant ISO normalisé (« …:00Z », sans millisecondes) : les dates se comparent comme des
 *  chaînes, il faut donc un seul format. */
const normInstant = (v) => (v ? isoUtc(Date.parse(v)) : null);

function checkRating(value, where) {
  if (value == null) return;
  if (!isObject(value)) throw new Error(`avis illisible (${where})`);
  try {
    cleanRating(value.rating);
  } catch {
    throw new Error(`note invalide (${where}) : ${JSON.stringify(value.rating)}`);
  }
}

function checkWatch(value, where) {
  if (value == null) return;
  if (!isObject(value)) throw new Error(`visionnage illisible (${where})`);
  if (value.plays != null && !(Number.isInteger(value.plays) && value.plays >= 1)) {
    throw new Error(`nombre de visionnages invalide (${where})`);
  }
  checkTypes(value, where, { instants: ["watched_at", "last_watched_at"] });
}

/** Vérifie une série ou un film AVANT toute écriture : une entrée invalide est ignorée en
 *  entier, jamais importée à moitié. */
export function validateEntry(entry, kind) {
  if (!isObject(entry)) throw new Error("entrée illisible");
  if (typeof entry.title !== "string" || !entry.title.trim()) throw new Error("titre manquant");
  checkTypes(entry, "titre", { text: TEXT_FIELDS, ints: INT_FIELDS, days: DAY_FIELDS,
    instants: ["synced_at"] });
  if (entry.library != null) {
    if (!isObject(entry.library) || !STATUSES.includes(entry.library.status)) {
      throw new Error(`statut inconnu : ${JSON.stringify(entry.library?.status)}`);
    }
    checkTypes(entry.library, "bibliothèque", { instants: ["added_at"] });
  }
  checkRating(entry.rating, "titre");
  if (kind === MOVIE) {
    checkWatch(entry.watch, "film");
    return;
  }
  if (entry.episodes != null && !Array.isArray(entry.episodes)) throw new Error("épisodes illisibles");
  for (const item of entry.episodes || []) {
    if (!isObject(item) || !Number.isInteger(item.season)) throw new Error("épisode sans saison");
    if (item.number != null && !Number.isInteger(item.number)) throw new Error("numéro d'épisode invalide");
    const where = `S${item.season}E${item.number ?? "?"}`;
    checkTypes(item, where, { text: ["title", "title_fr"], ints: ["runtime", "tvmaze_episode_id"],
      days: ["airdate"], instants: ["airstamp"] });
    checkWatch(item.watch, where);
    checkRating(item.rating, where);
  }
}

function importShow(store, entry, report) {
  validateEntry(entry, SHOW);
  let show = findShow(store, entry);
  if (!show) {
    const base = Object.fromEntries(SHOW_FIELDS.filter((n) => !empty(entry[n]))
      .map((n) => [n, entry[n]]));
    show = { ...base, key: showKey({ ...entry, manual_id: newId() }),
      premiered: entry.premiered || null, is_manual: Boolean(entry.is_manual),
      synced_at: entry.synced_at || null };
    report.showsAdded += 1;
  } else {
    show = fill(show, entry, SHOW_FIELDS);
    show.premiered = show.premiered || entry.premiered || null;
  }
  store.put("shows", show);
  importLibrary(store, SHOW, show.key, entry.library, report);
  importRating(store, SHOW, show.key, entry.rating, report);

  const existing = store.episodesOf(show.key);
  const byTvmaze = new Map(existing.filter((e) => e.tvmaze_episode_id)
    .map((e) => [e.tvmaze_episode_id, e]));
  const byNumber = new Map(existing.filter((e) => e.number != null)
    .map((e) => [`${e.season}|${e.number}`, e]));
  // Spécial sans id TVMaze (série saisie à la main) : retrouvé par saison + titre.
  const bySpecial = new Map(existing.filter((e) => e.number == null)
    .map((e) => [`${e.season}|${e.title || ""}`, e]));
  for (const item of entry.episodes || []) {
    let ep = item.tvmaze_episode_id ? byTvmaze.get(item.tvmaze_episode_id) : null;
    if (!ep && item.number != null) ep = byNumber.get(`${item.season}|${item.number}`);
    if (!ep && item.number == null && !item.tvmaze_episode_id) {
      ep = bySpecial.get(`${item.season}|${item.title || ""}`);
    }
    if (!ep) {
      ep = {
        ...Object.fromEntries(EPISODE_FIELDS.map((n) => [n, item[n] ?? null])),
        is_special: Boolean(item.is_special), airdate: item.airdate || null,
        airstamp: normInstant(item.airstamp), show: show.key,
      };
      ep.key = episodeKey(show.key, ep);
      store.put("episodes", ep);
      report.episodesAdded += 1;
    }
    importWatch(store, EPISODE, ep.key, item.watch, report);
    importRating(store, EPISODE, ep.key, item.rating, report);
  }
  return show;
}

function importMovie(store, entry, report) {
  validateEntry(entry, MOVIE);
  let movie = findMovie(store, entry);
  if (!movie) {
    const base = Object.fromEntries(MOVIE_FIELDS.filter((n) => !empty(entry[n]))
      .map((n) => [n, entry[n]]));
    movie = { ...base, key: movieKey({ ...entry, manual_id: newId() }),
      is_manual: Boolean(entry.is_manual), synced_at: entry.synced_at || null };
    report.moviesAdded += 1;
  } else {
    movie = fill(movie, entry, MOVIE_FIELDS);
  }
  movie.release_date_fr = movie.release_date_fr || entry.release_date_fr || null;
  movie.release_date_original = movie.release_date_original || entry.release_date_original || null;
  store.put("movies", movie);
  importLibrary(store, MOVIE, movie.key, entry.library, report);
  importWatch(store, MOVIE, movie.key, entry.watch, report);
  importRating(store, MOVIE, movie.key, entry.rating, report);
  return movie;
}

/** Clés possibles d'un titre (pour retrouver les éléments des listes). */
function keysOf(entry, kind) {
  const keys = [];
  if (kind === SHOW && entry.tvmaze_id) keys.push(`tvmaze:${entry.tvmaze_id}`);
  if (kind === SHOW && entry.tvdb_id) keys.push(`tvdb:${entry.tvdb_id}`);
  if (kind === MOVIE && entry.tmdb_id) keys.push(`tmdb:${entry.tmdb_id}`);
  if (typeof entry.title === "string" && entry.title) {
    keys.push(`title:${entry.title.trim().toLowerCase()}|${entry.year ?? ""}`);
  }
  return keys;
}

function skip(report, label, entry, error) {
  const title = entry && typeof entry === "object" ? entry.title : null;
  console.warn(`Import JSON : ${label} ignoré (${title})`, error);
  report.skipped.push(`${label} « ${title || "?"} » : donnée invalide (${error.message})`);
}

function ignored(report, label, row) {
  console.warn(`Import JSON : ${label}`, row);
  report.skipped.push(label);
}

/** Fusionne un fichier d'échange dans le Store (voir l'en-tête du module). */
export function importData(store, data) {
  const report = new ExchangeReport();
  const ids = { [SHOW]: new Map(), [MOVIE]: new Map() };
  for (const [kind, entries, fn, label] of [[SHOW, data.shows, importShow, "Série"],
    [MOVIE, data.movies, importMovie, "Film"]]) {
    for (const entry of entries || []) {
      try {
        const record = fn(store, entry, report);
        for (const k of keysOf(entry, kind)) ids[kind].set(k, record.key);
      } catch (error) {
        skip(report, label, entry, error);
      }
    }
  }
  for (const entry of data.lists || []) {
    const name = isObject(entry) && typeof entry.name === "string" ? entry.name.trim() : "";
    if (!name) {
      ignored(report, "Liste sans nom ignorée", entry);
      continue;
    }
    let list = store.get("lists", name);
    if (!list) {
      list = { key: name, name, description: entry.description ?? null, items: [] };
      report.lists += 1;
    }
    const items = [...list.items];
    const present = new Set(items.map((i) => refKey(i.kind, i.ref)));
    for (const item of Array.isArray(entry.items) ? entry.items : []) {
      const ref = isObject(item) && Object.hasOwn(ids, item.kind)
        ? keysOf(item, item.kind).map((k) => ids[item.kind].get(k)).find(Boolean) : null;
      if (!ref) {
        console.warn(`Import JSON : élément de la liste ${name} introuvable`, item);
        report.skipped.push(`Liste « ${name} » : « ${item?.title || "?"} » introuvable`);
      } else if (!present.has(refKey(item.kind, ref))) {
        items.push({ kind: item.kind, ref });
        present.add(refKey(item.kind, ref));
      }
    }
    store.put("lists", { ...list, items });
  }
  for (const row of data.dismissed || []) {
    if (!isObject(row) || !row.tmdb_id || ![SHOW, MOVIE].includes(row.kind)) {
      ignored(report, "Titre écarté illisible ignoré", row);
      continue;
    }
    const key = `${row.kind}:${row.tmdb_id}`;
    if (!store.get("dismissed", key)) {
      store.put("dismissed", { key, kind: row.kind, tmdb_id: row.tmdb_id, title: row.title ?? null,
        dismissed_at: row.dismissed_at || isoUtc() });
    }
  }
  for (const row of data.theme_preferences || []) {
    if (!isObject(row) || !row.feature || ![1, -1].includes(row.weight)) {
      ignored(report, "Préférence de thème illisible ignorée", row);
      continue;
    }
    const pref = store.get("themes", row.feature);
    if (pref?.updated_at && row.updated_at && pref.updated_at >= row.updated_at) continue;
    store.put("themes", { key: row.feature, feature: row.feature, label: row.label || row.feature,
      weight: row.weight, updated_at: row.updated_at || isoUtc() });
  }
  const platforms = data.settings?.my_platforms;
  if (Array.isArray(platforms) && platforms.length && !store.setting("my_platforms", []).length) {
    store.setSetting("my_platforms", platforms);
  }
  return report;
}
