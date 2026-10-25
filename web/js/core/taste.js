// Profil de goûts (port de watchlog/core/taste.py) : TF-IDF des mots-clés TMDB et genres des
// titres aimés, cosinus pour la ressemblance d'un candidat. Les tables (mots-clés ignorés,
// traductions) sont générées depuis Python dans taste-data.js.

import {
  BOOST, GENRE_ALIASES, GENRE_FACTOR, IGNORED_KEYWORDS, KEYWORDS_FR, MIN_SUPPORT, NEGATIVE_FACTOR,
  TV_GENRE_SPLIT,
} from "./taste-data.js";

export function keywordLabel(name) {
  return KEYWORDS_FR[name] || name.charAt(0).toUpperCase() + name.slice(1);
}

/** Mots-clés TMDB ([{id, name}]) -> traits {"k:<id>": libellé}, sans les mots-clés inutiles. */
export function keywordFeatures(keywords) {
  const features = {};
  for (const kw of keywords || []) {
    const name = (kw.name || "").trim().toLowerCase();
    // « paris, france » : un lieu, pas un goût
    if (!name || kw.id == null || IGNORED_KEYWORDS.has(name) || name.includes(", ")) continue;
    features[`k:${kw.id}`] = keywordLabel(name);
  }
  return features;
}

export function genreFeatures(names) {
  const features = {};
  for (const raw of names || []) {
    const name = GENRE_ALIASES[raw] || raw;
    features[`g:${name}`] = name;
  }
  return features;
}

/** Ids de genres TMDB -> noms ; les genres combinés des séries sont séparés. */
export function tmdbGenreNames(ids, genreNames) {
  const names = [];
  for (const id of ids || []) {
    for (const name of TV_GENRE_SPLIT[String(id)] || [genreNames.get(id)]) {
      if (name && !names.includes(name)) names.push(name);
    }
  }
  return names;
}

export const isGenre = (feature) => feature.startsWith("g:");
export const keywordId = (feature) => (feature.startsWith("k:") ? Number(feature.slice(2)) : null);

/** IDF lissé : log((1 + N) / (1 + titres ayant le trait)) + 1. */
export function computeIdf(corpus) {
  const df = new Map();
  for (const features of corpus) for (const f of Object.keys(features)) df.set(f, (df.get(f) || 0) + 1);
  const n = corpus.length;
  return new Map([...df].map(([f, count]) => [f, Math.log((1 + n) / (1 + count)) + 1]));
}

export class Profile {
  constructor({ weights = new Map(), labels = new Map(), counts = new Map(), idf = new Map() } = {}) {
    Object.assign(this, { weights, labels, counts, idf, reduced: new Set(), boosted: new Set() });
  }

  get norm() {
    let sum = 0;
    for (const w of this.weights.values()) sum += w * w;
    return Math.sqrt(sum) || 1;
  }

  /** Vecteur TF-IDF d'un titre (trait présent = son IDF ; inconnu = l'IDF maximal). */
  vector(features) {
    const fallback = this.idf.size ? Math.max(...this.idf.values()) : 1;
    return new Map(Object.keys(features).map((f) => [f,
      (this.idf.get(f) ?? fallback) * (isGenre(f) ? GENRE_FACTOR : 1)]));
  }

  /** Cosinus entre le titre et le profil (0 = rien en commun ; négatif = à l'opposé). */
  similarity(features) {
    const vec = this.vector(features);
    if (!vec.size) return 0;
    let dot = 0;
    let sq = 0;
    for (const [f, v] of vec) {
      dot += (this.weights.get(f) || 0) * v;
      sq += v * v;
    }
    return dot / (this.norm * Math.sqrt(sq));
  }

