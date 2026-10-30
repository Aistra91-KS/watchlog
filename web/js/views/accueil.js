// Page « Accueil » : l'essentiel tout de suite, comme la page Python (accueil.py) : chiffres de
// la semaine, épisodes à suivre, prochaines sorties, derniers visionnages, films à voir et
// suggestions (dernier calcul de la page Suggestions, jamais relancé ici).

import { daysBetween, fmtRuntime, today } from "../core/dates.js";
import {
  UPCOMING_DAYS, activity, continueWatching, moviesToWatch, recentWatches, upcoming,
} from "../core/home.js";
import { markWatched, unmarkWatched } from "../core/library.js";
import { EPISODE, MATCH_TO_CHECK, MOVIE, SHOW, refKey } from "../core/model.js";
import { watchedAgo } from "../core/progress.js";
import { dayEntryLabel } from "../core/queue.js";
import { WEEKDAYS, globalCounts, remainingTime, timeSpent, watchesFrame } from "../core/stats.js";
import { html, movieHref, poster, previewHref, showHref } from "./dom.js";
import { icon } from "./icons.js";
import { savedSuggestions } from "./shared.js";
import { alert, badge, metric, metrics } from "./ui.js";

const SUGGESTIONS_SHOWN = 6;   // suggestions affichées (les plus pertinentes)
const UPCOMING_SHOWN = 12;     // lignes de « Prochaines sorties » (la suite : page Calendrier)
const NEW_DAYS = 7;            // pastille « Nouveau » : sorti depuis moins de NEW_DAYS jours
const MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
  "septembre", "octobre", "novembre", "décembre"];

