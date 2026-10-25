// Constantes partagées (mêmes valeurs que watchlog/core/models.py) et clés des enregistrements.

export const SHOW = "show";
export const EPISODE = "episode";
export const MOVIE = "movie";

export const WATCHING = "watching";
export const TO_WATCH = "to_watch";
export const FOR_LATER = "for_later";
export const STOPPED = "stopped";
export const COMPLETED = "completed";
export const STATUSES = [WATCHING, TO_WATCH, FOR_LATER, STOPPED, COMPLETED];
export const STATUS_LABELS = {
  [WATCHING]: "En cours", [TO_WATCH]: "À voir", [FOR_LATER]: "Plus tard",
  [STOPPED]: "Arrêté", [COMPLETED]: "Terminé",
};

// Correspondance d'une série avec TVMaze (import Bingers côté Python)
export const MATCH_OK = "ok";
export const MATCH_REMAPPED = "remapped";
export const MATCH_TO_CHECK = ["uncertain", "not_found", "numbering_mismatch"];

// Statut TVMaze d'une série finie
export const ENDED = "Ended";

// Émotions (codes Bingers -> libellés), comme library.EMOTION_LABELS
export const EMOTION_LABELS = {
  touched: "Ému", thrilled: "Captivé", scared: "Effrayé", amused: "Amusé",
  sad: "Triste", shocked: "Choqué", inspired: "Inspiré", bored: "Ennuyé",
};

/** Identifiant court et unique pour une saisie manuelle (pas d'id TVMaze / TMDB). */
export function newId() {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

/** Clé d'une série : TVMaze si connu, sinon saisie manuelle. */
export function showKey(entry) {
  return entry.tvmaze_id ? `tvmaze:${entry.tvmaze_id}` : `manual:${entry.manual_id || newId()}`;
}

/** Clé d'un film : TMDB si connu, sinon saisie manuelle. */
export function movieKey(entry) {
  return entry.tmdb_id ? `tmdb:${entry.tmdb_id}` : `manual:${entry.manual_id || newId()}`;
}

/** Clé d'un épisode : id TVMaze, sinon série + saison + numéro (ou titre pour un spécial). */
export function episodeKey(show, ep) {
  if (ep.tvmaze_episode_id) return `ep:${ep.tvmaze_episode_id}`;
  const position = ep.number ?? `sp:${ep.title || ""}`;
  return `${show}|${ep.season}|${position}`;
}

/** Clé d'une ligne liée à un titre (bibliothèque, visionnage, note). */
export function refKey(kind, ref) {
  return `${kind}:${ref}`;
}

/** « S01E02 » ou « S01 spécial ». */
export function episodeCode(season, number) {
  const s = String(season).padStart(2, "0");
  return number == null ? `S${s} spécial` : `S${s}E${String(number).padStart(2, "0")}`;
}

/** Titre d'épisode à afficher : français (TMDB) s'il existe, sinon TVMaze. */
export function episodeTitle(ep) {
  return ep.title_fr || ep.title || null;
}

/** Résumé d'une série à afficher : français (TMDB) s'il existe, sinon TVMaze. */
export function showSummary(show) {
  return show.summary_fr || show.summary || null;
}

/** « Drame, Comédie » -> ["Drame", "Comédie"]. */
export function splitGenres(genres) {
  return (genres || "").split(",").map((g) => g.trim()).filter(Boolean);
}

/** Minuscules sans accents, pour la recherche (« Été » -> « ete »). */
export function normalize(text) {
  return (text || "").normalize("NFD").replace(/\p{Mn}/gu, "").toLowerCase();
}
