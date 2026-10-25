// Point d'entrée de l'app web : ouverture du stockage, clients d'API, routeur (#/page/param),
// actions déclarées dans le HTML (data-action, data-change, data-submit, data-input).

import { syncLibrary, syncReportLines } from "./core/adding.js";
import { fmtAgo, isoUtc } from "./core/dates.js";
import { refreshCompleted } from "./core/library.js";
import { TMDBClient } from "./api/tmdb.js";
import { TVMazeClient } from "./api/tvmaze.js";
import { IdbCache, askPersistent, loadStore, openDb, persist } from "./storage/idb.js";
import { readSecret } from "./storage/secret.js";
import { html } from "./views/dom.js";
import { icon } from "./views/icons.js";
import { loadPrefs } from "./views/prefs.js";
import * as shared from "./views/shared.js";
import * as ui from "./views/ui.js";
import * as aRegarder from "./views/a-regarder.js";
import * as bibliotheque from "./views/bibliotheque.js";
import * as calendrier from "./views/calendrier.js";
import * as film from "./views/film.js";
import * as serie from "./views/serie.js";

// Pages courantes chargées tout de suite ; les autres à la première visite (page plus légère).
const ROUTES = {
  "a-regarder": aRegarder, bibliotheque, calendrier, serie, film,
  suggestions: () => import("./views/suggestions.js"),
  statistiques: () => import("./views/statistiques.js"),
  ajouter: () => import("./views/ajouter.js"),
  parametres: () => import("./views/parametres.js"),
};
const DEFAULT_ROUTE = "a-regarder";
// Menu principal (les fiches série / film n'y sont pas).
const NAV = [
  ["a-regarder", "À regarder"], ["bibliotheque", "Bibliothèque"], ["calendrier", "Calendrier"],
  ["suggestions", "Suggestions"], ["statistiques", "Statistiques"], ["ajouter", "Ajouter"],
  ["parametres", "Paramètres"],
];
// Délai avant d'écrire les modifications (plusieurs clics rapides = une seule écriture).
const SAVE_DELAY_MS = 400;
// Synchro automatique à l'ouverture si la dernière date de plus de 24 h.
const AUTO_SYNC_AFTER_MS = 86_400_000;
// Durée d'affichage des messages (assez longue pour une loupe ou un lecteur d'écran).
const TOAST_MS = { info: 8000, error: 15000 };
// Attente après un redimensionnement avant de redessiner les graphiques
const RESIZE_DELAY_MS = 250;
// Largeur sous laquelle le menu se replie derrière un bouton (comme la barre de Streamlit).
const MENU_BREAKPOINT_PX = 900;
// Contrôles dont on retrouve l'équivalent après un nouveau rendu (pour y remettre le focus).
const FOCUS_ATTRS = ["data-action", "data-change", "data-input", "data-submit", "name",
  "data-ref", "data-kind", "data-id", "data-season", "data-show"];

const app = {
  store: null, db: null, cache: null, tvmaze: null, tmdb: null,
  route: { name: DEFAULT_ROUTE, params: [] }, view: null, ui: { openDetails: new Map() },
  sync: { running: false, done: 0, total: 0, label: "" },
};

// --- Clients et enregistrement -----------------------------------------------------

app.resetClients = () => {
  app.tvmaze = new TVMazeClient({ cache: app.cache });
  app.tmdb = new TMDBClient({ secret: readSecret(), cache: app.cache });
};

let saveTimer = null;
app.save = () => {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(app.flush, SAVE_DELAY_MS);
};

/** Écrit les modifications. Renvoie false (et affiche un bandeau) si l'écriture échoue. */
app.flush = async () => {
  clearTimeout(saveTimer);
  try {
    await persist(app.db, app.store);
    setSaveError(null);
    return true;
  } catch (error) {
    console.error("Enregistrement impossible", error);
    setSaveError("Tes dernières modifications ne sont pas enregistrées (stockage du navigateur "
      + "plein ou bloqué). Exporte tes données depuis Paramètres pour ne rien perdre.");
    return false;
  }
};

