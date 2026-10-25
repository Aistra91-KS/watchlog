// Tests du cœur de l'app web : dates, Store, actions de bibliothèque, « À regarder »,
// progression. Aucun appel réseau, aucune API du navigateur.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  addDays, fmtAgo, fmtDate, fmtRuntime, localDate, localDayToUtc, weekStart,
} from "../js/core/dates.js";
import {
  addRewatch, addSeasonRewatch, getRating, getWatch, markSeasonWatched, markWatched,
  markWatchedUntil, refreshCompleted, removeRewatch, removeSeasonRewatch, setRating,
  setRatingValue, setShowWatchDates, setStatus, unmarkWatched, upsertWatch,
} from "../js/core/library.js";
import { COMPLETED, EPISODE, MOVIE, SHOW, STOPPED, TO_WATCH, refKey } from "../js/core/model.js";
import { lastWatchedLabel, libraryOverview, progressLabel, SORT_PAUSED } from "../js/core/progress.js";
import { ApiError } from "../js/api/http.js";
import {
  buildQueue, calendar, checkPlatforms, dayEntryLabel, filterOnPlatforms, groupDay, nextEpisode,
  releasesBetween, NO_DATE, RELEASED, SORT_RECENT, UPCOMING,
} from "../js/core/queue.js";
import { addEpisode, addMovie, addShow, makeStore, watch } from "./helpers.js";

const TODAY = "2026-10-12";

test("dates : heure de Paris, jours, formats", () => {
  // 23 h 30 UTC le 31 mars = 1 h 30 le 1er avril à Paris (heure d'été).
  assert.equal(localDate("2026-03-31T23:30:00Z", "2026-03-31"), "2026-04-01");
  assert.equal(localDate(null, "2026-03-31"), "2026-03-31");
  assert.equal(localDayToUtc("2026-07-14"), "2026-07-14T10:00:00Z");   // été : UTC+2
  assert.equal(localDayToUtc("2026-01-14"), "2026-01-14T11:00:00Z");   // hiver : UTC+1
  assert.equal(addDays("2026-02-28", 1), "2026-03-01");
  assert.equal(weekStart("2026-10-25"), "2026-10-19");                 // dimanche -> lundi
  assert.equal(fmtDate("2026-10-25"), "25/10/2026");
  assert.equal(fmtRuntime(125), "2 h 05");
  assert.equal(fmtRuntime(45), "45 min");
  assert.equal(fmtRuntime(120), "2 h");
});

test("dates : temps écoulé (comme dates.fmt_ago)", () => {
  const now = Date.parse("2026-10-22T12:00:00Z");
  assert.equal(fmtAgo("2026-10-22T11:59:40Z", now), "à l'instant");
  assert.equal(fmtAgo("2026-10-22T11:55:00Z", now), "il y a 5 min");
  assert.equal(fmtAgo("2026-10-22T09:10:00Z", now), "il y a 2 h");
  assert.equal(fmtAgo("2026-10-21T11:00:00Z", now), "il y a 1 jour");
  assert.equal(fmtAgo("2026-10-18T12:00:00Z", now), "il y a 4 jours");
  assert.equal(fmtAgo("pas une date", now), "à l'instant");
});

test("Store : épisodes indexés par série et clés modifiées", () => {
  const store = makeStore();
  const show = addShow(store, "Le Royaume");
  addEpisode(store, show, 1, 2, { id: 12 });
  addEpisode(store, show, 1, 1, { id: 11 });
  addEpisode(store, show, 1, null, { id: 10, title: "Spécial" });
  assert.deepEqual(store.episodesOf(show).map((e) => e.number), [1, 2, null]);
  const dirty = store.takeDirty();
  assert.equal(dirty.episodes.length, 3);
  assert.deepEqual(store.takeDirty(), {});
  store.remove("episodes", "ep:12");
  assert.equal(store.episodesOf(show).length, 2);
  assert.deepEqual(store.takeDirty(), { episodes: ["ep:12"] });
});

