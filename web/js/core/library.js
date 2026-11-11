// Actions de l'utilisateur sur sa bibliothèque (port de watchlog/core/library.py) :
// marquer vu, statut, favori, note. Mêmes règles que l'app Python.

import { isoUtc, localDayToUtc, today as todayParis, localDate } from "./dates.js";
import {
  COMPLETED, ENDED, EPISODE, FOR_LATER, MATCH_OK, MATCH_REMAPPED, MOVIE, SHOW, STATUSES, STOPPED,
  TO_WATCH, WATCHING, refKey,
} from "./model.js";

// Statuts d'une série choisis à la main : jamais remplacés par le statut automatique
// (« À voir », « En cours », « Terminé », déduits des épisodes vus), sauf série finie et
// entièrement vue.
export const MANUAL_SHOW_STATUSES = [FOR_LATER, STOPPED];

function earliest(a, b) {
  if (!a) return b || null;
  if (!b) return a;
  return a < b ? a : b;
}

function latest(a, b) {
  if (!a) return b || null;
  if (!b) return a;
  return a > b ? a : b;
}

// --- Visionnages -------------------------------------------------------------

export function getWatch(store, kind, ref) {
  return store.get("watches", refKey(kind, ref)) || null;
}

/** Ajoute un visionnage ou le fusionne (premier = le plus ancien, dernier = le plus récent,
 *  plays = le maximum) : relancer la même fusion ne change rien. */
export function upsertWatch(store, kind, ref, watchedAt = null, lastWatchedAt = null, plays = 1) {
  const last = lastWatchedAt || watchedAt;
  const existing = getWatch(store, kind, ref);
  const next = existing
    ? {
      ...existing,
      watched_at: earliest(existing.watched_at, watchedAt),
      last_watched_at: latest(existing.last_watched_at, last),
      plays: Math.max(existing.plays || 1, plays || 1),
    }
    : {
      key: refKey(kind, ref), kind, ref, watched_at: watchedAt, last_watched_at: last,
      plays: Math.max(plays || 1, 1),
    };
  const changed = !existing || existing.watched_at !== next.watched_at
    || existing.last_watched_at !== next.last_watched_at || existing.plays !== next.plays;
  if (changed) store.put("watches", next);
  return { watch: next, changed };
}

/** Premier visionnage d'un titre ; un film de la bibliothèque passe alors en « Terminé »
 *  (vu, il n'a plus rien « à voir » : library._first_watch), le statut de la série d'un
 *  épisode est recalculé. `at` : null = date inconnue. */
function firstWatch(store, kind, ref, at) {
  const watch = store.put("watches", {
    key: refKey(kind, ref), kind, ref, watched_at: at, last_watched_at: at, plays: 1,
  });
  const item = kind === MOVIE ? getLibraryItem(store, MOVIE, ref) : null;
  if (item && item.status !== COMPLETED) store.put("library", { ...item, status: COMPLETED });
  if (kind === EPISODE) refreshEpisodeShow(store, ref);
  return watch;
}

/** Marque un épisode ou un film comme vu (rien si c'est déjà le cas) ; un film de la
 *  bibliothèque passe en « Terminé ». */
export function markWatched(store, kind, ref, when = null) {
  const existing = getWatch(store, kind, ref);
  if (existing) return existing;
  return firstWatch(store, kind, ref, when || isoUtc());
}

/** Retire le visionnage. Un film « Terminé » n'est plus vu : il repasse en `backTo`
 *  (« Annuler » remet le statut d'avant), « À voir » par défaut ; le statut de la série
 *  d'un épisode est recalculé. */
export function unmarkWatched(store, kind, ref, backTo = null) {
  const removed = store.remove("watches", refKey(kind, ref));
  const item = kind === MOVIE ? getLibraryItem(store, MOVIE, ref) : null;
  if (item && item.status === COMPLETED) {
    store.put("library", { ...item, status: STATUSES.includes(backTo) ? backTo : TO_WATCH });
  }
  if (removed && kind === EPISODE) refreshEpisodeShow(store, ref);
  return removed;
}

/** Date de visionnage choisie par l'utilisateur (un jour, midi heure de Paris) ; marque vu
 *  si ce n'était pas le cas (un film passe alors en « Terminé »). */
