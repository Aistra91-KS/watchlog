// Client HTTP des API (port de watchlog/core/http.py) : cache, débit limité, réessais.
// Le cache est repéré par le chemin + les paramètres publics : jamais la clé TMDB.

export class ApiError extends Error {
  constructor(message, { status = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// Réessais sur 429 et 5xx : attentes successives (ms), Retry-After prime s'il est donné.
const RETRY_DELAYS = [1000, 3000, 8000];
const RETRY_STATUSES = new Set([429, 500, 502, 503, 504]);
const DAY_MS = 86_400_000;

/** Au plus `limit` requêtes par fenêtre de `windowMs` (fenêtre glissante). */
export class RateLimiter {
  constructor(limit, windowMs, { now = () => Date.now(), sleep = defaultSleep } = {}) {
    Object.assign(this, { limit, windowMs, now, sleep, stamps: [], queue: Promise.resolve() });
  }

  acquire() {
    // File d'attente : les requêtes parallèles passent l'une après l'autre dans le limiteur.
    this.queue = this.queue.then(async () => {
      for (;;) {
        const t = this.now();
        this.stamps = this.stamps.filter((s) => t - s < this.windowMs);
        if (this.stamps.length < this.limit) {
          this.stamps.push(t);
          return;
        }
        await this.sleep(this.windowMs - (t - this.stamps[0]) + 5);
      }
    });
    return this.queue;
  }
}

function defaultSleep(ms) {
  return new Promise((resolve) => { setTimeout(resolve, ms); });
}

/** Cache en mémoire (tests) ; storage/idb.js fournit la même interface dans le navigateur. */
export class MemoryCache {
  constructor() { this.rows = new Map(); }

  async get(url) { return this.rows.get(url) || null; }

  async put(row) { this.rows.set(row.url, row); }
}

function encodeParams(params) {
  const entries = Array.isArray(params) ? params : Object.entries(params || {});
  return entries.filter(([, v]) => v !== undefined && v !== null)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
}

export class CachedClient {
  constructor(name, baseUrl, { cache, limiter, headers = {}, authParams = {},
    fetchFn = (...args) => fetch(...args), now = () => Date.now(), sleep = defaultSleep } = {}) {
    Object.assign(this, { name, baseUrl, cache, limiter, headers, authParams, fetchFn, now, sleep });
  }

  /** Clé de cache : chemin + paramètres publics (les identifiants n'y sont jamais). */
  cacheKey(path, params) {
    const query = encodeParams(params);
    return query ? `${this.name}:${path}?${query}` : `${this.name}:${path}`;
  }

  /** Réponse brute, avec réessais. ApiError lisible si le service ne répond pas. */
  async request(path, params) {
    const query = encodeParams([...(Array.isArray(params) ? params : Object.entries(params || {})),
      ...Object.entries(this.authParams)]);
    const url = `${this.baseUrl}${path}${query ? `?${query}` : ""}`;
    for (let attempt = 0; ; attempt += 1) {
      if (this.limiter) await this.limiter.acquire();
      let response;
      try {
        response = await this.fetchFn(url, { headers: { Accept: "application/json", ...this.headers } });
      } catch (error) {
        if (attempt < RETRY_DELAYS.length) {
          await this.sleep(RETRY_DELAYS[attempt]);
          continue;
        }
        throw new ApiError(`${this.name} injoignable (réseau coupé ?)`);
      }
      if (RETRY_STATUSES.has(response.status) && attempt < RETRY_DELAYS.length) {
        const retryAfter = Number(response.headers?.get?.("Retry-After"));
        await this.sleep(retryAfter > 0 ? retryAfter * 1000 : RETRY_DELAYS[attempt]);
        continue;
      }
      return response;
    }
  }

  /** JSON d'un endpoint, mis en cache `ttl` (ms, ou fonction(data) -> ms).
   *  404 -> null. `force` : ignore le cache et ne s'y replie pas en cas de panne. */
  async getJson(path, params = null, { ttl = DAY_MS, force = false } = {}) {
    const key = this.cacheKey(path, params);
    const cached = this.cache ? await this.cache.get(key) : null;
    if (cached && !force && cached.expires > this.now()) return cached.data;
    let response;
    try {
      response = await this.request(path, params);
    } catch (error) {
      if (cached && !force) {
        console.warn(`${this.name} : ${path} en panne, ancienne copie utilisée`, error);
        return cached.data;
      }
      throw error;
    }
    if (response.status === 404) {
      await this.#store(key, null, DAY_MS);
      return null;
    }
    if (response.status === 401) throw new ApiError(`${this.name} : identifiants refusés`, { status: 401 });
    if (!response.ok) {
      if (cached && !force) {
        console.warn(`${this.name} : ${path} HTTP ${response.status}, ancienne copie utilisée`);
        return cached.data;
      }
      throw new ApiError(`${this.name} : erreur HTTP ${response.status}`, { status: response.status });
    }
    let data;
    try {
      data = await response.json();
    } catch {
      throw new ApiError(`${this.name} : réponse illisible (réseau filtré ?)`);
    }
    await this.#store(key, data, typeof ttl === "function" ? ttl(data) : ttl);
    return data;
  }

  async #store(url, data, ttlMs) {
    if (!this.cache) return;
    try {
      await this.cache.put({ url, data, fetched_at: this.now(), expires: this.now() + ttlMs });
    } catch (error) {
      // Cache plein ou indisponible : la donnée reste valable, on le signale seulement.
      console.warn(`${this.name} : réponse non mise en cache (${url})`, error);
    }
  }
}

export { DAY_MS };
