// Stockage dans le navigateur (IndexedDB) : une table par collection du Store, plus le cache
// des API. Rien ne quitte l'appareil. La clé TMDB est à part (localStorage, secret.js).

import { COLLECTIONS, Store } from "../core/store.js";

const DB_NAME = "watchlog";
const DB_VERSION = 1;
const CACHE = "apicache";
// Au-delà, les réponses d'API les plus anciennes sont effacées au démarrage.
const CACHE_MAX_AGE_MS = 120 * 86_400_000;

function promisify(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function done(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("Écriture annulée"));
  });
}

export function openDb() {
  const request = indexedDB.open(DB_NAME, DB_VERSION);
  request.onupgradeneeded = () => {
    const db = request.result;
    for (const name of COLLECTIONS) {
      if (!db.objectStoreNames.contains(name)) db.createObjectStore(name, { keyPath: "key" });
    }
    if (!db.objectStoreNames.contains(CACHE)) db.createObjectStore(CACHE, { keyPath: "url" });
  };
  return promisify(request);
}

/** Lit toutes les collections dans un Store neuf. */
export async function loadStore(db) {
  const store = new Store();
  const tx = db.transaction(COLLECTIONS, "readonly");
  const results = await Promise.all(COLLECTIONS.map((name) => promisify(tx.objectStore(name).getAll())));
  COLLECTIONS.forEach((name, i) => store.load(name, results[i]));
  return store;
}

/** Écrit ce qui a changé dans le Store. Une erreur remonte : l'appelant l'affiche. */
export async function persist(db, store) {
  const dirty = store.takeDirty();
  const names = Object.keys(dirty);
  if (!names.length) return 0;
  const tx = db.transaction(names, "readwrite");
  let count = 0;
  for (const name of names) {
    const table = tx.objectStore(name);
    for (const key of dirty[name]) {
      const record = store.get(name, key);
      if (record) table.put(record);
      else table.delete(key);
      count += 1;
    }
  }
  try {
    await done(tx);
  } catch (error) {
    // Rien n'est perdu en mémoire : on remet les clés à écrire pour le prochain essai.
    for (const name of names) for (const key of dirty[name]) store.dirty.get(name).add(key);
    throw error;
  }
  return count;
}

/** Efface toutes les données de l'app (bibliothèque et cache). */
export async function clearAll(db) {
  const names = [...COLLECTIONS, CACHE];
  const tx = db.transaction(names, "readwrite");
  for (const name of names) tx.objectStore(name).clear();
  await done(tx);
}

/** Cache des réponses d'API, même interface que http.MemoryCache. */
export class IdbCache {
  constructor(db) { this.db = db; }

  async get(url) {
    return (await promisify(this.db.transaction(CACHE).objectStore(CACHE).get(url))) || null;
  }

  async put(row) {
    const tx = this.db.transaction(CACHE, "readwrite");
    tx.objectStore(CACHE).put(row);
    await done(tx);
  }

  /** Supprime les réponses trop anciennes. Renvoie le nombre de lignes effacées. */
  async prune(now = Date.now()) {
    const tx = this.db.transaction(CACHE, "readwrite");
    const table = tx.objectStore(CACHE);
    let removed = 0;
    await new Promise((resolve, reject) => {
      const cursor = table.openCursor();
      cursor.onsuccess = () => {
        const c = cursor.result;
        if (!c) { resolve(); return; }
        if (now - (c.value.fetched_at || 0) > CACHE_MAX_AGE_MS) { c.delete(); removed += 1; }
        c.continue();
      };
      cursor.onerror = () => reject(cursor.error);
    });
    await done(tx);
    return removed;
  }

  async clear() {
    const tx = this.db.transaction(CACHE, "readwrite");
    tx.objectStore(CACHE).clear();
    await done(tx);
  }
}

/** Demande au navigateur de ne pas effacer les données (sinon possible si l'espace manque). */
export async function askPersistent() {
  if (!navigator.storage?.persist) return null;
  try {
    return (await navigator.storage.persisted()) || (await navigator.storage.persist());
  } catch (error) {
    console.warn("Stockage persistant : demande impossible", error);
    return null;
  }
}

export async function storageEstimate() {
  try {
    return navigator.storage?.estimate ? await navigator.storage.estimate() : null;
  } catch {
    return null;
  }
}
