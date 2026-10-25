// Tests du profil de goûts et du moteur de suggestions (faux client TMDB, aucun réseau).

import assert from "node:assert/strict";
import { test } from "node:test";

import { EPISODE, MOVIE, SHOW, STOPPED, TO_WATCH } from "../js/core/model.js";
import {
  computeAll, dismiss, diversify, engagementFactor, filterItems, librarySignals, matchPercent,
  recencyFactor, sourceWeight,
} from "../js/core/suggestions.js";
import {
  buildProfile, genreFeatures, keywordFeatures, setPreferences, preferences, tmdbGenreNames,
} from "../js/core/taste.js";
import { addEpisode, addMovie, addShow, makeStore, watch } from "./helpers.js";

const DYSTOPIA = { id: 4565, name: "dystopia" };
const SUPERHERO = { id: 9715, name: "superhero" };
const ANIMATION = { id: 1, name: "cartoon" };

test("traits : mots-clés ignorés, lieux, traduction, genres", () => {
  assert.deepEqual(keywordFeatures([DYSTOPIA, { id: 2, name: "sequel" }, { id: 3, name: "paris, france" },
    { id: 4, name: "lighthouse" }]), { "k:4565": "Dystopie", "k:4": "Lighthouse" });
  assert.deepEqual(genreFeatures(["Anime", "Drame"]), { "g:Animation": "Animation", "g:Drame": "Drame" });
  assert.deepEqual(tmdbGenreNames([10765, 18], new Map([[18, "Drame"]])),
    ["Science-Fiction", "Fantastique", "Drame"]);
});

test("profil : support minimal, titre rejeté, plus de / moins de", () => {
  const signals = [
    [2, { "k:4565": "Dystopie", "k:1": "Rare" }],
    [1, { "k:4565": "Dystopie" }],
    [-1, { "k:9": "Rejeté" }],
  ];
  const profile = buildProfile(signals, [{ "k:4565": "Dystopie" }, { "k:7": "Autre" }],
    new Map([["k:9715", ["Super-héros", 1]], ["g:Animation", ["Animation", -1]]]));
  assert.ok(profile.weights.get("k:4565") > 0);
  assert.ok(!profile.weights.has("k:1"));                    // vu dans un seul titre aimé
  assert.ok(profile.weights.get("k:9") < 0);                 // rejeté : éloigne
  assert.ok(profile.weights.get("k:9715") > profile.weights.get("k:4565"));
  assert.ok(profile.reduced.has("g:Animation"));
  assert.ok(profile.similarity({ "k:4565": "" }) > profile.similarity({ "k:7": "" }));
  assert.deepEqual(profile.explain({ "k:4565": "Dystopie", "g:Drame": "Drame" }), ["Dystopie"]);
});

test("poids des titres : note, statut, favori, engagement, récence", () => {
  assert.equal(sourceWeight(STOPPED, false, null), -1);
  assert.equal(sourceWeight(TO_WATCH, false, 5), 3);
  assert.equal(sourceWeight(STOPPED, true, null), 2);
  assert.equal(engagementFactor(SHOW, 0), 0.5);
  assert.ok(Math.abs(engagementFactor(SHOW, 50) - 1.3) < 1e-9);
  assert.equal(engagementFactor(MOVIE, 0, 2), 1.4);
  assert.equal(recencyFactor(null), 0.8);
  assert.ok(Math.abs(recencyFactor("2026-10-12T20:00:00Z", "2026-10-12") - 1.3) < 1e-9);
});

test("« % pour toi » : absolu, plafonné, réduit par « moins de »", () => {
  assert.equal(matchPercent(0, 0), 1);
  assert.equal(matchPercent(1e9, 0.3), 100);
  assert.equal(matchPercent(5, 0.15), 50);
  assert.equal(matchPercent(5, 0.15, true), 15);
});

test("diversification : un quasi-doublon recule", () => {
  const item = (id, score, kws) => ({ tmdbId: id, score, features: Object.fromEntries(kws.map((k) => [k, k])) });
  const ranked = diversify([item(1, 1, ["k:1", "k:2"]), item(2, 0.95, ["k:1", "k:2"]),
    item(3, 0.9, ["k:3"])], 3);
  assert.deepEqual(ranked.map((s) => s.tmdbId), [1, 3, 2]);
});

class FakeTMDB {
  constructor({ failKeywords = false } = {}) {
    this.failKeywords = failKeywords;
    this.calls = 0;
  }

  async available() { return true; }

  async findTvByTvdb() { return null; }

  async genres() { return new Map([[18, "Drame"], [16, "Animation"]]); }