const plural = (n, word, many = `${word}s`) => `${n} ${n > 1 ? many : word}`;
const weekday = (day) => WEEKDAYS[(new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7];

/** « Aujourd'hui », « Demain », sinon « Samedi 17/10 » (comme accueil.day_name). */
function dayName(day, todayDay) {
  const delta = daysBetween(todayDay, day);
  if (delta === 0) return "Aujourd'hui";
  if (delta === 1) return "Demain";
  return `${weekday(day)} ${day.slice(8, 10)}/${day.slice(5, 7)}`;
}

function sectionTitle(id, iconName, title, count = null) {
  return html`<h2 class="section-title" id="${id}">${icon(iconName)} ${title}${count === null ? ""
    : html` <span class="count">(${count})</span>`}</h2>`;
}

function moreLink(href, text) {
  return html`<p class="more-link"><a href="${href}">${icon("arrow_forward")} ${text}</a></p>`;
}

/** Chiffres clés : activité récente, ce qui reste à voir, temps passé. */
function keyMetrics(store, day) {
  const frame = watchesFrame(store);
  const recent = activity(frame, day);
  const remaining = remainingTime(store, day);
  const spent = timeSpent(frame);
  const counts = globalCounts(store);
  const periodHelp = (p, what) => `${plural(p.episodes, "épisode")} et ${plural(p.movies, "film")} vus `
    + `${what} (premier visionnage).${p.ignored ? ` ${p.ignored} visionnage(s) datés d'un jour `
      + "d'import ne sont pas comptés : leur date n'est pas celle du visionnage." : ""}`;
  const estimated = remaining.estimated
    ? ` Durée inconnue pour ${remaining.estimated} élément(s) : estimée.` : "";
  return metrics([
    metric("Vus ces 7 jours", recent.week.total, { bordered: true, delta: fmtRuntime(recent.week.minutes),
      help: periodHelp(recent.week, "ces 7 jours") }),
    metric(`Vus en ${MONTHS[Number(day.slice(5, 7)) - 1]}`, recent.month.total, { bordered: true,
      delta: fmtRuntime(recent.month.minutes), help: periodHelp(recent.month, "ce mois-ci") }),
    metric("Jours d'affilée", recent.streak, { bordered: true,
      delta: recent.streak && !recent.watchedToday ? "Un épisode aujourd'hui pour continuer" : "",
      help: "Jours consécutifs avec au moins un épisode ou un film vu, jusqu'à aujourd'hui (ou "
        + "hier, si rien n'est encore vu aujourd'hui)." }),
    metric("À rattraper", fmtRuntime(remaining.totalMinutes) || "—", { bordered: true,
      delta: `${remaining.episodes} ép. · ${plural(remaining.movies, "film")}`,
      help: `Épisodes et films sortis, pas encore vus (En cours et À voir), comme la page À regarder.${
        estimated}` }),
    metric("Temps total", `${(spent.totalMinutes / 1440).toFixed(1).replace(".", ",")} jours`, {
      bordered: true, delta: `${counts.episodesWatched.toLocaleString("fr-FR")} ép. · ${plural(
        counts.moviesWatched, "film")}`,
      help: "Temps passé devant tes épisodes et films vus (revisionnages compris)." }),
  ], { label: "Chiffres clés", five: true });
}

function nextRow(entry, day) {
  const { item } = entry;
  const label = `${item.title} ${item.code}`;
  const fresh = item.releaseDate && daysBetween(item.releaseDate, day) < NEW_DAYS;
  const details = [entry.lastWatched ? html`${icon("history")} ${watchedAgo(entry.lastWatched, day)}`
    : html`${icon("fiber_new")} Pas commencée`,
  plural(entry.remaining, "épisode restant", "épisodes restants"),
  item.runtime ? html`${icon("schedule")} ${fmtRuntime(item.runtime)}` : null].filter(Boolean);
  return html`<li class="row is-show">
    <a class="row-poster" href="${showHref(item.show)}" tabindex="-1" aria-hidden="true">${poster(
      item.posterUrl, item.title, "sm")}</a>
    <div class="row-main">
      <a class="row-title" href="${showHref(item.show)}">${item.title}</a>
      <p class="row-sub">${badge(item.code, "blue")} ${fresh ? badge("Nouveau", "green", "fiber_new") : ""}
        ${item.episodeTitle || ""}</p>
      <p class="row-meta">${details.reduce((a, b) => html`${a} · ${b}`)}</p>
    </div>
    <div class="row-actions"><button type="button" class="btn" data-action="home-seen"
      data-kind="${EPISODE}" data-ref="${item.ref}" data-label="${label}"
      aria-label="${`Vu : ${label}`}">${icon("check")} Vu</button></div>
  </li>`;
}

function upcomingBlock(days, day) {
  let shown = 0;
  const total = days.reduce((n, [, entries]) => n + entries.length, 0);
  const blocks = [];
  for (const [date, entries] of days) {
    const visible = entries.slice(0, UPCOMING_SHOWN - shown);
    if (!visible.length) break;
    shown += visible.length;
    blocks.push(html`<h3 class="home-day">${dayName(date, day)}</h3>
      <ul class="home-list" role="list">${visible.map((entry) => {
        const first = entry.items[0];
        const link = first.kind === EPISODE ? showHref(first.show) : movieHref(first.ref);
        return html`<li><a href="${link}">${first.title}</a>${first.kind === EPISODE
          ? badge(dayEntryLabel(entry), "blue") : badge("Film", "orange")}</li>`;
      })}</ul>`);
  }
  return html`<section aria-labelledby="home-upcoming">
    ${sectionTitle("home-upcoming", "event_upcoming", "Prochaines sorties")}
    ${days.length ? blocks : html`<p class="caption">Aucune sortie dans les ${UPCOMING_DAYS}
      prochains jours.</p>`}
    ${total > shown ? html`<p class="caption">Et ${plural(total - shown, "autre sortie",
      "autres sorties")} cette semaine.</p>` : ""}
    ${moreLink("#/calendrier", "Ouvrir le calendrier")}
  </section>`;
}

function recentBlock(recent, day) {
  return html`<section aria-labelledby="home-recent">
    ${sectionTitle("home-recent", "history", "Derniers visionnages")}
    ${recent.length ? html`<ul class="home-list" role="list">${recent.map((w) => html`<li>
      <a href="${w.kind === SHOW ? showHref(w.ref) : movieHref(w.ref)}">${w.title}${w.code
        ? ` · ${w.code}` : ""}</a><span class="caption">${watchedAgo(w.when, day)}</span></li>`)}</ul>`
      : html`<p class="caption">Rien de vu récemment (les dates d'import ne comptent pas).</p>`}
  </section>`;
}

function moviesBlock(items, total) {
  return html`<section aria-labelledby="home-movies">
    ${sectionTitle("home-movies", "movie", "Films à voir", total)}
    ${items.length ? html`<p class="caption">Les derniers ajoutés à ta bibliothèque d'abord.</p>
      <ul class="cards" role="list">${items.map((m) => html`<li class="card">
        <a href="${movieHref(m.ref)}" tabindex="-1" aria-hidden="true">${poster(m.posterUrl, m.title, "md")}</a>
        <a class="card-title" href="${movieHref(m.ref)}">${m.title}</a>
        <p class="card-meta">${[m.releaseDate?.slice(0, 4), fmtRuntime(m.runtime)].filter(Boolean)
          .join(" · ")}</p>
        <button type="button" class="btn" data-action="home-seen" data-kind="${MOVIE}"
          data-ref="${m.ref}" data-label="${m.title}" aria-label="${`Vu : ${m.title}`}">${icon("check")}
          Vu</button>
      </li>`)}</ul>`
      : html`<p class="caption">Aucun film sorti dans ta liste « En cours » ou « À voir ».</p>`}
  </section>`;
}

function suggestionsBlock(app) {
  const title = sectionTitle("home-suggestions", "recommend", "Suggestions pour toi");
  const wrap = (body) => html`<section aria-labelledby="home-suggestions">${title}${body}</section>`;
  if (!app.tmdb.configured) {
    return wrap(alert("info", html`Les suggestions ont besoin de TMDB :
      <a href="#/parametres">ajoute ta clé TMDB dans Paramètres</a>.`));
  }
  const result = savedSuggestions(app);
  if (!result) {
    return wrap(app.ui.suggRunning ? html`<p class="caption">Calcul des suggestions en cours…</p>`
      : html`<p>Pas encore de suggestions. <a href="#/suggestions">Calcule-les sur la page
        Suggestions</a> (une à deux minutes la première fois) : les meilleures s'afficheront ici.</p>`);
  }
  // Titres ajoutés depuis le calcul (ici ou ailleurs) : ce ne sont plus des suggestions.
  const showIds = new Set([...app.store.shows.values()].map((s) => s.tmdb_id).filter(Boolean));
  const inBase = (s) => (s.kind === SHOW ? showIds.has(s.tmdbId)
    : app.store.get("movies", `tmdb:${s.tmdbId}`));
  const items = result.items.filter((s) => !inBase(s)).slice(0, SUGGESTIONS_SHOWN);
  return wrap(html`${result.errors?.length ? html`<p class="caption warning-inline">Suggestions
    incomplètes (TMDB a mal répondu) : détail sur la page Suggestions.</p>` : ""}
    ${items.length ? html`<ul class="cards" role="list">${items.map((s) => html`<li class="card">
      <a href="${previewHref(s.kind, s.tmdbId)}" tabindex="-1" aria-hidden="true">${poster(s.posterUrl,
        s.title, "md")}</a>
      <a class="card-title" href="${previewHref(s.kind, s.tmdbId)}">${s.title}${s.year ? ` (${s.year})`
        : ""}</a>
      <p class="card-meta">${s.kind === MOVIE ? badge("Film", "orange") : badge("Série", "blue")}
        ${badge(`${s.match} % pour toi`, "green")}</p>
      ${s.streaming?.length ? html`<p class="card-meta">${s.streaming.join(", ")}</p>` : ""}
    </li>`)}</ul>` : html`<p class="caption">Aucune suggestion pour l'instant : ajoute et note des
      titres.</p>`}
    ${moreLink("#/suggestions", "Toutes les suggestions et leurs filtres")}`);
}

