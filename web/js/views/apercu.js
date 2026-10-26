// Page « Aperçu » (port de apercu.py) : infos détaillées d'un film ou d'une série TMDB,
// ouverte depuis les suggestions. Route #/apercu/serie|film/<id TMDB>. Le titre n'a pas
// besoin d'être dans la bibliothèque.

import { addMovie, addSuggestedShow } from "../core/adding.js";
import { fmtDate, fmtRuntime, today } from "../core/dates.js";
import { COMPLETED, MOVIE, SHOW, STATUSES, STATUS_LABELS, TO_WATCH } from "../core/model.js";
import { libraryKey, load, similar } from "../core/preview.js";
import { dismiss } from "../core/suggestions.js";
import {
  html, movieHref, options, poster, previewHref, safeImageUrl, showHref,
} from "./dom.js";
import { icon } from "./icons.js";
import { providersBlock } from "./providers.js";
import { forgetSuggestion } from "./suggestions.js";
import { alert, badge } from "./ui.js";

const KINDS = { serie: SHOW, film: MOVIE };
const STATUS_PAIRS = STATUSES.map((s) => [s, STATUS_LABELS[s]]);
const MIN_DATE = "1900-01-01";
// Vidéo YouTube : identifiant seul (11 caractères), lu sans cookie et seulement sur demande
const YOUTUBE_ID = /[?&]v=([\w-]{6,20})$/;

function castGrid(people) {
  return html`<ul class="cast" role="list">${people.map((p) => html`<li>
    ${safeImageUrl(p.photoUrl) ? html`<img src="${p.photoUrl}" alt="" loading="lazy"
      referrerpolicy="no-referrer">` : html`<span class="cast-empty" aria-hidden="true"></span>`}
    <span class="cast-name">${p.name}</span>${p.role ? html`<span class="caption">${p.role}</span>` : ""}
  </li>`)}</ul>`;
}

function trailerBlock(app, item) {
  const id = item.trailerUrl?.match(YOUTUBE_ID)?.[1];
  if (!id) return "";
  const on = app.ui.trailerOn === `${item.kind}-${item.tmdbId}`;
  return html`<h2>Bande-annonce</h2>
    ${on ? html`<div class="trailer"><iframe src="${`https://www.youtube-nocookie.com/embed/${id}?autoplay=1`}"
      title="${`Bande-annonce : ${item.title}`}" allow="autoplay; encrypted-media; picture-in-picture"
      allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe></div>`
      : html`<p><button type="button" class="btn" data-action="apercu-trailer"
        data-key="${`${item.kind}-${item.tmdbId}`}">${icon("play_circle")} Lire la bande-annonce</button></p>
        <p class="caption">La vidéo vient de YouTube : elle n'est chargée que si tu la lances.</p>`}`;
}

function addBlock(app, item, fromAdd) {
  const key = libraryKey(app.store, item.kind, item.tmdbId);
  if (key) {
    return html`${alert("success", "Déjà dans ta bibliothèque.")}
      <p><a href="${item.kind === SHOW ? showHref(key) : movieHref(key)}">${icon("open_in_new")}
        Ouvrir la fiche</a></p>`;
  }
  const status = app.ui.previewStatus || TO_WATCH;
  return html`<form class="row-form" data-submit="apercu-add" data-kind="${item.kind}"
    data-id="${item.tmdbId}" data-title="${item.title}">
    <label class="field field-wide"><span>Statut</span><select name="status" data-change="apercu-status"
      id="apercu-status">${options(STATUS_PAIRS, status)}</select></label>
    ${item.kind === MOVIE && status === COMPLETED ? html`<label class="field"><span>Vu le
      (facultatif)</span><input type="date" name="day" min="${MIN_DATE}" max="${today()}"></label>` : ""}
    <button class="btn btn-primary">Ajouter</button>
    ${fromAdd ? "" : html`<button type="button" class="btn" data-action="apercu-dismiss"
      data-kind="${item.kind}" data-id="${item.tmdbId}" data-title="${item.title}">Pas intéressé</button>`}
  </form>`;
}

// Page d'origine (3e partie de la route) -> lien « Retour » ; par défaut, les suggestions.
const ORIGIN_ADD = "ajouter";

