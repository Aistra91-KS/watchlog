// Point d'entrée de l'app web : ouverture du stockage, clients d'API, routeur (#/page/param),
// actions déclarées dans le HTML (data-action, data-change, data-submit, data-input).

import { syncLibrary, syncReportLines } from "./core/adding.js";
import { isoUtc } from "./core/dates.js";
import { refreshCompleted } from "./core/library.js";
import { TMDBClient } from "./api/tmdb.js";
import { TVMazeClient } from "./api/tvmaze.js";
import { IdbCache, askPersistent, loadStore, openDb, persist } from "./storage/idb.js";
import { readSecret } from "./storage/secret.js";
import { esc, html } from "./views/dom.js";
import * as shared from "./views/shared.js";
import * as aRegarder from "./views/a-regarder.js";
import * as ajouter from "./views/ajouter.js";
import * as bibliotheque from "./views/bibliotheque.js";
import * as calendrier from "./views/calendrier.js";
import * as film from "./views/film.js";
import * as parametres from "./views/parametres.js";
import * as serie from "./views/serie.js";
import * as statistiques from "./views/statistiques.js";
import * as suggestions from "./views/suggestions.js";

const ROUTES = {
  "a-regarder": aRegarder, bibliotheque, calendrier, suggestions, statistiques, ajouter,
  parametres, serie, film,
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

const app = {
  store: null, db: null, cache: null, tvmaze: null, tmdb: null,
  route: { name: DEFAULT_ROUTE, params: [] }, view: null, ui: {},
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

app.flush = async () => {
  clearTimeout(saveTimer);
  try {
    await persist(app.db, app.store);
    setSaveError(null);
  } catch (error) {
    console.error("Enregistrement impossible", error);
    setSaveError("Tes dernières modifications ne sont pas enregistrées (stockage du navigateur "
      + "plein ou bloqué). Exporte tes données depuis Paramètres pour ne rien perdre.");
  }
};

function setSaveError(message) {
  const box = document.getElementById("save-error");
  box.hidden = !message;
  box.textContent = message || "";
}

/** Message bref annoncé aux lecteurs d'écran (zone aria-live). */
app.toast = (message, kind = "info") => {
  const zone = document.getElementById("toast");
  const item = document.createElement("p");
  item.className = `toast toast-${kind}`;
  item.textContent = message;
  zone.append(item);
  setTimeout(() => item.remove(), kind === "error" ? 9000 : 4000);
};

// --- Synchronisation ---------------------------------------------------------------

app.runSync = async ({ quiet = false } = {}) => {
  if (app.sync.running) return null;
  Object.assign(app.sync, { running: true, done: 0, total: 0, label: "" });
  renderSyncStatus();
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

function renderSyncStatus() {
  const box = document.getElementById("sync-status");
  const { running, done, total, label } = app.sync;
  box.hidden = !running;
  box.textContent = running
    ? `Mise à jour des séries et films… ${total ? `${done}/${total}` : ""} ${label}` : "";
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
  document.getElementById("nav").innerHTML = html`<ul>${NAV.map(([name, label]) => html`
    <li><a href="#/${name}" ${name === current ? html`aria-current="page"` : ""}>${label}</a></li>`)}
    </ul>`.text;
}

/** Réaffiche la page courante (après une action), en gardant le défilement et le focus. */
app.rerender = async ({ focusHeading = false } = {}) => {
  const main = document.getElementById("main");
  const active = document.activeElement;
  const focusId = active && main.contains(active) ? active.dataset.focus || active.id : null;
  const scroll = window.scrollY;
  let content;
  try {
    content = await app.view.render(app, app.route.params);
  } catch (error) {
    console.error(`Affichage de ${app.route.name}`, error);
    content = html`<h1 tabindex="-1">Oups</h1><p class="error">Cette page n'a pas pu s'afficher :
      ${error.message}</p>`;
  }
  main.innerHTML = content.text;
  const heading = main.querySelector("h1");
  document.title = `${heading ? heading.textContent.trim() : "Watchlog"} · Watchlog`;
  if (focusHeading) {
    heading?.setAttribute("tabindex", "-1");
    heading?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
    return;
  }
  window.scrollTo(0, scroll);
  if (!focusId) return;
  const target = main.querySelector(`[data-focus="${CSS.escape(focusId)}"]`)
    || document.getElementById(focusId);
  if (target) {
    target.focus({ preventScroll: true });
    if (target.matches("input[type=search], input[type=text]")) {
      target.setSelectionRange(target.value.length, target.value.length);
    }
  } else if (heading) {
    // L'élément a disparu (ex. épisode vu retiré de la liste) : le focus revient au titre.
    heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: true });
  }
};

async function onRoute() {
  app.route = parseHash();
  app.view = ROUTES[app.route.name];
  renderNav();
  await app.rerender({ focusHeading: true });
}

app.navigate = (hash) => {
  if (location.hash === hash) onRoute();
  else location.hash = hash;
};

// --- Actions déclarées dans le HTML ---------------------------------------------------

async function dispatch(name, el, event) {
  const handler = app.view?.actions?.[name] || shared.actions[name];
  if (!handler) {
    console.warn(`Action inconnue : ${name}`);
    return;
  }
  try {
    const result = await handler(app, el, event);
    if (result !== false) {
      app.save();
      await app.rerender();
    }
  } catch (error) {
    console.error(`Action ${name}`, error);
    app.toast(error.message || "Action impossible.", "error");
  }
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
  window.addEventListener("hashchange", onRoute);
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
      (navigation privée ou stockage bloqué) : ${esc(error.message)}</p>`.text;
    return;
  }
  app.cache = new IdbCache(app.db);
  app.cache.prune().catch((error) => console.warn("Nettoyage du cache", error));
  app.resetClients();
  app.persistent = await askPersistent();
  if (refreshCompleted(app.store)) app.save();
  bindEvents();
  await onRoute();
  const last = Date.parse(app.store.setting("last_sync_at", "")) || 0;
  if (app.store.library.size && Date.now() - last > AUTO_SYNC_AFTER_MS && navigator.onLine !== false) {
    app.runSync({ quiet: true });
  }
}

start();
