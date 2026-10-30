// Aperçu d'un film ou d'une série TMDB pas forcément dans la bibliothèque (port de
// preview.py) : page « Aperçu », ouverte depuis les suggestions. Résumé, casting,
// bande-annonce, titres similaires.

import { posterUrl } from "./catalog.js";
import { MOVIE, SHOW, refKey } from "./model.js";
import { shortText } from "./suggestions.js";

export const TMDB_MEDIA = { [SHOW]: "tv", [MOVIE]: "movie" };
const BACKDROP_BASE = "https://image.tmdb.org/t/p/w1280";
export const PROFILE_BASE = "https://image.tmdb.org/t/p/w185";
const MAX_CAST = 10;
const OVERVIEW_LENGTH = 2000;   // résumé complet (adding.short_text(length=2000))

// Statuts TMDB traduits
export const STATUS_LABELS = {
  "Returning Series": "En cours de diffusion", Ended: "Terminée", Canceled: "Annulée",
  "In Production": "En production", Planned: "Prévue", Pilot: "Pilote", Released: "Sorti",
  "Post Production": "Post-production", Rumored: "Annoncé",
};

/** « AAAA-MM-JJ » valide, sinon null (TMDB renvoie parfois une chaîne vide). */
function parseDay(value) {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
}

/** Meilleure vidéo YouTube : bande-annonce > teaser, en français d'abord, officielle d'abord. */
export function pickTrailer(videos) {
  const candidates = (videos || []).filter((v) => v.site === "YouTube" && v.key
    && (v.type === "Trailer" || v.type === "Teaser"));
  if (!candidates.length) return null;
  const rank = (v) => [v.type !== "Trailer", v.iso_639_1 !== "fr", !v.official].map(Number);
  const best = candidates.reduce((a, b) => {
    const [ra, rb] = [rank(a), rank(b)];
    const diff = ra.findIndex((x, i) => x !== rb[i]);
    return diff !== -1 && rb[diff] < ra[diff] ? b : a;
  });
  return `https://www.youtube.com/watch?v=${best.key}`;
}

/** Fiche TMDB (avec credits et videos) -> aperçu. */
export function fromTmdb(kind, data) {
  const credits = data.credits || {};
  let title;
  let original;
  let released;
  let runtime;
  let makers;
  if (kind === SHOW) {
    [title, original] = [data.name, data.original_name];
    released = parseDay(data.first_air_date);
    runtime = (data.episode_run_time || [])[0] || null;
    makers = (data.created_by || []).map((c) => c.name).filter(Boolean);
  } else {
    [title, original] = [data.title, data.original_title];
    released = parseDay(data.release_date);
    runtime = data.runtime || null;
    makers = (credits.crew || []).filter((c) => c.job === "Director").map((c) => c.name);
  }
  return {
    kind, tmdbId: data.id, title: title || original || "Sans titre", originalTitle: original || null,
    released, year: released ? Number(released.slice(0, 4)) : null, tagline: data.tagline || null,
    overview: shortText(data.overview, OVERVIEW_LENGTH),
    genres: (data.genres || []).map((g) => g.name).filter(Boolean),
    posterUrl: posterUrl(data.poster_path),
    backdropUrl: data.backdrop_path ? `${BACKDROP_BASE}${data.backdrop_path}` : null,
    voteAverage: Number(data.vote_average) || 0, voteCount: Number(data.vote_count) || 0,
    status: STATUS_LABELS[data.status] || data.status || null, runtime,
    seasons: data.number_of_seasons ?? null, episodes: data.number_of_episodes ?? null,
    networks: (data.networks || []).map((n) => n.name).filter(Boolean), makers,
    cast: (credits.cast || []).slice(0, MAX_CAST).map((c) => ({ name: c.name || "",
      role: c.character || null, photoUrl: c.profile_path ? `${PROFILE_BASE}${c.profile_path}` : null })),
    trailerUrl: pickTrailer(data.videos?.results),
  };
}

/** Aperçu depuis TMDB. null si introuvable ; ApiError si TMDB ne répond pas. */
export async function load(tmdb, kind, tmdbId) {
  const data = await tmdb.details(TMDB_MEDIA[kind], tmdbId);
  return data ? fromTmdb(kind, data) : null;
}

/** Titres similaires (recommandations TMDB) : {id, title, year, posterUrl}. Une panne n'est
 *  pas grave ici (bloc facultatif) : liste vide, mais notée dans la console. */
export async function similar(tmdb, kind, tmdbId, limit = 6) {
  let results;
  try {
    results = await tmdb.recommendations(TMDB_MEDIA[kind], tmdbId);
  } catch (error) {
    console.warn(`Titres similaires non lus (${kind} ${tmdbId})`, error);
    return [];
  }
  return results.slice(0, limit).map((r) => {
    const released = parseDay(kind === SHOW ? r.first_air_date : r.release_date);
    return { id: r.id, title: r.name || r.title || "Sans titre",
      year: released ? Number(released.slice(0, 4)) : null, posterUrl: posterUrl(r.poster_path) };
  });
}

/** Clé du titre dans la bibliothèque (pour proposer sa fiche), sinon null. */
export function libraryKey(store, kind, tmdbId) {
  const records = kind === SHOW ? store.shows.values() : store.movies.values();
  for (const record of records) {
    if (record.tmdb_id === tmdbId && store.get("library", refKey(kind, record.key))) return record.key;
  }
  return null;
}
