// Client TMDB (films, recommandations, mots-clés, plateformes). Identifiants saisis par
// l'utilisateur dans Paramètres et gardés dans son navigateur : jeton de lecture (en-tête
// Bearer, commence par « eyJ ») ou clé d'API v3 (paramètre api_key). Jamais dans le cache.

import { extractReleaseDates } from "../core/catalog.js";
import { CachedClient, DAY_MS, RateLimiter } from "./http.js";

export const BASE_URL = "https://api.themoviedb.org/3";
export const ATTRIBUTION = "This product uses the TMDB API but is not endorsed or certified by TMDB.";

/** {headers, authParams} pour une valeur saisie (jeton v4 ou clé v3). */
export function credentials(secret) {
  const value = (secret || "").trim();
  if (!value) return { headers: {}, authParams: {} };
  return value.startsWith("eyJ")
    ? { headers: { Authorization: `Bearer ${value}` }, authParams: {} }
    : { headers: {}, authParams: { api_key: value } };
}

function movieTtl(movie) {
  const today = new Date().toISOString().slice(0, 10);
  const released = extractReleaseDates(movie || {}).some((d) => d && d <= today);
  return released ? 30 * DAY_MS : DAY_MS;
}

export class TMDBClient {
  constructor({ secret = "", cache, fetchFn, now, sleep } = {}) {
    const { headers, authParams } = credentials(secret);
    this.configured = Boolean(secret && secret.trim());
    const limiter = new RateLimiter(20, 1000, { now, sleep });
    this.http = new CachedClient("TMDB", BASE_URL, { cache, limiter, headers, authParams,
      fetchFn, now, sleep });
    this.connected = null;
  }

  /** Test GET /authentication : {ok, message}. Jamais les identifiants dans le message. */
  async checkConnection() {
    if (!this.configured) {
      this.connected = false;
      return { ok: false, message: "TMDB non connecté : saisis ta clé dans Paramètres." };
    }
    let response;
    try {
      response = await this.http.request("/authentication", null);
    } catch {
      this.connected = false;
      return { ok: false, message: "TMDB injoignable pour l'instant (réseau ?)." };
    }
    let success = false;
    try {
      success = response.status === 200 && (await response.json()).success;
    } catch {
      console.warn(`TMDB : réponse illisible au test de connexion (HTTP ${response.status})`);
    }
    this.connected = Boolean(success);
    return success ? { ok: true, message: "TMDB connecté." }
      : { ok: false, message: "TMDB : clé refusée, vérifie-la dans Paramètres." };
  }

  async available() {
    if (this.connected === null) await this.checkConnection();
    return this.connected;
  }

  async #get(path, params, ttl) {
    if (!(await this.available())) return null;
    return this.http.getJson(path, params, { ttl });
  }

  getMovie(id) {
    return this.#get(`/movie/${id}`, { language: "fr-FR", append_to_response: "release_dates" },
      movieTtl);
  }

  async searchMovies(query) {
    return (await this.#get("/search/movie", { query, language: "fr-FR" }, DAY_MS))?.results || [];
  }

  async searchTv(query) {
    return (await this.#get("/search/tv", { query, language: "fr-FR" }, DAY_MS))?.results || [];
  }

  async recommendations(media, id) {
    return (await this.#get(`/${media}/${id}/recommendations`, { language: "fr-FR" },
      30 * DAY_MS))?.results || [];
  }

  /** Mots-clés ([{id, name}], en anglais). Films : « keywords » ; séries : « results ». */
  async keywords(media, id) {
    const data = (await this.#get(`/${media}/${id}/keywords`, null, 90 * DAY_MS)) || {};
    return data.keywords || data.results || [];
  }

  async discoverByKeyword(media, keywordId, minVotes = 100) {
    return (await this.#get(`/discover/${media}`, {
      with_keywords: keywordId, sort_by: "vote_count.desc", "vote_count.gte": minVotes,
      language: "fr-FR",
    }, 7 * DAY_MS))?.results || [];
  }

  async trending(media) {
    return (await this.#get(`/trending/${media}/week`, { language: "fr-FR" }, DAY_MS))?.results || [];
  }

  async genres(media) {
    const data = await this.#get(`/genre/${media}/list`, { language: "fr-FR" }, 30 * DAY_MS);
    return new Map((data?.genres || []).map((g) => [g.id, g.name]));
  }

  details(media, id) {
    return this.#get(`/${media}/${id}`, { language: "fr-FR", append_to_response: "credits,videos",
      include_video_language: "fr,en,null" }, 7 * DAY_MS);
  }

  /** Plateformes par pays ({"FR": {flatrate: [...], ...}}), données JustWatch. */
  async watchProviders(media, id) {
    return (await this.#get(`/${media}/${id}/watch/providers`, null, 7 * DAY_MS))?.results || {};
  }

  async providerList(media, region = "FR") {
    return (await this.#get(`/watch/providers/${media}`, { watch_region: region, language: "fr-FR" },
      7 * DAY_MS))?.results || [];
  }

  async findTvByTvdb(tvdbId) {
    const data = await this.#get(`/find/${tvdbId}`, { external_source: "tvdb_id" }, 30 * DAY_MS);
    return data?.tv_results?.[0]?.id ?? null;
  }

  async tvExternalIds(id) {
    return (await this.#get(`/tv/${id}/external_ids`, null, 30 * DAY_MS)) || {};
  }

  tvSeason(id, season) {
    return this.#get(`/tv/${id}/season/${season}`, { language: "fr-FR" }, 7 * DAY_MS);
  }
}