function setSaveError(message) {
  const box = document.getElementById("save-error");
  box.hidden = !message;
  box.textContent = message || "";
}

/** Annonce aux lecteurs d'écran (zone permanente, hors du contenu qui est remplacé). */
app.announce = (text) => {
  const zone = document.getElementById("announce");
  zone.textContent = "";
  // Vidée puis remplie : le même texte deux fois de suite est quand même annoncé.
  setTimeout(() => { zone.textContent = text; }, 50);
};

/** Message bref. Les erreurs passent par une zone role="alert" (annoncées tout de suite). */
app.toast = (message, kind = "info") => {
  const zone = document.getElementById(kind === "error" ? "toast-alert" : "toast");
  const item = document.createElement("p");
  item.className = `toast toast-${kind}`;
  item.textContent = message;
  const close = Object.assign(document.createElement("button"), {
    type: "button", className: "toast-close", textContent: "×",
  });
  close.setAttribute("aria-label", "Fermer le message");
  close.addEventListener("click", () => item.remove());
  item.append(close);
  zone.append(item);
  let timer = setTimeout(() => item.remove(), TOAST_MS[kind] || TOAST_MS.info);
  // Survol ou focus : le message reste affiché.
  item.addEventListener("mouseenter", () => clearTimeout(timer));
  item.addEventListener("focusin", () => clearTimeout(timer));
  item.addEventListener("mouseleave", () => {
    timer = setTimeout(() => item.remove(), TOAST_MS.info);
  });
};

// --- Synchronisation ---------------------------------------------------------------

app.runSync = async ({ quiet = false } = {}) => {
  if (app.sync.running) {
    if (!quiet) app.toast("Mise à jour déjà en cours.");
    return null;
  }
  Object.assign(app.sync, { running: true, done: 0, total: 0, label: "" });
  renderSyncStatus();
  if (!quiet) app.announce("Mise à jour des séries et films commencée.");
  let report;
  try {
    report = await syncLibrary(app.store, app.tvmaze, app.tmdb, {
      progress: (done, total, label) => {
        Object.assign(app.sync, { done, total, label });
        renderSyncStatus();
      },
    });
    app.store.setSetting("last_sync_report", { at: isoUtc(), lines: syncReportLines(report),
      problems: report.errors.length });
    if (!quiet || report.errors.length) {
      app.toast(report.errors.length
        ? `Mise à jour faite, ${report.errors.length} problème(s) : détail dans Paramètres.`
        : `Mise à jour faite : ${report.shows} série(s), ${report.movies} film(s).`,
      report.errors.length ? "error" : "info");
    }
  } catch (error) {
    console.error("Synchronisation", error);
    app.toast(`Mise à jour impossible : ${error.message}`, "error");
  } finally {
    app.sync.running = false;
    renderSyncStatus();
    app.save();
    app.rerender();
  }
  return report;
};

/** État TMDB affiché dans le menu (comme l'app Python). */
function tmdbLabel() {
  const tmdb = app.tmdb;
  if (!tmdb?.configured) return "TMDB : non connecté (voir Paramètres)";
  if (tmdb.connected === true) return "TMDB : connecté";
  if (tmdb.connected === false) return "TMDB : clé refusée (voir Paramètres)";
  return tmdb.lastMessage ? "TMDB : injoignable pour l'instant" : "TMDB : test de la connexion…";
}

/** Bas du menu : TMDB, date de la dernière mise à jour ou progression de celle en cours.
 *  Visuel seulement (début et fin de la mise à jour sont annoncés à part). */
