// Page « Suggestions » : moteur local (suggestions.js) sur les données TMDB. Le résultat est
// gardé dans ce navigateur jusqu'à « Recalculer » (le premier calcul prend une à deux minutes,
// les suivants quelques secondes grâce au cache).

import { addMovie, addSuggestedShow } from "../core/adding.js";
import { fmtDate, isoUtc } from "../core/dates.js";
import { MOVIE, SHOW, STATUSES, STATUS_LABELS, TO_WATCH } from "../core/model.js";
import { REGION, parseProviders, streamingNames } from "../core/providers.js";
import {
  SORT_RATING, SORT_RECENT, SORT_RELEVANCE, TMDB_MEDIA, computeAll, dismiss, filterItems,
  undismissAll,
} from "../core/suggestions.js";
import { setPreferences } from "../core/taste.js";
import { html, options, poster, raw } from "./dom.js";
import { loadPrefs, savePrefs } from "./prefs.js";
import { alert, badge, expander, helpTip, segmented } from "./ui.js";

const SAVED = "suggestions_result";   // réglage (IndexedDB) : dernier résultat, sans les traits
const PREFS = "suggestions";
const DEFAULTS = { kind: "", genre: "", theme: "", sort: SORT_RELEVANCE, mine: false, minVote: 0 };
const PAGE_SIZE = 15;   // suggestions affichées, puis « Afficher plus » par tranche

function saved(app) {
  return app.ui.suggestions || app.store.setting(SAVED, null);
}

/** Résultat gardé : sans les traits (volumineux), avec les thèmes du profil pour les réglages. */
function toSaved(result) {
  return {
    computedAt: result.computedAt, errors: result.errors,
    items: result.items.map(({ features, ...rest }) => rest),
    top: result.profile.top(15, true).map(([f, label]) => [f, label]),
    themes: result.profile.top(40, true).map(([f, l]) => [f, l])
      .sort((a, b) => a[1].localeCompare(b[1], "fr")),
  };
}

// Infobulle du « % pour toi » (suggestions.py) : valeur absolue, qui ne décide pas seule de l'ordre
const MATCH_HELP = "% pour toi : ressemblance à tes thèmes préférés et recommandations par des titres "
  + "que tu as aimés (70 % et plus : très proche de tes goûts). L'ordre tient aussi compte de la "
  + "note TMDB et de la variété de la liste.";
const INTRO = "Watchlog analyse les thèmes de ce que tu regardes (dystopie, super-héros…), croise "
  + "les recommandations TMDB (ce que regardent les gens qui ont aimé les mêmes titres) et les "
  + "tendances de la semaine. Notes, favoris, séries vues en entier et liste « à voir » comptent "
  + "davantage ; séries arrêtées, mauvaises notes et « Pas intéressé » éloignent les titres "
  + "semblables. Tout est calculé sur ton appareil.";
const STATUS_PAIRS = STATUSES.map((st) => [st, STATUS_LABELS[st]]);

