// Tests de la synchro (faux clients TVMaze / TMDB) : échecs signalés et relus la fois suivante.

import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "../js/api/http.js";
import {
  addManualMovie, addMovie as addTmdbMovie, syncLibrary, syncReportLines, tvmazeStatusLabel,
} from "../js/core/adding.js";
import { getWatch, markWatched } from "../js/core/library.js";
import { COMPLETED, MOVIE as MOVIE_KIND, TO_WATCH } from "../js/core/model.js";
import { addMovie, addShow, makeStore } from "./helpers.js";

const NOW = Date.parse("2026-10-25T12:00:00Z");

class FakeTVMaze {
  constructor({ fail = new Set(), missing = new Set() } = {}) {
    Object.assign(this, { fail, missing, reads: [] });
  }

  async updatedSince() { return new Map(); }   // rien de changé chez TVMaze

  async getShow(id) {
    this.reads.push(id);
    if (this.fail.has(id)) throw new ApiError("TVMaze : erreur HTTP 500");
    if (this.missing.has(id)) return null;
    return { id, name: `Série ${id}`, status: "Running", _embedded: { episodes: [] } };
  }

  async getEpisodesWithSpecials() { return []; }
}

class DownTMDB {
  async available() { throw new ApiError("TMDB injoignable pour l'instant (réseau ?)."); }
}

test("série en échec : signalée, puis relue à la synchro suivante même sans changement", async () => {
  const store = makeStore();
  addShow(store, "Ok", { id: 1, synced_at: "2026-10-24T12:00:00Z" });
  addShow(store, "Panne", { id: 2, synced_at: "2026-10-24T12:00:00Z" });
  addShow(store, "Retirée", { id: 3, synced_at: "2026-10-24T12:00:00Z" });
  store.setSetting("last_sync_at", "2026-10-24T12:00:00Z");
  store.setSetting("sync_retry", ["tvmaze:2", "tvmaze:3"]);

  const tvmaze = new FakeTVMaze({ fail: new Set([2]), missing: new Set([3]) });
  const report = await syncLibrary(store, tvmaze, null, { now: NOW });
  assert.deepEqual(tvmaze.reads.sort(), [2, 3]);          // seules les séries à reprendre
  assert.equal(report.errors.length, 2);
  assert.ok(syncReportLines(report).some((l) => l.includes("introuvable sur TVMaze")));
  assert.deepEqual(store.setting("sync_retry"), ["tvmaze:2"]);

  const again = new FakeTVMaze();
  await syncLibrary(store, again, null, { now: NOW + 3_600_000 });
  assert.deepEqual(again.reads, [2]);
  assert.deepEqual(store.setting("sync_retry"), []);
});

test("TMDB injoignable : films signalés et gardés pour la prochaine fois", async () => {
  const store = makeStore();
  addMovie(store, "À venir", { id: 7, release: "2027-01-01" });
  store.setSetting("last_sync_at", "2026-10-24T12:00:00Z");
  const report = await syncLibrary(store, new FakeTVMaze(), new DownTMDB(), { now: NOW });
  assert.ok(report.errors[0].startsWith("TMDB : TMDB injoignable"));
  assert.deepEqual(store.setting("sync_retry"), ["tmdb:7"]);
});

test("statut de diffusion TVMaze en français (comme adding.tvmaze_status_label)", () => {
  assert.equal(tvmazeStatusLabel("Running"), "En cours de diffusion");
  assert.equal(tvmazeStatusLabel("Ended"), "Terminée");
  assert.equal(tvmazeStatusLabel("Pilot"), "Pilot");
  assert.equal(tvmazeStatusLabel(null), "");
});

test("film ajouté en « Terminé » : vu à la date choisie (comme adding._record_movie_watch)", async () => {
  const tmdb = { getMovie: async (id) => ({ id, title: "Fight Club", release_date: "1999-11-10" }) };
  let store = makeStore();
  let movie = await addTmdbMovie(store, tmdb, 550, { status: COMPLETED, watchedOn: "1999-11-10" });
  assert.equal(getWatch(store, MOVIE_KIND, movie.key).watched_at, "1999-11-10T11:00:00Z");
  store = makeStore();
  movie = await addTmdbMovie(store, tmdb, 550, { status: COMPLETED });
  assert.equal(getWatch(store, MOVIE_KIND, movie.key).watched_at, null);       // date inconnue
  store = makeStore();
  movie = await addTmdbMovie(store, tmdb, 550, { status: TO_WATCH, watchedOn: "2000-01-01" });
  assert.equal(getWatch(store, MOVIE_KIND, movie.key), null);
  markWatched(store, MOVIE_KIND, movie.key, "2020-02-02T20:00:00Z");
  await addTmdbMovie(store, tmdb, 550, { status: COMPLETED, watchedOn: "2000-01-01" });
  assert.equal(getWatch(store, MOVIE_KIND, movie.key).watched_at, "2020-02-02T20:00:00Z");   // gardé
  const manual = addManualMovie(store, { title: "Film perso", status: COMPLETED, watchedOn: "1995-03-03" });
  assert.equal(getWatch(store, MOVIE_KIND, manual.key).watched_at, "1995-03-03T11:00:00Z");
});