function renderSyncStatus() {
  const box = document.getElementById("sidebar-status");
  if (!box) return;
  const { running, done, total, label } = app.sync;
  const last = app.store?.setting("last_sync_at", null);
  const report = app.store?.setting("last_sync_report", null);
  let sync;
  if (running) {
    sync = html`${icon("sync")} Mise à jour en cours… ${total ? `${done}/${total}` : ""}
      <span class="sync-label">${label}</span>`;
  } else {
    sync = last ? `Mis à jour ${fmtAgo(last)}` : "Jamais mis à jour";
  }
  box.innerHTML = html`<p>${tmdbLabel()}</p><p class="${running ? "is-running" : ""}">${sync}</p>
    ${!running && report?.problems ? html`<p><a href="#/parametres">${report.problems}
      problème${report.problems > 1 ? "s" : ""} à la dernière mise à jour</a></p>` : ""}`.text;
}

// --- Routeur et rendu ----------------------------------------------------------------

function parseHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean)
    .map((p) => decodeURIComponent(p));
  const name = ROUTES[parts[0]] ? parts[0] : DEFAULT_ROUTE;
  return { name, params: parts.slice(1) };
}

function renderNav() {
  const current = ["serie", "film"].includes(app.route.name) ? "bibliotheque" : app.route.name;
  document.getElementById("nav").innerHTML = html`<ul role="list">${NAV.map(([name, label]) => html`
    <li><a href="#/${name}" ${name === current ? html`aria-current="page"` : ""}>${label}</a></li>`)}
    </ul>`.text;
}

// --- Menu repliable (petit écran) -------------------------------------------------------

const narrow = () => window.matchMedia(`(max-width: ${MENU_BREAKPOINT_PX}px)`).matches;

/** Ouvre / ferme le menu par-dessus la page ; le focus va au menu puis revient au bouton. */
function setMenu(open, { focus = true } = {}) {
  const toggle = document.getElementById("menu-toggle");
  const wasOpen = document.body.classList.contains("menu-open");
  document.body.classList.toggle("menu-open", open);
  document.getElementById("sidebar-backdrop").hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
  toggle.textContent = open ? "Fermer le menu" : "Menu";
  if (!focus) return;
  if (open) document.querySelector('#nav [aria-current="page"], #nav a')?.focus();
  else if (wasOpen) toggle.focus();
}

// --- Thème (clair / sombre / celui du système), comme le menu Settings de Streamlit -----

app.applyTheme = () => {
  const { mode } = loadPrefs("theme", { mode: "system" });
  if (mode === "light" || mode === "dark") document.documentElement.dataset.theme = mode;
  else delete document.documentElement.dataset.theme;
};

/** Signature d'un contrôle, pour retrouver son équivalent après un nouveau rendu. */
function focusKey(el) {
  if (el.dataset.focus) return `f:${el.dataset.focus}`;
  if (el.id) return `#${el.id}`;
  const parts = FOCUS_ATTRS.map((a) => el.getAttribute(a) || "");
  if (el.type === "checkbox" || el.type === "radio") parts.push(el.value);
  return parts.some(Boolean) ? `${el.tagName}|${parts.join("|")}` : null;
}

function findByFocusKey(main, key) {
  if (key.startsWith("f:")) return main.querySelector(`[data-focus="${CSS.escape(key.slice(2))}"]`);
  if (key.startsWith("#")) return document.getElementById(key.slice(1));
  return [...main.querySelectorAll("button, input, select, textarea, a, summary")]
    .find((el) => focusKey(el) === key) || null;
}

let renderSeq = 0;

/** Réaffiche la page courante (après une action), en gardant défilement, focus et blocs
 *  dépliés. Un rendu dépassé par un plus récent (navigation pendant un chargement) est jeté. */
