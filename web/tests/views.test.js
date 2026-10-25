// Chaque page s'affiche sans erreur (rendu = texte HTML, testable sous Node), et les données
// venues d'un fichier ou d'une API sont échappées (pas d'injection de HTML).

import assert from "node:assert/strict";
import { test } from "node:test";

import { EPISODE, MOVIE } from "../js/core/model.js";
import { html, poster, safeUrl } from "../js/views/dom.js";
import * as aRegarder from "../js/views/a-regarder.js";
import * as ajouter from "../js/views/ajouter.js";
import * as bibliotheque from "../js/views/bibliotheque.js";
import * as calendrier from "../js/views/calendrier.js";
import * as film from "../js/views/film.js";
import * as parametres from "../js/views/parametres.js";
import * as serie from "../js/views/serie.js";
import * as statistiques from "../js/views/statistiques.js";
import * as suggestions from "../js/views/suggestions.js";
import { addEpisode, addMovie, addShow, makeStore, watch } from "./helpers.js";

const EVIL = `<img src=x onerror="alert(1)">`;

function fakeApp() {
  const store = makeStore();
  const show = addShow(store, `Le Royaume ${EVIL}`, { id: 1, poster_url: "javascript:alert(1)",
    summary: EVIL, genres: "Drame, Science-Fiction", status: "watching" });
  addEpisode(store, show, 1, 1, { id: 11, airdate: "2020-01-01", title: EVIL });
  addEpisode(store, show, 1, 2, { id: 12, airdate: "2099-01-01" });
  watch(store, EPISODE, "ep:11");
  const movie = addMovie(store, "Fight Club", { id: 550, release: "1999-11-10" });
  watch(store, MOVIE, movie);
  const unavailable = async () => false;
  return {
    store, ui: {}, sync: { running: false }, persistent: true,
    tmdb: { configured: false, available: unavailable },
    tvmaze: {},
  };
}

const PAGES = { aRegarder, ajouter, bibliotheque, calendrier, parametres, statistiques, suggestions };

for (const [name, view] of Object.entries(PAGES)) {
  test(`page ${name} : affichage sans erreur, un seul titre h1, rien d'injecté`, async () => {
    const out = String(await view.render(fakeApp(), []));
    assert.equal(out.match(/<h1[\s>]/g)?.length, 1);
    assert.ok(!out.includes(EVIL), "HTML non échappé");
    assert.ok(!out.includes("javascript:"), "URL dangereuse");
  });
}

test("fiches série et film", async () => {
  const app = fakeApp();
  const showPage = String(await serie.render(app, ["tvmaze:1"]));
  assert.ok(showPage.includes("Saison 1") && showPage.includes("1/2 vus"));
  assert.ok(!showPage.includes(EVIL) && !showPage.includes("javascript:"));
  const moviePage = String(await film.render(app, ["tmdb:550"]));
  assert.ok(moviePage.includes("Fight Club") && moviePage.includes("Vu le"));
  assert.ok(String(await serie.render(app, ["inconnue"])).includes("introuvable"));
});

test("échappement et URL sûres", () => {
  assert.equal(String(html`<p>${EVIL}</p>`), "<p>&lt;img src=x onerror=&quot;alert(1)&quot;&gt;</p>");
  assert.equal(safeUrl("javascript:alert(1)"), null);
  assert.equal(safeUrl("https://static.tvmaze.com/a.jpg"), "https://static.tvmaze.com/a.jpg");
  assert.ok(String(poster("javascript:alert(1)", "X")).includes("poster-empty"));
});
