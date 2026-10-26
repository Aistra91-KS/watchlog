// Suggestions de séries et de films (port de watchlog/core/suggestions.py), moteur hybride
// entièrement local : signaux de la bibliothèque -> profil de goûts (taste.js) -> candidats
// (recommandations TMDB, thèmes forts, tendances) -> score, « % pour toi », diversification.
// Une panne TMDB n'est jamais avalée : comptée et signalée dans `errors`.

import { mapPool } from "../api/http.js";
import { posterUrl } from "./catalog.js";
import { isoUtc, parisDay, today as todayParis, daysBetween } from "./dates.js";
import { COMPLETED, EPISODE, FOR_LATER, MOVIE, SHOW, STOPPED, TO_WATCH, WATCHING, splitGenres }
  from "./model.js";
import { BULK_THRESHOLD } from "./stats.js";
import {
  buildProfile, genreFeatures, isGenre, keywordFeatures, keywordId, preferences, tmdbGenreNames,
} from "./taste.js";

export const TMDB_MEDIA = { [SHOW]: "tv", [MOVIE]: "movie" };

// Poids d'une note (1 à 5) : remplace le poids du statut.
const RATING_WEIGHTS = { 5: 3, 4: 2, 3: 1, 2: -1, 1: -2 };
const FAVORITE_BONUS = 2;
// « À voir » : l'envie du moment, aussi parlante qu'un titre vu.
const STATUS_WEIGHTS = { [STOPPED]: -1, [TO_WATCH]: 1, [FOR_LATER]: 0.7 };
const DEFAULT_WEIGHT = 1;
const DISMISSED_WEIGHT = -1;
const FULL_ENGAGEMENT = 50;   // épisodes vus à partir desquels une série compte à plein
const REWATCH_FACTOR = 1.4;   // film revu

const MAX_SOURCES = 200;      // sources par type : limite les appels TMDB
const MIN_VOTES = { [SHOW]: 30, [MOVIE]: 100 };
const MAX_REASONS = 3;
const MAX_THEMES = 3;
const RERANK_POOL = 120;      // candidats collaboratifs analysés (mots-clés) par type
const THEME_SEEDS = 6;        // thèmes forts explorés par type
const WORKERS = 6;            // appels TMDB en parallèle (débit limité par RateLimiter)

const W_COLLAB = 0.35;
const W_CONTENT = 0.5;
const W_QUALITY = 0.15;
const TRENDING_BONUS = 0.05;
const REDUCED_FACTOR = 0.3;   // titre qui a un thème « moins de ça »
const MIN_CONTENT = 0.35;     // titre venu des thèmes / tendances : ressemblance minimale
const DIVERSITY = 0.25;       // pénalité de ressemblance avec les titres déjà retenus (MMR)
const BAYES_VOTES = 200;
const BAYES_MEAN = 6.5;
const MATCH_FULL_COSINE = 0.3;
const MATCH_HALF_COLLAB = 5;
const MAX_FAILURE_SHARE = 0.05;  // au-delà, le résultat est signalé incomplet
const SUMMARY_LENGTH = 300;

/** Compteur des appels TMDB ratés par étape. */
class Failures {
  constructor() { this.counts = new Map(); }

  add(step, failed, total) {
    const c = this.counts.get(step) || [0, 0];
    this.counts.set(step, [c[0] + failed, c[1] + total]);
  }

  /** Au-delà de MAX_FAILURE_SHARE : résultat incomplet ; en dessous : signalé quand même. */
  messages() {
    return [...this.counts].filter(([, [failed]]) => failed > 0)
      .map(([step, [failed, total]]) => (failed / total > MAX_FAILURE_SHARE
        ? `TMDB n'a pas répondu pour ${failed} sur ${total} ${step} : suggestions incomplètes, `
          + "« Recalculer » plus tard."
        : `TMDB n'a pas répondu pour ${failed} sur ${total} ${step} (effet faible sur le résultat).`));
  }
}

export function shortText(text, length = SUMMARY_LENGTH) {
  const clean = (text || "").replace(/<[^>]+>/g, "").trim();
  if (clean.length <= length) return clean;
  const cut = clean.slice(0, length);
  return `${cut.slice(0, cut.lastIndexOf(" ") > 0 ? cut.lastIndexOf(" ") : length).replace(/[ ,;.]+$/, "")}…`;
}

export function sourceWeight(status, favorite, rating) {
  let weight = RATING_WEIGHTS[rating] ?? STATUS_WEIGHTS[status] ?? DEFAULT_WEIGHT;
  if (favorite) weight = Math.max(weight, 0) + FAVORITE_BONUS;
  return weight;
}

