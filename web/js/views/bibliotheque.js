// Page « Bibliothèque » : toutes les séries et tous les films suivis, avec leur progression.

import { MOVIE, SHOW, STATUSES, STATUS_LABELS } from "../core/model.js";
import {
  SORT_LABELS, SORT_TITLE, lastWatchedLabel, libraryOverview, progressLabel,
} from "../core/progress.js";
import { html, kindBadge, movieHref, options, poster, raw, showHref } from "./dom.js";
import { loadPrefs, savePrefs } from "./prefs.js";

const PREFS = "library";
const DEFAULTS = { kind: "", status: "", sort: SORT_TITLE, favoriteOnly: false };
const PAGE_SIZE = 90;

function card(entry) {
  const link = entry.kind === SHOW ? showHref(entry.ref) : movieHref(entry.ref);
  return html`<li class="card ${entry.kind === MOVIE ? "is-movie" : "is-show"}">
    <a href="${link}">
      ${poster(entry.posterUrl, entry.title)}
      <span class="card-title">${entry.favorite ? html`<span aria-hidden="true">★ </span><span class="sr-only">Favori :
        </span>` : ""}${entry.title}</span>
    </a>
    <p class="card-meta">${kindBadge(entry.kind)} ${STATUS_LABELS[entry.status]}${entry.year ? ` · ${entry.year}` : ""}</p>
    <p class="card-meta">${progressLabel(entry)}</p>
    <p class="card-meta muted">${lastWatchedLabel(entry)}</p>
  </li>`;
}

export function render(app) {
  const prefs = loadPrefs(PREFS, DEFAULTS);
  const search = app.ui.librarySearch || "";
  const entries = libraryOverview(app.store, { kind: prefs.kind || null,
    status: prefs.status || null, search, favoriteOnly: prefs.favoriteOnly, sort: prefs.sort });
  const limit = app.ui.libraryLimit || PAGE_SIZE;
  const shows = entries.filter((e) => e.kind === SHOW).length;
  return html`
  <h1>Bibliothèque</h1>
  <form class="filters" role="search" aria-label="Filtrer la bibliothèque">
    <label class="field field-wide"><span>Rechercher un titre</span>
      <input type="search" id="library-search" value="${search}" data-input="library-search"
        autocomplete="off"></label>
    <label class="field"><span>Type</span>
      <select data-change="library-pref" name="kind">${options([["", "Tout"], [SHOW, "Séries"],
        [MOVIE, "Films"]], prefs.kind)}</select></label>
    <label class="field"><span>Statut</span>
      <select data-change="library-pref" name="status">${options([["", "Tous"],
        ...STATUSES.map((s) => [s, STATUS_LABELS[s]])], prefs.status)}</select></label>
    <label class="field"><span>Tri</span>
      <select data-change="library-pref" name="sort">${options(Object.entries(SORT_LABELS),
        prefs.sort)}</select></label>
    <label class="check"><input type="checkbox" data-change="library-fav" ${prefs.favoriteOnly
      ? raw("checked") : ""}> Favoris seulement</label>
  </form>
  <p class="summary" data-announce>${shows} série${shows > 1 ? "s" : ""},
    ${entries.length - shows} film${entries.length - shows > 1 ? "s" : ""}</p>
  ${entries.length ? html`<ul class="cards" role="list">${entries.slice(0, limit).map(card)}</ul>`
    : html`<p class="empty">Rien ne correspond. <a href="#/ajouter">Ajouter un titre</a></p>`}
  ${entries.length > limit ? html`<button type="button" class="btn btn-ghost"
    data-action="library-more">Afficher plus (${entries.length - limit} restants)</button>` : ""}`;
}

export const actions = {
  "library-search"(app, el) {
    app.ui.librarySearch = el.value;
    app.ui.libraryLimit = PAGE_SIZE;
  },
  "library-pref"(app, el) {
    savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), [el.name]: el.value });
  },
  "library-fav"(app, el) {
    savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), favoriteOnly: el.checked });
  },
  "library-more"(app) { app.ui.libraryLimit = (app.ui.libraryLimit || PAGE_SIZE) + PAGE_SIZE; },
};
