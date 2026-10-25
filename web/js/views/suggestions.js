// Page « Suggestions » : moteur local (suggestions.js) sur les données TMDB. Le résultat est
// gardé dans ce navigateur jusqu'à « Recalculer » (le premier calcul prend une à deux minutes,
// les suivants quelques secondes grâce au cache).

import { addMovie, addSuggestedShow } from "../core/adding.js";
import { fmtDate, isoUtc } from "../core/dates.js";
import { MOVIE, SHOW } from "../core/model.js";
import { REGION, parseProviders, streamingNames } from "../core/providers.js";
import {
  SORT_RATING, SORT_RECENT, SORT_RELEVANCE, TMDB_MEDIA, computeAll, dismiss, filterItems,
  undismissAll,
} from "../core/suggestions.js";
import { setPreferences } from "../core/taste.js";
import { html, options, poster, raw } from "./dom.js";
import { loadPrefs, savePrefs } from "./prefs.js";

const SAVED = "suggestions_result";   // réglage (IndexedDB) : dernier résultat, sans les traits
const PREFS = "suggestions";
const DEFAULTS = { kind: "", genre: "", theme: "", sort: SORT_RELEVANCE, mine: false, minVote: 0 };
const PAGE_SIZE = 30;

function saved(app) {
  return app.ui.suggestions || app.store.setting(SAVED, null);
}

/** Résultat gardé : sans les traits (volumineux), avec les thèmes du profil pour les réglages. */
function toSaved(result) {
  const labels = new Map();
  for (const s of result.items) for (const [f, label] of Object.entries(s.features)) labels.set(f, label);
  return {
    computedAt: result.computedAt, errors: result.errors,
    items: result.items.map(({ features, ...rest }) => rest),
    top: result.profile.top(15, true).map(([f, label]) => [f, label]),
    themes: [...new Map([...result.profile.top(80, true).map(([f, l]) => [f, l]), ...labels])]
      .filter(([f]) => f.startsWith("k:")).sort((a, b) => a[1].localeCompare(b[1], "fr")),
  };
}

function card(app, s) {
  const mine = new Set(app.store.setting("my_platforms", []));
  return html`<li class="row ${s.kind === MOVIE ? "is-movie" : "is-show"} suggestion">
    ${poster(s.posterUrl, s.title, "sm")}
    <div class="row-main">
      <p class="row-title">${s.title} <span class="muted">${s.year || ""}</span></p>
      <p class="row-sub"><span class="badge ${s.kind === MOVIE ? "badge-movie" : "badge-show"}">${s.kind === MOVIE
        ? "Film" : "Série"}</span> <span class="match">${s.match} % pour toi</span>
        ${s.trending ? html`<span class="tag">Tendance de la semaine</span>` : ""}
        <span class="muted">· TMDB ${s.voteAverage.toFixed(1)}</span></p>
      ${s.because.length ? html`<p class="reasons">Parce que tu as aimé ${s.because.join(", ")}</p>` : ""}
      ${s.matchedThemes.length ? html`<p class="reasons">Thèmes en commun : ${s.matchedThemes.join(", ")}</p>` : ""}
      ${s.streaming?.length ? html`<p class="reasons">Sur ${s.streaming.map((p) => (mine.has(p)
        ? html`<strong>${p}</strong>` : p)).reduce((a, b) => html`${a}, ${b}`)}</p>` : ""}
      ${s.overview ? html`<details><summary>Résumé</summary><p>${s.overview}</p></details>` : ""}
      <div class="row-actions">
        <button type="button" class="btn" data-action="sugg-add" data-kind="${s.kind}" data-id="${s.tmdbId}"
          data-title="${s.title}">Ajouter à « À voir »</button>
        <button type="button" class="btn btn-ghost" data-action="sugg-dismiss" data-kind="${s.kind}"
          data-id="${s.tmdbId}" data-title="${s.title}">Pas intéressé</button>
      </div>
    </div>
  </li>`;
}

