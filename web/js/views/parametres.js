// Page « Paramètres » : clé TMDB (gardée dans ce navigateur), mes plateformes, échange avec
// l'app Python (fichier JSON), synchronisation, stockage.

import { fmtDate } from "../core/dates.js";
import { exportData, importData, readJson } from "../core/exchange.js";
import { refreshCompleted } from "../core/library.js";
import { platformChoices } from "../core/providers.js";
import { clearAll, storageEstimate } from "../storage/idb.js";
import { maskSecret, readSecret, saveSecret } from "../storage/secret.js";
import { externalLink, html, raw } from "./dom.js";

const REPO_URL = "https://github.com/Aistra91-KS/watchlog";
const TMDB_KEY_HELP = "https://www.themoviedb.org/settings/api";

async function platformsSection(app) {
  if (!app.tmdb.configured) {
    return html`<p class="muted">Ajoute d'abord ta clé TMDB : la liste vient de TMDB / JustWatch.</p>`;
  }
  const saved = app.store.setting("my_platforms", []);
  let names;
  try {
    const lists = await Promise.all([app.tmdb.providerList("movie"), app.tmdb.providerList("tv")]);
    names = platformChoices(lists, saved);
  } catch (error) {
    console.warn("Liste des plateformes", error);
    return html`<p class="error">Liste des plateformes indisponible : ${error.message}</p>`;
  }
  const chosen = new Set(saved);
  return html`<form data-submit="save-platforms">
    <fieldset class="chips chips-wrap"><legend>Celles auxquelles tu as accès</legend>
      ${names.map((name) => html`<label class="chip"><input type="checkbox" name="platform"
        value="${name}" ${chosen.has(name) ? raw("checked") : ""}> ${name}</label>`)}
    </fieldset>
    <button class="btn">Enregistrer mes plateformes</button></form>`;
}

function count(map, kind) {
  return [...map.values()].filter((r) => !kind || r.kind === kind).length;
}

