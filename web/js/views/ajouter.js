// Page « Ajouter », comme la page Python (ajouter.py) : onglets « Rechercher » (séries sur
// TVMaze et films sur TMDB, en une seule recherche) et « Saisie manuelle ».

import {
  addManualMovie, addManualShow, addMovie, addShow, tvmazeStatusLabel,
} from "../core/adding.js";
import { posterUrl } from "../core/catalog.js";
import { MOVIE, SHOW, STATUSES, STATUS_LABELS, TO_WATCH, refKey } from "../core/model.js";
import { shortText } from "../core/suggestions.js";
import { html, movieHref, options, poster, previewHref, showHref } from "./dom.js";
import { icon } from "./icons.js";
import { alert, badge, currentTab, segmented, tabPanel, tabs } from "./ui.js";

const STATUS_PAIRS = STATUSES.map((s) => [s, STATUS_LABELS[s]]);
const TABS = ["Rechercher", "Saisie manuelle"];
const SUMMARY_LENGTH = 220;   // longueur du résumé dans les résultats (adding.SUMMARY_LENGTH)
// Page Aperçu ouverte d'ici : son lien « Retour » revient à la recherche.
export const PREVIEW_ORIGIN = "ajouter";

/** « Voir la fiche » d'un titre pas encore ajouté : page Aperçu (données TMDB, comme
 *  ajouter.py). Film : lien direct ; série : id TMDB cherché au clic, par son id TVDB.
 *  Rien sans clé TMDB, ni pour une série sans id TVDB. */
function previewLink(app, r) {
  if (!app.tmdb.configured) return "";
  if (r.kind === MOVIE) {
    return html`<p><a href="${previewHref(MOVIE, r.id, PREVIEW_ORIGIN)}">${icon("info")} Voir la
      fiche<span class="sr-only"> de ${r.title}</span></a></p>`;
  }
  if (!r.tvdb) return "";
  return html`<p><button type="button" class="btn btn-tertiary" data-action="add-preview"
    data-tvdb="${r.tvdb}" data-title="${r.title}">${icon("info")} Voir la fiche<span
    class="sr-only"> de ${r.title}</span></button></p>`;
}

function resultCard(app, r) {
  const inLibrary = app.store.get("library", refKey(r.kind, r.key));
  const href = r.kind === SHOW ? showHref(r.key) : movieHref(r.key);
  const details = [r.original && r.original !== r.title ? `Titre original : ${r.original}` : "",
    r.network, r.status].filter(Boolean);
  return html`<li class="row is-plain">
    ${poster(r.poster, r.title, "sm")}
    <div class="row-main">
      <p>${r.kind === SHOW ? badge("Série", "blue") : badge("Film", "orange")}
        <strong>${r.title}${r.year ? ` (${r.year})` : ""}</strong></p>
      ${details.length ? html`<p class="caption">${details.join(" · ")}</p>` : ""}
      ${r.summary ? html`<p>${r.summary}</p>` : ""}
      ${inLibrary ? "" : previewLink(app, r)}
      ${inLibrary ? html`<p>Déjà dans la bibliothèque · <a href="${href}">Ouvrir la fiche</a></p>`
        : html`<form class="row-form" data-submit="${r.kind === SHOW ? "add-show" : "add-movie"}"
          data-id="${r.id}">
          <label class="field field-wide"><span>Statut</span><select name="status"
            aria-label="${`Statut pour ${r.title}`}">${options(STATUS_PAIRS, TO_WATCH)}</select></label>
          <button class="btn" aria-label="${`Ajouter ${r.title}`}">Ajouter</button>
          ${r.kind === SHOW ? html`<details class="until"><summary>J'en suis à…</summary>
            <div class="row-form">
              <label class="field"><span>Saison</span><input name="season" type="number" min="1"
                inputmode="numeric" aria-label="${`Saison en cours de ${r.title}`}"></label>
              <label class="field"><span>Épisode</span><input name="episode" type="number" min="1"
                inputmode="numeric" aria-label="${`Dernier épisode vu de ${r.title}`}"></label>
            </div>
            <p class="caption">Tous les épisodes jusqu'à celui-ci inclus (hors spéciaux) seront
              marqués vus.</p></details>` : ""}
        </form>`}
    </div>
  </li>`;
}

function searchTab(app, ui) {
  const results = ui.results;
  const shows = results ? results.filter((r) => r.kind === SHOW).length : 0;
  return html`<p class="caption">Séries cherchées sur TVMaze, films sur TMDB.</p>
    <form class="box stack" role="search" data-submit="add-search">
      <label class="field"><span>Titre d'une série ou d'un film</span>
        <input type="search" name="query" id="add-query" value="${ui.query}" required autocomplete="off"></label>
      <button class="btn">Rechercher</button>
    </form>
    ${ui.errors.map((e) => alert("warning", e))}
    ${results ? (results.length ? html`<p class="caption" data-announce>${shows} série(s) et
      ${results.length - shows} film(s)</p>
      <ul class="rows" role="list">${results.map((r) => resultCard(app, r))}</ul>`
      : html`<p data-announce>Aucun résultat pour « ${ui.query} ».</p>`) : ""}`;
}

