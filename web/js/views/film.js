// Fiche d'un film : infos, statut, vu / revu, avis, plateformes (si TMDB est connecté).

import { upsertMovieFromTmdb } from "../core/catalog.js";
import { fmtDate, fmtRuntime } from "../core/dates.js";
import { MOVIE, splitGenres } from "../core/model.js";
import { html, poster } from "./dom.js";
import { providersBlock } from "./providers.js";
import {
  favoriteButton, ratingForm, removeButton, statusSelect, watchButton, watchDetails,
} from "./shared.js";

export async function render(app, [key]) {
  const movie = app.store.get("movies", key);
  if (!movie) {
    return html`<h1>Film introuvable</h1><p><a href="#/bibliotheque">Retour à la bibliothèque</a></p>`;
  }
  const release = movie.release_date_fr || movie.release_date_original;
  const genres = splitGenres(movie.genres);
  return html`
  <article class="sheet is-movie">
    <header class="sheet-head">
      ${poster(movie.poster_url, movie.title, "lg")}
      <div>
        <h1>${movie.title}</h1>
        ${movie.original_title && movie.original_title !== movie.title
          ? html`<p class="muted">${movie.original_title}</p>` : ""}
        <p class="meta">${[release ? `Sortie ${fmtDate(release)}` : movie.year, fmtRuntime(movie.runtime)]
          .filter(Boolean).join(" · ")}</p>
        ${genres.length ? html`<p class="tags">${genres.map((g) => html`<span class="tag">${g}</span>`)}</p>` : ""}
        <div class="sheet-controls">
          ${watchButton(app.store, MOVIE, key, movie.title)}
          ${statusSelect(app.store, MOVIE, key, "movie-status")}
          ${favoriteButton(app.store, MOVIE, key)}
          ${movie.tmdb_id ? html`<button type="button" class="btn btn-ghost" data-action="movie-refresh"
            data-ref="${key}">Mettre à jour</button>` : ""}
        </div>
        ${watchDetails(app.store, MOVIE, key)}
      </div>
    </header>
    ${movie.overview ? html`<p class="summary-text">${movie.overview}</p>` : ""}
    ${movie.tmdb_id ? await providersBlock(app, "movie", movie.tmdb_id) : ""}
    <section><h2>Mon avis</h2>${ratingForm(app.store, MOVIE, key)}</section>
    ${app.store.get("library", `${MOVIE}:${key}`) ? removeButton(MOVIE, key) : ""}
  </article>`;
}

export const actions = {
  async "movie-refresh"(app, el) {
    const movie = app.store.get("movies", el.dataset.ref);
    const data = await app.tmdb.getMovie(movie.tmdb_id);
    if (!data) throw new Error("TMDB non connecté ou film introuvable.");
    upsertMovieFromTmdb(app.store, data);
    app.toast("Film mis à jour.");
  },
};