app.rerender = async ({ focusHeading = false } = {}) => {
  const seq = ++renderSeq;
  const main = document.getElementById("main");
  const active = document.activeElement;
  const key = !focusHeading && active && main.contains(active) ? focusKey(active) : null;
  // Rang du contrôle parmi ceux de la même action : si sa ligne disparaît (épisode vu), le
  // focus va au suivant (le prochain « Vu ? »), pas tout en haut.
  const action = key && active.dataset.action;
  const rank = action ? [...main.querySelectorAll(`[data-action="${CSS.escape(action)}"]`)].indexOf(active) : -1;
  for (const d of main.querySelectorAll("details[data-keep]")) app.ui.openDetails.set(d.dataset.keep, d.open);
  const scroll = window.scrollY;
  // Largeur utile du contenu : les graphiques s'y ajustent (comme width="stretch" en Python).
  const style = getComputedStyle(main);
  app.ui.contentWidth = main.clientWidth - parseFloat(style.paddingLeft)
    - parseFloat(style.paddingRight);
  let content;
  try {
    const view = typeof app.view === "function" ? await app.view() : app.view;
    content = await view.render(app, app.route.params);
  } catch (error) {
    console.error(`Affichage de ${app.route.name}`, error);
    content = html`<h1>Oups</h1><p class="error">Cette page n'a pas pu s'afficher :
      ${error.message}</p>`;
  }
  if (seq !== renderSeq) return;
  main.innerHTML = content.text;
  renderSyncStatus();   // l'état TMDB a pu changer (clé enregistrée, test...)
  for (const d of main.querySelectorAll("details[data-keep]")) {
    if (app.ui.openDetails.has(d.dataset.keep)) d.open = app.ui.openDetails.get(d.dataset.keep);
  }
  // Graphiques plus larges que l'écran : on montre d'abord la fin (les mois récents).
  for (const el of main.querySelectorAll("[data-scroll-end]")) el.scrollLeft = el.scrollWidth;
  const heading = main.querySelector("h1");
  heading?.setAttribute("tabindex", "-1");
  document.title = `${heading ? heading.textContent.trim() : "Watchlog"} · Watchlog`;
  if (focusHeading) {
    heading?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
    return;
  }
  window.scrollTo(0, scroll);
  const summary = main.querySelector("[data-announce]");
  if (summary) app.announce(summary.textContent.trim());
  const wanted = app.ui.focusAfter;
  app.ui.focusAfter = null;
  const target = (wanted && document.getElementById(wanted)) || (key && findByFocusKey(main, key));
  if (target) {
    target.closest("details")?.setAttribute("open", "");
    target.focus({ preventScroll: true });
    if (target.matches("input[type=search], input[type=text]")) {
      target.setSelectionRange(target.value.length, target.value.length);
    }
  } else if (key) {
    const same = action ? [...main.querySelectorAll(`[data-action="${CSS.escape(action)}"]`)] : [];
    const next = same[Math.min(rank, same.length - 1)];
    // L'élément a disparu : le suivant de même action, sinon le titre de la page.
    (next || heading)?.focus({ preventScroll: !next });
  }
};

async function onRoute() {
  app.route = parseHash();
  app.view = ROUTES[app.route.name];
  renderNav();
  if (document.body.classList.contains("menu-open")) setMenu(false, { focus: false });
  await app.rerender({ focusHeading: true });
}

app.navigate = (hash) => {
  if (location.hash === hash) onRoute();
  else location.hash = hash;
};

// --- Actions déclarées dans le HTML ---------------------------------------------------

async function dispatch(name, el, event) {
  const view = typeof app.view === "function" ? await app.view() : app.view;
  const handler = view?.actions?.[name] || shared.actions[name] || ui.actions[name];
  if (!handler) {
    console.warn(`Action inconnue : ${name}`);
    return;
  }
  try {
    const result = await handler(app, el, event);
    if (result === false) return;
  } catch (error) {
    console.error(`Action ${name}`, error);
    app.toast(error.message || "Action impossible.", "error");
  }
  // Toujours un nouveau rendu, même après une erreur : un bouton désactivé (« Ajout… »)
  // redevient utilisable.
  app.save();
  await app.rerender();
}

