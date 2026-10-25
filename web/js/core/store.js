// Données de l'app en mémoire : une Map par collection, chaque enregistrement a une `key`.
// Toute modification passe par put/remove, qui notent la clé « à enregistrer » : la couche de
// stockage (storage/idb.js) n'écrit que ce qui a changé. Aucune dépendance au navigateur :
// testable sous Node.

export const COLLECTIONS = [
  "shows", "episodes", "movies", "library", "watches", "ratings", "lists", "dismissed",
  "themes", "settings",
];

export class Store {
  constructor() {
    for (const name of COLLECTIONS) this[name] = new Map();
    this.dirty = new Map(COLLECTIONS.map((name) => [name, new Set()]));
    this.byShow = new Map();          // clé de série -> Set des clés de ses épisodes
    this.listeners = new Set();
    this.version = 0;                 // augmente à chaque modification (caches des vues)
  }

  /** Charge des enregistrements sans les marquer à enregistrer (lecture du stockage). */
  load(name, records) {
    for (const record of records) {
      this[name].set(record.key, record);
      if (name === "episodes") this.#index(record);
    }
  }

  get(name, key) {
    return this[name].get(key);
  }

  put(name, record) {
    if (!record || record.key == null) throw new Error(`Enregistrement sans clé (${name})`);
    const previous = this[name].get(record.key);
    if (name === "episodes" && previous && previous.show !== record.show) {
      this.byShow.get(previous.show)?.delete(record.key);
    }
    this[name].set(record.key, record);
    if (name === "episodes") this.#index(record);
    this.#touch(name, record.key);
    return record;
  }

  remove(name, key) {
    const previous = this[name].get(key);
    if (!previous) return false;
    this[name].delete(key);
    if (name === "episodes") this.byShow.get(previous.show)?.delete(key);
    this.#touch(name, key);
    return true;
  }

  /** Épisodes d'une série, triés par saison puis numéro (spéciaux à la fin de leur saison). */
  episodesOf(showKey) {
    const keys = this.byShow.get(showKey) || new Set();
    return [...keys].map((k) => this.episodes.get(k)).filter(Boolean).sort(episodeOrder);
  }

  setting(key, fallback = null) {
    return this.settings.get(key)?.value ?? fallback;
  }

  setSetting(key, value) {
    this.put("settings", { key, value });
  }

  /** Clés modifiées depuis le dernier appel (et remise à zéro). */
  takeDirty() {
    const out = {};
    for (const [name, keys] of this.dirty) {
      if (keys.size) out[name] = [...keys];
      keys.clear();
    }
    return out;
  }

  onChange(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  #index(record) {
    if (!this.byShow.has(record.show)) this.byShow.set(record.show, new Set());
    this.byShow.get(record.show).add(record.key);
  }

  #touch(name, key) {
    this.dirty.get(name).add(key);
    this.version += 1;
    for (const listener of this.listeners) listener(name, key);
  }
}

/** Ordre des épisodes : saison, numéro (spécial = après les épisodes réguliers), date. */
export function episodeOrder(a, b) {
  return (a.season - b.season)
    || ((a.number ?? 10_000) - (b.number ?? 10_000))
    || String(a.airdate || "").localeCompare(String(b.airdate || ""));
}
