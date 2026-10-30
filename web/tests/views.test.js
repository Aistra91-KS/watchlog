// Chaque page s'affiche sans erreur (rendu = texte HTML, testable sous Node), et les données
// venues d'un fichier ou d'une API sont échappées (pas d'injection de HTML).

import assert from "node:assert/strict";
import { test } from "node:test";

import { setRating } from "../js/core/library.js";
import { EPISODE, MOVIE, SHOW } from "../js/core/model.js";
import { html, poster, safeUrl } from "../js/views/dom.js";
import * as shared from "../js/views/shared.js";
import * as accueil from "../js/views/accueil.js";
import * as aRegarder from "../js/views/a-regarder.js";
import * as ajouter from "../js/views/ajouter.js";
import * as apercu from "../js/views/apercu.js";
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
    tvmaze: { getCast: async () => [] },
  };
}

const PAGES = { accueil, aRegarder, ajouter, bibliotheque, calendrier, parametres, statistiques, suggestions };

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

test("demi-notes : dans « Mon avis », le tableau des épisodes et les statistiques", async () => {
  const app = fakeApp();
  setRating(app.store, SHOW, "tvmaze:1", { rating: 4.5 });
  setRating(app.store, EPISODE, "ep:11", { rating: 2.5 });
  const showPage = String(await serie.render(app, ["tvmaze:1"]));
  assert.match(showPage, /<option value="4.5" selected>4,5 \/ 5<\/option>/);   // Mon avis
  assert.match(showPage, /<option value="2.5" selected>2,5<\/option>/);         // épisode
  app.ui.tabs = { stats: "Notes" };
  const stats = String(statistiques.render(app));
  assert.ok(stats.includes("★★★★½") && stats.includes("4,5 sur 5"));
});

test("échappement et URL sûres", () => {
  assert.equal(String(html`<p>${EVIL}</p>`), "<p>&lt;img src=x onerror=&quot;alert(1)&quot;&gt;</p>");
  assert.equal(safeUrl("javascript:alert(1)"), null);
  assert.equal(safeUrl("https://static.tvmaze.com/a.jpg"), "https://static.tvmaze.com/a.jpg");
  assert.ok(String(poster("javascript:alert(1)", "X")).includes("poster-empty"));
});

test("« À regarder », « Sur mes plateformes » : recherche, puis liste filtrée", async (t) => {
  const saved = new Map([["watchlog.prefs.queue", JSON.stringify({ mine: true })]]);
  globalThis.localStorage = { getItem: (k) => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v) };
  t.after(() => { delete globalThis.localStorage; });
  const app = fakeApp();
  app.store.setSetting("my_platforms", ["Netflix"]);
  app.store.put("shows", { ...app.store.get("shows", "tvmaze:1"), tmdb_id: 10 });
  addMovie(app.store, "Pas vu", { id: 551, release: "2001-01-01" });

  assert.match(String(await aRegarder.render(app, [])), /pas appliqué/);   // pas de clé TMDB

  app.tmdb = { configured: true, connected: true, available: async () => true,
    watchProviders: async (media) => (media === "tv"
      ? { FR: { flatrate: [{ provider_name: "Netflix" }] } } : { FR: { rent: [{ provider_name: "Netflix" }] } }) };
  const done = new Promise((resolve) => { app.rerender = resolve; });
  assert.match(String(await aRegarder.render(app, [])), /Recherche des\s+plateformes/);
  await done;
  const out = String(await aRegarder.render(app, []));
  assert.match(out, /Le Royaume/);
  assert.ok(!out.includes("Pas vu"), "film seulement en location : masqué");
});

test("calendrier : vues Mois, Semaine et Liste ; statistiques : chaque onglet", async (t) => {
  const saved = new Map();
  globalThis.localStorage = { getItem: (k) => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v) };
  t.after(() => { delete globalThis.localStorage; });
  const app = fakeApp();
  for (const view of ["mois", "semaine", "liste"]) {
    saved.set("watchlog.prefs.calendar", JSON.stringify({ view }));
    const out = String(calendrier.render(app));
    assert.equal(out.match(/<h1[\s>]/g)?.length, 1);
    assert.ok(!out.includes(EVIL));
    assert.ok(view === "liste" ? out.includes("prochains jours") : out.includes("cal-grid"), view);
  }
  for (const tab of ["Activité", "Séries", "Films", "Notes"]) {
    app.ui.tabs = { stats: tab };
    const out = String(statistiques.render(app));
    assert.match(out, new RegExp(`aria-selected="true"[^>]*>${tab}<`));
    assert.ok(!out.includes(EVIL), tab);
  }
});

