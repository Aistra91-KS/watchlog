// Régénère tests/fixtures/web-export.json (fichier « écrit par l'app web », relu par
// tests/test_exchange.py côté Python). À relancer si le format d'échange change :
//   node web/tests/tools/web-export-fixture.mjs

import { readFileSync, writeFileSync } from "node:fs";

import { addManualMovie } from "../../js/core/adding.js";
import { exportData, importData, readJson } from "../../js/core/exchange.js";
import { markWatched, setStatus } from "../../js/core/library.js";
import { Store } from "../../js/core/store.js";

const source = new URL("../fixtures/python-export.json", import.meta.url);
const dest = new URL("../../../tests/fixtures/web-export.json", import.meta.url);

const store = new Store();
importData(store, readJson(readFileSync(source, "utf-8")));
setStatus(store, "show", "tvmaze:101", "stopped");
markWatched(store, "episode", "ep:2", "2026-10-20T19:00:00Z");
const movie = addManualMovie(store, { title: "Film du téléphone", year: 2026 });
markWatched(store, "movie", movie.key, "2026-10-21T20:00:00Z");
writeFileSync(dest, `${JSON.stringify(exportData(store, new Date("2026-10-25T12:00:00Z")), null, 1)}\n`);
console.log(`Écrit : ${dest.pathname}`);