export function setWatchDate(store, kind, ref, day) {
  const at = day ? localDayToUtc(day) : null;
  const existing = getWatch(store, kind, ref);
  if (!existing) return firstWatch(store, kind, ref, at);
  return store.put("watches", {
    ...existing,
    watched_at: at, last_watched_at: existing.plays > 1 ? existing.last_watched_at : at,
  });
}

/** Moment d'un revisionnage : l'heure exacte si c'est aujourd'hui (ou sans date), sinon midi. */
function rewatchTime(day) {
  return !day || day === todayParis() ? isoUtc() : localDayToUtc(day);
}

/** Un visionnage de plus (revisionnage, library.add_rewatch) daté de `day` (vide ou
 *  aujourd'hui : maintenant). Le dernier visionnage n'avance que si la date est plus
 *  récente (un revisionnage oublié, saisi après coup, ne le recule pas). Pas encore vu :
 *  vu ce jour-là. */
export function addRewatch(store, kind, ref, day = null) {
  const when = rewatchTime(day);
  const existing = getWatch(store, kind, ref);
  if (!existing) return markWatched(store, kind, ref, when);
  const last = [existing.last_watched_at, when].filter(Boolean).sort().at(-1);
  return store.put("watches", { ...existing, plays: (existing.plays || 1) + 1,
    last_watched_at: last });
}

/** Retire un revisionnage (plays - 1, jamais sous 1) ; revenu à un seul visionnage, le dernier
 *  visionnage redevient le premier. */
export function removeRewatch(store, kind, ref) {
  const existing = getWatch(store, kind, ref);
  if (!existing || (existing.plays || 1) <= 1) return existing;
  const plays = existing.plays - 1;
  return store.put("watches", { ...existing, plays,
    last_watched_at: plays === 1 ? existing.watched_at : existing.last_watched_at });
}

/** Épisode déjà diffusé (date française au plus tard aujourd'hui). */
export function isAired(ep, day = todayParis()) {
  const date = localDate(ep.airstamp, ep.airdate);
  return Boolean(date && date <= day);
}

function markEpisodes(store, episodes) {
  let count = 0;
  const now = isoUtc();
  for (const ep of episodes) {
    if (!getWatch(store, EPISODE, ep.key)) {
      markWatched(store, EPISODE, ep.key, now);
      count += 1;
    }
  }
  return count;
}

/** Toute une saison vue (épisodes déjà diffusés ; spéciaux si demandé). */
export function markSeasonWatched(store, showKey, season, includeSpecials = false, day) {
  const episodes = store.episodesOf(showKey).filter((ep) => ep.season === season
    && (includeSpecials || !ep.is_special) && isAired(ep, day));
  return markEpisodes(store, episodes);
}

/** « J'en suis là » : tous les épisodes jusqu'à celui-ci compris (ordre saison / numéro). */
export function markWatchedUntil(store, episodeKey, includeSpecials = false) {
  const target = store.get("episodes", episodeKey);
  if (!target) return 0;
  const episodes = [];
  for (const ep of store.episodesOf(target.show)) {
    if (ep.is_special && !includeSpecials && ep.key !== target.key) continue;
    episodes.push(ep);
    if (ep.key === target.key) break;
  }
  return markEpisodes(store, episodes);
}

/** Change d'un coup la date des épisodes déjà vus d'une série, toutes saisons ou celles
 *  données (library.set_show_watch_dates). Pour une série vue il y a longtemps, cochée d'un
 *  bloc : sans ça, tout tombe à la date du jour et fausse les statistiques. day = null : date
 *  inconnue. Les épisodes pas vus ne sont pas touchés. Renvoie le nombre d'épisodes changés. */
export function setShowWatchDates(store, showKey, day, seasons = null) {
  const episodes = store.episodesOf(showKey).filter((ep) => getWatch(store, EPISODE, ep.key)
    && (!seasons || seasons.includes(ep.season)));
  for (const ep of episodes) setWatchDate(store, EPISODE, ep.key, day);
  return episodes.length;
}

function watchedEpisodes(store, showKey, seasons, includeSpecials) {
  return store.episodesOf(showKey).filter((ep) => seasons.includes(ep.season)
    && (includeSpecials || !ep.is_special) && getWatch(store, EPISODE, ep.key));
}

/** Revisionnage des épisodes déjà vus des saisons données (library.add_season_rewatch) ;
 *  les non vus sont ignorés. Renvoie le nombre d'épisodes concernés. */