export async function render(app, [type, idText, from]) {
  // « Pas intéressé » n'a de sens que pour une suggestion : pas depuis la recherche.
  const fromAdd = from === ORIGIN_ADD;
  const back = fromAdd
    ? html`<p><a href="#/ajouter">${icon("arrow_back")} Retour à la recherche</a></p>`
    : html`<p><a href="#/suggestions">${icon("arrow_back")} Retour aux suggestions</a></p>`;
  const kind = KINDS[type];
  const tmdbId = Number(idText);
  if (!kind || !Number.isInteger(tmdbId)) {
    return html`<h1>Aperçu</h1>${alert("info", html`Aucun titre sélectionné. Ouvre un titre depuis la
      page <a href="#/suggestions">Suggestions</a>.`)}`;
  }
  if (!app.tmdb.configured) {
    return html`<h1>Aperçu</h1>${alert("info", html`L'aperçu a besoin de TMDB :
      <a href="#/parametres">ajoute ta clé TMDB dans Paramètres</a>.`)}`;
  }
  let item;
  try {
    item = await load(app.tmdb, kind, tmdbId);
  } catch (error) {
    console.warn(`Aperçu ${type} ${tmdbId}`, error);
    return html`<h1>Aperçu</h1>${back}${alert("error", `Informations indisponibles : ${error.message}`)}`;
  }
  if (!item) {
    return html`<h1>Aperçu</h1>${back}${alert("info", "Titre introuvable sur TMDB (ou TMDB non connecté).")}`;
  }
  const others = await similar(app.tmdb, kind, tmdbId);
  const message = app.ui.previewMessage;
  app.ui.previewMessage = null;
  const facts = [];
  if (item.released) facts.push(`${kind === SHOW ? "Première diffusion" : "Sortie"} : ${fmtDate(item.released)}`);
  if (kind === SHOW) {
    if (item.seasons) facts.push(`${item.seasons} saison(s), ${item.episodes || "?"} épisodes`);
    if (item.runtime) facts.push(`~${fmtRuntime(item.runtime)} par épisode`);
    if (item.networks.length) facts.push(item.networks.join(", "));
  } else if (item.runtime) {
    facts.push(`Durée : ${fmtRuntime(item.runtime)}`);
  }
  if (item.status) facts.push(item.status);
  return html`
  ${back}
  ${message ? alert("success", message) : ""}
  ${safeImageUrl(item.backdropUrl) ? html`<div class="backdrop"><img src="${item.backdropUrl}" alt=""
    referrerpolicy="no-referrer"></div>` : ""}
  <div class="sheet-head">
    <div>${poster(item.posterUrl, item.title, "lg")}</div>
    <div>
      <h1>${item.title}${item.year ? ` (${item.year})` : ""}</h1>
      ${item.originalTitle && item.originalTitle !== item.title
        ? html`<p class="caption">Titre original : ${item.originalTitle}</p>` : ""}
      ${item.tagline ? html`<p><em>${item.tagline}</em></p>` : ""}
      <p>${kind === SHOW ? badge("Série", "blue") : badge("Film", "orange")}
        ${item.genres.map((g) => badge(g, "gray"))}
        ${item.voteCount ? badge(`★ ${item.voteAverage.toFixed(1).replace(".", ",")}/10 · ${item.voteCount
          .toLocaleString("fr-FR")} votes`, "orange") : ""}</p>
      ${facts.length ? html`<p>${facts.join(" · ")}</p>` : ""}
      ${item.makers.length ? html`<p><strong>${kind === SHOW ? "Création" : "Réalisation"}</strong> :
        ${item.makers.join(", ")}</p>` : ""}
      ${item.overview ? html`<p class="summary-text">${item.overview}</p>` : ""}
      ${await providersBlock(app, kind === SHOW ? "tv" : "movie", tmdbId)}
      ${addBlock(app, item, fromAdd)}
    </div>
  </div>
  ${trailerBlock(app, item)}
  ${item.cast.length ? html`<h2>Casting</h2>${castGrid(item.cast)}` : ""}
  ${others.length ? html`<h2>Titres similaires</h2>
    <ul class="cards similar" role="list">${others.map((o) => html`<li class="card">
      <a href="${previewHref(kind, o.id, from)}" tabindex="-1" aria-hidden="true">${poster(o.posterUrl, o.title)}</a>
      <a class="card-title" href="${previewHref(kind, o.id, from)}">${o.title}${o.year ? ` (${o.year})` : ""}</a>
    </li>`)}</ul>` : ""}`;
}

export const actions = {
  "apercu-trailer"(app, el) { app.ui.trailerOn = el.dataset.key; },
  "apercu-status"(app, el) { app.ui.previewStatus = el.value; },
  async "apercu-add"(app, form) {
    const button = form.querySelector("button:not([type])");
    button.disabled = true;
    button.textContent = "Ajout…";
    const id = Number(form.dataset.id);
    const data = new FormData(form);
    const status = data.get("status") || TO_WATCH;
    if (form.dataset.kind === SHOW) {
      await addSuggestedShow(app.store, app.tvmaze, app.tmdb, id, { title: form.dataset.title, status });
    } else {
      await addMovie(app.store, app.tmdb, id, { status, watchedOn: data.get("day") || null });
    }
    forgetSuggestion(app, form.dataset.kind, id);
    app.ui.previewStatus = null;
    app.ui.previewMessage = `« ${form.dataset.title} » a été ajouté à la bibliothèque.`;
  },
  "apercu-dismiss"(app, el) {
    dismiss(app.store, el.dataset.kind, Number(el.dataset.id), el.dataset.title);
    forgetSuggestion(app, el.dataset.kind, Number(el.dataset.id));
    app.ui.previewMessage = "Ce titre ne sera plus suggéré.";
  },
};
