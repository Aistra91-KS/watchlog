// Fiches à partir des réponses des API (port de watchlog/core/catalog.py) :
// TVMaze pour les séries, TMDB pour les films et les textes français des épisodes.

import { isoUtc } from "./dates.js";
import { episodeKey, showKey } from "./model.js";

// Genres TVMaze (anglais) -> libellés français, alignés sur ceux de TMDB.
export const TVMAZE_GENRES = {
  Drama: "Drame", Comedy: "Comédie", Action: "Action", Adventure: "Aventure",
  "Science-Fiction": "Science-Fiction", Fantasy: "Fantastique", Crime: "Crime",
  Thriller: "Thriller", Horror: "Horreur", Mystery: "Mystère", Romance: "Romance",
  Family: "Familial", Children: "Jeunesse", Anime: "Anime", Supernatural: "Surnaturel",
  Music: "Musique", Medical: "Médical", Legal: "Judiciaire", Espionage: "Espionnage",
  History: "Histoire", War: "Guerre", Western: "Western", Sports: "Sport",
  Food: "Cuisine", Travel: "Voyage", Nature: "Nature", DIY: "Bricolage", Adult: "Adulte",
};

export const POSTER_BASE = "https://image.tmdb.org/t/p/w342";
// Types de sortie TMDB retenus pour la date France : 3 = cinéma, 4 = numérique.
const FR_RELEASE_TYPES = [3, 4];
// Écart maximal entre dates TVMaze et TMDB d'un même épisode (sinon numérotation différente).
const MAX_AIRDATE_GAP_DAYS = 3;
// Titre fabriqué par TMDB faute de traduction (« Épisode 5 ») : pas un vrai titre.
const GENERIC_EPISODE_TITLE = /^((é|e)pisod(e|io)\s*#?\s*[\d.]+|ep\.?\s*\d+|folge\s*\d+)$/i;

export function cleanHtml(text) {
  if (!text) return null;
  return text.replace(/<[^>]+>/g, "").trim() || null;
}

export function showGenres(data) {
  return (data.genres || []).map((g) => TVMAZE_GENRES[g] || g).join(", ") || null;
}

export function movieGenres(data) {
  return (data.genres || []).map((g) => g.name).filter(Boolean).join(", ") || null;
}

export function posterUrl(path) {
  return path ? `${POSTER_BASE}${path}` : null;
}

const day = (value) => (value ? String(value).slice(0, 10) : null);

/** Fiche série + épisodes depuis TVMaze. Le titre d'une série existante n'est pas changé. */
export function upsertShowFromTvmaze(store, data, episodes, { title = null } = {}) {
  const key = showKey({ tvmaze_id: data.id });
  const existing = store.get("shows", key);
  const externals = data.externals || {};
  const channel = data.network || data.webChannel || {};
  const show = {
    ...(existing || { key, title: title || data.name || "Sans titre", match_status: "ok" }),
    tvmaze_id: data.id,
    original_title: existing?.original_title || data.name || null,
    tvdb_id: existing?.tvdb_id || externals.thetvdb || null,
    imdb_id: externals.imdb || existing?.imdb_id || null,
    status: data.status || null,
    network: channel.name || null,
    premiered: day(data.premiered),
    poster_url: data.image?.medium || existing?.poster_url || null,
    summary: cleanHtml(data.summary),
    genres: showGenres(data) || existing?.genres || null,
    synced_at: isoUtc(),
  };
  store.put("shows", show);

  const all = new Map((data._embedded?.episodes || []).map((ep) => [ep.id, ep]));
  for (const ep of episodes || []) all.set(ep.id, ep);
  for (const raw of all.values()) {
    const ekey = episodeKey(key, { tvmaze_episode_id: raw.id });
    const old = store.get("episodes", ekey);
    const number = raw.number ?? null;
    store.put("episodes", {
      ...(old || {}), key: ekey, show: key, tvmaze_episode_id: raw.id,
      season: raw.season || 0, number, is_special: number == null, title: raw.name || null,
      title_fr: old?.title_fr ?? null, airdate: day(raw.airdate), airstamp: raw.airstamp
        ? isoUtc(Date.parse(raw.airstamp)) : null,
      // TVMaze donne souvent la durée du créneau (pub comprise) : la durée TMDB prime.
      tmdb_runtime: old?.tmdb_runtime ?? null, runtime: old?.tmdb_runtime || raw.runtime || null,
    });
  }
  return show;
}

/** (date de sortie en France, date d'origine) d'une fiche film TMDB. */
export function extractReleaseDates(movie) {
  const frDates = [];
  for (const country of movie.release_dates?.results || []) {
    if (country.iso_3166_1 !== "FR") continue;
    for (const release of country.release_dates || []) {
      if (FR_RELEASE_TYPES.includes(release.type) && release.release_date) {
        frDates.push(day(release.release_date));
      }
    }
  }
  frDates.sort();
  return [frDates[0] || null, day(movie.release_date)];
}

/** Fiche film depuis TMDB (get_movie). */
export function upsertMovieFromTmdb(store, data) {
  const key = `tmdb:${data.id}`;
  const existing = store.get("movies", key);
  const [fr, original] = extractReleaseDates(data);
  const movie = {
    ...(existing || { key, tmdb_id: data.id, title: data.title || "Sans titre" }),
    original_title: data.original_title || existing?.original_title || null,
    imdb_id: data.imdb_id || existing?.imdb_id || null,
    release_date_fr: fr, release_date_original: original,
    year: original ? Number(original.slice(0, 4)) : existing?.year ?? null,
    runtime: data.runtime || existing?.runtime || null,
    overview: data.overview || existing?.overview || null,
    genres: movieGenres(data) || existing?.genres || null,
    poster_url: posterUrl(data.poster_path) || existing?.poster_url || null,
    synced_at: isoUtc(),
  };
  return store.put("movies", movie);
}

export function frenchEpisodeTitle(name) {
  const text = (name || "").trim();
  return !text || GENERIC_EPISODE_TITLE.test(text) ? null : text;
}

/** Complète les épisodes d'une saison avec TMDB (durée réelle, titre français), si les dates
 *  concordent. Renvoie {runtimes, titles, unmatched}. */
export function applyTmdbSeason(store, showKeyValue, season, data) {
  const result = { runtimes: 0, titles: 0, unmatched: 0 };
  const tmdbEpisodes = new Map((data?.episodes || []).map((e) => [e.episode_number, e]));
  for (const ep of store.episodesOf(showKeyValue)) {
    if (ep.season !== season || ep.number == null) continue;
    const t = tmdbEpisodes.get(ep.number) || {};
    const runtime = t.runtime || null;
    const title = frenchEpisodeTitle(t.name);
    if (!runtime && !title && !ep.title_fr) continue;
    const tmdbDate = day(t.air_date);
    const gap = ep.airdate && tmdbDate
      ? Math.abs(Date.parse(ep.airdate) - Date.parse(tmdbDate)) / 86_400_000 : Infinity;
    if (gap > MAX_AIRDATE_GAP_DAYS) {
      result.unmatched += 1;
      continue;
    }
    const next = { ...ep, title_fr: title };
    if (runtime) {
      next.tmdb_runtime = runtime;
      if (ep.runtime !== runtime) result.runtimes += 1;
      next.runtime = runtime;
    }
    if (title && title !== ep.title_fr) result.titles += 1;
    store.put("episodes", next);
  }
  return result;
}