/** Série : de 0,5 (un épisode) à 1,3 (50 épisodes et plus). Film revu : 1,4. */
export function engagementFactor(kind, episodes = 0, plays = 1) {
  if (kind === SHOW) return 0.5 + 0.8 * Math.min(1, Math.log1p(episodes) / Math.log1p(FULL_ENGAGEMENT));
  return plays > 1 ? REWATCH_FACTOR : 1;
}

/** Récent : jusqu'à 1,3 ; il y a un an ~1 ; ancien ou inconnu (jour d'import) : 0,8. */
export function recencyFactor(last, today = todayParis()) {
  if (!last) return 0.8;
  const age = Math.max(0, daysBetween(parisDay(last), today));
  return 0.8 + 0.5 * Math.exp(-age / 365);
}

/** (kind:clé) -> [épisodes vus, visionnages, dernier visionnage daté hors jours d'import]. */
function activity(store) {
  const perDay = new Map();
  for (const w of store.watches.values()) {
    if (w.last_watched_at) perDay.set(parisDay(w.last_watched_at), (perDay.get(parisDay(w.last_watched_at)) || 0) + 1);
  }
  const bulk = new Set([...perDay].filter(([, n]) => n >= BULK_THRESHOLD).map(([d]) => d));
  const dated = (v) => (v && !bulk.has(parisDay(v)) ? v : null);
  const out = new Map();
  for (const w of store.watches.values()) {
    if (w.kind === MOVIE) {
      out.set(`${MOVIE}:${w.ref}`, [0, w.plays || 1, dated(w.last_watched_at)]);
      continue;
    }
    const ep = store.get("episodes", w.ref);
    if (!ep) continue;
    const key = `${SHOW}:${ep.show}`;
    const entry = out.get(key) || [0, 1, null];
    entry[0] += 1;
    const last = dated(w.last_watched_at);
    if (last && (!entry[2] || last > entry[2])) entry[2] = last;
    out.set(key, entry);
  }
  return out;
}

/** Titres de la bibliothèque avec id TMDB et force de leur signal, les plus marquants d'abord. */
export function librarySignals(store, kind = null, today = todayParis()) {
  const act = activity(store);
  const rows = [];
  for (const lib of store.library.values()) {
    if (kind && lib.kind !== kind) continue;
    const obj = store.get(lib.kind === SHOW ? "shows" : "movies", lib.ref);
    if (!obj?.tmdb_id) continue;
    const rating = store.get("ratings", `${lib.kind}:${lib.ref}`)?.rating ?? null;
    let weight = sourceWeight(lib.status, lib.favorite, rating);
    const [episodes, plays, last] = act.get(`${lib.kind}:${lib.ref}`) || [0, 1, null];
    if (weight > 0 && [WATCHING, COMPLETED].includes(lib.status)) {
      weight *= engagementFactor(lib.kind, episodes, plays) * recencyFactor(last, today);
    }
    rows.push({ added: lib.added_at || "", source: { kind: lib.kind, tmdbId: obj.tmdb_id,
      title: obj.title, weight: Math.round(weight * 1000) / 1000, genres: splitGenres(obj.genres) } });
  }
  rows.sort((a, b) => (Math.abs(b.source.weight) - Math.abs(a.source.weight))
    || b.added.localeCompare(a.added));
  return rows.map((r) => r.source);
}

function excludedIds(store, kind) {
  const ids = new Set();
  for (const obj of store[kind === SHOW ? "shows" : "movies"].values()) if (obj.tmdb_id) ids.add(obj.tmdb_id);
  for (const d of store.dismissed.values()) if (d.kind === kind) ids.add(d.tmdb_id);
  return ids;
}

function toSuggestion(kind, data, genreNames) {
  const [title, original, released] = kind === SHOW
    ? [data.name, data.original_name, data.first_air_date]
    : [data.title, data.original_title, data.release_date];
  const year = released ? Number(String(released).slice(0, 4)) || null : null;
  return {
    kind, tmdbId: data.id, title: title || original || "Sans titre", originalTitle: original || null,
    year, overview: shortText(data.overview), posterUrl: posterUrl(data.poster_path),
    genres: tmdbGenreNames(data.genre_ids, genreNames), voteAverage: Number(data.vote_average) || 0,
    voteCount: Number(data.vote_count) || 0, score: 0, match: 0, because: [], themes: [],
    matchedThemes: [], trending: false, fromThemes: false, collab: 0, content: 0, features: {},
  };
}

