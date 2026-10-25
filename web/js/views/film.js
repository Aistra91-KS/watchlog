// Fiche d'un film, comme la page Python (film.py) : affiche et infos, plateformes, visionnage
// (date modifiable, revisionnages), statut et favori, avis.

import { upsertMovieFromTmdb } from "../core/catalog.js";
import { fmtDate, fmtRuntime, localDayToUtc, parisDay, today } from "../core/dates.js";
import {
  addRewatch, getLibraryItem, getWatch, markWatched, removeRewatch, setWatchDate, unmarkWatched,
} from "../core/library.js";
import { MOVIE } from "../core/model.js";
import { html, poster, raw } from "./dom.js";
import { providersBlock } from "./providers.js";
import { favoriteCheck, ratingForm, removeButton, statusSelect } from "./shared.js";
import { alert } from "./ui.js";

const MIN_DATE = "1900-01-01";

/** Visionnage : date du premier (modifiable), revisionnage daté, compteur. */
function watchBlock(store, key, day) {
  const w = getWatch(store, MOVIE, key);
  if (!w) {
    return html`<div class="watch-row" data-watch-row>
      <label class="field"><span>Date de visionnage</span><input type="date" name="day" value="${day}"
        min="${MIN_DATE}" max="${day}" aria-describedby="film-day-help"></label>
      <label class="check"><input type="checkbox" name="unknown"> Date inconnue</label>
      <button type="button" class="btn btn-primary" data-action="film-watch" data-ref="${key}">Vu</button>
      <p class="caption sr-only" id="film-day-help">Pour un film vu il y a longtemps, choisis la date.</p>
    </div>`;
  }
  const first = w.watched_at ? parisDay(w.watched_at) : "";
  const plays = w.plays || 1;
  const last = w.last_watched_at ? parisDay(w.last_watched_at) : "";
  return html`<div class="watch-rows">
    <div class="watch-row">
      <label class="field"><span>Vu le</span><input type="date" value="${first}" min="${MIN_DATE}"
        max="${day}" data-change="watch-date" data-kind="${MOVIE}" data-ref="${key}"
        title="Date du premier visionnage (vide : date inconnue)."></label>
      <button type="button" class="btn" data-action="film-forget-date" data-ref="${key}"
        ${w.watched_at ? "" : raw("disabled")}
        title="Vu, mais tu ne sais plus quand : il ne compte dans aucun mois des statistiques.">Date
        inconnue</button>
      <button type="button" class="btn" data-action="unwatch" data-kind="${MOVIE}" data-ref="${key}">Marquer
        non vu</button>
    </div>
    ${w.watched_at ? "" : html`<p class="caption">Date de visionnage inconnue : choisis une date pour la
      renseigner.</p>`}
    <div class="watch-row two" data-watch-row>
      <label class="field"><span>Revu le</span><input type="date" name="day" value="${day}"
        min="${MIN_DATE}" max="${day}" title="Date du revisionnage (aujourd'hui par défaut)."></label>
      <button type="button" class="btn" data-action="film-rewatch" data-ref="${key}">Revu</button>
    </div>
    ${plays > 1 ? html`<div class="watch-row two"><p class="caption">Vu ${plays} fois${last
      ? ` · dernier visionnage le ${fmtDate(last)}` : ""}</p>
      <button type="button" class="btn" data-action="unrewatch" data-kind="${MOVIE}"
        data-ref="${key}">Retirer un revisionnage</button></div>` : html`<p class="caption">Vu 1 fois</p>`}
  </div>`;
}

