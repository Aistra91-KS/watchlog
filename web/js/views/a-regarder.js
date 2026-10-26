// Page « À regarder » : épisodes et films non vus, sortis d'abord, puis à venir, puis sans date.
// Même présentation que la page Python (a_regarder.py) : filtres, compteurs, cartes.

import { daysBetween, fmtDate, fmtRuntime, today } from "../core/dates.js";
import { markWatched, markWatchedUntil, unmarkWatched } from "../core/library.js";
import {
  EPISODE, MATCH_TO_CHECK, MOVIE, SHOW, STATUSES, STATUS_LABELS, TO_WATCH, WATCHING, normalize,
  refKey,
} from "../core/model.js";
import {
  AVAILABILITY_LABELS, NO_DATE, RELEASED, SORT_DATE, SORT_RECENT, UPCOMING, buildQueue,
  checkPlatforms, filterOnPlatforms, titleKey,
} from "../core/queue.js";
import { html, movieHref, options, poster, raw, showHref } from "./dom.js";
import { icon } from "./icons.js";
import { loadPrefs, savePrefs } from "./prefs.js";
import { alert, badge, helpTip, metric, metrics } from "./ui.js";

const PREFS = "queue";
const DEFAULTS = { kind: "all", nextOnly: true, statuses: [WATCHING, TO_WATCH], sort: SORT_DATE,
  mine: false, show: "", specials: false, availabilities: [RELEASED, UPCOMING, NO_DATE] };
// Au-delà, la liste est coupée (« Afficher plus ») : une page légère sur téléphone.
const PAGE_SIZE = 60;
// Plateformes d'un titre gardées une heure entre deux affichages (comme le logiciel)
const PLATFORMS_TTL_MS = 3_600_000;
// Titre dont TMDB n'a pas répondu : relu au plus tôt une minute après (jamais d'ancienne valeur
// à la place d'une panne, sans relancer la recherche à chaque affichage).
const PLATFORMS_RETRY_MS = 60_000;
// Pastille « Nouveau » : sorti depuis moins de NEW_DAYS jours
const NEW_DAYS = 7;
// Titres des sections, dans l'ordre de tri de buildQueue (icônes de l'app Python)
const SECTION_TITLES = { [RELEASED]: ["play_circle", "Disponibles"],
  [UPCOMING]: ["event_upcoming", "À venir"], [NO_DATE]: ["help", "Sans date"] };

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
  return alert("info", html`${icon("visibility_off")} ${result.hidden} titre${s} masqué${s} faute
    d'information sur les plateformes (${details.join(" ; ")}). Décoche « Sur mes plateformes » pour
    les voir.`);
}

/** Message si le filtre ne peut pas s'appliquer (TMDB non connecté) ; sinon "". */
function tmdbProblem(app) {
  if (!app.tmdb?.configured) return "TMDB non connecté : saisis ta clé dans Paramètres.";
  if (app.tmdb.connected === false) return "TMDB : clé refusée, vérifie-la dans Paramètres.";
  return "";
}

/** « Aujourd'hui », « Hier », « Dans 3 jours »... au-delà d'une semaine, la date. */
export function relativeDay(day, todayDay) {
  if (!day) return "Sans date";
  const delta = daysBetween(todayDay, day);
  const names = { 0: "Aujourd'hui", [-1]: "Hier", 1: "Demain" };
  if (delta in names) return names[delta];
  if (delta > 1 && delta <= 7) return `Dans ${delta} jours`;
  if (delta < -1 && delta >= -7) return `Il y a ${-delta} jours`;
  return fmtDate(day);
}

function itemRow(item, day) {
  const link = item.kind === EPISODE ? showHref(item.show) : movieHref(item.ref);
  const label = `${item.title}${item.code ? ` ${item.code}` : ""}`;
  const released = item.availability === RELEASED;
  const fresh = released && item.releaseDate && daysBetween(item.releaseDate, day) < NEW_DAYS;
  return html`<li class="row ${item.kind === MOVIE ? "is-movie" : "is-show"} ${released ? ""
    : "is-upcoming"}">
    <a class="row-poster" href="${link}" tabindex="-1" aria-hidden="true">${poster(item.posterUrl,
      item.title, "sm")}</a>
    <div class="row-main">
      <a class="row-title" href="${link}">${item.title}</a>
      <p class="row-sub">${item.kind === EPISODE ? badge(item.code, "blue") : badge("Film", "orange")}
        ${fresh ? badge("Nouveau", "green", "fiber_new") : ""}
        ${item.episodeTitle ? html`<span class="${released ? "" : "muted"}">${item.episodeTitle}</span>`
          : ""}</p>
      <p class="row-meta">${icon("calendar_today")} ${relativeDay(item.releaseDate, day)}${item.runtime
        ? html` · ${icon("schedule")} ${fmtRuntime(item.runtime)}` : ""}</p>
    </div>
    ${released ? html`<div class="row-actions">
      <button type="button" class="btn" data-action="aq-seen" data-kind="${item.kind}"
        data-ref="${item.ref}" data-label="${label}" aria-label="${`Vu : ${label}`}">
        ${icon("check")} Vu</button>
      ${item.kind === EPISODE ? html`<button type="button" class="btn" data-action="aq-until"
        data-ref="${item.ref}" data-label="${label}" title="Marque vus tous les épisodes précédents"
        aria-label="${`Vu jusqu'ici : tous les épisodes jusqu'à ${label}`}">${icon("done_all")}
        Vu jusqu'ici</button>` : ""}
    </div>` : ""}
  </li>`;
}

