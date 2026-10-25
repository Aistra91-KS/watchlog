// Préférences d'affichage (filtres, tris) gardées dans ce navigateur. Simple confort : si le
// stockage local est bloqué, les valeurs par défaut s'appliquent.

const PREFIX = "watchlog.prefs.";

export function loadPrefs(name, defaults) {
  try {
    const saved = JSON.parse(localStorage.getItem(PREFIX + name) || "null");
    return saved && typeof saved === "object" ? { ...defaults, ...saved } : { ...defaults };
  } catch {
    return { ...defaults };
  }
}

export function savePrefs(name, value) {
  try {
    localStorage.setItem(PREFIX + name, JSON.stringify(value));
  } catch (error) {
    console.warn(`Préférences « ${name} » non gardées`, error.name);
  }
}