function preferencesForm(app, result) {
  const current = [...app.store.themes.values()];
  const more = new Set(current.filter((t) => t.weight > 0).map((t) => t.feature));
  const less = new Set(current.filter((t) => t.weight < 0).map((t) => t.feature));
  const choices = new Map(result.themes);
  for (const t of current) if (!choices.has(t.feature)) choices.set(t.feature, t.label);
  for (const g of ["Animation", "Familial", "Comédie", "Horreur", "Documentaire", "Romance"]) {
    choices.set(`g:${g}`, `${g} (genre)`);
  }
  const list = [...choices].sort((a, b) => a[1].localeCompare(b[1], "fr"));
  const select = (name, chosen) => html`<select name="${name}" multiple size="6">${list.map(([f, label]) =>
    html`<option value="${f}" ${chosen.has(f) ? raw("selected") : ""}>${label}</option>`)}</select>`;
  return html`<details class="prefs"><summary>Ton profil de goûts et tes réglages</summary>
    <p>Thèmes les plus marquants de ta bibliothèque : ${result.top.map(([, label]) =>
      html`<span class="tag">${label}</span>`)}</p>
    <form class="inline-form" data-submit="sugg-prefs">
      <label class="field"><span>Plus de…</span>${select("more", more)}</label>
      <label class="field"><span>Moins de…</span>${select("less", less)}</label>
      <button class="btn">Enregistrer et recalculer</button>
    </form>
    <p class="muted small">Ctrl / Cmd + clic pour en choisir plusieurs. « Plus de » explore
      aussi ce thème ; « Moins de » divise le score des titres concernés.</p>
    ${app.store.dismissed.size ? html`<p>${app.store.dismissed.size} titre(s) écarté(s).
      <button type="button" class="btn btn-ghost" data-action="sugg-undismiss">Tout remettre</button></p>` : ""}
  </details>`;
}

export function render(app) {
  if (!app.tmdb.configured) {
    return html`<h1>Suggestions</h1><p class="warning">Les suggestions s'appuient sur TMDB :
      <a href="#/parametres">ajoute ta clé TMDB dans Paramètres</a>.</p>`;
  }
  const running = app.ui.suggRunning;
  const result = saved(app);
  const prefs = loadPrefs(PREFS, DEFAULTS);
  const intro = html`<p class="muted">Calculées sur ton appareil à partir de ta bibliothèque :
    ce que regardent les gens qui ont aimé les mêmes titres (TMDB), les thèmes qui reviennent
    chez toi (dystopie, super-héros…) et la note des titres.</p>`;
  if (running) {
    return html`<h1>Suggestions</h1>${intro}<p role="status" class="sync-status" id="sugg-progress">
      ${running.label || "Calcul…"} ${running.total ? `${running.done}/${running.total}` : ""}</p>
      <p class="muted">Le premier calcul prend une à deux minutes ; les suivants quelques secondes.</p>`;
  }
  if (!result) {
    return html`<h1>Suggestions</h1>${intro}
      <p><button type="button" class="btn" data-action="sugg-compute">Calculer mes suggestions</button></p>`;
  }
  const items = filterItems(result.items, {
    kinds: prefs.kind ? [prefs.kind] : [SHOW, MOVIE], genres: prefs.genre ? [prefs.genre] : [],
    themes: prefs.theme ? [prefs.theme] : [], sort: prefs.sort, minVote: Number(prefs.minVote) || 0,
    platforms: prefs.mine ? app.store.setting("my_platforms", []) : [],
  });
  const genres = [...new Set(result.items.flatMap((s) => s.genres))].sort((a, b) => a.localeCompare(b, "fr"));
  const themes = [...new Set(result.items.flatMap((s) => s.themes))].sort((a, b) => a.localeCompare(b, "fr"));
  const limit = app.ui.suggLimit || PAGE_SIZE;
  const needsPlatforms = prefs.mine && result.items.some((s) => !s.streaming);
  return html`
  <h1>Suggestions</h1>
  <p class="muted">Calculées le ${fmtDate(result.computedAt)}.
    <button type="button" class="btn btn-ghost" data-action="sugg-compute">Recalculer</button></p>
  ${result.errors.map((e) => html`<p class="warning">${e}</p>`)}
  ${preferencesForm(app, result)}
  <form class="filters" aria-label="Filtrer les suggestions">
    <label class="field"><span>Type</span><select data-change="sugg-pref" name="kind">${options(
      [["", "Tout"], [SHOW, "Séries"], [MOVIE, "Films"]], prefs.kind)}</select></label>
    <label class="field"><span>Genre</span><select data-change="sugg-pref" name="genre">${options(
      [["", "Tous"], ...genres.map((g) => [g, g])], prefs.genre)}</select></label>
    <label class="field"><span>Thème</span><select data-change="sugg-pref" name="theme">${options(
      [["", "Tous"], ...themes.map((t) => [t, t])], prefs.theme)}</select></label>
    <label class="field"><span>Note TMDB min.</span><select data-change="sugg-pref" name="minVote">${options(
      [["0", "Toutes"], ["6", "6+"], ["7", "7+"], ["8", "8+"]], String(prefs.minVote))}</select></label>
    <label class="field"><span>Tri</span><select data-change="sugg-pref" name="sort">${options(
      [[SORT_RELEVANCE, "Pertinence"], [SORT_RATING, "Note TMDB"], [SORT_RECENT, "Plus récents"]],
      prefs.sort)}</select></label>
    <label class="check"><input type="checkbox" data-change="sugg-mine" ${prefs.mine ? raw("checked") : ""}
      ${app.store.setting("my_platforms", []).length ? "" : raw("disabled")}> Sur mes plateformes</label>
  </form>
  ${needsPlatforms ? html`<p class="warning">Plateformes pas encore lues pour ces suggestions.
    <button type="button" class="btn" data-action="sugg-platforms">Lire les plateformes</button></p>` : ""}
  <p class="summary" role="status">${items.length} suggestion${items.length > 1 ? "s" : ""}</p>
  <ul class="rows">${items.slice(0, limit).map((s) => card(app, s))}</ul>
  ${items.length > limit ? html`<button type="button" class="btn btn-ghost" data-action="sugg-more">
    Afficher plus</button>` : ""}
  <p class="credit">Données TMDB ; disponibilités JustWatch (via TMDB).</p>`;
}