test("visionnages : fusion idempotente, revisionnage, saison, « j'en suis là »", () => {
  const store = makeStore();
  const show = addShow(store, "Le Royaume");
  const e1 = addEpisode(store, show, 1, 1, { id: 1, airdate: "2020-01-01" });
  const e2 = addEpisode(store, show, 1, 2, { id: 2, airdate: "2020-01-08" });
  addEpisode(store, show, 1, 3, { id: 3, airdate: "2099-01-01" });
  addEpisode(store, show, 2, 1, { id: 4, airdate: "2020-02-01" });

  let r = upsertWatch(store, EPISODE, e1, "2024-02-01T00:00:00Z", null, 1);
  assert.ok(r.changed);
  r = upsertWatch(store, EPISODE, e1, "2024-03-01T00:00:00Z", null, 1);
  assert.equal(r.watch.watched_at, "2024-02-01T00:00:00Z");
  assert.equal(r.watch.last_watched_at, "2024-03-01T00:00:00Z");
  assert.ok(!upsertWatch(store, EPISODE, e1, "2024-03-01T00:00:00Z", null, 1).changed);
  assert.equal(addRewatch(store, EPISODE, e1).plays, 2);

  assert.equal(markSeasonWatched(store, show, 1, false, TODAY), 1);   // e2 (e3 pas sorti)
  assert.ok(unmarkWatched(store, EPISODE, e2));
  assert.equal(markWatchedUntil(store, "ep:4"), 3);                   // e2, e3, S02E01
  assert.equal(markWatched(store, EPISODE, e2).plays, 1);
});

test("statut, note et passage automatique en « Terminé »", () => {
  const store = makeStore();
  const show = addShow(store, "Fini", { status: "watching", status_tvmaze: null });
  store.put("shows", { ...store.get("shows", show), status: "Ended" });
  const e1 = addEpisode(store, show, 1, 1, { id: 1, airdate: "2020-01-01" });
  addEpisode(store, show, 1, null, { id: 2, airdate: "2020-01-02" });  // spécial : ignoré
  assert.equal(refreshCompleted(store, TODAY), 0);
  watch(store, EPISODE, e1);
  assert.equal(refreshCompleted(store, TODAY), 1);
  assert.equal(store.get("library", refKey(SHOW, show)).status, COMPLETED);

  assert.throws(() => setStatus(store, SHOW, show, "regardé"), /Statut inconnu/);
  assert.equal(setStatus(store, SHOW, show, STOPPED).status, STOPPED);
  assert.equal(setRating(store, SHOW, show, { rating: 4, emotions: ["thrilled"] }).emotions, "thrilled");
  assert.throws(() => setRating(store, SHOW, show, { rating: 6 }), /Note invalide/);
  assert.equal(setRating(store, SHOW, show, {}), null);
  assert.equal(store.get("ratings", refKey(SHOW, show)), undefined);
});

function queueStore() {
  const store = makeStore();
  const a = addShow(store, "Alpha", { id: 1 });
  addEpisode(store, a, 1, 1, { id: 11, airdate: "2026-01-05" });
  addEpisode(store, a, 1, 2, { id: 12, airdate: "2026-12-01" });
  addEpisode(store, a, 1, 3, { id: 13 });
  const b = addShow(store, "Bêta", { id: 2, status: TO_WATCH });
  addEpisode(store, b, 1, 1, { id: 21, airdate: "2025-06-01" });
  addEpisode(store, b, 1, 2, { id: 22, airdate: "2025-06-08" });
  const stopped = addShow(store, "Arrêtée", { id: 3, status: STOPPED });
  addEpisode(store, stopped, 1, 1, { id: 31, airdate: "2025-01-01" });
  const film = addMovie(store, "Film", { id: 9, release: "2026-10-01" });
  return { store, a, b, film };
}

test("« À regarder » : ordre par date, sections, statuts, prochain épisode", () => {
  const { store, a, b } = queueStore();
  const items = buildQueue(store, { today: TODAY });
  assert.deepEqual(items.map((i) => `${i.title} ${i.code}`.trim()),
    ["Bêta S01E01", "Bêta S01E02", "Alpha S01E01", "Film", "Alpha S01E02", "Alpha S01E03"]);
  assert.deepEqual([...new Set(items.map((i) => i.availability))], [RELEASED, UPCOMING, NO_DATE]);
  assert.deepEqual(buildQueue(store, { today: TODAY, nextOnly: true, kinds: [EPISODE] })
    .map((i) => i.ref), ["ep:21", "ep:11"]);
  assert.equal(nextEpisode(store, a, TODAY).code, "S01E01");
  watch(store, EPISODE, "ep:11", "2026-10-10T20:00:00Z");
  watch(store, EPISODE, "ep:21", "2026-09-01T20:00:00Z");
  assert.deepEqual(buildQueue(store, { today: TODAY, sort: SORT_RECENT, kinds: [EPISODE],
    availabilities: [RELEASED] }).map((i) => i.title), ["Bêta"]);
  assert.equal(nextEpisode(store, b, TODAY).code, "S01E02");
});