test("ajouter : onglets, résultats séries + films, saisie manuelle", async () => {
  const app = fakeApp();
  app.tvmaze = { search: async () => [{ show: { id: 9, name: EVIL, premiered: "2021-01-01",
    summary: `<p>${EVIL}</p>`, status: "Ended" } }] };
  const form = { querySelector: () => null };
  globalThis.FormData = class { get() { return "rives"; } };
  try {
    String(ajouter.render(app, []));
    await ajouter.actions["add-search"](app, form);
  } finally {
    delete globalThis.FormData;
  }
  const out = String(ajouter.render(app, []));
  assert.ok(out.includes("1 série(s) et") && out.includes("Films non cherchés"));
  assert.ok(out.includes("Terminée") && !out.includes(EVIL));
  app.ui.tabs = { add: "Saisie manuelle" };
  assert.ok(String(ajouter.render(app, [])).includes("Ajouter la série"));
});

test("aperçu d'un titre TMDB : rien d'injecté, déjà dans la bibliothèque, titre inconnu", async () => {
  const app = fakeApp();
  app.tmdb = { configured: true, details: async (media, id) => (id === 550 ? {
    id: 550, title: EVIL, overview: EVIL, release_date: "1999-11-10", genres: [{ name: EVIL }],
    poster_path: "/p.jpg", backdrop_path: "/b.jpg", vote_average: 8.4, vote_count: 30000,
    credits: { cast: [{ name: EVIL, character: EVIL, profile_path: "/c.jpg" }], crew: [] },
    videos: { results: [{ site: "YouTube", type: "Trailer", key: "abcdefghijk", iso_639_1: "fr" }] },
  } : null), recommendations: async () => [{ id: 9, title: EVIL, release_date: "2001-01-01" }],
  watchProviders: async () => ({}) };
  const out = String(await apercu.render(app, ["film", "550"]));
  assert.equal(out.match(/<h1[\s>]/g)?.length, 1);
  assert.ok(!out.includes(EVIL) && !out.includes("javascript:"));
  assert.ok(out.includes("Déjà dans ta bibliothèque") && out.includes("Lire la bande-annonce"));
  assert.ok(out.includes("#/apercu/film/9"));
  assert.match(String(await apercu.render(app, ["film", "1"])), /introuvable/);
  assert.match(String(await apercu.render(app, ["rien", "x"])), /Aucun titre/);
});

test("ajouter : « Voir la fiche » d'un résultat ouvre l'aperçu, retour à la recherche", async () => {
  const app = fakeApp();
  app.ui.add = { query: "x", errors: [], manualKind: "show", results: [
    { kind: "show", id: 9, key: "tvmaze:9", tvdb: 42, title: "Silo" },
    { kind: "show", id: 8, key: "tvmaze:8", tvdb: null, title: "Sans TVDB" },
    { kind: "movie", id: 27205, key: "tmdb:27205", title: "Inception" }] };
  assert.ok(!String(ajouter.render(app, [])).includes("Voir la"), "sans clé TMDB : pas de lien");
  app.tmdb = { configured: true, findTvByTvdb: async (tvdb) => (tvdb === 42 ? 125988 : null) };
  const out = String(ajouter.render(app, []));
  assert.ok(out.includes('href="#/apercu/film/27205/ajouter"'));
  assert.equal(out.match(/data-action="add-preview"/g)?.length, 1);   // série sans TVDB : rien
  let target = null;
  app.navigate = (hash) => { target = hash; };
  assert.equal(await ajouter.actions["add-preview"](app, { dataset: { tvdb: "42", title: "Silo" } }), false);
  assert.equal(target, "#/apercu/serie/125988/ajouter");
  await assert.rejects(ajouter.actions["add-preview"](app, { dataset: { tvdb: "7", title: "Absente" } }),
    /introuvable sur TMDB/);

  app.tmdb = { configured: true, details: async () => ({ id: 27205, title: "Inception" }),
    recommendations: async () => [{ id: 9, title: "Autre" }], watchProviders: async () => ({}) };
  const preview = String(await apercu.render(app, ["film", "27205", "ajouter"]));
  assert.ok(preview.includes("Retour à la recherche") && !preview.includes("Pas intéressé"));
  assert.ok(preview.includes("#/apercu/film/9/ajouter"));
  assert.ok(String(await apercu.render(app, ["film", "27205"])).includes("Pas intéressé"));
});

