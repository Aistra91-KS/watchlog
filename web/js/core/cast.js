// Personnages d'un titre, pour retrouver un nom oublié (« Personnage préféré » de Mon avis),
// port de watchlog/core/cast.py. Séries : distribution principale TVMaze (photo du personnage
// quand elle existe), complétée par les rôles récurrents TMDB. Films : casting TMDB. Une source
// qui ne répond pas est signalée (console.warn) et nommée dans `errors` : liste incomplète.

import { PROFILE_BASE } from "./preview.js";

export const MAX_CHARACTERS = 24;   // personnages proposés, les plus présents d'abord
// Rôles qui ne sont pas des personnages (l'acteur joue son propre rôle), en minuscules
const SELF_ROLES = new Set(["self", "himself", "herself", "themselves", "lui-même", "elle-même"]);
// Mentions TMDB en fin de rôle, retirées : « Rick Sanchez (voice) » -> « Rick Sanchez »
const ROLE_NOTES = /(\s*\((voice|uncredited|archive footage|voix)\))+\s*$/i;

/** « Walter White (Bryan Cranston) ». */
export function characterLabel(c) {
  return c.actor ? `${c.name} (${c.actor})` : c.name;
}

/** Nom du personnage sans les mentions « (voice) »... ; null si ce n'est pas un personnage. */
export function cleanRole(role) {
  const name = (role || "").trim().replace(ROLE_NOTES, "").trim();
  return name && !SELF_ROLES.has(name.toLowerCase()) ? name : null;
}

/** Un personnage par nom (le premier vu) ; une photo manquante est prise sur un doublon. */
function merge(characters) {
  const kept = new Map();
  for (const c of characters) {
    const key = c.name.toLowerCase();
    if (!kept.has(key)) kept.set(key, c);
    else if (!kept.get(key).photoUrl && c.photoUrl) kept.get(key).photoUrl = c.photoUrl;
  }
  return [...kept.values()].slice(0, MAX_CHARACTERS);
}

const profile = (path) => (path ? `${PROFILE_BASE}${path}` : null);

function tmdbCast(cast) {
  return [...cast].sort((a, b) => (a.order ?? 999) - (b.order ?? 999))
    .map((p) => ({ name: cleanRole(p.character), actor: p.name || null, photoUrl: profile(p.profile_path) }))
    .filter((c) => c.name);
}

/** Casting TMDB de toute une série : les rôles les plus présents (épisodes) d'abord. */
function tmdbAggregate(cast) {
  const roles = [];
  for (const p of cast) {
    for (const role of p.roles || []) {
      const name = cleanRole(role.character);
      if (name) {
        roles.push([role.episode_count || 0, p.order ?? 999,
          { name, actor: p.name || null, photoUrl: profile(p.profile_path) }]);
      }
    }
  }
  return roles.sort((a, b) => (b[0] - a[0]) || (a[1] - b[1])).map(([, , c]) => c);
}

function tvmazeCast(entries) {
  const out = [];
  for (const entry of entries) {
    if (entry.self) continue;
    const person = entry.person || {};
    const name = cleanRole(entry.character?.name);
    if (name) {
      out.push({ name, actor: person.name || null,
        photoUrl: entry.character?.image?.medium || person.image?.medium || null });
    }
  }
  return out;
}

/** Personnages d'une série : TVMaze (principaux), puis TMDB (récurrents). {characters, errors}. */
export async function showCast(tvmaze, tmdb, show) {
  const errors = [];
  let characters = [];
  if (show.tvmaze_id) {
    try {
      characters = tvmazeCast(await tvmaze.getCast(show.tvmaze_id));
    } catch (error) {
      console.warn(`Casting TVMaze indisponible pour « ${show.title} »`, error);
      errors.push("TVMaze");
    }
  }
  if (show.tmdb_id && tmdb.configured) {
    try {
      const data = await tmdb.tvAggregateCredits(show.tmdb_id);   // null : clé refusée
      characters = characters.concat(tmdbAggregate(data?.cast || []));
    } catch (error) {
      console.warn(`Casting TMDB indisponible pour « ${show.title} »`, error);
      errors.push("TMDB");
    }
  }
  return { characters: merge(characters), errors };
}

/** Personnages d'un film (casting TMDB, même fiche en cache que la page Aperçu). */
export async function movieCast(tmdb, movie) {
  if (!movie.tmdb_id || !tmdb.configured) return { characters: [], errors: [] };
  try {
    const data = await tmdb.details("movie", movie.tmdb_id);
    return { characters: merge(tmdbCast(data?.credits?.cast || [])), errors: [] };
  } catch (error) {
    console.warn(`Casting TMDB indisponible pour « ${movie.title} »`, error);
    return { characters: [], errors: ["TMDB"] };
  }
}