export async function render(app, [key]) {
  const movie = app.store.get("movies", key);
  if (!movie) {
    return html`<h1>Film introuvable</h1>${alert("info", html`Aucun film sélectionné.
      <a href="#/bibliotheque">Aller à la bibliothèque</a>`)}`;
  }
  const day = today();
  const lines = [movie.release_date_fr ? `Sortie en France : ${fmtDate(movie.release_date_fr)}` : "",
    movie.release_date_original && movie.release_date_original !== movie.release_date_fr
      ? `Sortie d'origine : ${fmtDate(movie.release_date_original)}` : ""].filter(Boolean);
  if (!lines.length && movie.year) lines.push(`Année : ${movie.year}`);
  if (movie.runtime) lines.push(`Durée : ${fmtRuntime(movie.runtime)}`);
  if (movie.is_manual) lines.push("Saisie manuelle");
  const inLibrary = getLibraryItem(app.store, MOVIE, key);
  return html`
  <article class="sheet is-movie">
    <div class="sheet-head">
      <div>${poster(movie.poster_url, movie.title, "lg")}</div>
      <div>
        <h1>${movie.title}</h1>
        ${movie.original_title && movie.original_title !== movie.title
          ? html`<p class="caption">Titre original : ${movie.original_title}</p>` : ""}
        ${lines.length ? html`<p class="meta">${lines.join(" · ")}</p>` : ""}
        ${movie.overview ? html`<p class="summary-text">${movie.overview}</p>` : ""}
        ${!movie.is_manual && !movie.tmdb_id ? alert("info", "Ce film n'a pas d'identifiant TMDB : seules "
          + "les informations de l'import sont connues (pas de résumé ni d'affiche).") : ""}
        ${movie.tmdb_id && !movie.is_manual ? await providersBlock(app, "movie", movie.tmdb_id) : ""}
        ${watchBlock(app.store, key, day)}
      </div>
    </div>
    ${inLibrary ? "" : alert("info", "Ce film n'est pas dans ta bibliothèque : choisis un statut pour l'y ajouter.")}
    <div class="sheet-controls">
      ${statusSelect(app.store, MOVIE, key, "movie-status")}
      <div class="controls-side">
        ${inLibrary ? favoriteCheck(app.store, MOVIE, key) : ""}
        ${movie.tmdb_id ? html`<button type="button" class="btn" data-action="movie-refresh"
          data-ref="${key}">Mettre à jour</button>` : ""}
      </div>
    </div>
    ${ratingForm(app.store, MOVIE, key)}
    ${inLibrary ? removeButton(MOVIE, key) : ""}
  </article>`;
}

const rowDay = (el) => el.closest("[data-watch-row]")?.querySelector('input[name="day"]')?.value || null;

export const actions = {
  "film-watch"(app, el) {
    const row = el.closest("[data-watch-row]");
    const unknown = row.querySelector('input[name="unknown"]').checked;
    const day = rowDay(el);
    markWatched(app.store, MOVIE, el.dataset.ref, !unknown && day && day !== today()
      ? localDayToUtc(day) : null);
    if (unknown) setWatchDate(app.store, MOVIE, el.dataset.ref, null);
    app.toast("Film marqué vu.");
  },
  "film-forget-date"(app, el) {
    setWatchDate(app.store, MOVIE, el.dataset.ref, null);
    app.toast("Date de visionnage retirée : vu, date inconnue.");
  },
  "film-rewatch"(app, el) {
    const w = addRewatch(app.store, MOVIE, el.dataset.ref, rowDay(el));
    app.toast(`Revisionnage ajouté : vu ${w.plays} fois.`);
  },
  unwatch(app, el) {
    unmarkWatched(app.store, MOVIE, el.dataset.ref);
    app.toast("Film marqué non vu.");
  },
  unrewatch(app, el) {
    const w = removeRewatch(app.store, MOVIE, el.dataset.ref);
    app.toast(w && w.plays > 1 ? `Revisionnage retiré : vu ${w.plays} fois.`
      : "Revisionnage retiré : vu une fois.");
  },
  async "movie-refresh"(app, el) {
    const movie = app.store.get("movies", el.dataset.ref);
    const data = await app.tmdb.getMovie(movie.tmdb_id);
    if (!data) throw new Error("TMDB non connecté ou film introuvable.");
    upsertMovieFromTmdb(app.store, data);
    app.toast("Film mis à jour.");
  },
};