/** Note TMDB pondérée par le nombre de votes (moyenne bayésienne), ramenée entre 0 et 1. */
export function quality(voteAverage, voteCount) {
  const bayes = (voteCount * voteAverage + BAYES_VOTES * BAYES_MEAN) / (voteCount + BAYES_VOTES);
  return Math.min(1, Math.max(0, (bayes - 5.5) / 3));
}

/** « % pour toi », de 1 à 100, indépendant des autres candidats. */
export function matchPercent(collab, content, reduced = false) {
  const resemblance = Math.min(1, Math.max(0, content) / MATCH_FULL_COSINE);
  const recommended = collab > 0 ? collab / (collab + MATCH_HALF_COLLAB) : 0;
  let value = (W_CONTENT * resemblance + W_COLLAB * recommended) / (W_CONTENT + W_COLLAB);
  if (reduced) value *= REDUCED_FACTOR;
  return Math.max(1, Math.min(100, Math.round(100 * value)));
}

const keywordsOf = (s) => new Set(Object.keys(s.features).filter((f) => !isGenre(f)));

/** Réordonne pour éviter les titres quasi identiques à la suite (MMR, Jaccard des mots-clés). */
export function diversify(items, limit) {
  const pool = [...items].sort((a, b) => b.score - a.score);
  const chosen = [];
  const adjusted = (s) => {
    const kws = keywordsOf(s);
    let overlap = 0;
    for (const c of chosen) {
      const other = keywordsOf(c);
      if (!kws.size || !other.size) continue;
      let inter = 0;
      for (const k of kws) if (other.has(k)) inter += 1;
      overlap = Math.max(overlap, inter / (kws.size + other.size - inter));
    }
    return s.score - DIVERSITY * overlap;
  };
  while (pool.length && chosen.length < limit) {
    let best = pool[0];
    let bestScore = adjusted(best);
    for (const s of pool.slice(1, 40)) {
      const v = adjusted(s);
      if (v > bestScore) { best = s; bestScore = v; }
    }
    best.score = bestScore;
    pool.splice(pool.indexOf(best), 1);
    chosen.push(best);
  }
  return chosen;
}

export function rank(candidates, reasons, profile, limit) {
  const items = [...candidates.values()];
  for (const s of items) s.content = profile.similarity({ ...s.features, ...genreFeatures(s.genres) });
  const topCollab = Math.max(0, ...items.map((s) => s.collab));
  const topContent = Math.max(0, ...items.map((s) => s.content));
  const kept = [];
  for (const s of items) {
    const collab = s.collab > 0 && topCollab > 0 ? Math.log1p(s.collab) / Math.log1p(topCollab) : 0;
    const content = s.content > 0 && topContent > 0 ? s.content / topContent : 0;
    if (s.collab <= 0 && !((s.fromThemes || s.trending) && content >= MIN_CONTENT)) continue;
    s.score = W_COLLAB * collab + W_CONTENT * content + W_QUALITY * quality(s.voteAverage, s.voteCount)
      + (s.trending ? TRENDING_BONUS : 0);
    const traits = new Set([...Object.keys(s.features), ...Object.keys(genreFeatures(s.genres))]);
    const reduced = [...profile.reduced].some((f) => traits.has(f));
    if (reduced) s.score *= REDUCED_FACTOR;
    s.match = matchPercent(s.collab, s.content, reduced);
    kept.push(s);
  }
  const ranked = diversify(kept, limit);
  for (const s of ranked) {
    const titles = reasons.get(s.tmdbId) || new Map();
    s.because = [...titles].sort((a, b) => b[1] - a[1]).slice(0, MAX_REASONS).map(([t]) => t);
    s.matchedThemes = profile.explain(s.features, MAX_THEMES);
    s.themes = [...new Set(Object.values(s.features))];
  }
  return ranked;
}

async function fetchFeatures(tmdb, refs, failures, step, progress, label) {
  const unique = [...new Map(refs.map((r) => [`${r[0]}:${r[1]}`, r])).values()];
  let failed = 0;
  const results = await mapPool(unique, async ([kind, id]) => {
    try {
      return keywordFeatures(await tmdb.keywords(TMDB_MEDIA[kind], id));
    } catch (error) {
      console.warn(`Mots-clés TMDB non lus pour ${kind} ${id}`, error);
      failed += 1;
      return {};
    }
  }, WORKERS, (done, total) => progress(done, total, label));
  failures.add(step, failed, unique.length);
  return new Map(unique.map(([kind, id], i) => [`${kind}:${id}`, results[i]]));
}