export function addSeasonRewatch(store, showKey, seasons, day = null, includeSpecials = false) {
  const episodes = watchedEpisodes(store, showKey, seasons, includeSpecials);
  for (const ep of episodes) addRewatch(store, EPISODE, ep.key, day);
  return episodes.length;
}

/** Retire un revisionnage aux épisodes revus des saisons données
 *  (library.remove_season_rewatch). Renvoie le nombre d'épisodes qui en avaient un. */
export function removeSeasonRewatch(store, showKey, seasons, includeSpecials = false) {
  let count = 0;
  for (const ep of watchedEpisodes(store, showKey, seasons, includeSpecials)) {
    if ((getWatch(store, EPISODE, ep.key).plays || 1) > 1) {
      removeRewatch(store, EPISODE, ep.key);
      count += 1;
    }
  }
  return count;
}

/** Retire les visionnages d'une saison. */
export function unmarkSeason(store, showKey, season) {
  let count = 0;
  for (const ep of store.episodesOf(showKey)) {
    if (ep.season === season && unmarkWatched(store, EPISODE, ep.key)) count += 1;
  }
  return count;
}

// --- Bibliothèque (statuts) --------------------------------------------------

export function getLibraryItem(store, kind, ref) {
  return store.get("library", refKey(kind, ref)) || null;
}

/** Ajoute à la bibliothèque ; un élément existant n'est PAS modifié. */
export function ensureLibraryItem(store, kind, ref, status = TO_WATCH, favorite = false,
  addedAt = null) {
  const existing = getLibraryItem(store, kind, ref);
  if (existing) return existing;
  return store.put("library", {
    key: refKey(kind, ref), kind, ref, status, favorite, added_at: addedAt || isoUtc(),
  });
}

/** Change le statut (ajoute à la bibliothèque si besoin). Série : seuls « Plus tard » et
 *  « Arrêté » se choisissent ; tout autre statut est remplacé par celui que donnent les
 *  épisodes vus (refreshShowStatuses). */
export function setStatus(store, kind, ref, status) {
  if (!STATUSES.includes(status)) throw new Error(`Statut inconnu : ${status}`);
  const item = ensureLibraryItem(store, kind, ref, status);
  if (item.status !== status) store.put("library", { ...item, status });
  if (kind === SHOW) refreshShowStatuses(store, todayParis(), ref);
  return getLibraryItem(store, kind, ref);
}

export function setFavorite(store, kind, ref, favorite) {
  const item = ensureLibraryItem(store, kind, ref, TO_WATCH);
  return store.put("library", { ...item, favorite: Boolean(favorite) });
}

/** Retire de la bibliothèque : la fiche, l'historique et les notes sont gardés. */
export function removeFromLibrary(store, kind, ref) {
  return store.remove("library", refKey(kind, ref));
}

/** Statut d'une série d'après ses épisodes (library.auto_show_status) : rien vu = « À voir » ;
 *  commencée avec des épisodes sortis à voir = « En cours » ; à jour = « Terminé » (elle
 *  repassera « En cours » à la sortie d'un nouvel épisode). */
export function autoShowStatus(watched, remaining) {
  if (!watched) return TO_WATCH;
  return remaining ? WATCHING : COMPLETED;
}

/** Remet le statut des séries de la bibliothèque (ou d'une seule, `showKey`) d'accord avec
 *  les épisodes vus (library.refresh_show_statuses). Épisodes réguliers seulement ; « sorti »
 *  = date française au plus tard aujourd'hui (sans date : seulement pour une série saisie à
 *  la main). « Plus tard » et « Arrêté » sont des choix : gardés, sauf série finie (Ended)
 *  entièrement vue. Séries à la correspondance TVMaze à vérifier : pas touchées.
 *  Renvoie le nombre de séries dont le statut a changé. */
export function refreshShowStatuses(store, day = todayParis(), showKey = null) {
  const items = showKey === null ? [...store.library.values()]
    : [getLibraryItem(store, SHOW, showKey)].filter(Boolean);
  let changed = 0;
  for (const item of items) {
    if (item.kind !== SHOW) continue;
    const show = store.get("shows", item.ref);
    if (!show || ![MATCH_OK, MATCH_REMAPPED].includes(show.match_status || MATCH_OK)) continue;
    let watched = 0;
    let remaining = 0;
    for (const ep of store.episodesOf(item.ref)) {
      if (ep.is_special) continue;
      if (getWatch(store, EPISODE, ep.key)) watched += 1;
      else if (isAired(ep, day) || (show.is_manual && !localDate(ep.airstamp, ep.airdate))) {
        remaining += 1;
      }
    }
    const status = autoShowStatus(watched, remaining);
    if (MANUAL_SHOW_STATUSES.includes(item.status)
      && !(status === COMPLETED && show.status === ENDED)) continue;
    if (item.status !== status) {
      store.put("library", { ...item, status });
      changed += 1;
    }
  }
  return changed;
}

