// Page « Aperçu » d'un titre pas forcément dans la bibliothèque (ouverte depuis la recherche de
// la page Ajouter) : #/apercu/serie/<id TVMaze> ou #/apercu/film/<id TMDB>.
// Série : fiche TVMaze, complétée par TMDB si la clé est saisie (français, casting, plateformes).

import { fmtDate, fmtRuntime } from "../core/dates.js";
import { MOVIE, SHOW } from "../core/model.js";
import { PREVIEW_KINDS, fromTmdb, fromTvmaze, mergeTmdb } from "../core/preview.js";
import { actions, movieAddForm, showAddForm } from "./ajouter.js";
import { externalLink, html, kindBadge, poster, safeImageUrl } from "./dom.js";
import { providersBlock } from "./providers.js";

export { actions };

const BACK = html`<p><a href="#/ajouter">← Retour à la recherche</a></p>`;

function notFound(message) {
  return html`<h1>Aperçu indisponible</h1><p class="muted">${message}</p>${BACK}`;
}

/** Série : TVMaze, puis TMDB (via l'id TVDB) si possible. Une panne TMDB n'empêche pas
 *  l'aperçu, mais est signalée (`warning`). */
async function loadShow(app, tvmazeId) {
  const data = await app.tvmaze.getShow(tvmazeId);
  if (!data) return { item: null };
  let item = fromTvmaze(data);
  let warning = null;
  if (app.tmdb.configured && item.tvdbId) {
    try {
      const tmdbId = await app.tmdb.findTvByTvdb(item.tvdbId);
      const details = tmdbId ? await app.tmdb.details("tv", tmdbId) : null;
      if (details) item = mergeTmdb(item, fromTmdb(SHOW, details));
    } catch (error) {
      console.warn(`Aperçu : fiche TMDB non lue (${item.title}, TVMaze ${tvmazeId})`, error);
      warning = `Textes français, casting et plateformes indisponibles (TMDB : ${error.message}).`;
    }
  }
  return { item, warning };
}

async function loadMovie(app, tmdbId) {
  const data = await app.tmdb.details("movie", tmdbId);
  return { item: data ? fromTmdb(MOVIE, data) : null };
}

function facts(item) {
  const show = item.kind === SHOW;
  const out = [];
  if (item.released) out.push(`${show ? "Première diffusion" : "Sortie"} : ${fmtDate(item.released)}`);
  if (show && item.seasons) out.push(`${item.seasons} saison(s), ${item.episodes || "?"} épisodes`);
  if (item.runtime) out.push(show ? `~${fmtRuntime(item.runtime)} par épisode` : `Durée : ${fmtRuntime(item.runtime)}`);
  if (show && item.networks.length) out.push(item.networks.join(", "));
  if (item.status) out.push(item.status);
  return out.join(" · ");
}

function castList(cast) {
  return html`<section><h2>Casting</h2><ul class="cast" role="list">${cast.map((p) => html`<li>
    ${safeImageUrl(p.photoUrl) ? html`<img src="${p.photoUrl}" alt="" loading="lazy" decoding="async"
      referrerpolicy="no-referrer">` : html`<span class="cast-empty" aria-hidden="true"></span>`}
    <span class="cast-name">${p.name}</span>${p.role ? html`<span class="muted small">${p.role}</span>` : ""}
  </li>`)}</ul></section>`;
}

export async function render(app, [type, rawId]) {
  const kind = PREVIEW_KINDS[type];
  const id = Number(rawId);
  if (!kind || !Number.isInteger(id) || id <= 0) return notFound("Aucun titre sélectionné.");
  if (kind === MOVIE && !app.tmdb.configured) {
    return notFound("La fiche d'un film vient de TMDB : ajoute ta clé TMDB dans Paramètres.");
  }
  let loaded;
  try {
    loaded = kind === SHOW ? await loadShow(app, id) : await loadMovie(app, id);
  } catch (error) {
    console.warn(`Aperçu ${type} ${id}`, error);
    return notFound(`Informations indisponibles : ${error.message}`);
  }
  const { item, warning } = loaded;
  if (!item) return notFound(`Titre introuvable sur ${kind === SHOW ? "TVMaze" : "TMDB"}.`);
  const label = item.kind === SHOW ? "Création" : "Réalisation";
  return html`
  ${BACK}
  <article class="sheet ${kind === MOVIE ? "is-movie" : "is-show"}">
    <header class="sheet-head">
      ${poster(item.posterUrl, item.title, "lg")}
      <div>
        <h1>${item.title} ${item.year ? html`<span class="muted">(${item.year})</span>` : ""}</h1>
        ${item.originalTitle && item.originalTitle !== item.title
          ? html`<p class="muted">Titre original : ${item.originalTitle}</p>` : ""}
        ${item.tagline ? html`<p><em>${item.tagline}</em></p>` : ""}
        <p class="tags">${kindBadge(kind)} ${item.genres.map((g) => html`<span class="tag">${g}</span>`)}
          ${item.voteAverage ? html`<span class="tag">★ ${item.voteAverage.toFixed(1)}/10${item.voteCount
            ? ` · ${item.voteCount} votes` : ""}</span>` : ""}</p>
        <p class="meta">${facts(item)}</p>
        ${item.makers.length ? html`<p><strong>${label}</strong> : ${item.makers.join(", ")}</p>` : ""}
        <div class="sheet-controls">${kind === SHOW ? showAddForm(app, id, item.title)
          : movieAddForm(app, id, item.title)}</div>
      </div>
    </header>
    ${warning ? html`<p class="warning">${warning}</p>` : ""}
    ${item.overview ? html`<p class="summary-text">${item.overview}</p>` : ""}
    ${item.trailerUrl ? html`<p>${externalLink(item.trailerUrl, "Voir la bande-annonce (YouTube)")}</p>` : ""}
    ${item.tmdbId ? await providersBlock(app, kind === SHOW ? "tv" : "movie", item.tmdbId) : ""}
    ${item.cast.length ? castList(item.cast) : ""}
    <p class="credit">${kind === SHOW ? "Série : TVMaze (CC BY-SA)" : "Film : TMDB"}${kind === SHOW
      && item.tmdbId ? ", compléments TMDB" : ""}.</p>
  </article>`;
}
