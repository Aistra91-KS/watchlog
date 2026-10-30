// Tests de l'accueil (core/home.js, port de home.py). Mêmes cas que tests/test_home.py.

import assert from "node:assert/strict";
import { test } from "node:test";

import { addDays } from "../js/core/dates.js";
import {
  activity, continueWatching, currentStreak, moviesToWatch, recentWatches, upcoming,
} from "../js/core/home.js";
import { EPISODE, FOR_LATER, MOVIE, SHOW, STOPPED, TO_WATCH, refKey } from "../js/core/model.js";
import { dayEntryLabel } from "../js/core/queue.js";
import { BULK_THRESHOLD, watchesFrame } from "../js/core/stats.js";
import { addEpisode, addMovie, addShow, makeStore } from "./helpers.js";

const TODAY = "2026-10-12";
const noon = (day) => `${day}T10:00:00Z`;   // midi à Paris (heure d'été)
const before = (n) => addDays(TODAY, -n);

function seen(store, kind, ref, first, last = first) {
  store.put("watches", { key: refKey(kind, ref), kind, ref, watched_at: first, last_watched_at: last,
    plays: 1 });
}

// --- Activité ------------------------------------------------------------------------

test("accueil : jours d'affilée jusqu'à aujourd'hui (ou hier)", () => {
  const days = [TODAY, before(1), before(2), before(4)];
  assert.equal(currentStreak(days, TODAY), 3);
  assert.equal(currentStreak(days.slice(1), TODAY), 2);
  assert.equal(currentStreak([before(2)], TODAY), 0);
  assert.equal(currentStreak([], TODAY), 0);
});

test("accueil : semaine, mois en cours et série de jours", () => {
  const store = makeStore();
  const show = addShow(store, "Série");
  const eps = [1, 2, 3, 4].map((n) => addEpisode(store, show, 1, n, { id: n, airdate: "2026-01-01",
    runtime: n === 3 ? null : 40 }));
  const movie = addMovie(store, "Film", { release: "2025-01-01" });
  seen(store, EPISODE, eps[0], noon(TODAY));
  seen(store, EPISODE, eps[1], noon(before(1)));
  seen(store, EPISODE, eps[2], noon(before(6)));
  seen(store, EPISODE, eps[3], noon(before(7)));
  seen(store, MOVIE, movie, noon("2026-09-30"));
  const result = activity(watchesFrame(store), TODAY);
  assert.deepEqual([result.week.episodes, result.week.movies, result.week.minutes], [3, 0, 120]);
  assert.deepEqual([result.month.episodes, result.month.movies, result.month.total], [4, 0, 4]);
  assert.equal(result.streak, 2);
  assert.ok(result.watchedToday);
});

test("accueil : jours d'import ignorés dans l'activité", () => {
  const store = makeStore();
  const show = addShow(store, "Importée");
  for (let n = 1; n <= BULK_THRESHOLD; n += 1) {
    seen(store, EPISODE, addEpisode(store, show, 1, n, { id: n, airdate: "2020-01-01" }), noon(before(2)));
  }
  const result = activity(watchesFrame(store), TODAY);
  assert.equal(result.week.total, 0);
  assert.equal(result.week.ignored, BULK_THRESHOLD);
  assert.equal(result.streak, 0);
});

// --- À suivre, films, sorties, derniers visionnages ------------------------------------

test("accueil : à suivre, séries vues récemment d'abord", () => {
  const store = makeStore();
  const eps = {};
  for (const [id, title, status] of [[1, "Ancienne"], [2, "Récente"], [3, "Pas commencée", TO_WATCH],
    [4, "Plus tard", FOR_LATER]]) {
    const show = addShow(store, title, { id, ...(status ? { status } : {}) });
    eps[title] = [1, 2, 3].map((n) => addEpisode(store, show, 1, n, { id: id * 10 + n,
      airdate: `2026-01-0${n}` }));
  }
  addEpisode(store, "tvmaze:2", 1, 4, { id: 99, airdate: addDays(TODAY, 3) });
  seen(store, EPISODE, eps.Ancienne[0], noon(before(40)));
  seen(store, EPISODE, eps["Récente"][0], noon(before(1)));
  seen(store, EPISODE, eps["Récente"][1], noon(before(1)));
  const { entries, total } = continueWatching(store, { today: TODAY });
  assert.equal(total, 3);
  assert.deepEqual(entries.map((e) => [e.item.title, e.item.code, e.remaining]), [
    ["Récente", "S01E03", 1], ["Ancienne", "S01E02", 2], ["Pas commencée", "S01E01", 3]]);
  assert.equal(entries[2].lastWatched, null);
  assert.equal(continueWatching(store, { today: TODAY, limit: 1 }).entries.length, 1);
});