test("accueil : blocs remplis, « Vu » puis « Annuler », suggestions du dernier calcul", async () => {
  const app = fakeApp();
  app.announce = () => {};
  let out = String(await accueil.render(app, []));
  for (const text of ["Vus ces 7 jours", "affilée", "À suivre", "Prochaines sorties",
    "Derniers visionnages", "Films à voir", "ajoute ta clé TMDB"]) {
    assert.ok(out.includes(text), text);
  }
  assert.ok(!out.includes("S01E02"), "épisode pas encore sorti : pas dans « À suivre »");
  app.store.put("episodes", { ...app.store.get("episodes", "ep:12"), airdate: "2020-02-01" });
  out = String(await accueil.render(app, []));
  assert.match(out, /S01E02/);
  accueil.actions["home-seen"](app, { dataset: { kind: EPISODE, ref: "ep:12", label: "Le Royaume S01E02" } });
  out = String(await accueil.render(app, []));
  assert.ok(out.includes("marqué vu") && !out.includes('data-ref="ep:12"'));
  accueil.actions["home-undo"](app);
  assert.match(String(await accueil.render(app, [])), /data-ref="ep:12"/);

  app.tmdb = { configured: true };
  assert.match(String(await accueil.render(app, [])), /Calcule-les sur la page/);
  app.store.setSetting("suggestions_result", { computedAt: "2026-10-01T00:00:00Z", errors: [], items: [
    { kind: MOVIE, tmdbId: 550, title: "Fight Club", match: 90, posterUrl: null },   // déjà vu
    { kind: SHOW, tmdbId: 9, title: EVIL, year: 2020, match: 72, posterUrl: "javascript:alert(1)",
      streaming: ["Netflix"] }] });
  out = String(await accueil.render(app, []));
  assert.ok(out.includes("72 % pour toi") && out.includes("#/apercu/serie/9"));
  assert.ok(!out.includes("Fight Club (") && !out.includes("90 % pour toi"), "déjà en base : masqué");
  assert.ok(!out.includes(EVIL) && !out.includes("javascript:"));
});

test("« Mon avis » : personnages proposés (liste et photos), rien d'injecté", async () => {
  const app = fakeApp();
  app.tvmaze = { getCast: async () => [
    { person: { name: EVIL, image: { medium: "https://static.tvmaze.com/p.jpg" } },
      character: { name: "Walter White", image: { medium: "javascript:alert(1)" } } },
    { person: { name: "Aaron Paul", image: { medium: "https://static.tvmaze.com/p2.jpg" } },
      character: { name: EVIL } }] };
  const out = String(await serie.render(app, ["tvmaze:1"]));
  assert.ok(out.includes('list="cast-tvmaze:1"') && out.includes('value="Walter White"'));
  assert.match(out, />Walter White \(&lt;img/);   // « Personnage (Acteur) », échappé
  assert.ok(out.includes("Revoir les personnages") && out.includes('data-action="pick-character"'));
  assert.ok(out.includes('src="https://static.tvmaze.com/p2.jpg"'));
  assert.equal(out.match(/class="cast-pick"/g)?.length, 1, "photo dangereuse : pas affichée");
  assert.ok(!out.includes(EVIL) && !out.includes("javascript:"));
  const input = { value: "", focus() {} };
  const form = { querySelector: () => input };
  let said = "";
  app.announce = (text) => { said = text; };
  const result = shared.actions["pick-character"](app, { closest: () => form,
    dataset: { name: "Walter White" } });
  assert.equal(result, false);   // pas de nouveau rendu : le reste du formulaire est gardé
  assert.equal(input.value, "Walter White");
  assert.match(said, /Walter White/);
  // Une panne : liste incomplète signalée, nouvel essai au prochain affichage.
  const app2 = fakeApp();
  app2.tvmaze = { getCast: async () => { throw new Error("injoignable"); } };
  const warn = console.warn;
  console.warn = () => {};
  try {
    assert.match(String(await serie.render(app2, ["tvmaze:1"])), /TVMaze n'a pas répondu/);
  } finally {
    console.warn = warn;
  }
  assert.ok(!app2.ui.cast.size);
});
