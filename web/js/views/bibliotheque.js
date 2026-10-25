// Page « Bibliothèque » : grille d'affiches des séries et films, avec leur progression.
// Même présentation que la page Python (bibliotheque.py) : filtres, cartes, « Ouvrir ».

import { MOVIE, SHOW, STATUSES, STATUS_LABELS } from "../core/model.js";
import {
  SORT_LABELS, SORT_PAUSED, SORT_RECENT, SORT_TITLE, lastWatchedLabel, libraryOverview,
  progressLabel,
} from "../core/progress.js";
import { html, movieHref, options, poster, raw, showHref } from "./dom.js";
import { loadPrefs, savePrefs } from "./prefs.js";
import { alert } from "./ui.js";

const PREFS = "library";
const DEFAULTS = { kind: "", statuses: [], sort: SORT_TITLE, favoriteOnly: false };
// Éléments affichés en plus à chaque « Afficher plus »
const CHUNK = 48;
// Tris où la date du dernier visionnage est affichée sur chaque carte
const DATED_SORTS = [SORT_RECENT, SORT_PAUSED];

function card(entry, dated) {
  const link = entry.kind === SHOW ? showHref(entry.ref) : movieHref(entry.ref);
  const details = [entry.kind === SHOW ? "Série" : "Film", entry.year, STATUS_LABELS[entry.status],
    entry.favorite ? "Favori" : null].filter(Boolean);
  const seen = dated ? lastWatchedLabel(entry) : "";
  // Le titre est le lien lu au clavier ; l'affiche et « Ouvrir » (comme l'app Python) sont
  // des raccourcis pour la souris et le doigt, hors de l'ordre de tabulation.
  return html`<li class="card">
    <a href="${link}" tabindex="-1" aria-hidden="true">${poster(entry.posterUrl, entry.title)}</a>
    <a class="card-title" href="${link}">${entry.title}</a>
    <p class="card-meta">${details.join(" · ")}</p>
    <p class="card-meta">${progressLabel(entry)}</p>
    ${seen ? html`<p class="card-meta">${seen}</p>` : ""}
    <a class="btn btn-wide card-open" href="${link}" tabindex="-1" aria-hidden="true">Ouvrir</a>
  </li>`;
}

export function render(app) {
  const prefs = loadPrefs(PREFS, DEFAULTS);
  const statuses = Array.isArray(prefs.statuses) ? prefs.statuses : [];
  const search = app.ui.librarySearch || "";
  const entries = libraryOverview(app.store, { kind: prefs.kind || null,
    status: statuses.length ? statuses : null, search, favoriteOnly: prefs.favoriteOnly,
    sort: prefs.sort });
  const shown = app.ui.libraryLimit || CHUNK;
  const n = entries.length;
  return html`
  <h1>Bibliothèque</h1>
  <form class="cols" style="--cols: 1fr 2fr 2fr 1.6fr" role="search" aria-label="Filtrer la bibliothèque">
    <label class="field"><span>Type</span>
      <select data-change="library-pref" name="kind">${options([["", "Tout"], [SHOW, "Séries"],
        [MOVIE, "Films"]], prefs.kind)}</select></label>
    <fieldset class="chips"><legend>Statut</legend>
      ${STATUSES.map((s) => html`<label class="chip"><input type="checkbox" value="${s}"
        data-change="library-status" ${statuses.includes(s) ? raw("checked") : ""}>
        ${STATUS_LABELS[s]}</label>`)}
    </fieldset>
    <label class="field"><span>Recherche</span>
      <input type="search" id="library-search" value="${search}" data-input="library-search"
        placeholder="Titre ou titre original" autocomplete="off"></label>
    <label class="field"><span>Tri</span>
      <select data-change="library-pref" name="sort">${options(Object.entries(SORT_LABELS),
        prefs.sort)}</select></label>
  </form>
  <p><label class="check"><input type="checkbox" data-change="library-fav" ${prefs.favoriteOnly
    ? raw("checked") : ""}> Favoris seulement</label></p>
  ${prefs.sort === SORT_PAUSED ? html`<p class="caption">Séries « En cours » seulement, celle
    délaissée depuis le plus longtemps en premier : de quoi choisir une série à reprendre.</p>` : ""}
  ${n ? html`
    <p class="caption" data-announce>${n} élément${n > 1 ? "s" : ""}${shown < n
      ? ` · ${shown} affichés` : ""}</p>
    <ul class="cards" role="list">${entries.slice(0, shown).map((e) => card(e,
      DATED_SORTS.includes(prefs.sort)))}</ul>
    ${shown < n ? html`<button type="button" class="btn" data-action="library-more">Afficher
      ${Math.min(CHUNK, n - shown)} de plus</button>` : ""}`
    : alert("info", html`Aucun élément ne correspond à ces filtres.
      <a href="#/ajouter">Ajouter un titre</a>`)}`;
}

export const actions = {
  "library-search"(app, el) {
    app.ui.librarySearch = el.value;
    app.ui.libraryLimit = CHUNK;
  },
  "library-pref"(app, el) {
    savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), [el.name]: el.value });
    app.ui.libraryLimit = CHUNK;
  },
  "library-status"(app, el) {
    const boxes = el.closest("fieldset").querySelectorAll("input:checked");
    savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), statuses: [...boxes].map((b) => b.value) });
    app.ui.libraryLimit = CHUNK;
  },
  "library-fav"(app, el) {
    savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), favoriteOnly: el.checked });
    app.ui.libraryLimit = CHUNK;
  },
  "library-more"(app) { app.ui.libraryLimit = (app.ui.libraryLimit || CHUNK) + CHUNK; },
};