/** Statut de la série d'un épisode qui vient d'être vu (ou de ne plus l'être). */
function refreshEpisodeShow(store, episodeKey) {
  const ep = store.get("episodes", episodeKey);
  if (ep) refreshShowStatuses(store, todayParis(), ep.show);
}

/** Passe en « Terminé » les films vus restés « À voir » (réparation : avant, « Vu » depuis
 *  À regarder ou l'Accueil ne changeait pas le statut). Seul « À voir » est corrigé : les
 *  autres statuts viennent de Bingers ou d'un choix à la main. Renvoie le nombre de films. */
export function completeWatchedMovies(store) {
  let count = 0;
  for (const item of store.library.values()) {
    if (item.kind !== MOVIE || item.status !== TO_WATCH || !getWatch(store, MOVIE, item.ref)) {
      continue;
    }
    store.put("library", { ...item, status: COMPLETED });
    count += 1;
  }
  return count;
}

// --- Notes -------------------------------------------------------------------

// Notes de 1 à 5 par demi-point (4,5 = quatre étoiles et demie), comme library.py.
export const RATING_MIN = 1;
export const RATING_MAX = 5;
export const RATING_VALUES = Array.from({ length: 2 * (RATING_MAX - RATING_MIN) + 1 },
  (_, i) => RATING_MIN + i / 2);
const HALF_STAR = "½";   // demi-étoile affichée après les étoiles pleines

/** Note valide (1 à 5 par demi-point) ou null ; sinon erreur lisible (library.clean_rating). */
export function cleanRating(value) {
  if (value == null) return null;
  if (typeof value !== "number" || !(value >= RATING_MIN && value <= RATING_MAX)
    || !Number.isInteger(2 * value)) {
    const shown = typeof value === "number" ? value : JSON.stringify(value);
    throw new Error(`Note invalide : ${shown} (attendu : 1 à 5, par demi-point)`);
  }
  return value;
}

/** 4.5 -> « 4,5 », 4 -> « 4 » (virgule à la française). */
export const formatRating = (value) => String(value).replace(".", ",");

/** 4.5 -> « ★★★★½ ». */
export const ratingStars = (value) => "★".repeat(Math.floor(value)) + (value % 1 ? HALF_STAR : "");

export function getRating(store, kind, ref) {
  return store.get("ratings", refKey(kind, ref)) || null;
}

function cleanText(text) {
  return text && String(text).trim() ? String(text).trim() : null;
}

/** Note (1 à 5 par demi-point, ou null), commentaire, émotions (liste de codes), personnage
 *  préféré. Tout vide : la note est supprimée. */
export function setRating(store, kind, ref, { rating = null, note = null, emotions = [],
  favoriteCharacter } = {}) {
  rating = cleanRating(rating);
  const existing = getRating(store, kind, ref);
  const character = favoriteCharacter === undefined
    ? existing?.favorite_character ?? null : cleanText(favoriteCharacter);
  const emotionsText = emotions && emotions.length ? emotions.join(", ") : null;
  const text = cleanText(note);
  if (rating == null && !text && !emotionsText && !character) {
    store.remove("ratings", refKey(kind, ref));
    return null;
  }
  return store.put("ratings", {
    key: refKey(kind, ref), kind, ref, rating, note: text, emotions: emotionsText,
    favorite_character: character,
  });
}

/** Change seulement la note (1 à 5, null = pas de note) : le reste de l'avis est gardé
 *  (library.set_rating_value, colonne « Note » des tableaux d'épisodes). */
export function setRatingValue(store, kind, ref, rating) {
  const existing = getRating(store, kind, ref);
  return setRating(store, kind, ref, { rating, note: existing?.note ?? null,
    emotions: splitEmotions(existing?.emotions) });
}

export function splitEmotions(emotions) {
  return (emotions || "").split(",").map((e) => e.trim()).filter(Boolean);
}