export function render(app) {
  const day = today();
  const date = `${weekday(day)} ${Number(day.slice(8, 10))} ${MONTHS[Number(day.slice(5, 7)) - 1]} `
    + day.slice(0, 4);
  if (!app.store.library.size) {
    return html`<h1>Accueil</h1><p class="caption">${date}</p>
      <div class="empty"><p>Ta bibliothèque est vide.</p>
      <p class="actions"><a class="btn" href="#/ajouter">Ajouter une série ou un film</a>
        <a class="btn" href="#/parametres">Importer depuis l'app Python</a></p></div>`;
  }
  const store = app.store;
  const next = continueWatching(store, { today: day });
  const movies = moviesToWatch(store, { today: day });
  const toCheck = [...store.shows.values()].filter((s) => MATCH_TO_CHECK.includes(s.match_status)
    && store.get("library", refKey(SHOW, s.key))).length;
  const last = app.ui.homeLastAction;
  return html`
  <h1>Accueil</h1>
  <p class="caption">${date}</p>
  ${toCheck ? html`<p class="caption">${plural(toCheck, "série")} à vérifier (correspondance TVMaze) :
    à corriger dans l'app Python (Paramètres), puis réexporter.</p>` : ""}
  ${last ? html`<p class="caption last-action">${last.text}
    <button type="button" class="btn" data-action="home-undo">Annuler</button></p>` : ""}
  ${keyMetrics(store, day)}
  <div class="home-cols">
    <section aria-labelledby="home-next">
      ${sectionTitle("home-next", "play_circle", "À suivre", next.total)}
      ${next.entries.length ? html`<p class="caption">Le prochain épisode de chaque série, celles
        regardées récemment d'abord.</p>
        <ul class="rows" role="list">${next.entries.map((e) => nextRow(e, day))}</ul>`
        : html`<p class="caption">Aucun épisode sorti à voir dans tes séries « En cours » ou
          « À voir ».</p>`}
      ${moreLink("#/a-regarder", "Tout voir dans À regarder")}
    </section>
    <div>
      ${upcomingBlock(upcoming(store, { today: day }), day)}
      ${recentBlock(recentWatches(store), day)}
    </div>
  </div>
  ${moviesBlock(movies.items, movies.total)}
  ${suggestionsBlock(app)}`;
}

export const actions = {
  "home-seen"(app, el) {
    markWatched(app.store, el.dataset.kind, el.dataset.ref);
    app.ui.homeLastAction = { text: `« ${el.dataset.label} » marqué vu.`,
      undo: [el.dataset.kind, el.dataset.ref] };
    app.announce(app.ui.homeLastAction.text);
  },
  "home-undo"(app) {
    const [kind, ref] = app.ui.homeLastAction?.undo || [];
    if (kind) unmarkWatched(app.store, kind, ref);
    app.ui.homeLastAction = null;
  },
};