function card(app, s) {
  const mine = new Set(app.store.setting("my_platforms", []));
  const id = `${s.kind}-${s.tmdbId}`;
  const details = [s.originalTitle && s.originalTitle !== s.title ? `Titre original : ${s.originalTitle}`
    : null, s.genres.length ? s.genres.join(", ") : null,
  s.voteAverage ? `Note TMDB : ${s.voteAverage.toFixed(1).replace(".", ",")}/10` : null].filter(Boolean);
  return html`<li class="row is-plain suggestion">
    ${poster(s.posterUrl, s.title, "sm")}
    <div class="row-main">
      <h2 class="row-title">${s.title}${s.year ? ` (${s.year})` : ""}</h2>
      <p class="row-sub">${s.kind === MOVIE ? badge("Film", "orange")
        : badge("Série", "blue")} ${badge(`${s.match} % pour toi`, "green")}
        ${s.trending ? badge("Tendance de la semaine", "orange", "trending_up") : ""}
        ${helpTip(MATCH_HELP)}</p>
      ${details.length ? html`<p class="caption">${details.join(" · ")}</p>` : ""}
      ${s.matchedThemes.length ? html`<p class="caption">Thèmes en commun : ${s.matchedThemes.join(", ")}</p>` : ""}
      ${s.because.length ? html`<p class="caption">Parce que tu as vu : ${s.because.join(", ")}</p>`
        : s.fromThemes ? html`<p class="caption">Trouvé en explorant tes thèmes préférés.</p>` : ""}
      ${s.streaming?.length ? html`<p class="caption">En streaming : ${s.streaming.map((p) => (mine.has(p)
        ? html`<strong>${p}</strong>` : p)).reduce((a, b) => html`${a}, ${b}`)}</p>` : ""}
      ${s.overview ? html`<p class="overview">${s.overview}</p>` : ""}
      <form class="row-form" data-submit="sugg-add" data-kind="${s.kind}" data-id="${s.tmdbId}"
        data-title="${s.title}">
        <label class="field field-wide"><span>Statut</span><select name="status" id="${`sugg-status-${id}`}"
          aria-label="${`Statut pour ${s.title}`}">${options(STATUS_PAIRS, TO_WATCH)}</select></label>
        <button class="btn" aria-label="${`Ajouter ${s.title}`}">Ajouter</button>
        <button type="button" class="btn" data-action="sugg-dismiss" data-kind="${s.kind}"
          data-id="${s.tmdbId}" data-title="${s.title}"
          aria-label="${`Pas intéressé par ${s.title}`}">Pas intéressé</button>
      </form>
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
  // Cases à cocher (pas de liste à sélection multiple : difficile au tactile et au clavier).
  const group = (name, legend, chosen) => html`<fieldset class="chips"><legend>${legend}</legend>
    ${list.map(([f, label]) => html`<label class="chip"><input type="checkbox" name="${name}" value="${f}"
      data-label="${label.replace(" (genre)", "")}" ${chosen.has(f) ? raw("checked") : ""}> ${label}</label>`)}
  </fieldset>`;
  return expander("Ton profil de goûts", html`
    ${result.top.length ? html`<p><strong>Thèmes qui reviennent le plus dans ce que tu aimes</strong></p>
      <p>${result.top.map(([, label]) => badge(label, "blue"))}</p>`
      : html`<p class="caption">Pas encore de thèmes : ajoute et note des titres.</p>`}
    <p><strong>Oriente tes suggestions</strong></p>
    <p class="caption">« Plus de » : le thème compte autant que ton plus fort, et Watchlog va chercher
      les titres phares de ce thème. « Moins de » : les titres qui l'ont passent loin derrière.
      Genres et thèmes (mots-clés TMDB) sont proposés.</p>
    <form data-submit="sugg-prefs">
      <div class="cols cols-2">${group("more", "Plus de…", more)}${group("less", "Moins de…", less)}</div>
      <button class="btn">Enregistrer et recalculer</button>
    </form>`, { keep: "sugg-prefs", iconName: "psychology" });
}

function dismissedBlock(app) {
  const titles = [...app.store.dismissed.values()].map((d) => d.title || `#${d.tmdb_id}`);
  if (!titles.length) return "";
  return expander(`Titres écartés (${titles.length})`, html`<p>${titles.join(", ")}</p>
    <button type="button" class="btn" data-action="sugg-undismiss">Tout réafficher</button>`,
  { keep: "sugg-dismissed" });
}