function bindEvents() {
  const main = document.getElementById("main");
  main.addEventListener("click", (event) => {
    const el = event.target.closest("[data-action]");
    if (el && main.contains(el)) {
      event.preventDefault();
      dispatch(el.dataset.action, el, event);
    }
  });
  main.addEventListener("change", (event) => {
    const el = event.target.closest("[data-change]");
    if (el) dispatch(el.dataset.change, el, event);
  });
  main.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-submit]");
    if (form) {
      event.preventDefault();
      dispatch(form.dataset.submit, form, event);
    }
  });
  let inputTimer = null;
  main.addEventListener("input", (event) => {
    const el = event.target.closest("[data-input]");
    if (!el) return;
    clearTimeout(inputTimer);
    inputTimer = setTimeout(() => dispatch(el.dataset.input, el, event), 250);
  });
  // Menu repliable : bouton, clic à côté, Échap ; retour au menu large en agrandissant.
  document.getElementById("menu-toggle").addEventListener("click", () => {
    setMenu(!document.body.classList.contains("menu-open"));
  });
  document.getElementById("sidebar-backdrop").addEventListener("click", () => setMenu(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("menu-open")) setMenu(false);
  });
  window.matchMedia(`(max-width: ${MENU_BREAKPOINT_PX}px)`).addEventListener("change", () => {
    if (!narrow()) setMenu(false, { focus: false });
  });
  // Onglets (role="tab") : flèches gauche / droite, Début / Fin, comme un menu d'onglets natif.
  main.addEventListener("keydown", (event) => {
    const tab = event.target.closest('[role="tab"]');
    if (!tab) return;
    const tabs = [...tab.closest('[role="tablist"]').querySelectorAll('[role="tab"]')];
    const moves = { ArrowRight: 1, ArrowLeft: -1, Home: -Infinity, End: Infinity };
    if (!(event.key in moves)) return;
    event.preventDefault();
    const i = tabs.indexOf(tab);
    const step = moves[event.key];
    const next = Number.isFinite(step) ? tabs[(i + step + tabs.length) % tabs.length]
      : tabs[step < 0 ? 0 : tabs.length - 1];
    next.focus();
    next.click();
  });
  // Lien d'évitement : le routeur utilise le hash, on déplace donc le focus sans y toucher.
  document.querySelector(".skip-link").addEventListener("click", (event) => {
    event.preventDefault();
    main.focus();
  });
  window.addEventListener("hashchange", () => {
    if (location.hash.startsWith("#/") || !location.hash) onRoute();
  });
  // Fenêtre redimensionnée : les pages à graphiques sont redessinées à la nouvelle largeur.
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const width = main.clientWidth;
      if (app.route.name === "statistiques" && Math.abs(width - (app.ui.lastWidth || 0)) > 40) {
        app.ui.lastWidth = width;
        app.rerender();
      }
    }, RESIZE_DELAY_MS);
  });
  // Onglet caché ou fermé : on enregistre tout de suite.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") app.flush();
  });
  window.addEventListener("pagehide", () => app.flush());
}

// --- Démarrage -------------------------------------------------------------------------

async function start() {
  try {
    app.db = await openDb();
    app.store = await loadStore(app.db);
  } catch (error) {
    console.error("Stockage du navigateur", error);
    document.getElementById("main").innerHTML = html`<h1>Stockage indisponible</h1>
      <p class="error">Watchlog garde tes données dans le navigateur, mais il refuse l'accès
      (navigation privée ou stockage bloqué) : ${error.message}</p>`.text;
    return;
  }
  app.cache = new IdbCache(app.db);
  app.cache.prune().catch((error) => console.warn("Nettoyage du cache", error));
  app.resetClients();
  app.persistent = await askPersistent();
  if (refreshCompleted(app.store)) app.save();
  bindEvents();
  await onRoute();
  // Test de la clé TMDB au démarrage, comme le logiciel : l'état s'affiche dans le menu.
  if (app.tmdb.configured) {
    app.tmdb.checkConnection().catch((error) => console.warn("Test TMDB", error))
      .finally(renderSyncStatus);
  }
  const last = Date.parse(app.store.setting("last_sync_at", "")) || 0;
  if (app.store.library.size && Date.now() - last > AUTO_SYNC_AFTER_MS && navigator.onLine !== false) {
    app.runSync({ quiet: true });
  }
}

app.applyTheme();
start();
