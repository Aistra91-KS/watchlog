// Tests de l'aperçu (port de tests/test_preview.py, plus la fiche série TVMaze) et de sa page.

import assert from "node:assert/strict";
import { test } from "node:test";

import { MOVIE, SHOW } from "../js/core/model.js";
import { fromTmdb, fromTvmaze, mergeTmdb, pickTrailer } from "../js/core/preview.js";
import * as ajouter from "../js/views/ajouter.js";
import * as apercu from "../js/views/apercu.js";
import { makeStore } from "./helpers.js";

const EVIL = `<img src=x onerror="alert(1)">`;

const MOVIE_DATA = {
  id: 27205, title: "Inception", original_title: "Inception", release_date: "2010-07-15",
  tagline: "Votre esprit abrite la scène du crime.", overview: "<p>Dom Cobb…</p>",
  genres: [{ id: 28, name: "Action" }], poster_path: "/p.jpg", vote_average: 8.4,
  vote_count: 38000, status: "Released", runtime: 148,
  credits: {
    cast: [{ name: "Leonardo DiCaprio", character: "Cobb", profile_path: "/l.jpg" },
      { name: EVIL, character: "", profile_path: null }],
    crew: [{ name: "Christopher Nolan", job: "Director" },
      { name: "Hans Zimmer", job: "Original Music Composer" }],
  },
  videos: { results: [
    { site: "YouTube", type: "Clip", key: "clip", iso_639_1: "fr", official: true },
    { site: "YouTube", type: "Trailer", key: "en1", iso_639_1: "en", official: true },
    { site: "YouTube", type: "Trailer", key: "fr1", iso_639_1: "fr", official: false },
    { site: "Vimeo", type: "Trailer", key: "v", iso_639_1: "fr", official: true }] },
};

const TV_DATA = {
  id: 1403, name: "Marvel : Les Agents du S.H.I.E.L.D.",
  original_name: "Marvel's Agents of S.H.I.E.L.D.", first_air_date: "2013-09-24",
  episode_run_time: [43], number_of_seasons: 7, number_of_episodes: 136, status: "Ended",
  networks: [{ name: "ABC" }], created_by: [{ name: "Joss Whedon" }], overview: "Phil Coulson…",
  vote_average: 7.5, vote_count: 3000, credits: { cast: [] }, videos: { results: [] },
};

const TVMAZE_DATA = {
  id: 31, name: "Marvel's Agents of S.H.I.E.L.D.", premiered: "2013-09-24", status: "Ended",
  genres: ["Action", "Science-Fiction"], summary: `<p>The missions ${EVIL}</p>`, averageRuntime: 60,
  rating: { average: 8.1 }, network: { name: "ABC" }, externals: { thetvdb: 263365 },
  image: { medium: "https://static.tvmaze.com/31.jpg" },
  _embedded: { seasons: [{ number: 1 }, { number: 2 }],
    episodes: [{ id: 1 }, { id: 2 }, { id: 3 }] },
};

test("aperçu d'un film TMDB", () => {
  const item = fromTmdb(MOVIE, MOVIE_DATA);
  assert.equal(item.title, "Inception");
  assert.equal(item.year, 2010);
  assert.equal(item.overview, "Dom Cobb…");
  assert.deepEqual(item.makers, ["Christopher Nolan"]);
  assert.equal(item.status, "Sorti");
  assert.equal(item.runtime, 148);
  assert.equal(item.cast[0].photoUrl, "https://image.tmdb.org/t/p/w185/l.jpg");
  assert.equal(item.cast[1].role, null);
  assert.equal(item.trailerUrl, "https://www.youtube.com/watch?v=fr1");
});