function manualTab(ui) {
  const kind = ui.manualKind;
  return html`<p class="caption">Pour un titre absent de TVMaze ou de TMDB (il n'aura pas de mise à
    jour automatique).</p>
    ${segmented("Type", "manual-kind", [[SHOW, "Série"], [MOVIE, "Film"]], kind, "add-manual-kind")}
    <form class="box stack" data-submit="add-manual" data-kind="${kind}">
      <label class="field"><span>Titre</span><input name="title" required></label>
      ${kind === SHOW ? html`
        <label class="field"><span>Nombre d'épisodes par saison</span>
          <input name="seasons" placeholder="ex. 10, 8, 8" inputmode="numeric"
            aria-describedby="add-seasons-help"></label>
        <p class="caption" id="add-seasons-help">Un nombre par saison, séparés par des virgules.</p>` : html`
        <label class="field"><span>Année (facultatif)</span><input name="year" type="number" min="1880"
          max="2100"></label>
        <label class="field"><span>Durée en minutes (facultatif)</span><input name="runtime" type="number"
          min="1" max="1000"></label>`}
      <label class="field"><span>Statut</span><select name="status">${options(STATUS_PAIRS, TO_WATCH)}</select></label>
      <button class="btn">${kind === SHOW ? "Ajouter la série" : "Ajouter le film"}</button>
    </form>`;
}

export function render(app) {
  const ui = app.ui.add || (app.ui.add = { query: "", results: null, errors: [], manualKind: SHOW });
  const tab = currentTab(app, "add", TABS);
  return html`
  <h1>Ajouter</h1>
  ${tabs(app, "add", TABS, "Façon d'ajouter")}
  ${tabPanel(app, "add", TABS, tab === TABS[0] ? searchTab(app, ui) : manualTab(ui))}`;
}

/** Résultats TVMaze et TMDB au même format (adding.search_all). */
function showResults(list) {
  return (list || []).map(({ show }) => ({
    kind: SHOW, id: show.id, key: `tvmaze:${show.id}`, tvdb: show.externals?.thetvdb || null,
    title: show.name, original: null,
    year: show.premiered?.slice(0, 4), poster: show.image?.medium,
    network: (show.network || show.webChannel || {}).name, status: tvmazeStatusLabel(show.status),
    summary: shortText(show.summary, SUMMARY_LENGTH),
  }));
}

function movieResults(list) {
  return (list || []).map((m) => ({
    kind: MOVIE, id: m.id, key: `tmdb:${m.id}`, title: m.title, original: m.original_title,
    year: m.release_date?.slice(0, 4), poster: posterUrl(m.poster_path), network: "", status: "",
    summary: shortText(m.overview, SUMMARY_LENGTH),
  }));
}

export const actions = {
  async "add-search"(app, form) {
    const ui = app.ui.add;
    ui.query = (new FormData(form).get("query") || "").trim();
    ui.errors = [];
    if (!ui.query) return;
    const [shows, movies] = await Promise.allSettled([app.tvmaze.search(ui.query),
      app.tmdb.configured ? app.tmdb.searchMovies(ui.query) : Promise.resolve(null)]);
    if (shows.status === "rejected") {
      console.warn("Recherche TVMaze", shows.reason);
      ui.errors.push(`Séries non cherchées : TVMaze n'a pas répondu (${shows.reason.message}).`);
    }
    if (!app.tmdb.configured) {
      ui.errors.push("Films non cherchés : TMDB n'est pas connecté (clé dans Paramètres). Tu peux "
        + "ajouter un film dans « Saisie manuelle ».");
    } else if (movies.status === "rejected") {
      console.warn("Recherche TMDB", movies.reason);
      ui.errors.push(`Films non cherchés : TMDB n'a pas répondu (${movies.reason.message}).`);
    }
    ui.results = [...showResults(shows.value), ...movieResults(movies.value)];
  },
  "add-manual-kind"(app, el) { app.ui.add.manualKind = el.value; },
  async "add-preview"(app, el) {
    const tmdbId = await app.tmdb.findTvByTvdb(Number(el.dataset.tvdb));
    if (!tmdbId) {
      throw new Error(`Pas de fiche détaillée pour « ${el.dataset.title} » : série introuvable sur TMDB.`);
    }
    app.navigate(previewHref(SHOW, tmdbId, PREVIEW_ORIGIN));
    return false;
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
    app.toast(`« ${show.title} » a été ajouté à la bibliothèque.${warnings.length
      ? " Textes français indisponibles pour l'instant." : ""}`);
  },
  async "add-movie"(app, form) {
    form.querySelector("button").disabled = true;
    const movie = await addMovie(app.store, app.tmdb, Number(form.dataset.id),
      { status: new FormData(form).get("status") });
    app.toast(`« ${movie.title} » a été ajouté à la bibliothèque.`);
  },
  "add-manual"(app, form) {
    const data = new FormData(form);
    const status = data.get("status");
    if (form.dataset.kind === SHOW) {
      const seasons = String(data.get("seasons") || "").split(/[,;\s]+/).map(Number)
        .filter((n) => Number.isInteger(n) && n > 0);
      const show = addManualShow(app.store, { title: data.get("title"), seasons, status });
      app.navigate(showHref(show.key));
    } else {
      const movie = addManualMovie(app.store, { title: data.get("title"),
        year: Number(data.get("year")) || null, runtime: Number(data.get("runtime")) || null, status });
      app.navigate(movieHref(movie.key));
    }
    app.toast("Titre ajouté.");
  },
};
