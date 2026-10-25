// Page « À regarder » : épisodes et films non vus, sortis d'abord, puis à venir, puis sans date.

import { fmtDate, fmtRuntime } from "../core/dates.js";
import { EPISODE, MOVIE, STATUSES, STATUS_LABELS, TO_WATCH, WATCHING } from "../core/model.js";
import {
  AVAILABILITY_LABELS, NO_DATE, RELEASED, SORT_DATE, SORT_RECENT, UPCOMING, buildQueue,
  checkPlatforms, filterOnPlatforms, titleKey,
} from "../core/queue.js";
import { html, movieHref, options, poster, raw, showHref } from "./dom.js";
import { loadPrefs, savePrefs } from "./prefs.js";
import { watchButton } from "./shared.js";

const PREFS = "queue";
const DEFAULTS = { kind: "all", nextOnly: true, statuses: [WATCHING, TO_WATCH], sort: SORT_DATE,
  mine: false };
// Au-delà, la liste est coupée (« Afficher plus ») : une page légère sur téléphone.
const PAGE_SIZE = 60;
// Plateformes d'un titre gardées une heure entre deux affichages (comme le logiciel)
const PLATFORMS_TTL_MS = 3_600_000;
// Titre dont TMDB n'a pas répondu : relu au plus tôt une minute après (jamais d'ancienne valeur
// à la place d'une panne, sans relancer la recherche à chaque affichage).
const PLATFORMS_RETRY_MS = 60_000;

// --- Filtre « Sur mes plateformes » ----------------------------------------------------

/** {clé du titre: {at, names} | {at, noId} | {at, failed}}, en mémoire pour la session. */
function platformCache(app) {
  app.ui.platformCache ??= new Map();
  return app.ui.platformCache;
}

function isFresh(entry, now) {
  if (!entry) return false;
  return now - entry.at < (entry.failed ? PLATFORMS_RETRY_MS : PLATFORMS_TTL_MS);
}

/** Lance la recherche des plateformes des titres pas encore connus, puis réaffiche la page. */
function startPlatformCheck(app, missing) {
  if (app.ui.platformLoading) return;
  app.ui.platformLoading = { done: 0, total: new Set(missing.map(titleKey)).size };
  const cache = platformCache(app);
  checkPlatforms(app.store, app.tmdb, missing, (done, total) => {
    Object.assign(app.ui.platformLoading, { done, total });
    const box = typeof document !== "undefined" && document.getElementById("aq-platforms-progress");
    if (box) box.textContent = `Recherche des plateformes de chaque titre… ${done}/${total}`;
  }).then((check) => {
    const at = Date.now();
    for (const [key, names] of check.streaming) cache.set(key, { at, names });
    for (const [key, title] of check.noId) cache.set(key, { at, noId: title });
    for (const [key, title] of check.failed) cache.set(key, { at, failed: title });
  }).catch((error) => {
    console.error("Plateformes des titres", error);
    const at = Date.now();
    for (const item of missing) cache.set(titleKey(item), { at, failed: item.title });
  }).finally(() => {
    app.ui.platformLoading = null;
    app.save?.();            // ids TMDB de séries trouvés via TVDB : gardés
    app.rerender?.();
  });
}

/** Filtre la liste ; null tant que les plateformes de certains titres sont en cours de lecture. */
function platformFilter(app, items, platforms) {
  const cache = platformCache(app);
  const now = Date.now();
  const missing = items.filter((item) => !isFresh(cache.get(titleKey(item)), now));
  if (missing.length) {
    startPlatformCheck(app, missing);
    return null;
  }
  const check = { streaming: new Map(), noId: new Map(), failed: new Map() };
  for (const item of items) {
    const key = titleKey(item);
    const entry = cache.get(key);
    if (entry.names) check.streaming.set(key, entry.names);
    else if (entry.noId) check.noId.set(key, entry.noId);
    else check.failed.set(key, entry.failed);
  }
  return filterOnPlatforms(items, check, platforms);
}