export function render(app) {
  const intro = html`<p class="caption">${INTRO}</p>`;
  if (!app.tmdb.configured) {
    return html`<h1>Suggestions</h1>${intro}${alert("info", html`Les suggestions ont besoin de TMDB :
      <a href="#/parametres">ajoute ta clé TMDB dans Paramètres</a>.`)}`;
  }
  const running = app.ui.suggRunning;
  const result = saved(app);
  const prefs = loadPrefs(PREFS, DEFAULTS);
  if (running) {
    const ratio = running.total ? Math.min(1, running.done / running.total) : 0;
    return html`<h1>Suggestions</h1>${intro}<div class="progress" aria-live="off">
      <p id="sugg-progress">${running.label || "Calcul…"} ${running.total ? `${running.done}/${running.total}` : ""}</p>
      <div class="bar"><span id="sugg-bar" style="width:${(ratio * 100).toFixed(1)}%"></span></div></div>
      <p class="caption">Analyse de tes goûts… La première fois, cela peut prendre une à deux minutes ;
        ensuite, quelques secondes.</p>`;
  }
  if (!result) {
    return html`<h1>Suggestions</h1>${intro}
      <p><button type="button" class="btn btn-primary" data-action="sugg-compute">Calculer mes
        suggestions</button></p>`;
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
  const mine = app.store.setting("my_platforms", []);
  return html`
  <h1>Suggestions</h1>
  ${intro}
  ${result.errors.map((e) => alert("warning", e))}
  ${preferencesForm(app, result)}
  <form class="cols" style="--cols: 3fr 2fr 1fr 1fr" aria-label="Type et tri des suggestions">
    ${segmented("Type", "kind", [["", "Tout"], [SHOW, "Séries"], [MOVIE, "Films"]], prefs.kind, "sugg-pref")}
    <label class="field"><span>Trier par</span><select data-change="sugg-pref" name="sort">${options(
      [[SORT_RELEVANCE, "Pertinence"], [SORT_RATING, "Note TMDB"], [SORT_RECENT, "Plus récents"]],
      prefs.sort)}</select></label>
    <button type="button" class="btn" data-action="sugg-reset" title="Retire tous les filtres.">Réinitialiser</button>
    <button type="button" class="btn" data-action="sugg-compute"
      title="${`Calculées le ${fmtDate(result.computedAt)} ; refait le calcul avec l'état actuel de la bibliothèque.`}">Recalculer</button>
  </form>
  <form class="cols" style="--cols: 1fr 1fr 1fr 1fr" aria-label="Filtrer les suggestions">
    <label class="field"><span>Genres</span><select data-change="sugg-pref" name="genre">${options(
      [["", "Tous les genres"], ...genres.map((g) => [g, g])], prefs.genre)}</select></label>
    <label class="field"><span>Thèmes</span><select data-change="sugg-pref" name="theme">${options(
      [["", "Tous les thèmes"], ...themes.map((t) => [t, t])], prefs.theme)}</select></label>
    <label class="field"><span>Note TMDB minimale</span><select data-change="sugg-pref" name="minVote">${options(
      [["0", "Toutes"], ["6", "6 et plus"], ["7", "7 et plus"], ["8", "8 et plus"]], String(prefs.minVote))}</select></label>
    <span class="check"><label class="check"><input type="checkbox" data-change="sugg-mine"
      ${prefs.mine ? raw("checked") : ""} ${mine.length ? "" : raw("disabled")}> Sur mes plateformes</label>
      ${helpTip(mine.length ? `Seulement les titres disponibles sur ${mine.join(", ")}.`
        : "Choisis d'abord tes plateformes dans Paramètres > Mes plateformes.")}</span>
  </form>
  ${needsPlatforms ? alert("warning", html`Plateformes pas encore lues pour ces suggestions.
    <button type="button" class="btn" data-action="sugg-platforms">Lire les plateformes</button>`) : ""}
  <p class="caption" data-announce>${items.length} suggestion${items.length > 1 ? "s" : ""} · calculées
    le ${fmtDate(result.computedAt)}</p>
  ${!result.items.length ? html`<p>Aucune suggestion pour l'instant : ajoute des titres à ta
    bibliothèque.</p>` : !items.length ? html`<p>Aucune suggestion avec ces filtres.</p>` : ""}
  <ul class="rows" role="list">${items.slice(0, limit).map((s) => card(app, s))}</ul>
  ${items.length > limit ? html`<button type="button" class="btn" data-action="sugg-more">
    Afficher plus</button>` : ""}
  ${dismissedBlock(app)}`;
}

async function compute(app) {
  app.ui.suggRunning = { done: 0, total: 0, label: "Préparation…" };
  app.announce("Calcul des suggestions commencé, une à deux minutes au premier passage.");
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
          const bar = document.getElementById("sugg-bar");
          if (bar && total) bar.style.width = `${Math.min(100, (done / total) * 100).toFixed(1)}%`;
          lastPaint = now;
        }
      },
    });
    app.ui.suggestions = toSaved(result);
    app.store.setSetting(SAVED, app.ui.suggestions);
    app.ui.suggLimit = PAGE_SIZE;
    app.announce(`Suggestions prêtes : ${result.items.length} titres.`);
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
  async "sugg-add"(app, form) {
    const button = form.querySelector("button:not([type])");
    button.disabled = true;
    button.textContent = "Ajout…";
    const id = Number(form.dataset.id);
    const status = new FormData(form).get("status") || TO_WATCH;
    if (form.dataset.kind === SHOW) {
      await addSuggestedShow(app.store, app.tvmaze, app.tmdb, id, { title: form.dataset.title, status });
    } else {
      await addMovie(app.store, app.tmdb, id, { status });
    }
    updateItems(app, (items) => items.filter((s) => !(s.kind === form.dataset.kind && s.tmdbId === id)));
    app.toast(`« ${form.dataset.title} » a été ajouté à la bibliothèque (${STATUS_LABELS[status]}).`);
  },
  "sugg-reset"(app) {
    savePrefs(PREFS, { ...DEFAULTS, sort: loadPrefs(PREFS, DEFAULTS).sort });
    app.ui.suggLimit = PAGE_SIZE;
  },
  "sugg-undismiss"(app) {
    const n = undismissAll(app.store);
    app.toast(`${n} titre(s) remis dans les suggestions.`);
    return compute(app);
  },
  async "sugg-prefs"(app, form) {
    const pick = (name) => new Map([...form.querySelectorAll(`input[name="${name}"]:checked`)]
      .map((box) => [box.value, box.dataset.label]));
    setPreferences(app.store, pick("more"), pick("less"), isoUtc());
    await compute(app);
  },
};
