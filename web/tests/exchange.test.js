// Tests du fichier d'échange côté web, et contrat avec l'app Python : un fichier écrit par
// Python (fixtures/python-export.json, régénéré par tests/test_exchange.py) doit être relu
// et réécrit à l'identique.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { exportData, importData, readJson } from "../js/core/exchange.js";
import { EPISODE, MOVIE, SHOW, STOPPED, refKey } from "../js/core/model.js";
import { makeStore } from "./helpers.js";

const PYTHON_EXPORT = readFileSync(new URL("./fixtures/python-export.json", import.meta.url), "utf-8");

function withoutVolatile(data) {
  const copy = structuredClone(data);
  delete copy.exported_at;
  delete copy.app;
  return copy;
}

test("contrat : un export Python est relu puis réécrit à l'identique", () => {
  const source = readJson(PYTHON_EXPORT);
  const store = makeStore();
  const report = importData(store, source);
  assert.deepEqual([report.showsAdded, report.moviesAdded, report.episodesAdded], [1, 1, 2]);
  assert.equal(report.skipped.length, 0);
  assert.deepEqual(withoutVolatile(exportData(store)), withoutVolatile(source));
});

test("import idempotent, fusion des visionnages, le fichier fait référence pour le statut", () => {
  const store = makeStore();
  importData(store, readJson(PYTHON_EXPORT));
  const again = importData(store, readJson(PYTHON_EXPORT));
  assert.deepEqual([again.showsAdded, again.episodesAdded, again.watches, again.libraryChanges,
    again.ratings, again.lists], [0, 0, 0, 0, 0, 0]);

  const data = readJson(PYTHON_EXPORT);
  const show = data.shows[0];
  show.library.status = STOPPED;
  show.episodes[0].watch = null;                       // absent : on ne supprime rien
  show.episodes[1].watch = { watched_at: "2024-06-01T20:00:00Z", last_watched_at: null, plays: 1 };
  const report = importData(store, data);
  assert.deepEqual([report.libraryChanges, report.watches], [1, 1]);
  assert.equal(store.get("library", refKey(SHOW, "tvmaze:101")).status, STOPPED);
  assert.equal([...store.watches.values()].filter((w) => w.kind === EPISODE).length, 2);
  assert.equal(store.get("watches", refKey(MOVIE, "tmdb:550")).plays, 2);
});

test("entrées invalides : listées dans le rapport, pas avalées", () => {
  const store = makeStore();
  const report = importData(store, {
    format: "watchlog", version: 1,
    shows: [{ title: "Mauvais statut", tvmaze_id: 5, library: { status: "regardé" }, episodes: [] },
      { tvmaze_id: 6 }],
    lists: [{ name: "L", items: [{ kind: MOVIE, tmdb_id: 999, title: "Inconnu" }] }],
  });
  assert.equal(report.skipped.length, 3);
  assert.ok(report.lines().some((l) => l.includes("Inconnu")));
});

test("readJson refuse les fichiers qui ne sont pas des exports Watchlog", () => {
  assert.throws(() => readJson("pas du json"), /illisible/);
  assert.throws(() => readJson('{"format": "autre"}'), /format inconnu/);
  assert.throws(() => readJson('{"format": "watchlog", "version": 99}'), /non prise en charge/);
});

test("aucun identifiant TMDB dans l'export, même s'il traîne dans les réglages", () => {
  const store = makeStore();
  store.setSetting("tmdb_secret", "eyJ-secret");
  assert.ok(!JSON.stringify(exportData(store)).includes("eyJ-secret"));
});