  /** Thèmes (mots-clés) du titre qui le rapprochent le plus du profil. */
  explain(features, limit = 3) {
    const shared = [];
    for (const [f, v] of this.vector(features)) {
      const w = this.weights.get(f) || 0;
      if (w > 0 && !isGenre(f)) shared.push([w * v, f]);
    }
    shared.sort((a, b) => (b[0] - a[0]) || this.label(a[1]).localeCompare(this.label(b[1])));
    return shared.slice(0, limit).map(([, f]) => this.label(f));
  }

  label(feature) {
    return this.labels.get(feature) || feature;
  }

  /** Traits les plus marquants : [trait, libellé, poids]. */
  top(limit = 15, keywordsOnly = false) {
    return [...this.weights].filter(([f, w]) => w > 0 && !(keywordsOnly && isGenre(f)))
      .map(([f, w]) => [f, this.label(f), w])
      .sort((a, b) => (b[2] - a[2]) || a[1].localeCompare(b[1]))
      .slice(0, limit);
  }
}

/** Profil : signals = [[poids, traits]] (poids négatif = rejeté), corpus = traits des
 *  candidats (pour l'IDF), preferences = Map(trait -> [libellé, +1 | -1]). */
export function buildProfile(signals, corpus, preferences = new Map()) {
  const idf = computeIdf([...corpus, ...signals.map(([, f]) => f)]);
  const positive = new Map();
  const negative = new Map();
  const counts = new Map();
  const labels = new Map();
  const total = signals.reduce((s, [w]) => s + (w > 0 ? w : 0), 0) || 1;
  for (const [weight, features] of signals) {
    for (const [f, label] of Object.entries(features)) {
      labels.set(f, label);
      if (weight > 0) {
        positive.set(f, (positive.get(f) || 0) + weight);
        counts.set(f, (counts.get(f) || 0) + 1);
      } else if (weight < 0) {
        negative.set(f, (negative.get(f) || 0) - weight);
      }
    }
  }
  const weights = new Map();
  for (const f of new Set([...positive.keys(), ...negative.keys()])) {
    if ((counts.get(f) || 0) < MIN_SUPPORT && !isGenre(f) && !negative.get(f)) continue;
    let value = ((positive.get(f) || 0) - NEGATIVE_FACTOR * (negative.get(f) || 0)) / total;
    value *= (idf.get(f) ?? 1) * (isGenre(f) ? GENRE_FACTOR : 1);
    if (value) weights.set(f, value);
  }
  const profile = new Profile({ weights, labels, counts, idf });
  const strongest = Math.max(1e-9, ...[...weights.values()].filter((w) => w > 0));
  const reference = [...weights.values()].some((w) => w > 0) ? strongest : 1;
  for (const [f, [label, direction]] of preferences) {
    if (!profile.labels.has(f)) profile.labels.set(f, label);
    if (direction > 0) {
      profile.boosted.add(f);
      profile.weights.set(f, Math.max(profile.weights.get(f) || 0, reference * BOOST));
    } else if (direction < 0) {
      profile.reduced.add(f);
      profile.weights.set(f, Math.min(profile.weights.get(f) || 0, 0));
    }
  }
  return profile;
}

/** Préférences de thèmes du Store : Map(trait -> [libellé, poids]). */
export function preferences(store) {
  return new Map([...store.themes.values()].map((t) => [t.feature, [t.label, t.weight]]));
}

/** Remplace les préférences : more / less = Map(trait -> libellé). */
export function setPreferences(store, more, less, now) {
  const wanted = new Map([...more].map(([f, label]) => [f, [label, 1]]));
  for (const [f, label] of less) if (!more.has(f)) wanted.set(f, [label, -1]);
  for (const row of [...store.themes.values()]) {
    const want = wanted.get(row.feature);
    if (!want) store.remove("themes", row.key);
    else if (want[0] !== row.label || want[1] !== row.weight) {
      store.put("themes", { ...row, label: want[0], weight: want[1], updated_at: now });
    }
    wanted.delete(row.feature);
  }
  for (const [f, [label, weight]] of wanted) {
    store.put("themes", { key: f, feature: f, label, weight, updated_at: now });
  }
}