async function collaborative(tmdb, kind, sources, excluded, genreNames, failures, progress, label) {
  const media = TMDB_MEDIA[kind];
  const candidates = new Map();
  const reasons = new Map();
  let failed = 0;
  const lists = await mapPool(sources, async (source) => {
    try {
      return await tmdb.recommendations(media, source.tmdbId);
    } catch (error) {
      console.warn(`Recommandations TMDB non lues pour ${source.title}`, error);
      failed += 1;
      return [];
    }
  }, WORKERS, (done, total) => progress(done, total, label));
  sources.forEach((source, i) => {
    const results = lists[i];
    results.forEach((data, rankIndex) => {
      if (data.id == null || excluded.has(data.id) || (data.vote_count || 0) < MIN_VOTES[kind]) return;
      // 1 pour le premier recommandé, ~0,5 pour le dernier de la liste
      const contribution = source.weight * (1 - rankIndex / (2 * results.length));
      if (!candidates.has(data.id)) candidates.set(data.id, toSuggestion(kind, data, genreNames));
      candidates.get(data.id).collab += contribution;
      if (contribution > 0) {
        if (!reasons.has(data.id)) reasons.set(data.id, new Map());
        const r = reasons.get(data.id);
        r.set(source.title, (r.get(source.title) || 0) + contribution);
      }
    });
  });
  if (sources.length && failed === sources.length) {
    throw new Error("TMDB indisponible : aucune recommandation n'a pu être lue.");
  }
  failures.add("titres (recommandations)", failed, sources.length);
  return { candidates, reasons };
}

async function explore(tmdb, kind, profile, candidates, excluded, genreNames, failures) {
  const media = TMDB_MEDIA[kind];
  const seeds = profile.top(THEME_SEEDS, true).map(([f]) => f);
  for (const f of profile.boosted) if (keywordId(f) && !seeds.includes(f)) seeds.push(f);
  const added = [];
  const add = (data, trending = false) => {
    if (data.id == null || excluded.has(data.id) || (data.vote_count || 0) < MIN_VOTES[kind]) return;
    if (!candidates.has(data.id)) {
      candidates.set(data.id, toSuggestion(kind, data, genreNames));
      added.push(candidates.get(data.id));
    }
    if (trending) candidates.get(data.id).trending = true;
    else candidates.get(data.id).fromThemes = true;
  };
  let failed = 0;
  const minVotes = kind === MOVIE ? 100 : 50;
  for (const feature of seeds) {
    try {
      for (const data of await tmdb.discoverByKeyword(media, keywordId(feature), minVotes)) add(data);
    } catch (error) {
      console.warn(`Exploration du thème ${profile.label(feature)} non lue`, error);
      failed += 1;
    }
  }
  try {
    for (const data of await tmdb.trending(media)) add(data, true);
  } catch (error) {
    console.warn(`Tendances TMDB (${media}) non lues`, error);
    failed += 1;
  }
  failures.add("thèmes et tendances explorés", failed, seeds.length + 1);
  return added;
}

/** Complète l'id TMDB des séries qui n'en ont pas (via TVDB). Une série sans id n'entre ni
 *  dans le profil ni dans les recommandations : les échecs sont comptés dans `failures`. */
export async function resolveShowTmdbIds(store, tmdb, failures = null) {
  let found = 0;
  let failed = 0;
  let total = 0;
  for (const lib of store.library.values()) {
    const show = lib.kind === SHOW ? store.get("shows", lib.ref) : null;
    if (!show || show.tmdb_id || !show.tvdb_id) continue;
    total += 1;
    try {
      const id = await tmdb.findTvByTvdb(show.tvdb_id);
      if (id) {
        store.put("shows", { ...show, tmdb_id: id });
        found += 1;
      }
    } catch (error) {
      console.warn(`Id TMDB introuvable pour ${show.title} (TVDB ${show.tvdb_id})`, error);
      failed += 1;
    }
  }
  failures?.add("séries de ta bibliothèque (id TMDB)", failed, total);
  return found;
}

