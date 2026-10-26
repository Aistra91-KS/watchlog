// Page « Ajouter » : recherche d'une série (TVMaze) ou d'un film (TMDB), ou saisie manuelle.

import { addManualMovie, addManualShow, addMovie, addShow } from "../core/adding.js";
import { posterUrl } from "../core/catalog.js";
import { MOVIE, SHOW, STATUSES, STATUS_LABELS, TO_WATCH, refKey } from "../core/model.js";
import { href, html, movieHref, options, poster, showHref } from "./dom.js";

const STATUS_PAIRS = STATUSES.map((s) => [s, STATUS_LABELS[s]]);

/** Lien vers l'aperçu d'un résultat (#/apercu/serie/<id TVMaze> ou #/apercu/film/<id TMDB>). */
export const previewHref = (kind, id) => href("apercu", kind === SHOW ? "serie" : "film", id);

/** Formulaire d'ajout d'une série TVMaze (statut, « j'en suis à »), ou lien vers sa fiche
 *  si elle est déjà dans la bibliothèque. Aussi utilisé par la page Aperçu. */
export function showAddForm(app, tvmazeId, name) {
  const key = `tvmaze:${tvmazeId}`;
  if (app.store.get("library", refKey(SHOW, key))) {
    return html`<a class="btn btn-ghost" href="${showHref(key)}">Dans ma bibliothèque</a>`;
  }
  return html`<form class="add-form" data-submit="add-show" data-id="${tvmazeId}">
    <label class="field"><span>Statut</span><select name="status"
      aria-label="${`Statut pour ${name}`}">${options(STATUS_PAIRS, TO_WATCH)}</select></label>
    <details class="until"><summary>J'en suis à…</summary>
      <label class="field"><span>Saison</span><input name="season" type="number" min="1"
        inputmode="numeric" aria-label="${`Saison en cours de ${name}`}"></label>
      <label class="field"><span>Épisode</span><input name="episode" type="number" min="1"
        inputmode="numeric" aria-label="${`Dernier épisode vu de ${name}`}"></label>
    </details>
    <button class="btn" aria-label="${`Ajouter la série ${name}`}">Ajouter</button>
  </form>`;
}

/** Formulaire d'ajout d'un film TMDB, ou lien vers sa fiche (comme showAddForm). */
export function movieAddForm(app, tmdbId, title) {
  const key = `tmdb:${tmdbId}`;
  if (app.store.get("library", refKey(MOVIE, key))) {
    return html`<a class="btn btn-ghost" href="${movieHref(key)}">Dans ma bibliothèque</a>`;
  }
  return html`<form class="add-form" data-submit="add-movie" data-id="${tmdbId}">
    <label class="field"><span>Statut</span><select name="status"
      aria-label="${`Statut pour ${title}`}">${options(STATUS_PAIRS, TO_WATCH)}</select></label>
    <button class="btn" aria-label="${`Ajouter le film ${title}`}">Ajouter</button>
  </form>`;
}

function showResult(app, { show }) {
  const year = show.premiered?.slice(0, 4);
  const channel = (show.network || show.webChannel || {}).name;
  return html`<li class="row is-show">
    ${poster(show.image?.medium, show.name, "sm")}
    <div class="row-main">
      <a class="row-title" href="${previewHref(SHOW, show.id)}">${show.name}<span
        class="sr-only"> : voir la fiche</span></a>
      <p class="row-meta">${[year, channel, show.status === "Ended" ? "Terminée" : null]
        .filter(Boolean).join(" · ")}</p>
    </div>
    ${showAddForm(app, show.id, show.name)}
  </li>`;
}

function movieResult(app, movie) {
  return html`<li class="row is-movie">
    ${poster(posterUrl(movie.poster_path), movie.title, "sm")}
    <div class="row-main">
      <a class="row-title" href="${previewHref(MOVIE, movie.id)}">${movie.title}<span
        class="sr-only"> : voir la fiche</span></a>
      <p class="row-meta">${[movie.release_date?.slice(0, 4), movie.original_title !== movie.title
        ? movie.original_title : null].filter(Boolean).join(" · ")}</p>
    </div>
    ${movieAddForm(app, movie.id, movie.title)}
  </li>`;
}

