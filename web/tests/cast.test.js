// Tests des personnages proposés pour « Personnage préféré » (core/cast.js, port de
// cast.py). Mêmes cas que tests/test_cast.py.

import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "../js/api/http.js";
import { cleanRole, movieCast, showCast } from "../js/core/cast.js";

const TVMAZE_CAST = [
  { person: { name: "Bryan Cranston", image: { medium: "https://static.tvmaze.com/p1.jpg" } },
    character: { name: "Walter White", image: { medium: "https://static.tvmaze.com/c1.jpg" } } },
  { person: { name: "Aaron Paul", image: { medium: "https://static.tvmaze.com/p2.jpg" } },
    character: { name: "Jesse Pinkman", image: null } },
  { person: { name: "Présentateur" }, character: { name: "Lui-même" }, self: true },
];
const AGGREGATE = { cast: [
  { name: "Bryan Cranston", profile_path: "/bc.jpg", order: 0,
    roles: [{ character: "Walter White", episode_count: 62 }] },
  { name: "Bob Odenkirk", profile_path: "/bo.jpg", order: 5,
    roles: [{ character: "Saul Goodman", episode_count: 43 }] },
  { name: "Dean Norris", profile_path: null, order: 3,
    roles: [{ character: "Hank Schrader", episode_count: 62 }, { character: "Self", episode_count: 1 }] },
] };
const MOVIE_DETAILS = { id: 550, credits: { cast: [
  { name: "Brad Pitt", character: "Tyler Durden", profile_path: "/bp.jpg", order: 1 },
  { name: "Edward Norton", character: "Narrator", profile_path: "/en.jpg", order: 0 },
  { name: "Meat Loaf", character: "Robert 'Bob' Paulson (uncredited)", order: 2 },
  { name: "Inconnu", character: "", order: 3 },
] } };

const failing = async () => { throw new ApiError("injoignable"); };
const tvmaze = (fail = false) => ({ getCast: fail ? failing : async () => TVMAZE_CAST });
const tmdb = (fail = false) => ({ configured: true, tvAggregateCredits: fail ? failing : async () => AGGREGATE,
  details: fail ? failing : async () => MOVIE_DETAILS });
const SHOW_RECORD = { key: "tvmaze:169", title: "Breaking Bad", tvmaze_id: 169, tmdb_id: 1396 };
const labels = (found) => found.characters.map((c) => (c.actor ? `${c.name} (${c.actor})` : c.name));

test("personnages : noms nettoyés", () => {
  assert.equal(cleanRole("  Rick Sanchez (voice) "), "Rick Sanchez");
  assert.equal(cleanRole("Bob (uncredited) (voice)"), "Bob");
  assert.equal(cleanRole("Himself"), null);
  assert.equal(cleanRole(""), null);
  assert.equal(cleanRole(null), null);
});

test("personnages d'une série : TVMaze puis rôles récurrents TMDB", async (t) => {
  t.mock.method(console, "warn", () => {});
  const found = await showCast(tvmaze(), tmdb(), SHOW_RECORD);
  assert.deepEqual(labels(found), ["Walter White (Bryan Cranston)", "Jesse Pinkman (Aaron Paul)",
    "Hank Schrader (Dean Norris)", "Saul Goodman (Bob Odenkirk)"]);
  assert.equal(found.characters[0].photoUrl, "https://static.tvmaze.com/c1.jpg");
  assert.equal(found.characters[1].photoUrl, "https://static.tvmaze.com/p2.jpg");
  assert.equal(found.characters[3].photoUrl, "https://image.tmdb.org/t/p/w185/bo.jpg");
  assert.deepEqual(found.errors, []);
  const partial = await showCast(tvmaze(true), tmdb(), SHOW_RECORD);
  assert.deepEqual(partial.errors, ["TVMaze"]);
  assert.equal(partial.characters[0].name, "Walter White");
  assert.deepEqual((await showCast(tvmaze(), tmdb(true), SHOW_RECORD)).errors, ["TMDB"]);
  assert.equal(console.warn.mock.callCount(), 2);
  const noKey = await showCast(tvmaze(), { configured: false }, SHOW_RECORD);
  assert.equal(noKey.characters.length, 2);
});

test("personnages d'un film : ordre du générique", async (t) => {
  t.mock.method(console, "warn", () => {});
  const found = await movieCast(tmdb(), { title: "Fight Club", tmdb_id: 550 });
  assert.deepEqual(labels(found), ["Narrator (Edward Norton)", "Tyler Durden (Brad Pitt)",
    "Robert 'Bob' Paulson (Meat Loaf)"]);
  assert.deepEqual((await movieCast(tmdb(true), { title: "Fight Club", tmdb_id: 550 })).errors, ["TMDB"]);
  assert.deepEqual(await movieCast(tmdb(), { title: "Manuel" }), { characters: [], errors: [] });
});
