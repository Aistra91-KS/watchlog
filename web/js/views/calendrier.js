// Page « Calendrier » : sorties des séries et films suivis, en vue Mois, Semaine ou Liste,
// comme la page Python (calendrier.py).
// - Mois / Semaine : toutes les sorties de la période (les déjà vues en grisé, avec ✓).
// - Liste : les sorties non vues des 60 prochains jours, groupées par semaine.

import { addDays, daysBetween, fmtDate, today, weekStart } from "../core/dates.js";
import { EPISODE, MOVIE } from "../core/model.js";
import { calendar, dayEntryLabel, groupDay, releasesBetween } from "../core/queue.js";
import { html, movieHref, poster, raw, showHref } from "./dom.js";
import { icon } from "./icons.js";
import { loadPrefs, savePrefs } from "./prefs.js";
import { alert, metric, metrics, segmented } from "./ui.js";

const PREFS = "calendar";
const DEFAULTS = { view: "mois", type: "all", seen: true, specials: false };
const DAYS = 60;
const MAX_PER_DAY = 4;          // vue Mois : au-delà, « +N autre(s) »
const DAY_NAMES = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
const MONTH_NAMES = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
  "septembre", "octobre", "novembre", "décembre"];
const VIEWS = [["mois", "Mois"], ["semaine", "Semaine"], ["liste", "Liste"]];
const TYPES = [["all", "Tout"], [EPISODE, "Séries"], [MOVIE, "Films"]];