/** Faux TMDB pour les plateformes (comme PlatformTMDB de tests/test_queue.py). */
class PlatformTMDB {
  constructor(offers, { failing = new Set(), find = new Map(), available = true } = {}) {
    Object.assign(this, { offers, failing, find, ready: available, calls: [] });
  }

  async available() { return this.ready; }

  async watchProviders(media, id) {
    this.calls.push(`${media}:${id}`);
    if (this.failing.has(`${media}:${id}`)) throw new ApiError("TMDB indisponible (simulé)");
    return { FR: this.offers[`${media}:${id}`] || {} };
  }

  async findTvByTvdb(tvdbId) { return this.find.get(tvdbId) ?? null; }
}

const offer = (kind, ...names) => ({ [kind]: names.map((n, i) => ({ provider_name: n,
  display_priority: i })) });

test("« Sur mes plateformes » : un appel par titre, titres inconnus signalés", async () => {
  const store = makeStore();
  const shows = [addShow(store, "Sur Netflix", { id: 1, tmdb_id: 10 }),
    addShow(store, "Via TVDB", { id: 2, tvdb_id: 500 }),
    addShow(store, "Saisie à la main", { id: 3 }),
    addShow(store, "En panne", { id: 4, tmdb_id: 30 })];
  for (const [i, show] of shows.entries()) {
    addEpisode(store, show, 1, 1, { airdate: "2026-01-01", id: i * 10 + 1 });
    addEpisode(store, show, 1, 2, { airdate: "2026-01-08", id: i * 10 + 2 });
  }
  addMovie(store, "Film en location", { id: 40, release: "2025-01-01" });
  addMovie(store, "Film gratuit", { id: 41, release: "2025-02-01" });
  addMovie(store, "Film sans id", { id: 42, release: "2025-03-01", tmdb_id: null });
  const tmdb = new PlatformTMDB({ "tv:10": offer("flatrate", "Netflix"),
    "tv:20": offer("flatrate", "Canal+"), "movie:40": offer("rent", "Netflix"),
    "movie:41": offer("ads", "Netflix", "TF1+") },
  { failing: new Set(["tv:30"]), find: new Map([[500, 20]]) });
  const items = buildQueue(store, { today: TODAY });

  const check = await checkPlatforms(store, tmdb, items);

  assert.deepEqual(tmdb.calls.sort(), ["movie:40", "movie:41", "tv:10", "tv:20", "tv:30"]);
  assert.equal(store.get("shows", shows[1]).tmdb_id, 20);       // trouvé via TVDB, gardé
  const result = filterOnPlatforms(items, check, ["Netflix"]);
  assert.deepEqual(result.items.map((i) => `${i.title} ${i.code}`.trim()),
    ["Film gratuit", "Sur Netflix S01E01", "Sur Netflix S01E02"]);
  assert.deepEqual(result.noId, ["Film sans id", "Saisie à la main"]);
  assert.deepEqual(result.failed, ["En panne"]);
  assert.equal(result.hidden, 3);
  const both = filterOnPlatforms(items, check, ["Netflix", "Canal+"]);
  assert.deepEqual(new Set(both.items.map((i) => i.title)),
    new Set(["Sur Netflix", "Via TVDB", "Film gratuit"]));
});

test("« Sur mes plateformes » : TMDB non connecté = panne, jamais « pas dispo »", async () => {
  const store = makeStore();
  const show = addShow(store, "Série", { tmdb_id: 10 });
  addEpisode(store, show, 1, 1, { airdate: "2026-01-01" });
  const tmdb = new PlatformTMDB({}, { available: false });
  const items = buildQueue(store, { today: TODAY });
  const result = filterOnPlatforms(items, await checkPlatforms(store, tmdb, items), ["Netflix"]);
  assert.deepEqual([result.items, result.failed, tmdb.calls], [[], ["Série"], []]);
});

