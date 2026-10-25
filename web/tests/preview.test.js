// Tests de l'aperçu d'un titre TMDB (page Aperçu, depuis les suggestions), repris de
// tests/test_preview.py. Aucun appel réseau.

import assert from "node:assert/strict";
import { test } from "node:test";

import { removeFromLibrary } from "../js/core/library.js";
import { MOVIE, SHOW } from "../js/core/model.js";
import { libraryKey, load, pickTrailer, similar } from "../js/core/preview.js";
import { addMovie, makeStore } from "./helpers.js";

const MOVIE_DATA = {
  id: 27205, title: "Inception", original_title: "Inception", release_date: "2010-07-15",
  tagline: "Votre esprit abrite la scène du crime.", overview: "<p>Dom Cobb…</p>",
  genres: [{ id: 28, name: "Action" }], poster_path: "/p.jpg", backdrop_path: "/b.jpg",
  vote_average: 8.4, vote_count: 38000, status: "Released", runtime: 148,
  credits: { cast: [{ name: "Leonardo DiCaprio", character: "Cobb", profile_path: "/l.jpg" },
    { name: "Inconnu", character: "", profile_path: null }],
  crew: [{ name: "Christopher Nolan", job: "Director" },
    { name: "Hans Zimmer", job: "Original Music Composer" }] },
  videos: { results: [
    { site: "YouTube", type: "Clip", key: "clip", iso_639_1: "fr", official: true },
    { site: "YouTube", type: "Trailer", key: "en1", iso_639_1: "en", official: true },
    { site: "YouTube", type: "Trailer", key: "fr1", iso_639_1: "fr", official: false },
    { site: "Vimeo", type: "Trailer", key: "v", iso_639_1: "fr", official: true }] },
};

const SHOW_DATA = {
  id: 1403, name: "Marvel : Les Agents du S.H.I.E.L.D.",
  original_name: "Marvel's Agents of S.H.I.E.L.D.", first_air_date: "2013-09-24",
  episode_run_time: [43], number_of_seasons: 7, number_of_episodes: 136, status: "Ended",
  networks: [{ name: "ABC" }], created_by: [{ name: "Joss Whedon" }],
  credits: { cast: [] }, videos: { results: [] },
};

const tmdb = {
  details: async (media, id) => ({ "movie:27205": MOVIE_DATA, "tv:1403": SHOW_DATA })[`${media}:${id}`]
    ?? null,
  recommendations: async () => [
    { id: 9, title: "Interstellar", release_date: "2014-11-05", poster_path: "/i.jpg" },
    { id: 10, name: "Sans date" }],
};

test("aperçu d'un film", async () => {
  const item = await load(tmdb, MOVIE, 27205);
  assert.deepEqual([item.title, item.year, item.released], ["Inception", 2010, "2010-07-15"]);
  assert.deepEqual([item.overview, item.genres], ["Dom Cobb…", ["Action"]]);
  assert.deepEqual([item.makers, item.runtime, item.status], [["Christopher Nolan"], 148, "Sorti"]);
  assert.equal(item.cast[0].name, "Leonardo DiCaprio");
  assert.ok(item.cast[0].photoUrl.endsWith("/l.jpg"));
  assert.deepEqual([item.cast[1].role, item.cast[1].photoUrl], [null, null]);
  assert.equal(item.trailerUrl, "https://www.youtube.com/watch?v=fr1");   // bande-annonce FR d'abord
  assert.ok(item.backdropUrl.endsWith("/b.jpg"));
});

test("aperçu d'une série, titre inconnu", async () => {
  const item = await load(tmdb, SHOW, 1403);
  assert.equal(item.originalTitle, "Marvel's Agents of S.H.I.E.L.D.");
  assert.deepEqual([item.seasons, item.episodes, item.runtime], [7, 136, 43]);
  assert.deepEqual([item.networks, item.makers], [["ABC"], ["Joss Whedon"]]);
  assert.deepEqual([item.status, item.trailerUrl, item.cast], ["Terminée", null, []]);
  assert.equal(await load(tmdb, MOVIE, 1), null);
});

test("bande-annonce : teaser à défaut, rien sans vidéo", () => {
  assert.ok(pickTrailer([{ site: "YouTube", type: "Teaser", key: "t", iso_639_1: "en" }]).endsWith("v=t"));
  assert.equal(pickTrailer([]), null);
});

test("titres similaires ; panne TMDB = liste vide", async () => {
  const items = await similar(tmdb, MOVIE, 27205);
  assert.deepEqual(items[0], { id: 9, title: "Interstellar", year: 2014,
    posterUrl: "https://image.tmdb.org/t/p/w342/i.jpg" });
  assert.equal(items[1].year, null);
  const down = { recommendations: async () => { throw new Error("TMDB indisponible (simulé)"); } };
  assert.deepEqual(await similar(down, MOVIE, 27205), []);
});

test("titre déjà dans la bibliothèque", () => {
  const store = makeStore();
  const key = addMovie(store, "Inception", { id: 27205 });
  assert.equal(libraryKey(store, MOVIE, 27205), key);
  assert.equal(libraryKey(store, MOVIE, 1), null);
  removeFromLibrary(store, MOVIE, key);
  assert.equal(libraryKey(store, MOVIE, 27205), null);                   // en base mais retiré
});
