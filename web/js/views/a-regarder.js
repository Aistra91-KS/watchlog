// Page « À regarder » : épisodes et films non vus, sortis d'abord, puis à venir, puis sans date.

import { fmtDate, fmtRuntime } from "../core/dates.js";
import { EPISODE, MOVIE, STATUSES, STATUS_LABELS, TO_WATCH, WATCHING } from "../core/model.js";
import {
  AVAILABILITY_LABELS, NO_DATE, RELEASED, SORT_DATE, SORT_RECENT, UPCOMING, buildQueue,
} from "../core/queue.js";
import { html, movieHref, options, poster, raw, showHref } from "./dom.js";
import { loadPrefs, savePrefs } from "./prefs.js";
import { watchButton } from "./shared.js";

const PREFS = "queue";
const DEFAULTS = { kind: "all", nextOnly: true, statuses: [WATCHING, TO_WATCH], sort: SORT_DATE };
// Au-delà, la liste est coupée (« Afficher plus ») : une page légère sur téléphone.
const PAGE_SIZE = 60;

function itemRow(store, item) {
  const link = item.kind === EPISODE ? showHref(item.show) : movieHref(item.ref);
  const label = `${item.title}${item.code ? ` ${item.code}` : ""}`;
  return html`<li class="row ${item.kind === MOVIE ? "is-movie" : "is-show"}">
    <a class="row-poster" href="${link}" tabindex="-1" aria-hidden="true">${poster(item.posterUrl, item.title, "sm")}</a>
    <div class="row-main">
      <a class="row-title" href="${link}">${item.title}</a>
      <p class="row-sub">${item.code ? html`<span class="code">${item.code}</span> ` : html`<span class="badge badge-movie">Film</span> `}
        ${item.episodeTitle || ""}</p>
      <p class="row-meta">${item.releaseDate ? fmtDate(item.releaseDate) : "Date inconnue"}
        ${item.runtime ? ` · ${fmtRuntime(item.runtime)}` : ""}
        ${item.status ? ` · ${STATUS_LABELS[item.status]}` : ""}</p>
    </div>
    ${item.availability === RELEASED ? watchButton(store, item.kind, item.ref, label) : ""}
  </li>`;
}

export function render(app) {
  const prefs = loadPrefs(PREFS, DEFAULTS);
  const kinds = prefs.kind === "all" ? [EPISODE, MOVIE] : [prefs.kind];
  const items = buildQueue(app.store, { kinds, statuses: prefs.statuses, nextOnly: prefs.nextOnly,
    sort: prefs.sort });
  const limit = app.ui.queueLimit || PAGE_SIZE;
  const sections = [RELEASED, UPCOMING, NO_DATE].map((availability) => {
    const list = items.filter((i) => i.availability === availability);
    return { availability, list };
  });
  let shown = 0;
  const empty = !app.store.library.size;
  return html`
  <h1>À regarder</h1>
  ${empty ? html`<div class="empty">
    <p>Ta bibliothèque est vide.</p>
    <p><a class="btn" href="#/ajouter">Ajouter une série ou un film</a>
      <a class="btn btn-ghost" href="#/parametres">Importer depuis l'app Python</a></p></div>` : ""}
  <form class="filters" aria-label="Filtres">
    <label class="field"><span>Type</span>
      <select data-change="queue-kind">${options([["all", "Tout"], [EPISODE, "Épisodes"],
        [MOVIE, "Films"]], prefs.kind)}</select></label>
    <label class="field"><span>Ordre</span>
      <select data-change="queue-sort">${options([[SORT_DATE, "Date de sortie"],
        [SORT_RECENT, "Séries vues récemment d'abord"]], prefs.sort)}</select></label>
    <label class="check"><input type="checkbox" data-change="queue-next" ${prefs.nextOnly
      ? raw("checked") : ""}> Prochain épisode seulement</label>
    <fieldset class="chips"><legend>Statuts</legend>
      ${STATUSES.map((s) => html`<label class="chip"><input type="checkbox" value="${s}"
        data-change="queue-status" ${prefs.statuses.includes(s) ? raw("checked") : ""}>
        ${STATUS_LABELS[s]}</label>`)}
    </fieldset>
  </form>
  <p class="summary" role="status">${items.length} élément${items.length > 1 ? "s" : ""}</p>
  ${sections.filter((s) => s.list.length).map(({ availability, list }) => {
    const visible = list.slice(0, Math.max(0, limit - shown));
    shown += visible.length;
    return html`<section class="queue-section">
      <h2>${AVAILABILITY_LABELS[availability]} <span class="count">(${list.length})</span></h2>
      <ul class="rows">${visible.map((item) => itemRow(app.store, item))}</ul>
      ${visible.length < list.length ? html`<p class="more">${list.length - visible.length} de plus…</p>` : ""}
    </section>`;
  })}
  ${items.length > limit ? html`<button type="button" class="btn btn-ghost" data-action="queue-more">
    Afficher plus</button>` : ""}`;
}

function update(change) {
  savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), ...change });
}

export const actions = {
  "queue-kind"(app, el) { update({ kind: el.value }); },
  "queue-sort"(app, el) { update({ sort: el.value }); },
  "queue-next"(app, el) { update({ nextOnly: el.checked }); },
  "queue-status"(app, el) {
    const boxes = el.closest("fieldset").querySelectorAll("input:checked");
    update({ statuses: [...boxes].map((b) => b.value) });
  },
  "queue-more"(app) { app.ui.queueLimit = (app.ui.queueLimit || PAGE_SIZE) + PAGE_SIZE; },
};