test("accueil : films à voir, derniers ajoutés d'abord", () => {
  const store = makeStore();
  const add = (id, title, release, addedAt, status) => {
    const key = addMovie(store, title, { id, release, ...(status ? { status } : {}) });
    const lib = store.get("library", refKey(MOVIE, key));
    if (lib) store.put("library", { ...lib, added_at: addedAt });
    return key;
  };
  add(1, "Ancien ajout", "2020-01-01", "2025-01-01T00:00:00");
  add(2, "Dernier ajout", "2010-01-01", "2026-09-01T00:00:00");
  add(3, "Même jour, récent", "2024-01-01", "2025-01-01T00:00:00");
  add(4, "À voir", "2023-01-01", "2026-01-01T00:00:00", TO_WATCH);
  add(5, "Pas sorti", addDays(TODAY, 5), "2026-10-01T00:00:00");
  add(6, "Arrêté", "2020-01-01", null, STOPPED);
  seen(store, MOVIE, add(7, "Déjà vu", "2020-01-01", "2026-10-01T00:00:00"), noon(TODAY));
  const { items, total } = moviesToWatch(store, { today: TODAY, limit: 3 });
  assert.equal(total, 4);
  assert.deepEqual(items.map((m) => m.title), ["Dernier ajout", "À voir", "Même jour, récent"]);
});

test("accueil : prochaines sorties par jour", () => {
  const store = makeStore();
  const show = addShow(store, "Série");
  addEpisode(store, show, 2, 1, { id: 1, airdate: TODAY });
  addEpisode(store, show, 2, 2, { id: 2, airdate: TODAY });
  addEpisode(store, show, 2, 3, { id: 3, airdate: addDays(TODAY, 7) });
  addEpisode(store, show, 2, 4, { id: 4, airdate: addDays(TODAY, 8) });
  addMovie(store, "Film", { release: addDays(TODAY, 2) });
  assert.deepEqual(upcoming(store, { today: TODAY }).map(([day, entries]) => [day,
    entries.map(dayEntryLabel)]), [[TODAY, ["S02E01–E02"]], [addDays(TODAY, 2), ["Film"]],
    [addDays(TODAY, 7), ["S02E03"]]]);
});

test("accueil : derniers visionnages, un par titre, sans jour d'import", () => {
  const store = makeStore();
  const show = addShow(store, "Série");
  const e1 = addEpisode(store, show, 1, 1, { id: 1, airdate: "2026-01-01" });
  const e2 = addEpisode(store, show, 1, 2, { id: 2, airdate: "2026-01-02" });
  const film = addMovie(store, "Film", { id: 1 });
  const revu = addMovie(store, "Revu", { id: 2 });
  seen(store, EPISODE, e1, noon(before(3)));
  seen(store, EPISODE, e2, noon(before(1)));
  seen(store, MOVIE, film, noon(before(2)));
  seen(store, MOVIE, revu, noon("2020-05-01"), noon(TODAY));
  assert.deepEqual(recentWatches(store).map((r) => [r.kind, r.title, r.code]), [
    [MOVIE, "Revu", ""], [SHOW, "Série", "S01E02"], [MOVIE, "Film", ""]]);
  assert.deepEqual(recentWatches(store, { limit: 2 }).map((r) => r.title), ["Revu", "Série"]);
  const imported = addShow(store, "Importée", { id: 9 });
  for (let n = 1; n <= BULK_THRESHOLD; n += 1) {
    seen(store, EPISODE, addEpisode(store, imported, 1, n, { id: 100 + n }), noon(TODAY));
  }
  assert.ok(!recentWatches(store).some((r) => r.title === "Importée"));
});
