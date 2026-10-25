// Tests de la synchro (faux clients TVMaze / TMDB) : échecs signalés et relus la fois suivante.

import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "../js/api/http.js";
import { syncLibrary, syncReportLines, tvmazeStatusLabel } from "../js/core/adding.js";
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