/** Compteurs en tête de liste : ce qui est dispo, ce qui arrive, le temps à rattraper. */
function summary(items) {
  const released = items.filter((i) => i.availability === RELEASED);
  const episodes = released.filter((i) => i.kind === EPISODE).length;
  const minutes = released.reduce((sum, i) => sum + (i.runtime || 0), 0);
  return metrics([
    metric("Disponibles", released.length, { bordered: true }),
    metric("Épisodes / films dispo", `${episodes} / ${released.length - episodes}`, { bordered: true }),
    metric("À venir", items.length - released.length, { bordered: true }),
    metric("Temps à rattraper", fmtRuntime(minutes) || "—", { bordered: true }),
  ], { label: "Résumé de la liste" });
}

export function render(app) {
  const prefs = loadPrefs(PREFS, DEFAULTS);
  const kinds = prefs.kind === "all" ? [EPISODE, MOVIE] : [prefs.kind];
  const day = today();
  let items = buildQueue(app.store, { kinds, statuses: prefs.statuses, nextOnly: prefs.nextOnly,
    availabilities: prefs.availabilities, includeSpecials: prefs.specials, sort: prefs.sort,
    today: day });
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
  // Choix d'une série : seulement celles présentes dans la liste.
  const shows = new Map(items.filter((i) => i.kind === EPISODE).map((i) => [i.show, i.title]));
  const showChoice = prefs.kind !== MOVIE && shows.has(prefs.show) ? prefs.show : "";
  if (showChoice) items = items.filter((i) => i.show === showChoice);
  const showPairs = [["", "Toutes les séries"], ...[...shows].sort((a, b) => normalize(a[1])
    .localeCompare(normalize(b[1])))];
  const toCheck = [...app.store.shows.values()].filter((s) => MATCH_TO_CHECK.includes(s.match_status)
    && app.store.get("library", refKey(SHOW, s.key))).length;
  const limit = app.ui.queueLimit || PAGE_SIZE;
  let shown = 0;
  const empty = !app.store.library.size;
  const last = app.ui.queueLastAction;
  const chips = (legend, change, pairs, chosen) => html`<fieldset class="chips"><legend>${legend}</legend>
    ${pairs.map(([v, label]) => html`<label class="chip"><input type="checkbox" value="${v}"
      data-change="${change}" ${chosen.includes(v) ? raw("checked") : ""}> ${label}</label>`)}
  </fieldset>`;
  return html`
  <h1>À regarder</h1>
  ${empty ? html`<div class="empty">
    <p>Ta bibliothèque est vide.</p>
    <p class="actions"><a class="btn" href="#/ajouter">Ajouter une série ou un film</a>
      <a class="btn" href="#/parametres">Importer depuis l'app Python</a></p></div>` : ""}
  <form class="cols" style="--cols: 1fr 2fr 2fr 2fr" aria-label="Filtres">
    <label class="field"><span>Type</span>
      <select data-change="queue-pref" name="kind">${options([["all", "Tout"], [EPISODE, "Épisodes"],
        [MOVIE, "Films"]], prefs.kind)}</select></label>
    <label class="field"><span>Série</span>
      <select data-change="queue-pref" name="show" ${prefs.kind === MOVIE ? raw("disabled") : ""}>${options(
        showPairs, showChoice)}</select></label>
    ${chips("Disponibilité", "queue-availability", [RELEASED, UPCOMING, NO_DATE]
      .map((a) => [a, AVAILABILITY_LABELS[a]]), prefs.availabilities)}
    ${chips("Statut", "queue-status", STATUSES.map((st) => [st, STATUS_LABELS[st]]), prefs.statuses)}
  </form>
  <form class="cols" style="--cols: 2fr 2fr 2fr 2fr" aria-label="Options">
    <label class="check"><input type="checkbox" data-change="queue-flag" name="nextOnly" ${prefs.nextOnly
      ? raw("checked") : ""}> Par série : prochain épisode seulement</label>
    <label class="check"><input type="checkbox" data-change="queue-flag" name="specials" ${prefs.specials
      ? raw("checked") : ""}> Inclure les épisodes spéciaux</label>
    <span class="check"><label class="check"><input type="checkbox" data-change="queue-flag" name="mine"
      ${onMine ? raw("checked") : ""} ${platforms.length ? "" : raw("disabled")}> Sur mes plateformes</label>
      ${helpTip(platforms.length ? `Seulement les titres disponibles sur ${platforms.join(", ")} `
        + "(abonnement ou gratuit, en France)."
        : "Choisis d'abord tes plateformes dans Paramètres > Mes plateformes.")}</span>
    <label class="field"><span>Trier par</span>
      <select data-change="queue-pref" name="sort">${options([[SORT_DATE, "Date de sortie"],
        [SORT_RECENT, "Dernier visionnage"]], prefs.sort)}</select></label>
  </form>
  ${toCheck ? html`<p class="caption">${toCheck} série${toCheck > 1 ? "s" : ""} à vérifier
    (correspondance TVMaze) : à corriger dans l'app Python (Paramètres), puis réexporter.</p>` : ""}
  ${last ? html`<p class="caption last-action">${last.text}
    ${last.undo ? html`<button type="button" class="btn" data-action="aq-undo">Annuler</button>` : ""}</p>`
    : ""}
  ${platformWarning ? alert("warning", platformWarning) : ""}
  ${loading ? html`<div class="progress" aria-live="off"><p id="aq-platforms-progress">Recherche des
    plateformes de chaque titre… ${app.ui.platformLoading?.total
      ? `${app.ui.platformLoading.done}/${app.ui.platformLoading.total}` : ""}</p></div>` : html`
  ${!prefs.statuses.length || !prefs.availabilities.length
    ? alert("info", "Choisis au moins un statut et une disponibilité.")
    : !items.length ? alert("info", "Rien à regarder avec ces filtres.") : html`
  ${summary(items)}
  <p class="sr-only" data-announce>${items.length} élément${items.length > 1 ? "s" : ""}</p>
  ${[RELEASED, UPCOMING, NO_DATE].map((availability) => {
    const list = items.filter((i) => i.availability === availability);
    if (!list.length) return "";
    const visible = list.slice(0, Math.max(0, limit - shown));
    shown += visible.length;
    const [iconName, title] = SECTION_TITLES[availability];
    return html`<section class="queue-section">
      <h2 class="section-title">${icon(iconName)} ${title} <span class="count">(${list.length})</span></h2>
      <ul class="rows" role="list">${visible.map((item) => itemRow(item, day))}</ul>
      ${visible.length < list.length ? html`<p class="more">${list.length - visible.length} de plus…</p>` : ""}
    </section>`;
  })}
  ${items.length > limit ? html`<button type="button" class="btn" data-action="queue-more">
    Afficher plus</button>` : ""}`}
  ${hiddenNotice(platformResult)}`}`;
}

function update(change) {
  savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), ...change });
}

const checked = (el) => [...el.closest("fieldset").querySelectorAll("input:checked")].map((b) => b.value);

export const actions = {
  "queue-pref"(app, el) { update({ [el.name]: el.value }); },
  "queue-flag"(app, el) { update({ [el.name]: el.checked }); },
  "queue-status"(app, el) { update({ statuses: checked(el) }); },
  "queue-availability"(app, el) { update({ availabilities: checked(el) }); },
  "queue-more"(app) { app.ui.queueLimit = (app.ui.queueLimit || PAGE_SIZE) + PAGE_SIZE; },
  "aq-seen"(app, el) {
    markWatched(app.store, el.dataset.kind, el.dataset.ref);
    app.ui.queueLastAction = { text: `« ${el.dataset.label} » marqué vu.`,
      undo: [el.dataset.kind, el.dataset.ref] };
    app.announce(app.ui.queueLastAction.text);
  },
  "aq-until"(app, el) {
    const { specials } = loadPrefs(PREFS, DEFAULTS);
    const n = markWatchedUntil(app.store, el.dataset.ref, specials);
    const s = n > 1 ? "s" : "";
    app.ui.queueLastAction = { text: `${n} épisode${s} marqué${s} vu${s} jusqu'à ${el.dataset.label}.`,
      undo: null };
    app.announce(app.ui.queueLastAction.text);
  },
  "aq-undo"(app) {
    const [kind, ref] = app.ui.queueLastAction?.undo || [];
    if (kind) unmarkWatched(app.store, kind, ref);
    app.ui.queueLastAction = null;
  },
};