export async function render(app) {
  const secret = readSecret();
  const status = app.ui.tmdbStatus;
  const report = app.store.setting("last_sync_report", null);
  const lastSync = app.store.setting("last_sync_at", null);
  const estimate = await storageEstimate();
  const confirmClear = app.ui.confirmClear;
  return html`
  <h1>Paramètres</h1>

  <section aria-labelledby="tmdb-h"><h2 id="tmdb-h">Connexion TMDB</h2>
    <p>TMDB fournit les films, les textes français, les suggestions et les plateformes.
      Crée une clé gratuite sur ${externalLink(TMDB_KEY_HELP, "themoviedb.org")}
      (« Clé d'API » ou « Jeton d'accès en lecture »), puis colle-la ici.
      <strong>Elle reste dans ce navigateur</strong> : jamais envoyée ailleurs qu'à TMDB, jamais
      dans le fichier d'export.</p>
    <p>${secret ? html`Clé enregistrée : <code>${maskSecret(secret)}</code>` : "Aucune clé enregistrée."}
      ${status ? html` <span class="${status.ok ? "ok" : "error"}" data-announce>${status.message}</span>` : ""}</p>
    <form class="inline-form" data-submit="save-secret">
      <label class="field field-wide"><span>Clé d'API ou jeton TMDB</span>
        <input type="password" name="secret" autocomplete="off" spellcheck="false"></label>
      <button class="btn">Enregistrer et tester</button>
    </form>
    ${secret ? html`<p><button type="button" class="btn btn-ghost" data-action="test-secret">Tester la connexion</button>
      <button type="button" class="btn btn-ghost btn-danger" data-action="forget-secret">Oublier la clé</button></p>` : ""}
  </section>

  <section aria-labelledby="platforms-h"><h2 id="platforms-h">Mes plateformes</h2>
    <p class="muted">Pour mettre en avant les titres disponibles sur tes abonnements.</p>
    ${await platformsSection(app)}
  </section>

  <section aria-labelledby="exchange-h"><h2 id="exchange-h">Échange avec l'app Python</h2>
    <p>Les données de cette page restent dans ce navigateur. Pour retrouver ta bibliothèque du PC,
      exporte-la depuis l'app Python (Paramètres, section « App web ») puis importe le fichier ici ;
      dans l'autre sens, exporte ici puis importe sur le PC. Les visionnages s'ajoutent (jamais
      supprimés) ; pour un titre déjà présent, le fichier importé fait référence pour le statut,
      le favori et la note.</p>
    <p><button type="button" class="btn" data-action="export-json">Exporter mes données (.json)</button></p>
    <form class="inline-form" data-submit="import-json">
      <label class="field field-wide"><span>Fichier exporté par Watchlog (.json)</span>
        <input type="file" name="file" accept=".json,application/json" required></label>
      <button class="btn">Importer</button>
    </form>
    ${app.ui.importReport ? html`<pre class="report" data-announce>${app.ui.importReport.join("\n")}</pre>` : ""}
  </section>

  <section aria-labelledby="sync-h"><h2 id="sync-h">Mise à jour des séries et films</h2>
    <p>Nouveaux épisodes (TVMaze), dates de sortie des films (TMDB). Automatique à l'ouverture
      si la dernière date de plus de 24 h.
      ${lastSync ? `Dernière : ${fmtDate(lastSync)}.` : "Jamais faite."}</p>
    <p><button type="button" class="btn" data-action="sync" ${app.sync.running ? raw("disabled") : ""}>
      ${app.sync.running ? "Mise à jour en cours…" : "Mettre à jour maintenant"}</button></p>
    ${report ? html`<details data-keep="sync-report"><summary>Dernier rapport (${fmtDate(report.at)}${report.problems
      ? `, ${report.problems} problème(s)` : ""})</summary>
      <pre class="report">${report.lines.join("\n")}</pre></details>` : ""}
  </section>

  <section aria-labelledby="storage-h"><h2 id="storage-h">Stockage</h2>
    <p>${count(app.store.library, "show")} séries et ${count(app.store.library, "movie")} films
      dans la bibliothèque, ${app.store.watches.size} visionnages.
      ${estimate ? ` Espace utilisé : ${(estimate.usage / 1_048_576).toFixed(1)} Mo.` : ""}</p>
    <p>${app.persistent === true ? "Le navigateur garde tes données durablement."
      : html`<span class="warning-inline">Le navigateur peut effacer ces données s'il manque de place
        (ou si tu vides son historique) : exporte-les de temps en temps.</span>`}</p>
    <div class="confirm ${confirmClear ? "confirming" : ""}">
      <button type="button" class="btn btn-ghost" data-action="clear-cache">Vider le cache des API</button>
      <button type="button" class="btn btn-ghost btn-danger" data-action="ask-clear" id="clear-ask">Tout effacer</button>
      <span class="confirm-box" role="group" aria-label="Confirmer l'effacement">
        Bibliothèque, visionnages et notes de ce navigateur seront effacés.
        <button type="button" class="btn btn-danger" data-action="clear-all" id="clear-yes">Oui, tout effacer</button>
        <button type="button" class="btn btn-ghost" data-action="cancel-clear">Annuler</button>
      </span>
    </div>
  </section>

  <section aria-labelledby="about-h"><h2 id="about-h">À propos</h2>
    <p>Watchlog, suivi de séries et de films. Code source et version à installer sur PC :
      ${externalLink(REPO_URL, "GitHub")}.</p>
    <p class="credit">Données séries : ${externalLink("https://www.tvmaze.com", "TVMaze")}
      (CC BY-SA). This product uses the TMDB API but is not endorsed or certified by TMDB.
      Disponibilités : JustWatch (via TMDB).</p>
  </section>`;
}

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const link = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const actions = {
  async "save-secret"(app, form) {
    const value = new FormData(form).get("secret");
    if (!String(value || "").trim()) throw new Error("Colle ta clé TMDB dans le champ.");
    saveSecret(value);
    app.resetClients();
    app.ui.tmdbStatus = await app.tmdb.checkConnection();
  },
  async "test-secret"(app) {
    app.resetClients();
    app.ui.tmdbStatus = await app.tmdb.checkConnection();
  },
  "forget-secret"(app) {
    saveSecret("");
    app.resetClients();
    app.ui.tmdbStatus = null;
    app.toast("Clé TMDB oubliée par ce navigateur.");
  },
  "save-platforms"(app, form) {
    app.store.setSetting("my_platforms", new FormData(form).getAll("platform"));
    app.toast("Plateformes enregistrées.");
  },
  async "export-json"(app) {
    await app.flush();
    const data = exportData(app.store);
    download(`watchlog-${new Date().toISOString().slice(0, 10)}.json`, JSON.stringify(data));
    app.toast(`Export prêt : ${data.shows.length} séries, ${data.movies.length} films.`);
    return false;
  },
  async "import-json"(app, form) {
    const file = new FormData(form).get("file");
    if (!file || !file.size) throw new Error("Choisis un fichier.");
    const report = importData(app.store, readJson(await file.text()));
    refreshCompleted(app.store);
    const saved = await app.flush();
    app.ui.importReport = report.lines();
    if (!saved) {
      app.toast("Import lu mais pas enregistré : le stockage du navigateur refuse l'écriture.",
        "error");
    } else {
      app.toast(report.skipped.length ? `Import fait, ${report.skipped.length} entrée(s) ignorée(s).`
        : "Import fait.", report.skipped.length ? "error" : "info");
    }
  },
  sync(app) {
    app.runSync();
  },
  async "clear-cache"(app) {
    await app.cache.clear();
    app.toast("Cache vidé.");
    return false;
  },
  "ask-clear"(app) {
    app.ui.confirmClear = true;
    app.ui.focusAfter = "clear-yes";   // posé après le rendu (asynchrone sur cette page)
  },
  "cancel-clear"(app) {
    app.ui.confirmClear = false;
    app.ui.focusAfter = "clear-ask";
  },
  async "clear-all"(app) {
    app.store.takeDirty();   // rien ne doit être réécrit après l'effacement
    await clearAll(app.db);
    location.reload();
    return false;
  },
};