export function render(app) {
  const ui = app.ui.add || (app.ui.add = { kind: SHOW, query: "", results: null, error: null });
  const tmdbMissing = ui.kind === MOVIE && !app.tmdb.configured;
  return html`
  <h1>Ajouter</h1>
  <form class="search-form" role="search" data-submit="add-search">
    <fieldset class="segmented"><legend>Type</legend>
      <label><input type="radio" name="kind" value="${SHOW}" ${ui.kind === SHOW ? html`checked` : ""}> Série</label>
      <label><input type="radio" name="kind" value="${MOVIE}" ${ui.kind === MOVIE ? html`checked` : ""}> Film</label>
    </fieldset>
    <label class="field field-wide"><span>Titre</span>
      <input type="search" name="query" id="add-query" value="${ui.query}" required autocomplete="off"></label>
    <button class="btn">Rechercher</button>
  </form>
  ${tmdbMissing ? html`<p class="warning">La recherche de films passe par TMDB :
    <a href="#/parametres">ajoute ta clé TMDB dans Paramètres</a>, ou saisis le film à la main
    plus bas.</p>` : ""}
  ${ui.error ? html`<p class="error" role="alert">${ui.error}</p>` : ""}
  ${ui.results ? html`<section><h2 data-announce>${ui.results.length} résultat${ui.results.length > 1 ? "s" : ""}</h2>
    ${ui.results.length ? html`<ul class="rows" role="list">${ui.results.map((r) => (ui.resultKind === SHOW
      ? showResult(app, r) : movieResult(app, r)))}</ul>`
      : html`<p class="muted">Aucun résultat. Essaie le titre original, ou saisis-le à la main.</p>`}
  </section>` : ""}
  <section><h2>Saisie manuelle</h2>
    <p class="muted">Pour un titre introuvable : il n'aura pas de mise à jour automatique.</p>
    <form class="manual" data-submit="add-manual">
      <fieldset class="segmented"><legend>Type</legend>
        <label><input type="radio" name="kind" value="${SHOW}" checked> Série</label>
        <label><input type="radio" name="kind" value="${MOVIE}"> Film</label>
      </fieldset>
      <label class="field field-wide"><span>Titre</span><input name="title" required></label>
      <label class="field"><span>Épisodes par saison (série)</span>
        <input name="seasons" placeholder="ex. 10, 8, 8" inputmode="numeric"></label>
      <label class="field"><span>Année (film)</span><input name="year" type="number" min="1880" max="2100"></label>
      <label class="field"><span>Statut</span><select name="status">${options(STATUS_PAIRS, TO_WATCH)}</select></label>
      <button class="btn">Ajouter</button>
    </form>
  </section>
  <p class="credit">Recherche de séries : TVMaze (CC BY-SA). Films : TMDB.</p>`;
}

export const actions = {
  async "add-search"(app, form) {
    const data = new FormData(form);
    const ui = app.ui.add;
    Object.assign(ui, { kind: data.get("kind"), query: (data.get("query") || "").trim(), error: null });
    if (!ui.query) return;
    try {
      if (ui.kind === SHOW) {
        ui.results = await app.tvmaze.search(ui.query);
      } else {
        if (!app.tmdb.configured) { ui.results = null; return; }
        ui.results = await app.tmdb.searchMovies(ui.query);
      }
      ui.resultKind = ui.kind;
    } catch (error) {
      console.warn("Recherche", error);
      ui.results = null;
      ui.error = `Recherche impossible : ${error.message}`;
    }
  },
  async "add-show"(app, form) {
    const data = new FormData(form);
    const season = Number(data.get("season"));
    const episode = Number(data.get("episode"));
    form.querySelector("button").disabled = true;
    const { show, warnings } = await addShow(app.store, app.tvmaze, app.tmdb, Number(form.dataset.id), {
      status: data.get("status"),
      untilEpisode: season && episode ? { season, number: episode } : null,
    });
    app.toast(`« ${show.title} » ajoutée.${warnings.length ? " Textes français indisponibles pour l'instant." : ""}`);
  },
  async "add-movie"(app, form) {
    form.querySelector("button").disabled = true;
    const movie = await addMovie(app.store, app.tmdb, Number(form.dataset.id),
      { status: new FormData(form).get("status") });
    app.toast(`« ${movie.title} » ajouté.`);
  },
  "add-manual"(app, form) {
    const data = new FormData(form);
    const status = data.get("status");
    if (data.get("kind") === SHOW) {
      const seasons = String(data.get("seasons") || "").split(/[,;\s]+/).map(Number)
        .filter((n) => Number.isInteger(n) && n > 0);
      const show = addManualShow(app.store, { title: data.get("title"), seasons, status });
      app.navigate(showHref(show.key));
    } else {
      const movie = addManualMovie(app.store, { title: data.get("title"),
        year: Number(data.get("year")) || null, status });
      app.navigate(movieHref(movie.key));
    }
    app.toast("Titre ajouté.");
  },
};
