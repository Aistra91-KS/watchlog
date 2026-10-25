// Fiche d'une série, comme la page Python (serie.py) : affiche et infos, temps passé et
// restant, prochain épisode, statut et favori, saisons dépliables (tableau des épisodes), avis.

import { enrichShowFromTmdb, tvmazeStatusLabel } from "../core/adding.js";
import { upsertShowFromTvmaze } from "../core/catalog.js";
import { fmtDate, fmtRuntime, localDate, today } from "../core/dates.js";
import {
  getLibraryItem, getWatch, isAired, markSeasonWatched, markWatched, markWatchedUntil,
  unmarkSeason, unmarkWatched,
} from "../core/library.js";
import {
  EPISODE, MATCH_TO_CHECK, SHOW, episodeCode, episodeTitle, showSummary,
} from "../core/model.js";
import { UPCOMING, nextEpisode } from "../core/queue.js";
import { formatDuration, showTime } from "../core/stats.js";
import { externalLink, html, poster, raw } from "./dom.js";
import { icon } from "./icons.js";
import { loadPrefs, savePrefs } from "./prefs.js";
import { providersBlock } from "./providers.js";
import { favoriteCheck, ratingForm, removeButton, statusSelect } from "./shared.js";
import { alert, expander, metric, metrics } from "./ui.js";

const PREFS = "serie";
const DEFAULTS = { specials: false };

