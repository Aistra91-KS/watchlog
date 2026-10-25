// Tests des statistiques (mêmes cas que tests/test_stats*.py, en plus court).

import assert from "node:assert/strict";
import { test } from "node:test";

import { EPISODE, MOVIE } from "../js/core/model.js";
import {
  bulkDays, finishEstimate, formatDuration, genreCounts, globalCounts, lastMonths, movieDecades,
  movieRuntimeStats, perMonth, perWeekday, perYear, records, remainingTime, showProgress,
  timeSpent, topShowsByTime, watchPace, watchesFrame, withoutDays,
} from "../js/core/stats.js";
import { addEpisode, addMovie, addShow, makeStore, watch } from "./helpers.js";

const TODAY = "2026-10-12";

function statsStore() {
  const store = makeStore();
  const show = addShow(store, "Le Royaume", { id: 1, genres: "Drame, Science-Fiction" });
  addEpisode(store, show, 1, 1, { id: 11, airdate: "2026-01-01", runtime: 50 });
  addEpisode(store, show, 1, 2, { id: 12, airdate: "2026-01-08", runtime: 50 });
  addEpisode(store, show, 1, 3, { id: 13, airdate: "2026-01-15", runtime: null });
  addEpisode(store, show, 1, 4, { id: 14, airdate: "2026-01-22", runtime: 50 });
  watch(store, EPISODE, "ep:11", "2026-10-05T20:00:00Z");
  watch(store, EPISODE, "ep:12", "2026-10-06T20:00:00Z", 2);
  watch(store, EPISODE, "ep:13", "2026-10-07T20:00:00Z");
  const movie = addMovie(store, "Fight Club", { id: 550, release: "1999-11-10", runtime: 139,
    genres: "Drame", release_date_original: "1999-10-15" });
  watch(store, MOVIE, movie, "2026-09-20T20:00:00Z");
  addMovie(store, "À voir", { id: 2, release: "2020-01-01", runtime: null, status: "to_watch" });
  return store;
}

test("tableau des visionnages, temps passé, chiffres clés", () => {
  const store = statsStore();
  const frame = watchesFrame(store);
  assert.equal(frame.length, 4);
  const time = timeSpent(frame);
  assert.deepEqual([time.episodeMinutes, time.movieMinutes, time.episodesWithoutRuntime],
    [150, 139, 1]);
  assert.equal(formatDuration(time.totalMinutes), "4 h 49 min");
  assert.equal(formatDuration(40), "40 min");
  assert.deepEqual(globalCounts(store), { shows: 1, movies: 2, completedShows: 0,
    episodesWatched: 3, moviesWatched: 1, ratings: 0 });
});

test("par mois, par année, par jour de semaine, records", () => {
  const frame = watchesFrame(statsStore());
  assert.deepEqual(lastMonths(3, TODAY), ["2026-08", "2026-09", "2026-10"]);
  assert.deepEqual(perMonth(frame, 3, TODAY), [
    { month: "2026-08", episodes: 0, movies: 0 }, { month: "2026-09", episodes: 0, movies: 1 },
    { month: "2026-10", episodes: 3, movies: 0 }]);
  assert.equal(perMonth(frame, 1, TODAY, "hours")[0].episodes, 2.5);
  assert.deepEqual(perYear(frame), [{ year: 2026, episodes: 3, movies: 1 }]);
  assert.equal(perWeekday(frame)[0].count, 1);                    // lundi 5 octobre
  const rec = records(frame);
  assert.deepEqual(rec.streak, [3, "2026-10-05", "2026-10-07"]);
  assert.deepEqual(rec.bestMonth, ["2026-10", 3]);
});

test("séries, genres, films", () => {
  const store = statsStore();
  const frame = watchesFrame(store);
  assert.deepEqual(topShowsByTime(frame).map((s) => [s.title, s.hours, s.episodes]),
    [["Le Royaume", 2.5, 3]]);
  assert.deepEqual(genreCounts(frame, EPISODE), [{ genre: "Drame", count: 1 },
    { genre: "Science-Fiction", count: 1 }]);
  assert.deepEqual(movieDecades(frame), [{ decade: 1990, label: "1990s", count: 1 }]);
  assert.equal(movieRuntimeStats(frame).average, 139);
  assert.deepEqual(showProgress(store, TODAY).map((r) => [r.seen, r.aired, r.percent]), [[3, 4, 75]]);
});

test("reste à voir, rythme, jours d'import ignorés", () => {
  const store = statsStore();
  const remaining = remainingTime(store, TODAY);
  assert.deepEqual([remaining.episodes, remaining.movies, remaining.totalMinutes, remaining.estimated],
    [1, 1, 50 + 110, 1]);
  const frame = watchesFrame(store);
  const pace = watchPace(frame, 7, [], TODAY);
  assert.equal(pace.watches, 2);                                     // du 06/10 au 12/10
  assert.equal(pace.minutes, 100);                                   // durée inconnue = moyenne
  assert.deepEqual(finishEstimate(160, 20, TODAY), { days: 8, date: "2026-10-20" });
  assert.equal(finishEstimate(160, 0, TODAY), null);

  const show = "tvmaze:1";
  for (let n = 0; n < 50; n += 1) {
    const key = addEpisode(store, show, 2, n + 1, { id: 100 + n, airdate: "2026-02-01" });
    watch(store, EPISODE, key, "2026-08-13T17:00:00Z");
  }
  const all = watchesFrame(store);
  const bulk = bulkDays(all);
  assert.deepEqual([...bulk.keys()], ["2026-08-13"]);
  assert.equal(withoutDays(all, bulk.keys()).length, 4);
  assert.equal(watchPace(all, 365, bulk.keys(), TODAY).start, "2026-08-13");
});