test("série à numérotation à vérifier : exclue de « À regarder »", () => {
  const { store, a } = queueStore();
  store.put("shows", { ...store.get("shows", a), match_status: "numbering_mismatch" });
  assert.ok(!buildQueue(store, { today: TODAY }).some((i) => i.title === "Alpha"));
});

test("calendrier, mois et regroupement par jour", () => {
  const { store } = queueStore();
  const a = addShow(store, "Gamma", { id: 4 });
  for (let n = 1; n <= 3; n += 1) addEpisode(store, a, 2, n, { id: 40 + n, airdate: "2026-10-20" });
  const items = calendar(store, { today: TODAY, days: 30 });
  assert.deepEqual(items.map((i) => i.title), ["Gamma", "Gamma", "Gamma"]);
  const [entry] = groupDay(items);
  assert.equal(dayEntryLabel(entry), "S02E01–E03");
  const month = releasesBetween(store, "2026-10-01", "2026-10-31", { today: TODAY });
  assert.deepEqual(month.map((i) => i.title), ["Film", "Gamma", "Gamma", "Gamma"]);
});

test("bibliothèque : progression, recherche sans accents, tri « en pause »", () => {
  const { store, a, b, film } = queueStore();
  watch(store, EPISODE, "ep:11", "2026-10-11T20:00:00Z");
  const all = libraryOverview(store, { today: TODAY });
  assert.deepEqual(all.map((e) => e.title), ["Alpha", "Arrêtée", "Bêta", "Film"]);
  const alpha = all.find((e) => e.ref === a);
  assert.equal(progressLabel(alpha, TODAY), "À jour");
  assert.equal(lastWatchedLabel(alpha, TODAY), "Vu hier");
  assert.equal(progressLabel(all.find((e) => e.ref === b), TODAY), "Pas commencé · 2 restants");
  assert.equal(progressLabel(all.find((e) => e.ref === film), TODAY), "Non vu");
  assert.deepEqual(libraryOverview(store, { search: "beta", today: TODAY }).map((e) => e.title), ["Bêta"]);
  assert.deepEqual(libraryOverview(store, { sort: SORT_PAUSED, today: TODAY }).map((e) => e.title),
    ["Alpha"]);
  assert.deepEqual(libraryOverview(store, { kind: MOVIE, today: TODAY }).map((e) => e.title), ["Film"]);
});

test("jour d'import : dernier visionnage ignoré", () => {
  const { store, a } = queueStore();
  for (let n = 0; n < 50; n += 1) {
    const key = addEpisode(store, a, 3, n + 1, { id: 1000 + n });
    watch(store, EPISODE, key, "2026-08-13T17:02:42Z");
  }
  const alpha = libraryOverview(store, { today: TODAY }).find((e) => e.ref === a);
  assert.equal(alpha.lastWatched, null);
  assert.equal(lastWatchedLabel(alpha, TODAY), "Date inconnue (import)");
});

test("revisionnage daté (comme library.add_rewatch / remove_rewatch)", () => {
  const store = makeStore();
  const movie = addMovie(store, "Le Phare", { id: 5 });
  watch(store, MOVIE, movie, "2026-03-01T20:00:00Z");
  // Revu le 14/07 : midi heure de Paris, dernier visionnage avancé
  let w = addRewatch(store, MOVIE, movie, "2026-07-14");
  assert.deepEqual([w.plays, w.watched_at, w.last_watched_at],
    [2, "2026-03-01T20:00:00Z", "2026-07-14T10:00:00Z"]);
  // Revisionnage oublié, saisi après coup : le dernier visionnage ne recule pas
  w = addRewatch(store, MOVIE, movie, "2026-05-01");
  assert.deepEqual([w.plays, w.last_watched_at], [3, "2026-07-14T10:00:00Z"]);
  removeRewatch(store, MOVIE, movie);
  w = removeRewatch(store, MOVIE, movie);
  assert.deepEqual([w.plays, w.last_watched_at], [1, "2026-03-01T20:00:00Z"]);
  assert.equal(removeRewatch(store, MOVIE, movie).plays, 1);           // jamais sous 1
  // Pas encore vu : simple visionnage ce jour-là
  const other = addMovie(store, "Brume", { id: 6 });
  w = addRewatch(store, MOVIE, other, "2025-12-24");
  assert.deepEqual([w.plays, w.watched_at], [1, "2025-12-24T11:00:00Z"]);
});