function episodeRow(store, show, ep, day) {
  const date = localDate(ep.airstamp, ep.airdate);
  const aired = isAired(ep, day);
  const w = getWatch(store, EPISODE, ep.key);
  const code = episodeCode(ep.season, ep.number);
  const label = `${show.title} ${code}`;
  return html`<tr class="${aired ? "" : "is-upcoming"}">
    <td>${code}</td><td>${date ? fmtDate(date) : ""}</td><td>${episodeTitle(ep) || ""}</td>
    <td>${ep.runtime ? `${ep.runtime} min` : ""}</td>
    <td><label class="check"><input type="checkbox" data-change="ep-toggle" data-ref="${ep.key}"
      data-focus="${`ep-${ep.key}`}" aria-label="${`Vu : ${label}`}" ${w ? raw("checked") : ""}
      ${aired || w ? "" : raw("disabled")}></label></td>
    <td>${w?.watched_at ? fmtDate(w.watched_at) : w ? "date inconnue" : ""}</td>
    <td>${aired && !w ? html`<button type="button" class="btn" data-action="watch-until"
      data-ref="${ep.key}" title="Marque vus tous les épisodes jusqu'à celui-ci inclus"
      aria-label="${`Vu jusqu'ici : tout vu jusqu'à ${label}`}">${icon("done_all")}<span
      aria-hidden="true">Jusqu'ici</span></button>` : ""}</td>
  </tr>`;
}

function seasonBlock(store, show, season, episodes, day) {
  const seen = episodes.filter((e) => getWatch(store, EPISODE, e.key)).length;
  const open = seen < episodes.length && seen > 0;
  const name = season === 0 ? "Spéciaux" : `Saison ${season}`;
  const upcoming = episodes.some((ep) => !isAired(ep, day));
  return expander(`${name} · ${seen}/${episodes.length} vus`, html`
    <div class="tbl-wrap"><table class="tbl" aria-label="${`Épisodes : ${name}`}">
      <thead><tr><th scope="col">Code</th><th scope="col">Date</th><th scope="col">Titre</th>
        <th scope="col">Durée</th><th scope="col">Vu</th><th scope="col">Vu le</th>
        <th scope="col">Vu jusqu'ici</th></tr></thead>
      <tbody>${episodes.map((ep) => episodeRow(store, show, ep, day))}</tbody>
    </table></div>
    ${upcoming ? html`<p class="caption">Les épisodes en gris ne sont pas encore diffusés.</p>` : ""}
    <div class="season-actions">
      ${seen < episodes.length ? html`<button type="button" class="btn" data-action="season-watch"
        data-show="${show.key}" data-season="${season}">Marquer la saison vue</button>` : ""}
      ${seen ? html`<button type="button" class="btn" data-action="season-unwatch"
        data-show="${show.key}" data-season="${season}">Tout décocher</button>` : ""}
    </div>`, { open, keep: `saison-${show.key}-${season}` });
}

/** Bloc « Prochain épisode » : le marquer vu sans chercher dans les saisons. */
function nextBlock(app, show, day) {
  const next = nextEpisode(app.store, show.key, day);
  const last = app.ui.serieLast?.show === show.key ? app.ui.serieLast : null;
  if (!next && !last) return "";
  let line = "";
  if (next && next.availability === UPCOMING) {
    line = html`<p><strong>Prochain épisode</strong> : ${next.code} le ${fmtDate(next.releaseDate)}</p>`;
  } else if (next) {
    const details = [next.episodeTitle, fmtDate(next.releaseDate), fmtRuntime(next.runtime)]
      .filter(Boolean);
    line = html`<p>Prochain épisode : <strong>${next.code}</strong>${details.length
      ? ` · ${details.join(" · ")}` : ""}</p>
      <button type="button" class="btn btn-primary" data-action="serie-next" data-ref="${next.ref}"
        data-code="${next.code}" aria-label="${`Vu : ${show.title} ${next.code}`}">Vu</button>`;
  }
  return html`<div class="box">
    ${line ? html`<div class="next">${line}</div>` : ""}
    ${last ? html`<div class="next"><p class="caption">${last.text}</p>
      <button type="button" class="btn" data-action="serie-undo">Annuler</button></div>` : ""}
  </div>`;
}

export async function render(app, [key]) {
  const show = app.store.get("shows", key);
  if (!show) {
    return html`<h1>Série introuvable</h1>${alert("info", html`Aucune série sélectionnée.
      <a href="#/bibliotheque">Aller à la bibliothèque</a>`)}`;
  }
  const prefs = loadPrefs(PREFS, DEFAULTS);
  const day = today();
  const all = app.store.episodesOf(key);
  const episodes = prefs.specials ? all : all.filter((ep) => !ep.is_special);
  const seasons = new Map();
  for (const ep of episodes) {
    const s = ep.is_special && ep.season === 0 ? 0 : ep.season;
    if (!seasons.has(s)) seasons.set(s, []);
    seasons.get(s).push(ep);
  }
  const regular = all.filter((ep) => !ep.is_special);
  const seen = regular.filter((ep) => getWatch(app.store, EPISODE, ep.key)).length;
  const time = showTime(app.store, key, day);
  const notes = [time.upcomingEpisodes ? `${time.upcomingEpisodes} épisode(s) pas encore diffusé(s) `
    + "non compté(s)" : "", time.estimated ? `durée inconnue pour ${time.estimated} épisode(s) : `
    + "estimée à la moyenne de la série" : ""].filter(Boolean).join(" · ");
  const details = [tvmazeStatusLabel(show.status), show.network,
    show.premiered ? `Première diffusion : ${fmtDate(show.premiered)}` : "",
    show.is_manual ? "Saisie manuelle" : ""].filter(Boolean);
  const inLibrary = getLibraryItem(app.store, SHOW, key);
  const left = time.remainingEpisodes;
  return html`
  <article class="sheet is-show">
    <div class="sheet-head">
      <div>${poster(show.poster_url, show.title, "lg")}</div>
      <div>
        <h1>${show.title}</h1>
        ${show.original_title && show.original_title !== show.title
          ? html`<p class="caption">Titre original : ${show.original_title}</p>` : ""}
        ${details.length ? html`<p class="meta">${details.join(" · ")}</p>` : ""}
        ${showSummary(show) ? html`<p class="summary-text">${showSummary(show)}</p>` : ""}
        ${show.tmdb_id && !show.is_manual ? await providersBlock(app, "tv", show.tmdb_id) : ""}
        ${regular.length ? html`<p class="caption">${seen}/${regular.length} épisodes vus (hors spéciaux)</p>` : ""}
        ${all.length ? metrics([
          metric("Temps passé", formatDuration(time.spentMinutes), { bordered: true }),
          metric("Temps restant", formatDuration(time.remainingMinutes), { bordered: true,
            delta: `${left} épisode${left > 1 ? "s" : ""} à voir` }),
        ], { label: "Temps passé et restant" }) : ""}
        ${notes ? html`<p class="caption">${notes[0].toUpperCase()}${notes.slice(1)}</p>` : ""}
      </div>
    </div>
    ${nextBlock(app, show, day)}
    ${MATCH_TO_CHECK.includes(show.match_status) ? alert("info", "La correspondance de cette série avec "
      + "TVMaze est à vérifier : corrige-la dans l'app Python (Paramètres, Correspondances à "
      + "vérifier), puis réexporte.") : ""}
    ${inLibrary ? "" : alert("info", "Cette série n'est pas dans ta bibliothèque : choisis un statut pour l'y ajouter.")}
    <div class="sheet-controls">
      ${statusSelect(app.store, SHOW, key, "show-status")}
      <div class="controls-side">
        ${inLibrary ? favoriteCheck(app.store, SHOW, key) : ""}
        ${show.tvmaze_id ? html`<button type="button" class="btn" data-action="show-refresh"
          data-ref="${key}">Mettre à jour</button>` : ""}
      </div>
    </div>
    <h2>Épisodes</h2>
    <p><label class="check"><input type="checkbox" data-change="serie-specials" ${prefs.specials
      ? raw("checked") : ""}> Afficher les épisodes spéciaux</label></p>
    ${seasons.size ? [...seasons].sort((a, b) => (a[0] || 999) - (b[0] || 999))
      .map(([season, list]) => seasonBlock(app.store, show, season, list, day))
      : html`<p>Aucun épisode connu pour cette série.</p>`}
    ${ratingForm(app.store, SHOW, key)}
    ${inLibrary ? removeButton(SHOW, key) : ""}
    ${show.tvmaze_id ? html`<p class="credit">Données de la série :
      ${externalLink(`https://www.tvmaze.com/shows/${show.tvmaze_id}`, "TVMaze")} (CC BY-SA).</p>` : ""}
  </article>`;
}

export const actions = {
  "ep-toggle"(app, el) {
    if (el.checked) markWatched(app.store, EPISODE, el.dataset.ref);
    else unmarkWatched(app.store, EPISODE, el.dataset.ref);
  },
  "serie-specials"(app, el) { savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), specials: el.checked }); },
  "serie-next"(app, el) {
    const ep = app.store.get("episodes", el.dataset.ref);
    markWatched(app.store, EPISODE, el.dataset.ref);
    app.ui.serieLast = { show: ep?.show, ref: el.dataset.ref, text: `${el.dataset.code} marqué vu.` };
    app.announce(app.ui.serieLast.text);
  },
  "serie-undo"(app) {
    if (app.ui.serieLast) unmarkWatched(app.store, EPISODE, app.ui.serieLast.ref);
    app.ui.serieLast = null;
  },
  "season-watch"(app, el) {
    const { specials } = loadPrefs(PREFS, DEFAULTS);
    const n = markSeasonWatched(app.store, el.dataset.show, Number(el.dataset.season), specials);
    app.toast(n ? `${n} épisode(s) marqué(s) vu(s).` : "Rien à cocher (épisodes pas encore sortis ?).");
  },
  "season-unwatch"(app, el) {
    const n = unmarkSeason(app.store, el.dataset.show, Number(el.dataset.season));
    app.toast(`${n} visionnage(s) retiré(s).`);
  },
  "watch-until"(app, el) {
    const { specials } = loadPrefs(PREFS, DEFAULTS);
    const n = markWatchedUntil(app.store, el.dataset.ref, specials);
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