/** Suggestions de séries et de films + profil utilisé. progress(fait, total, libellé). */
export async function computeAll(store, tmdb, { limit = 60, progress = () => {} } = {}) {
  const failures = new Failures();
  await resolveShowTmdbIds(store, tmdb, failures);
  const signals = [...librarySignals(store), ...[...store.dismissed.values()].map((d) => ({
    kind: d.kind, tmdbId: d.tmdb_id, title: d.title || "", weight: DISMISSED_WEIGHT, genres: [],
  }))];
  const features = await fetchFeatures(tmdb, signals.filter((s) => s.weight).map((s) => [s.kind, s.tmdbId]),
    failures, "titres de ta bibliothèque (thèmes)", progress, "1/4 Thèmes de ta bibliothèque");
  const genreNames = {};
  for (const kind of [SHOW, MOVIE]) {
    try {
      genreNames[kind] = await tmdb.genres(TMDB_MEDIA[kind]);
      failures.add("listes de genres", 0, 1);
    } catch (error) {
      console.warn(`Genres TMDB (${kind}) non lus`, error);
      genreNames[kind] = new Map();
      failures.add("listes de genres", 1, 1);
    }
  }
  const errors = [];
  const pools = new Map();
  const allReasons = new Map();
  const excluded = new Map();
  for (const [kind, label] of [[SHOW, "séries"], [MOVIE, "films"]]) {
    excluded.set(kind, excludedIds(store, kind));
    const sources = librarySignals(store, kind).filter((s) => s.weight !== 0).slice(0, MAX_SOURCES);
    try {
      const { candidates, reasons } = await collaborative(tmdb, kind, sources, excluded.get(kind),
        genreNames[kind], failures, progress, `2/4 Recommandations (${label})`);
      allReasons.set(kind, reasons);
      const best = [...candidates.values()].sort((a, b) => b.collab - a.collab).slice(0, RERANK_POOL);
      pools.set(kind, new Map(best.map((s) => [s.tmdbId, s])));
    } catch (error) {
      errors.push(`Suggestions de ${label} indisponibles : ${error.message}`);
    }
  }
  const poolRefs = [...pools].flatMap(([kind, pool]) => [...pool.keys()].map((id) => [kind, id]));
  for (const [k, v] of await fetchFeatures(tmdb, poolRefs, failures, "suggestions possibles (thèmes)",
    progress, "3/4 Thèmes des candidats")) features.set(k, v);
  const corpus = poolRefs.map(([k, id]) => features.get(`${k}:${id}`) || {});
  const signalFeatures = signals.map((s) => [s.weight,
    { ...(features.get(`${s.kind}:${s.tmdbId}`) || {}), ...genreFeatures(s.genres) }]);
  const profile = buildProfile(signalFeatures, corpus, preferences(store));

  const newRefs = [];
  for (const [kind, pool] of pools) {
    const added = await explore(tmdb, kind, profile, pool, excluded.get(kind), genreNames[kind], failures);
    newRefs.push(...added.map((s) => [kind, s.tmdbId]));
  }
  for (const [k, v] of await fetchFeatures(tmdb, newRefs, failures, "suggestions possibles (thèmes)",
    progress, "4/4 Exploration de tes thèmes")) features.set(k, v);

  const items = [];
  for (const [kind, pool] of pools) {
    for (const s of pool.values()) s.features = features.get(`${kind}:${s.tmdbId}`) || {};
    items.push(...rank(pool, allReasons.get(kind) || new Map(), profile, limit));
  }
  return { items, profile, errors: [...errors, ...failures.messages()], computedAt: isoUtc() };
}

/** « Pas intéressé » : le titre ne sera plus suggéré. */
export function dismiss(store, kind, tmdbId, title = null) {
  const key = `${kind}:${tmdbId}`;
  if (!store.get("dismissed", key)) {
    store.put("dismissed", { key, kind, tmdb_id: tmdbId, title, dismissed_at: isoUtc() });
  }
}

export function undismissAll(store) {
  const keys = [...store.dismissed.keys()];
  for (const key of keys) store.remove("dismissed", key);
  return keys.length;
}

export const SORT_RELEVANCE = "relevance";
export const SORT_RATING = "rating";
export const SORT_RECENT = "recent";

/** Filtres de la page (suggestions.filter_items) : genres / plateformes / thèmes = au moins un
 *  en commun ; years = [min, max] inclus (un titre sans année n'est gardé que sans ce filtre). */
export function filterItems(items, { kinds = [SHOW, MOVIE], genres = [], themes = [], platforms = [],
  streamingOnly = false, years = null, minVote = 0, sort = SORT_RELEVANCE } = {}) {
  const any = (wanted, have) => !wanted.length || wanted.some((w) => have.includes(w));
  const inYears = (s) => !years || (s.year != null && years[0] <= s.year && s.year <= years[1]);
  const result = items.filter((s) => kinds.includes(s.kind) && any(genres, s.genres)
    && any(themes, s.themes) && (!platforms.length || any(platforms, s.streaming || []))
    && (!streamingOnly || (s.streaming || []).length) && inYears(s) && s.voteAverage >= minVote);
  if (sort === SORT_RATING) result.sort((a, b) => (b.voteAverage - a.voteAverage) || (b.voteCount - a.voteCount));
  else if (sort === SORT_RECENT) result.sort((a, b) => ((b.year || 0) - (a.year || 0)) || (b.score - a.score));
  else result.sort((a, b) => b.score - a.score);
  return result;
}