  async keywords(media, id) {
    this.calls += 1;
    if (this.failKeywords && id >= 100) throw new Error("HTTP 500");
    const table = { 1: [DYSTOPIA], 2: [DYSTOPIA, SUPERHERO], 3: [ANIMATION], 101: [DYSTOPIA],
      102: [DYSTOPIA, SUPERHERO], 103: [ANIMATION], 104: [DYSTOPIA] };
    return table[id] || [];
  }

  async recommendations(media, id) {
    const rec = (rid, title, extra = {}) => ({ id: rid, title, name: title, vote_count: 5000,
      vote_average: 7.5, genre_ids: [18], overview: "Résumé", ...extra });
    if (media === "movie" && id === 1) return [rec(101, "Snowpiercer"), rec(103, "Dessin animé")];
    if (media === "movie" && id === 2) return [rec(102, "Watchmen"), rec(101, "Snowpiercer"), rec(1, "Déjà vu")];
    return [];
  }

  async discoverByKeyword(media) {
    return media === "movie" ? [{ id: 104, title: "The Society", vote_count: 3000, vote_average: 7,
      genre_ids: [18] }] : [];
  }

  async trending() { return []; }
}

function suggestionStore() {
  const store = makeStore();
  const m1 = addMovie(store, "Divergente", { id: 1, status: "completed" });
  const m2 = addMovie(store, "Matrix", { id: 2, status: "completed", favorite: true });
  watch(store, MOVIE, m1, "2026-09-01T20:00:00Z");
  watch(store, MOVIE, m2, "2026-09-02T20:00:00Z");
  addMovie(store, "Arrêté", { id: 3, status: STOPPED });
  const show = addShow(store, "Série sans id TMDB", { id: 50 });
  const ep = addEpisode(store, show, 1, 1, { id: 500 });
  watch(store, EPISODE, ep);
  return store;
}

test("moteur complet : déjà vus exclus, raisons, thèmes, écartés", async () => {
  const store = suggestionStore();
  const signals = librarySignals(store);
  assert.deepEqual(signals.map((s) => s.title), ["Matrix", "Divergente", "Arrêté"]);
  const result = await computeAll(store, new FakeTMDB());
  assert.deepEqual(result.errors, []);
  const titles = result.items.map((s) => s.title);
  assert.ok(!titles.includes("Déjà vu"));
  assert.equal(titles[0], "Snowpiercer");
  const snow = result.items[0];
  assert.deepEqual(snow.because, ["Matrix", "Divergente"]);
  assert.deepEqual(snow.matchedThemes, ["Dystopie"]);
  assert.ok(snow.match > 0 && snow.match <= 100);
  assert.ok(titles.includes("The Society"));                 // trouvé par les thèmes forts

  dismiss(store, MOVIE, 101, "Snowpiercer");
  setPreferences(store, new Map(), new Map([["k:1", "Dessin animé"]]), "2026-10-25T00:00:00Z");
  assert.equal(preferences(store).get("k:1")[1], -1);
  const again = await computeAll(store, new FakeTMDB());
  assert.ok(!again.items.some((s) => s.title === "Snowpiercer"));
});

test("pannes TMDB comptées et signalées, jamais avalées", async () => {
  const result = await computeAll(suggestionStore(), new FakeTMDB({ failKeywords: true }));
  assert.ok(result.errors.some((e) => e.includes("TMDB n'a pas répondu")));
});

test("filtres et tris de la page", () => {
  const items = [
    { kind: MOVIE, title: "A", genres: ["Drame"], themes: ["Dystopie"], streaming: ["Netflix"],
      voteAverage: 8, voteCount: 10, year: 2001, score: 0.5 },
    { kind: SHOW, title: "B", genres: ["Animation"], themes: [], streaming: [],
      voteAverage: 6, voteCount: 10, year: 2020, score: 0.9 },
  ];
  assert.deepEqual(filterItems(items).map((s) => s.title), ["B", "A"]);
  assert.deepEqual(filterItems(items, { sort: "rating" }).map((s) => s.title), ["A", "B"]);
  assert.deepEqual(filterItems(items, { themes: ["Dystopie"] }).map((s) => s.title), ["A"]);
  assert.deepEqual(filterItems(items, { streamingOnly: true }).map((s) => s.title), ["A"]);
  assert.deepEqual(filterItems(items, { kinds: [SHOW], minVote: 7 }), []);
  // Années (bornes comprises) ; un titre sans année est exclu dès qu'il y a ce filtre
  const withUnknown = [...items, { ...items[0], title: "C", year: null }];
  assert.deepEqual(filterItems(withUnknown, { years: [2000, 2010] }).map((s) => s.title), ["A"]);
  assert.deepEqual(filterItems(withUnknown, { years: [2001, 2020] }).map((s) => s.title), ["B", "A"]);
  assert.equal(filterItems(withUnknown).length, 3);
});
