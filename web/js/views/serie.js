// Fiche d'une série : infos, statut, prochain épisode, saisons et épisodes à cocher, avis.

import { enrichShowFromTmdb } from "../core/adding.js";
import { upsertShowFromTvmaze } from "../core/catalog.js";
import { fmtDate, fmtRuntime, localDate, today } from "../core/dates.js";
import {
  getWatch, isAired, markSeasonWatched, markWatchedUntil, unmarkSeason,
} from "../core/library.js";
import {
  EPISODE, MATCH_TO_CHECK, SHOW, episodeCode, episodeTitle, showSummary, splitGenres,
} from "../core/model.js";
import { nextEpisode } from "../core/queue.js";
import { externalLink, html, poster } from "./dom.js";
import {
  favoriteButton, ratingForm, removeButton, statusSelect, watchButton,
} from "./shared.js";

function seasonBlock(store, show, season, episodes, day) {
  const regular = episodes.filter((e) => !e.is_special);
  const seen = regular.filter((e) => getWatch(store, EPISODE, e.key)).length;
  const open = seen < regular.length && seen > 0;
  const name = season === 0 ? "Spéciaux" : `Saison ${season}`;
  return html`<details class="season" data-keep="${`saison-${show.key}-${season}`}" ${open ? html`open` : ""}>
    <summary><span class="season-name">${name}</span>
      <span class="season-count">${seen}/${regular.length} vus</span></summary>
    <div class="season-actions">
      <button type="button" class="btn btn-ghost" data-action="season-watch" data-show="${show.key}"
        data-season="${season}">Toute la saison vue</button>
      ${seen ? html`<button type="button" class="btn btn-ghost" data-action="season-unwatch"
        data-show="${show.key}" data-season="${season}">Tout décocher</button>` : ""}
    </div>
    <ol class="episodes">${episodes.map((ep) => {
      const date = localDate(ep.airstamp, ep.airdate);
      const aired = isAired(ep, day);
      const label = `${show.title} ${episodeCode(ep.season, ep.number)}`;
      return html`<li class="episode ${getWatch(store, EPISODE, ep.key) ? "is-seen" : ""}">
        <span class="code">${episodeCode(ep.season, ep.number)}</span>
        <span class="ep-title">${episodeTitle(ep) || ""}
          <span class="muted">${date ? fmtDate(date) : "date inconnue"}${ep.runtime
            ? ` · ${fmtRuntime(ep.runtime)}` : ""}</span></span>
        ${aired || getWatch(store, EPISODE, ep.key) ? watchButton(store, EPISODE, ep.key, label)
          : html`<span class="muted small">à venir</span>`}
        ${aired && !getWatch(store, EPISODE, ep.key) ? html`<button type="button"
          class="btn btn-link" data-action="watch-until" data-ref="${ep.key}"
          aria-label="${`J'en suis là : tout vu jusqu'à ${label}`}">J'en suis là</button>` : ""}
      </li>`;
    })}</ol>
  </details>`;
}