const weekday = (day) => (new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7;   // lundi = 0
const monthStart = (day) => `${day.slice(0, 7)}-01`;

function lastOfMonth(first) {
  const [y, m] = first.split("-").map(Number);
  return addDays(`${m === 12 ? y + 1 : y}-${String(m === 12 ? 1 : m + 1).padStart(2, "0")}-01`, -1);
}

function shiftMonth(first, step) {
  const [y, m] = first.split("-").map(Number);
  const month = m - 1 + step;
  const year = y + Math.floor(month / 12);
  return `${year}-${String(((month % 12) + 12) % 12 + 1).padStart(2, "0")}-01`;
}

function link(item) {
  return item.kind === EPISODE ? showHref(item.show) : movieHref(item.ref);
}

function longDay(day) {
  const [y, m, d] = day.split("-").map(Number);
  return `${DAY_NAMES[weekday(day)]} ${d} ${MONTH_NAMES[m - 1]} ${y}`;
}

/** Un titre dans une case : vignette + lien (Mois), grande affiche + lien (Semaine). */
function gridEntry(entry, big) {
  const first = entry.items[0];
  const seen = entry.items.every((i) => i.watched);
  const tip = `${seen ? "Déjà vu" : "Ouvrir la fiche"}${first.kind === EPISODE
    && entry.items.length === 1 && first.episodeTitle ? ` · ${first.episodeTitle}` : ""}`;
  return html`<li class="cal-item ${first.kind === MOVIE ? "is-movie" : ""} ${seen ? "is-seen" : ""}">
    ${poster(first.posterUrl, first.title, big ? "md" : "xs")}
    <a href="${link(first)}" title="${tip}">${seen ? html`<span aria-hidden="true">✓ </span><span
      class="sr-only">Déjà vu : </span>` : ""}${first.title} · ${dayEntryLabel(entry)}</a>
  </li>`;
}

function dayCell(day, items, todayDay, inPeriod, limit, big) {
  const entries = groupDay(items);
  const shown = limit ? entries.slice(0, limit) : entries;
  const state = day === todayDay ? "is-today" : inPeriod ? "" : "is-out";
  return html`<li class="cal-day ${state} ${entries.length ? "" : "is-empty"}">
    <div class="cal-num"><span class="sr-only">${longDay(day)}</span><span aria-hidden="true"><span
      class="wd">${DAY_NAMES[weekday(day)].slice(0, 3)}. </span>${Number(day.slice(8))}</span>
      ${day === todayDay ? html`<span class="tag-today">aujourd'hui</span>` : ""}</div>
    ${shown.length ? html`<ul class="cal-items" role="list">${shown.map((e) => gridEntry(e, big))}</ul>` : ""}
    ${limit && entries.length > limit ? html`<p class="cal-more">+${entries.length - limit} autre(s)</p>` : ""}
  </li>`;
}

/** Grille de 7 colonnes (lundi → dimanche). */
function grid(days, byDay, todayDay, inPeriod, limit, big = false) {
  return html`<div class="${big ? "cal-week" : ""}">
    <div class="cal-grid" aria-hidden="true">${DAY_NAMES.map((n) => html`<span class="cal-head">${n.slice(0, 3)}.</span>`)}</div>
    <ul class="cal-grid" role="list" aria-label="Sorties jour par jour">${days.map((d) => dayCell(d,
      byDay.get(d) || [], todayDay, inPeriod(d), limit, big))}</ul>
  </div>`;
}

/** Vue Liste : carte affiche + date + titre (lien vers la fiche) + épisode. */
function card(entry, todayDay) {
  const first = entry.items[0];
  const day = first.releaseDate;
  const when = day === todayDay ? "Aujourd'hui" : day === addDays(todayDay, 1) ? "Demain"
    : `${DAY_NAMES[weekday(day)].slice(0, 3)}. ${fmtDate(day).slice(0, 5)}`;
  let detail = dayEntryLabel(entry);
  if (first.kind === EPISODE && entry.items.length === 1 && first.episodeTitle) {
    detail += ` · ${first.episodeTitle}`;
  }
  return html`<li class="cal-card ${first.kind === MOVIE ? "is-movie" : ""}">
    ${poster(first.posterUrl, first.title, "sm")}
    <div><span class="cal-card-date">${when}</span>
      <a href="${link(first)}">${first.title}</a>
      <span class="caption">${detail}</span></div>
  </li>`;
}

function listView(items, todayDay) {
  if (!items.length) return alert("info", `Aucune sortie prévue dans les ${DAYS} prochains jours.`);
  const weeks = new Map();
  for (const item of items) {
    const monday = weekStart(item.releaseDate);
    if (!weeks.has(monday)) weeks.set(monday, []);
    weeks.get(monday).push(item);
  }
  return [...weeks].map(([monday, list]) => {
    const byDay = new Map();
    for (const item of list) {
      if (!byDay.has(item.releaseDate)) byDay.set(item.releaseDate, []);
      byDay.get(item.releaseDate).push(item);
    }
    return html`<section><h2>${monday === weekStart(todayDay) ? "Cette semaine"
      : `Semaine du lundi ${fmtDate(monday)}`}</h2>
      <ul class="cal-cards" role="list">${[...byDay.values()].flatMap((dayItems) => groupDay(dayItems))
        .map((entry) => card(entry, todayDay))}</ul></section>`;
  });
}

export function render(app) {
  const prefs = loadPrefs(PREFS, DEFAULTS);
  const view = VIEWS.some(([v]) => v === prefs.view) ? prefs.view : "mois";
  const day = today();
  const keep = (item) => (prefs.type === "all" || item.kind === prefs.type)
    && (prefs.seen || !item.watched);

  // Résumé en chiffres (sorties non vues à venir)
  const upcoming = calendar(app.store, { days: DAYS, today: day });
  const weekEnd = addDays(day, 6);
  const later = upcoming.filter((i) => i.releaseDate > day);
  const next = later[0];
  const summary = metrics([
    metric("Aujourd'hui", upcoming.filter((i) => i.releaseDate === day).length),
    metric("7 prochains jours", upcoming.filter((i) => i.releaseDate <= weekEnd).length),
    metric(`Films (${DAYS} j)`, upcoming.filter((i) => i.kind === MOVIE).length),
    next ? metric("Prochaine sortie", `J-${daysBetween(day, next.releaseDate)}`, { delta: next.title })
      : metric("Prochaine sortie", "—"),
  ], { label: "Résumé des sorties" });

  const controls = html`<form class="cols" style="--cols: 2fr 2fr 1.5fr 1.5fr" aria-label="Affichage">
    ${segmented("Vue", "view", VIEWS, view, "cal-view")}
    ${segmented("Type", "type", TYPES, prefs.type, "cal-type")}
    ${view !== "liste" ? html`<label class="check"><input type="checkbox" data-change="cal-flag"
      name="seen" ${prefs.seen ? raw("checked") : ""}> Épisodes et films vus</label>` : html`<span></span>`}
    <label class="check"><input type="checkbox" data-change="cal-flag" name="specials"
      ${prefs.specials ? raw("checked") : ""}> Épisodes spéciaux</label>
  </form>`;

  if (view === "liste") {
    const items = (prefs.specials ? calendar(app.store, { days: DAYS, today: day, includeSpecials: true })
      : upcoming).filter(keep);
    return html`<h1>Calendrier</h1>${summary}${controls}
      <p class="caption">Sorties non vues des ${DAYS} prochains jours (séries et films suivis, hors
        séries arrêtées ou terminées). Clic sur un titre : sa fiche.</p>
      ${listView(items, day)}`;
  }

  let start;
  let last;
  let first;
  let end;
  let title;
  if (view === "mois") {
    start = app.ui.calMonth || monthStart(day);
    last = lastOfMonth(start);
    first = weekStart(start);
    end = addDays(weekStart(last), 6);
    const [y, m] = start.split("-").map(Number);
    title = `${MONTH_NAMES[m - 1][0].toUpperCase()}${MONTH_NAMES[m - 1].slice(1)} ${y}`;
  } else {
    start = app.ui.calWeek || weekStart(day);
    last = addDays(start, 6);
    first = start;
    end = last;
    title = `Semaine du ${fmtDate(start)} au ${fmtDate(last)}`;
  }
  const items = releasesBetween(app.store, first, end, { includeSpecials: prefs.specials, today: day })
    .filter(keep);
  const byDay = new Map();
  for (const item of items) {
    if (!byDay.has(item.releaseDate)) byDay.set(item.releaseDate, []);
    byDay.get(item.releaseDate).push(item);
  }
  const days = [];
  for (let d = first; d <= end; d = addDays(d, 1)) days.push(d);
  const inPeriod = items.filter((i) => start <= i.releaseDate && i.releaseDate <= last);
  const seen = inPeriod.filter((i) => i.watched).length;
  return html`<h1>Calendrier</h1>${summary}${controls}
    <div class="cal-nav">
      <button type="button" class="btn" data-action="cal-shift" data-step="-1" data-start="${start}"
        title="Période précédente">${icon("chevron_left")} Précédent</button>
      <h2 data-announce>${title}</h2>
      <button type="button" class="btn" data-action="cal-shift" data-step="0">Aujourd'hui</button>
      <button type="button" class="btn" data-action="cal-shift" data-step="1" data-start="${start}"
        title="Période suivante">${icon("chevron_right")} Suivant</button>
    </div>
    ${view === "mois" ? grid(days, byDay, day, (d) => d.slice(0, 7) === start.slice(0, 7), MAX_PER_DAY)
      : grid(days, byDay, day, () => true, null, true)}
    <p class="caption">${inPeriod.length} sortie(s) sur la période, dont ${seen} déjà vue(s).
      Liseré orange : film. Clic sur un titre : sa fiche.</p>`;
}

function update(change) {
  savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), ...change });
}

export const actions = {
  "cal-view"(app, el) { update({ view: el.value }); },
  "cal-type"(app, el) { update({ type: el.value }); },
  "cal-flag"(app, el) { update({ [el.name]: el.checked }); },
  "cal-shift"(app, el) {
    const { view } = loadPrefs(PREFS, DEFAULTS);
    const step = Number(el.dataset.step);
    const day = today();
    if (view === "semaine") {
      app.ui.calWeek = step === 0 ? weekStart(day) : addDays(el.dataset.start, 7 * step);
    } else {
      app.ui.calMonth = step === 0 ? monthStart(day) : shiftMonth(el.dataset.start, step);
    }
  },
};
