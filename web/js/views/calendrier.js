// Page « Calendrier » : sorties non vues des prochaines semaines, groupées par semaine et jour.

import { fmtDate, fmtLongDay, today, weekStart } from "../core/dates.js";
import { EPISODE } from "../core/model.js";
import { calendar, dayEntryLabel, groupDay } from "../core/queue.js";
import { html, movieHref, options, poster, raw, showHref } from "./dom.js";
import { loadPrefs, savePrefs } from "./prefs.js";

const PREFS = "calendar";
const DEFAULTS = { days: 60, specials: false };

export function render(app) {
  const prefs = loadPrefs(PREFS, DEFAULTS);
  const day = today();
  const items = calendar(app.store, { days: Number(prefs.days), today: day,
    includeSpecials: prefs.specials });
  const weeks = new Map();
  for (const item of items) {
    const week = weekStart(item.releaseDate);
    if (!weeks.has(week)) weeks.set(week, new Map());
    const days = weeks.get(week);
    if (!days.has(item.releaseDate)) days.set(item.releaseDate, []);
    days.get(item.releaseDate).push(item);
  }
  return html`
  <h1>Calendrier</h1>
  <form class="filters" aria-label="Période">
    <label class="field"><span>Période</span>
      <select data-change="calendar-days">${options([["14", "2 semaines"], ["30", "30 jours"],
        ["60", "60 jours"], ["120", "4 mois"]], String(prefs.days))}</select></label>
    <label class="check"><input type="checkbox" data-change="calendar-specials"
      ${prefs.specials ? raw("checked") : ""}> Épisodes spéciaux</label>
  </form>
  <p class="muted">Séries et films suivis (sauf arrêtés et terminés), pas encore vus.</p>
  ${weeks.size ? [...weeks].map(([week, days]) => html`<section class="week">
    <h2>Semaine du ${fmtDate(week)}</h2>
    ${[...days].map(([date, list]) => html`<div class="day ${date === day ? "is-today" : ""}">
      <h3>${date === day ? "Aujourd'hui" : fmtLongDay(date)}</h3>
      <ul class="rows" role="list">${groupDay(list).map((entry) => {
        const first = entry.items[0];
        const link = first.kind === EPISODE ? showHref(first.show) : movieHref(first.ref);
        return html`<li class="row ${first.kind === EPISODE ? "is-show" : "is-movie"}">
          ${poster(first.posterUrl, first.title, "sm")}
          <div class="row-main"><a class="row-title" href="${link}">${first.title}</a>
            <p class="row-sub"><span class="code">${dayEntryLabel(entry)}</span>
              ${entry.items.length === 1 ? first.episodeTitle || "" : ""}</p></div>
        </li>`;
      })}</ul>
    </div>`)}
  </section>`) : html`<p class="empty">Aucune sortie prévue sur cette période.</p>`}`;
}

export const actions = {
  "calendar-days"(app, el) { savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), days: Number(el.value) }); },
  "calendar-specials"(app, el) {
    savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), specials: el.checked });
  },
};
