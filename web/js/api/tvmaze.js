// Client TVMaze (séries) : API publique sans clé, CORS autorisé. Données CC BY-SA.

import { CachedClient, DAY_MS, RateLimiter } from "./http.js";

export const BASE_URL = "https://api.tvmaze.com";
export const ENDED = "Ended";

/** Validité du cache : 7 jours pour une série terminée, 1 jour sinon. */
export function showTtl(status) {
  return status === ENDED ? 7 * DAY_MS : DAY_MS;
}

export class TVMazeClient {
  constructor({ cache, fetchFn, now, sleep } = {}) {
    // Limite TVMaze : environ 20 requêtes / 10 s par adresse IP.
    const limiter = new RateLimiter(20, 10_000, { now, sleep });
    this.http = new CachedClient("TVMaze", BASE_URL, { cache, limiter, fetchFn, now, sleep });
  }

  async search(query) {
    return (await this.http.getJson("/search/shows", { q: query }, { ttl: DAY_MS })) || [];
  }

  /** Fiche avec saisons et épisodes (hors spéciaux) dans `_embedded`. */
  getShow(id, force = false) {
    return this.http.getJson(`/shows/${id}`, [["embed[]", "seasons"], ["embed[]", "episodes"]],
      { force, ttl: (data) => showTtl(data?.status) });
  }

  /** Tous les épisodes, spéciaux compris (number = null pour un spécial). */
  async getEpisodesWithSpecials(id, status, force = false) {
    return (await this.http.getJson(`/shows/${id}/episodes`, { specials: 1 },
      { ttl: showTtl(status), force })) || [];
  }

  /** {id TVMaze: horodatage de dernière modification} pour since = day, week ou month. */
  async updatedSince(since = "week") {
    const data = (await this.http.getJson("/updates/shows", { since }, { ttl: 3_600_000 })) || {};
    return new Map(Object.entries(data).map(([k, v]) => [Number(k), v]));
  }
}