test("aperçu d'une série : TVMaze, complété par TMDB", () => {
  const base = fromTvmaze(TVMAZE_DATA);
  assert.equal(base.tvdbId, 263365);
  assert.deepEqual([base.seasons, base.episodes, base.runtime], [2, 3, 60]);
  assert.deepEqual(base.genres, ["Action", "Science-Fiction"]);
  assert.equal(base.status, "Terminée");
  const item = mergeTmdb(base, fromTmdb(SHOW, TV_DATA));
  assert.equal(item.title, "Marvel : Les Agents du S.H.I.E.L.D.");
  assert.equal(item.overview, "Phil Coulson…");
  assert.deepEqual(item.makers, ["Joss Whedon"]);
  assert.equal(item.tmdbId, 1403);
  assert.deepEqual([item.tvmazeId, item.seasons, item.episodes], [31, 2, 3]);  // TVMaze gardé
  assert.equal(item.voteCount, 3000);
});

test("bande-annonce : rien sans vidéo YouTube", () => {
  assert.equal(pickTrailer([]), null);
  assert.equal(pickTrailer(undefined), null);
  assert.equal(pickTrailer([{ site: "YouTube", type: "Teaser", key: "t" }]),
    "https://www.youtube.com/watch?v=t");
});

function fakeApp({ tmdbConfigured = true, tmdbFails = false } = {}) {
  return {
    store: makeStore(), ui: {},
    tvmaze: { getShow: async (id) => (id === 31 ? TVMAZE_DATA : null) },
    tmdb: {
      configured: tmdbConfigured,
      findTvByTvdb: async () => { if (tmdbFails) throw new Error("TMDB en panne"); return 1403; },
      details: async (media, id) => ({ "tv:1403": TV_DATA, "movie:27205": MOVIE_DATA })[`${media}:${id}`] || null,
      watchProviders: async () => ({ FR: { flatrate: [{ provider_name: "Disney Plus" }] } }),
    },
  };
}

test("page Aperçu : série, film, erreurs", async () => {
  const show = String(await apercu.render(fakeApp(), ["serie", "31"]));
  assert.equal(show.match(/<h1[\s>]/g)?.length, 1);
  assert.match(show, /Marvel : Les Agents/);
  assert.match(show, /data-submit="add-show" data-id="31"/);
  assert.match(show, /Disney Plus/);
  assert.match(show, /href="#\/ajouter"/);
  assert.ok(!show.includes(EVIL));

  const offline = String(await apercu.render(fakeApp({ tmdbConfigured: false }), ["serie", "31"]));
  assert.match(offline, /Marvel&#39;s Agents/);
  assert.ok(!offline.includes("Disney Plus"));
  const failing = String(await apercu.render(fakeApp({ tmdbFails: true }), ["serie", "31"]));
  assert.match(failing, /TMDB : TMDB en panne/);   // panne signalée, aperçu TVMaze quand même

  const movie = String(await apercu.render(fakeApp(), ["film", "27205"]));
  assert.match(movie, /Inception/);
  assert.match(movie, /Christopher Nolan/);
  assert.match(movie, /youtube\.com\/watch\?v=fr1/);
  assert.match(movie, /data-submit="add-movie"/);
  assert.ok(!movie.includes(EVIL));

  assert.match(String(await apercu.render(fakeApp({ tmdbConfigured: false }), ["film", "27205"])),
    /clé TMDB/);
  assert.match(String(await apercu.render(fakeApp(), ["serie", "999"])), /introuvable/);
  assert.match(String(await apercu.render(fakeApp(), ["autre", "1"])), /Aucun titre/);
  assert.equal(apercu.actions["add-show"], ajouter.actions["add-show"]);
});

test("page Ajouter : chaque résultat mène à son aperçu", () => {
  const app = fakeApp();
  app.ui.add = { kind: SHOW, query: "shield", results: [{ show: TVMAZE_DATA }], resultKind: SHOW };
  assert.match(String(ajouter.render(app)), /href="#\/apercu\/serie\/31"/);
  app.ui.add = { kind: MOVIE, query: "inception", resultKind: MOVIE,
    results: [{ id: 27205, title: "Inception", original_title: "Inception" }] };
  assert.match(String(ajouter.render(app)), /href="#\/apercu\/film\/27205"/);
});