export function render(app, [key]) {
  const show = app.store.get("shows", key);
  if (!show) {
    return html`<h1>Série introuvable</h1><p><a href="#/bibliotheque">Retour à la bibliothèque</a></p>`;
  }
  const day = today();
  const episodes = app.store.episodesOf(key);
  const seasons = new Map();
  for (const ep of episodes) {
    const s = ep.is_special && ep.season === 0 ? 0 : ep.season;
    if (!seasons.has(s)) seasons.set(s, []);
    seasons.get(s).push(ep);
  }
  const next = nextEpisode(app.store, key, day);
  const genres = splitGenres(show.genres);
  return html`
  <article class="sheet is-show">
    <header class="sheet-head">
      ${poster(show.poster_url, show.title, "lg")}
      <div>
        <h1>${show.title}</h1>
        ${show.original_title && show.original_title !== show.title
          ? html`<p class="muted">${show.original_title}</p>` : ""}
        <p class="meta">${[show.network, show.premiered?.slice(0, 4), show.status === "Ended"
          ? "Terminée" : show.status === "Running" ? "En production" : show.status]
          .filter(Boolean).join(" · ")}</p>
        ${genres.length ? html`<p class="tags">${genres.map((g) => html`<span class="tag">${g}</span>`)}</p>` : ""}
        <div class="sheet-controls">
          ${statusSelect(app.store, SHOW, key, "show-status")}
          ${favoriteButton(app.store, SHOW, key)}
          ${show.tvmaze_id ? html`<button type="button" class="btn btn-ghost" data-action="show-refresh"
            data-ref="${key}">Mettre à jour</button>` : ""}
        </div>
      </div>
    </header>
    ${MATCH_TO_CHECK.includes(show.match_status) ? html`<p class="warning">La numérotation de
      cette série n'est pas encore vérifiée : corrige-la dans l'app Python (Paramètres,
      Correspondances à vérifier), puis réexporte.</p>` : ""}
    ${showSummary(show) ? html`<p class="summary-text">${showSummary(show)}</p>` : ""}
    ${next ? html`<section class="next"><h2>Prochain épisode</h2>
      <p><span class="code">${next.code}</span> ${next.episodeTitle || ""}
        <span class="muted">${next.releaseDate ? fmtDate(next.releaseDate) : ""}</span></p>
      ${next.availability === "released" ? watchButton(app.store, EPISODE, next.ref,
        `${show.title} ${next.code}`) : html`<p class="muted">Pas encore sorti.</p>`}
    </section>` : ""}
    <section><h2>Épisodes</h2>
      ${seasons.size ? [...seasons].sort((a, b) => (a[0] || 999) - (b[0] || 999))
        .map(([season, list]) => seasonBlock(app.store, show, season, list, day))
        : html`<p class="muted">Aucun épisode connu.</p>`}
    </section>
    <section><h2>Mon avis</h2>${ratingForm(app.store, SHOW, key)}</section>
    ${app.store.get("library", `${SHOW}:${key}`) ? removeButton(SHOW, key) : ""}
    ${show.tvmaze_id ? html`<p class="credit">Données de la série :
      ${externalLink(`https://www.tvmaze.com/shows/${show.tvmaze_id}`, "TVMaze")} (CC BY-SA).</p>` : ""}
  </article>`;
}

export const actions = {
  "season-watch"(app, el) {
    const n = markSeasonWatched(app.store, el.dataset.show, Number(el.dataset.season));
    app.toast(n ? `${n} épisode(s) marqué(s) vu(s).` : "Rien à cocher (épisodes pas encore sortis ?).");
  },
  "season-unwatch"(app, el) {
    const n = unmarkSeason(app.store, el.dataset.show, Number(el.dataset.season));
    app.toast(`${n} visionnage(s) retiré(s).`);
  },
  "watch-until"(app, el) {
    const n = markWatchedUntil(app.store, el.dataset.ref);
    app.toast(`${n} épisode(s) marqué(s) vu(s).`);
  },
  async "show-refresh"(app, el) {
    const show = app.store.get("shows", el.dataset.ref);
    el.disabled = true;
    el.textContent = "Mise à jour…";
    const data = await app.tvmaze.getShow(show.tvmaze_id, true);
    if (!data) throw new Error("Série introuvable sur TVMaze.");
    const episodes = await app.tvmaze.getEpisodesWithSpecials(show.tvmaze_id, data.status, true);
    upsertShowFromTvmaze(app.store, data, episodes);
    const enrich = await enrichShowFromTmdb(app.store, app.tmdb, show.key);
    app.toast(enrich.errors.length ? `Série mise à jour, textes français indisponibles : ${enrich.errors[0]}`
      : "Série mise à jour.", enrich.errors.length ? "error" : "info");
  },
};