async function compute(app) {
  app.ui.suggRunning = { done: 0, total: 0, label: "Préparation…" };
  await app.rerender();
  let lastPaint = 0;
  try {
    const result = await computeAll(app.store, app.tmdb, {
      progress: (done, total, label) => {
        Object.assign(app.ui.suggRunning, { done, total, label });
        const now = Date.now();
        const box = document.getElementById("sugg-progress");
        if (box && now - lastPaint > 150) {
          box.textContent = `${label} ${total ? `${done}/${total}` : ""}`;
          lastPaint = now;
        }
      },
    });
    app.ui.suggestions = toSaved(result);
    app.store.setSetting(SAVED, app.ui.suggestions);
    app.ui.suggLimit = PAGE_SIZE;
  } finally {
    app.ui.suggRunning = null;
  }
}

function updateItems(app, change) {
  const result = saved(app);
  app.ui.suggestions = { ...result, items: change(result.items) };
  app.store.setSetting(SAVED, app.ui.suggestions);
}

export const actions = {
  async "sugg-compute"(app) { await compute(app); },
  "sugg-pref"(app, el) { savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), [el.name]: el.value }); },
  "sugg-mine"(app, el) { savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), mine: el.checked }); },
  "sugg-more"(app) { app.ui.suggLimit = (app.ui.suggLimit || PAGE_SIZE) + PAGE_SIZE; },
  async "sugg-platforms"(app, el) {
    el.disabled = true;
    let failed = 0;
    const items = saved(app).items;
    const updated = [];
    for (const [i, s] of items.entries()) {
      el.textContent = `Lecture… ${i + 1}/${items.length}`;
      if (s.streaming) { updated.push(s); continue; }
      try {
        const data = parseProviders((await app.tmdb.watchProviders(TMDB_MEDIA[s.kind], s.tmdbId))[REGION] || {});
        updated.push({ ...s, streaming: streamingNames(data) });
      } catch (error) {
        console.warn(`Plateformes non lues pour ${s.title}`, error);
        failed += 1;
        updated.push(s);
      }
    }
    updateItems(app, () => updated);
    if (failed) app.toast(`Plateformes inconnues pour ${failed} titre(s) : ils restent masqués.`, "error");
  },
  "sugg-dismiss"(app, el) {
    dismiss(app.store, el.dataset.kind, Number(el.dataset.id), el.dataset.title);
    updateItems(app, (items) => items.filter((s) => !(s.kind === el.dataset.kind
      && s.tmdbId === Number(el.dataset.id))));
    app.toast(`« ${el.dataset.title} » ne sera plus suggéré.`);
  },
  async "sugg-add"(app, el) {
    el.disabled = true;
    el.textContent = "Ajout…";
    const id = Number(el.dataset.id);
    if (el.dataset.kind === SHOW) {
      await addSuggestedShow(app.store, app.tvmaze, app.tmdb, id, { title: el.dataset.title });
    } else {
      await addMovie(app.store, app.tmdb, id);
    }
    updateItems(app, (items) => items.filter((s) => !(s.kind === el.dataset.kind && s.tmdbId === id)));
    app.toast(`« ${el.dataset.title} » ajouté à « À voir ».`);
  },
  "sugg-undismiss"(app) {
    const n = undismissAll(app.store);
    app.toast(`${n} titre(s) remis dans les suggestions (au prochain calcul).`);
  },
  async "sugg-prefs"(app, form) {
    const labels = new Map(saved(app).themes);
    for (const opt of form.querySelectorAll("option")) labels.set(opt.value, opt.textContent.replace(" (genre)", ""));
    const pick = (name) => new Map(new FormData(form).getAll(name).map((f) => [f, labels.get(f) || f]));
    setPreferences(app.store, pick("more"), pick("less"), isoUtc());
    await compute(app);
  },
};