/** Sous la liste : titres masqués parce qu'on ne connaît pas leurs plateformes. */
function hiddenNotice(result) {
  if (!result?.hidden) return "";
  const details = [];
  if (result.noId.length) details.push(`pas d'identifiant TMDB : ${result.noId.join(", ")}`);
  if (result.failed.length) details.push(`TMDB n'a pas répondu : ${result.failed.join(", ")}`);
  const s = result.hidden > 1 ? "s" : "";
  return html`<p class="warning">${result.hidden} titre${s} masqué${s} faute d'information sur les
    plateformes (${details.join(" ; ")}). Décoche « Sur mes plateformes » pour les voir.</p>`;
}

/** Message si le filtre ne peut pas s'appliquer (TMDB non connecté) ; sinon "". */
function tmdbProblem(app) {
  if (!app.tmdb?.configured) return "TMDB non connecté : saisis ta clé dans Paramètres.";
  if (app.tmdb.connected === false) return "TMDB : clé refusée, vérifie-la dans Paramètres.";
  return "";
}

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
  let items = buildQueue(app.store, { kinds, statuses: prefs.statuses, nextOnly: prefs.nextOnly,
    sort: prefs.sort });
  const platforms = app.store.setting("my_platforms", []);
  const onMine = prefs.mine && platforms.length > 0;
  let platformResult = null;
  let platformWarning = "";
  let loading = false;
  if (onMine && items.length) {
    const problem = tmdbProblem(app);
    if (problem) {
      platformWarning = `${problem} Le filtre « Sur mes plateformes » n'est pas appliqué : `
        + "toute la liste est affichée.";
    } else {
      platformResult = platformFilter(app, items, platforms);
      if (platformResult) items = platformResult.items;
      else loading = true;
    }
  }
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
    <div>
      <label class="check"><input type="checkbox" data-change="queue-mine"
        aria-describedby="aq-mine-help" ${onMine ? raw("checked") : ""}
        ${platforms.length ? "" : raw("disabled")}> Sur mes plateformes</label>
      <p class="muted small" id="aq-mine-help">${platforms.length
        ? `Seulement les titres disponibles sur ${platforms.join(", ")} (abonnement ou gratuit, en France).`
        : html`Choisis d'abord tes plateformes dans <a href="#/parametres">Paramètres</a>.`}</p>
    </div>
    <fieldset class="chips"><legend>Statuts</legend>
      ${STATUSES.map((s) => html`<label class="chip"><input type="checkbox" value="${s}"
        data-change="queue-status" ${prefs.statuses.includes(s) ? raw("checked") : ""}>
        ${STATUS_LABELS[s]}</label>`)}
    </fieldset>
  </form>
  ${platformWarning ? html`<p class="warning">${platformWarning}</p>` : ""}
  ${loading ? html`<p class="sync-status" id="aq-platforms-progress" aria-live="off">Recherche des
    plateformes de chaque titre… ${app.ui.platformLoading?.total
      ? `${app.ui.platformLoading.done}/${app.ui.platformLoading.total}` : ""}</p>` : html`
  <p class="summary" data-announce>${items.length} élément${items.length > 1 ? "s" : ""}</p>
  ${sections.filter((s) => s.list.length).map(({ availability, list }) => {
    const visible = list.slice(0, Math.max(0, limit - shown));
    shown += visible.length;
    return html`<section class="queue-section">
      <h2>${AVAILABILITY_LABELS[availability]} <span class="count">(${list.length})</span></h2>
      <ul class="rows" role="list">${visible.map((item) => itemRow(app.store, item))}</ul>
      ${visible.length < list.length ? html`<p class="more">${list.length - visible.length} de plus…</p>` : ""}
    </section>`;
  })}
  ${items.length > limit ? html`<button type="button" class="btn btn-ghost" data-action="queue-more">
    Afficher plus</button>` : ""}
  ${hiddenNotice(platformResult)}`}`;
}

function update(change) {
  savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), ...change });
}

export const actions = {
  "queue-kind"(app, el) { update({ kind: el.value }); },
  "queue-sort"(app, el) { update({ sort: el.value }); },
  "queue-next"(app, el) { update({ nextOnly: el.checked }); },
  "queue-mine"(app, el) { update({ mine: el.checked }); },
  "queue-status"(app, el) {
    const boxes = el.closest("fieldset").querySelectorAll("input:checked");
    update({ statuses: [...boxes].map((b) => b.value) });
  },
  "queue-more"(app) { app.ui.queueLimit = (app.ui.queueLimit || PAGE_SIZE) + PAGE_SIZE; },
};