test("dates de plusieurs épisodes d'un coup (comme library.set_show_watch_dates)", () => {
  const store = makeStore();
  const show = addShow(store, "Lost", { id: 1 });
  const other = addShow(store, "Autre", { id: 2 });
  const [s1e1, s1e2] = [addEpisode(store, show, 1, 1, { id: 1 }), addEpisode(store, show, 1, 2, { id: 2 })];
  const [s2e1, special] = [addEpisode(store, show, 2, 1, { id: 3 }), addEpisode(store, show, 2, null, { id: 4 })];
  const unseen = addEpisode(store, show, 2, 2, { id: 5 });
  const elsewhere = addEpisode(store, other, 1, 1, { id: 6 });
  for (const ep of [s1e1, s1e2, s2e1, special, elsewhere]) markWatched(store, EPISODE, ep);
  assert.equal(setShowWatchDates(store, show, "2015-03-01", [2]), 2);
  assert.equal(getWatch(store, EPISODE, special).watched_at, "2015-03-01T11:00:00Z");
  assert.notEqual(getWatch(store, EPISODE, s1e1).watched_at, null);
  assert.equal(getWatch(store, EPISODE, unseen), null);                       // pas marqué vu
  assert.equal(setShowWatchDates(store, show, null), 4);                     // date inconnue
  for (const ep of [s1e1, s1e2, s2e1, special]) assert.equal(getWatch(store, EPISODE, ep).watched_at, null);
  assert.notEqual(getWatch(store, EPISODE, elsewhere).watched_at, null);
});

test("revisionnage par saison (comme library.add_season_rewatch / remove_season_rewatch)", () => {
  const store = makeStore();
  const show = addShow(store, "Le Royaume", { id: 1 });
  const e11 = addEpisode(store, show, 1, 1, { id: 11 });
  const e12 = addEpisode(store, show, 1, 2, { id: 12 });
  const e13 = addEpisode(store, show, 1, 3, { id: 13 });
  const special = addEpisode(store, show, 1, null, { id: 19 });
  const e21 = addEpisode(store, show, 2, 1, { id: 21 });
  for (const ep of [e11, e12, special, e21]) markWatched(store, EPISODE, ep);
  assert.equal(addSeasonRewatch(store, show, [1], "2025-01-01"), 2);
  const plays = (ep) => getWatch(store, EPISODE, ep).plays;
  assert.deepEqual([e11, e12, special, e21].map(plays), [2, 2, 1, 1]);
  assert.equal(getWatch(store, EPISODE, e13), null);                         // pas vu
  assert.equal(addSeasonRewatch(store, show, [1, 2], null, true), 4);
  assert.equal(removeSeasonRewatch(store, show, [1, 2], true), 4);
  assert.equal(removeSeasonRewatch(store, show, [1, 2], true), 2);
  assert.equal(removeSeasonRewatch(store, show, [1, 2], true), 0);
});

test("note seule d'un épisode : le reste de l'avis est gardé (comme set_rating_value)", () => {
  const store = makeStore();
  setRating(store, EPISODE, "ep:5", { rating: 2, note: "Lent", emotions: ["bored"],
    favoriteCharacter: "Gus" });
  const r = setRatingValue(store, EPISODE, "ep:5", 4);
  assert.deepEqual([r.rating, r.note, r.emotions, r.favorite_character], [4, "Lent", "bored", "Gus"]);
  assert.equal(setRatingValue(store, EPISODE, "ep:5", null).note, "Lent");
  setRatingValue(store, EPISODE, "ep:6", 3);
  assert.equal(setRatingValue(store, EPISODE, "ep:6", null), null);
  assert.equal(getRating(store, EPISODE, "ep:6"), null);
  assert.throws(() => setRatingValue(store, EPISODE, "ep:6", 9));
});
