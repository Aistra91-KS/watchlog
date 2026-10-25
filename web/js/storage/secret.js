// Clé ou jeton TMDB de l'utilisateur : gardé dans CE navigateur seulement (localStorage), à
// part des données (jamais dans IndexedDB, l'export JSON, le cache des API ni les logs).

const KEY = "watchlog.tmdb_secret";

export function readSecret() {
  try {
    return localStorage.getItem(KEY) || "";
  } catch (error) {
    console.warn("Clé TMDB : stockage local inaccessible", error.name);
    return "";
  }
}

export function saveSecret(value) {
  const clean = (value || "").trim();
  if (clean) localStorage.setItem(KEY, clean);
  else localStorage.removeItem(KEY);
  return clean;
}

/** « eyJh…X9k » : assez pour reconnaître sa clé, jamais la valeur entière à l'écran. */
export function maskSecret(value) {
  if (!value) return "";
  return value.length <= 8 ? "••••" : `${value.slice(0, 4)}…${value.slice(-3)}`;
}
