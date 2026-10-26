// Aperçu d'un titre pas forcément dans la bibliothèque (page « Aperçu », depuis la recherche) :
// port de watchlog/core/preview.py (fiche TMDB), plus une fiche série TVMaze pour l'app web,
// où TMDB est facultatif (la recherche de séries passe par TVMaze).

import { TVMAZE_GENRES, cleanHtml, posterUrl } from "./catalog.js";
import { MOVIE, SHOW } from "./model.js";
import { shortText } from "./suggestions.js";

export const PROFILE_BASE = "https://image.tmdb.org/t/p/w185";
export const MAX_CAST = 10;
const OVERVIEW_LENGTH = 2000;

// Statuts TMDB traduits
export const STATUS_LABELS = {
  "Returning Series": "En cours de diffusion", Ended: "Terminée", Canceled: "Annulée",
  "In Production": "En production", Planned: "Prévue", Pilot: "Pilote",
  Released: "Sorti", "Post Production": "Post-production", Rumored: "Annoncé",
};

// Statuts de diffusion TVMaze traduits (comme TVMAZE_STATUS_LABELS de adding.py).
export const TVMAZE_STATUS_LABELS = {
  Running: "En cours de diffusion", Ended: "Terminée", "To Be Determined": "Indéterminé",
  "In Development": "En développement",
};

const day = (value) => (value ? String(value).slice(0, 10) : null);
const yearOf = (released) => (released ? Number(released.slice(0, 4)) || null : null);
const names = (list) => (list || []).map((x) => x?.name).filter(Boolean);

/** Meilleure vidéo YouTube : bande-annonce > teaser, en français d'abord, officielle d'abord. */
export function pickTrailer(videos) {
  const candidates = (videos || []).filter((v) => v.site === "YouTube" && v.key
    && (v.type === "Trailer" || v.type === "Teaser"));
  if (!candidates.length) return null;
  const rank = (v) => [v.type !== "Trailer", v.iso_639_1 !== "fr", !v.official].map(Number);
  const best = candidates.reduce((a, b) => {
    const [ra, rb] = [rank(a), rank(b)];
    const i = ra.findIndex((x, j) => x !== rb[j]);
    return i >= 0 && rb[i] < ra[i] ? b : a;
  });
  return `https://www.youtube.com/watch?v=${encodeURIComponent(best.key)}`;
}

/** Fiche TMDB (avec credits et videos) -> aperçu. */
export function fromTmdb(kind, data) {
  const credits = data.credits || {};
  const show = kind === SHOW;
  const title = show ? data.name : data.title;
  const original = (show ? data.original_name : data.original_title) || null;
  const released = day(show ? data.first_air_date : data.release_date);
  return {
    kind, tmdbId: data.id, tvmazeId: null, title: title || original || "Sans titre",
    originalTitle: original, released, year: yearOf(released), tagline: data.tagline || null,
    overview: shortText(data.overview, OVERVIEW_LENGTH), genres: names(data.genres),
    posterUrl: posterUrl(data.poster_path),
    voteAverage: Number(data.vote_average) || 0, voteCount: Number(data.vote_count) || 0,
    status: STATUS_LABELS[data.status] || data.status || null,
    runtime: (show ? data.episode_run_time?.[0] : data.runtime) || null,
    seasons: data.number_of_seasons ?? null, episodes: data.number_of_episodes ?? null,
    networks: names(data.networks),
    makers: show ? names(data.created_by)
      : (credits.crew || []).filter((c) => c.job === "Director").map((c) => c.name).filter(Boolean),
    cast: (credits.cast || []).slice(0, MAX_CAST).map((c) => ({
      name: c.name || "", role: c.character || null,
      photoUrl: c.profile_path ? `${PROFILE_BASE}${c.profile_path}` : null,
    })),
    trailerUrl: pickTrailer(data.videos?.results),
  };
}

/** Fiche série TVMaze (getShow, saisons et épisodes dans `_embedded`) -> aperçu. */
export function fromTvmaze(data) {
  const released = day(data.premiered);
  const episodes = data._embedded?.episodes;
  const seasons = data._embedded?.seasons;
  const channel = data.network || data.webChannel;
  return {
    kind: SHOW, tmdbId: null, tvmazeId: data.id, tvdbId: data.externals?.thetvdb || null,
    title: data.name || "Sans titre", originalTitle: data.name || null, released,
    year: yearOf(released), tagline: null,
    overview: shortText(cleanHtml(data.summary), OVERVIEW_LENGTH),
    genres: (data.genres || []).map((g) => TVMAZE_GENRES[g] || g),
    posterUrl: data.image?.medium || null,
    voteAverage: Number(data.rating?.average) || 0, voteCount: 0,
    status: TVMAZE_STATUS_LABELS[data.status] || data.status || null,
    runtime: data.averageRuntime || data.runtime || null,
    seasons: seasons ? seasons.filter((s) => s.number > 0).length || null : null,
    episodes: episodes ? episodes.length || null : null,
    networks: channel?.name ? [channel.name] : [], makers: [], cast: [], trailerUrl: null,
  };
}

// Champs de l'aperçu TVMaze gardés même si TMDB les connaît : saisons et épisodes comptés
// comme à l'ajout (numérotation TVMaze).
const TVMAZE_FIELDS = ["kind", "tvmazeId", "tvdbId", "seasons", "episodes"];

/** Complète l'aperçu TVMaze d'une série avec sa fiche TMDB (titre et résumé français, casting,
 *  bande-annonce, note, id TMDB pour les plateformes). Une valeur TMDB vide ne remplace rien. */
export function mergeTmdb(base, tmdb) {
  const merged = { ...base };
  for (const [field, value] of Object.entries(tmdb)) {
    if (TVMAZE_FIELDS.includes(field)) continue;
    if (value === null || value === "" || value === 0 || (Array.isArray(value) && !value.length)) continue;
    merged[field] = value;
  }
  return merged;
}

export const PREVIEW_KINDS = { serie: SHOW, film: MOVIE };
