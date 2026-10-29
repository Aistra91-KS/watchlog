// Page « Statistiques » : chiffres clés, temps pour finir la liste, puis onglets Activité,
// Séries, Films et Notes, comme la page Python (statistiques.py). Les jours d'import
// (historique daté du jour de l'import) sont exclus des graphiques par date, et c'est dit.

import { fmtDate, today } from "../core/dates.js";
import { formatRating, getRating, ratingStars } from "../core/library.js";
import { COMPLETED, EPISODE, FOR_LATER, MOVIE, SHOW, TO_WATCH, WATCHING } from "../core/model.js";
import {
  PACE_PERIODS, bulkDays, dailyCounts, finishEstimate, formatDuration, formatSpan, genreCounts,
  globalCounts, movieDecades, movieRuntimeStats, perMonth, perWeekday, perYear,
  ratingDistribution, records, remainingTime, showProgress, timeSpent, topRatings,
  topShowsByTime, watchPace, watchesFrame, withoutDays,
} from "../core/stats.js";
import { hbars, heatmap, monthLabel, stackedBars } from "./charts.js";
import { html, movieHref, raw, showHref } from "./dom.js";
import { loadPrefs, savePrefs } from "./prefs.js";
import { alert, currentTab, metric, metrics, tabPanel, tabs } from "./ui.js";

const PREFS = "stats";
const DEFAULTS = { withLater: false, ignoreBulk: true };
const TABS = ["Activité", "Séries", "Films", "Notes"];
const plural = (n, word) => `${n.toLocaleString("fr-FR")} ${word}${n > 1 ? "s" : ""}`;
const comma = (n, digits = 1) => n.toFixed(digits).replace(".", ",");

/** Durée courte pour une tuile : « 2 711 h » (ou « 45 min »), comme hours() côté Python. */
function hours(minutes) {
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60).toLocaleString("fr-FR")} h`;
}

function table(headers, rows, { label }) {
  return html`<div class="tbl-wrap"><table class="tbl" aria-label="${label}">
    <thead><tr>${headers.map((h) => html`<th scope="col" class="${h.num ? "num" : ""}">${h.label}</th>`)}</tr></thead>
    <tbody>${rows.map((r) => html`<tr>${r.map((c, i) => html`<td class="${headers[i].num ? "num" : ""}">${c}</td>`)}</tr>`)}</tbody>
  </table></div>`;
}

function finishSection(store, frame, bulk, day, withLater) {
  const statuses = withLater ? [WATCHING, TO_WATCH, FOR_LATER] : [WATCHING, TO_WATCH];
  const remaining = remainingTime(store, day, statuses);
  let estimated = remaining.estimated;
  const cards = PACE_PERIODS.map(([title, days]) => {
    const pace = watchPace(frame, days, bulk.keys(), day);
    estimated += pace.estimated;
    const end = finishEstimate(remaining.totalMinutes, pace.perDay, day);
    const help = `${formatDuration(Math.round(pace.perDay))} par jour : ${plural(pace.watches,
      "visionnage")} en ${plural(pace.days, "jour")}, depuis le ${fmtDate(pace.start)}.`;
    if (!end) return metric(title, "—", { delta: "rien vu sur la période", help });
    if (end.days === 0) return metric(title, "Liste finie", { help });
    return metric(title, formatSpan(day, end.date), { delta: `fin vers le ${fmtDate(end.date)}`, help });
  });
  let note = `En cours et À voir${withLater ? ", Plus tard" : ""} ; hors épisodes spéciaux. Rythme : `
    + "nouveaux visionnages (un revisionnage ne fait pas avancer la liste), jours d'import "
    + "exclus. Les prochaines sorties allongeront la liste.";
  if (estimated) note += ` Durée inconnue estimée (moyenne) pour ${estimated} élément(s).`;
  return html`<section><h2>Pour finir ta liste</h2>
    <p><label class="switch"><input type="checkbox" role="switch" data-change="stats-pref"
      name="withLater" ${withLater ? raw("checked") : ""}> Compter aussi « Plus tard »</label></p>
    ${metrics([metric("Reste à voir", hours(remaining.totalMinutes), {
      help: `${plural(remaining.episodes, "épisode")} et ${plural(remaining.movies, "film")} déjà sortis.` }),
    ...cards], { label: "Temps pour finir ta liste" })}
    <p class="caption">${note}</p>
  </section>`;
}

function activityTab(app, frame, bulk, day, prefs) {
  const width = app.ui.contentWidth || 900;
  const half = width >= 800 ? (width - 32) / 2 : width;
  const ignore = prefs.ignoreBulk && bulk.size > 0;
  const dated = ignore ? withoutDays(frame, bulk.keys()) : frame;
  const rec = records(dated);
  const weekdays = perWeekday(dated);
  const best = weekdays.reduce((a, b) => (b.count > a.count ? b : a), weekdays[0]);
  const monthly = perMonth(dated, 12, day).map((r) => ({ ...r, label: monthLabel(r.month) }));
  const hourly = perMonth(dated, 12, day, "hours").map((r) => ({ ...r, label: monthLabel(r.month) }));
  return html`
    ${bulk.size ? html`<p><label class="switch"><input type="checkbox" role="switch"
      data-change="stats-pref" name="ignoreBulk" ${prefs.ignoreBulk ? raw("checked") : ""}>
      Ignorer les jours d'import</label></p>
      ${ignore ? html`<p class="caption">${plural([...bulk.values()].reduce((a, b) => a + b, 0),
        "visionnage")} datés du jour de l'import (${[...bulk.keys()].map(fmtDate).join(", ")}) ne
        sont pas comptés ci-dessous.</p>` : ""}` : ""}
    ${metrics([
      rec.bestDay ? metric("Jour record (visionnages)", rec.bestDay[1], { delta: fmtDate(rec.bestDay[0]) }) : "",
      rec.streak ? metric("Jours d'affilée (record)", rec.streak[0], {
        delta: `${fmtDate(rec.streak[1])} → ${fmtDate(rec.streak[2])}` }) : "",
      rec.bestMonth ? metric("Mois record (visionnages)", rec.bestMonth[1],
        { delta: monthLabel(rec.bestMonth[0]) }) : "",
      best?.count ? metric("Jour préféré", best.label, { delta: `${best.count} visionnage(s)` }) : "",
    ], { label: "Records" })}
    <h2>Les 12 derniers mois, jour par jour</h2>
    ${heatmap(dailyCounts(dated, 365, day), { title: "Visionnages par jour sur un an", width })}
    <h2>Vus par mois</h2>
    <p class="caption">Date du premier visionnage.</p>
    ${stackedBars(monthly, { title: "Épisodes et films vus par mois, 12 derniers mois", width })}
    <h2>Heures passées par mois</h2>
    ${stackedBars(hourly, { title: "Heures passées par mois", unit: " h", width })}
    <div class="charts-2">
      <div><h2>Par jour de la semaine</h2>
        ${hbars(weekdays.map((d) => ({ label: d.label, value: d.count })),
          { title: "Visionnages par jour de la semaine" })}</div>
      <div><h2>Par année</h2>
        ${stackedBars(perYear(dated).map((r) => ({ ...r, label: String(r.year) })),
          { title: "Épisodes et films vus par année", width: half })}</div>
    </div>`;
}

function showsTab(store, frame, day) {
  const progress = showProgress(store, day);
  const completed = [...store.library.values()].filter((l) => l.kind === SHOW && l.status === COMPLETED)
    .map((l) => {
      const rows = frame.filter((r) => r.kind === EPISODE && r.show === l.ref);
      const last = rows.map((r) => r.day).filter(Boolean).sort().at(-1);
      return { key: l.ref, title: store.get("shows", l.ref)?.title || "?", episodes: rows.length, last };
    }).sort((a, b) => (b.last || "").localeCompare(a.last || ""));
  return html`
    <div class="charts-2">
      <div><h2>Où passe ton temps</h2>
        ${hbars(topShowsByTime(frame).map((s) => ({ label: s.title, value: s.minutes,
          text: `${formatDuration(s.minutes)} · ${s.episodes} ép.` })),
          { title: "Séries où tu as passé le plus de temps" })}</div>
      <div><h2>Genres des séries regardées</h2>
        ${genreCounts(frame, EPISODE).length ? hbars(genreCounts(frame, EPISODE).map((g) => ({
          label: g.genre, value: g.count })), { title: "Genres des séries regardées" })
          : html`<p>Genres pas encore connus (ils arrivent avec la mise à jour).</p>`}</div>
    </div>
    <h2>Séries en cours (${progress.length})</h2>
    ${progress.length ? html`${table([{ label: "Série" }, { label: "Avancement" }, { label: "Vus", num: true },
      { label: "Diffusés", num: true }, { label: "À voir", num: true }], progress.map((p) => [
      html`<a href="${showHref(p.show)}">${p.title}</a>`,
      html`<span class="hbar-inline"><span class="track" aria-hidden="true"><span class="fill"
        style="width:${p.percent}%"></span></span> ${p.percent} %</span>`,
      p.seen, p.aired, p.aired - p.seen]), { label: "Séries en cours" })}
      <p class="caption">Épisodes vus sur épisodes déjà diffusés (hors spéciaux).</p>`
      : html`<p>Aucune série en cours.</p>`}
    <h2>Séries terminées (${completed.length})</h2>
    ${completed.length ? table([{ label: "Série" }, { label: "Épisodes vus", num: true },
      { label: "Dernier visionnage" }], completed.map((c) => [html`<a href="${showHref(c.key)}">${c.title}</a>`,
      c.episodes, c.last ? fmtDate(c.last) : ""]), { label: "Séries terminées" })
      : html`<p>Aucune série terminée pour l'instant.</p>`}`;
}

function moviesTab(store, frame) {
  const runtimes = movieRuntimeStats(frame);
  const movies = frame.filter((r) => r.kind === MOVIE)
    .sort((a, b) => (b.day || "").localeCompare(a.day || "") || a.title.localeCompare(b.title, "fr"));
  const genres = genreCounts(frame, MOVIE);
  const decades = movieDecades(frame);
  return html`
    ${metrics([
      metric("Films vus", movies.length),
      runtimes ? metric("Durée moyenne", formatDuration(runtimes.average)) : "",
      runtimes ? metric("Le plus long", formatDuration(runtimes.longest[1]), { delta: runtimes.longest[0] }) : "",
      runtimes ? metric("Le plus court", formatDuration(runtimes.shortest[1]), { delta: runtimes.shortest[0] }) : "",
    ], { label: "Films en chiffres" })}
    <div class="charts-2">
      <div><h2>Genres des films vus</h2>
        ${genres.length ? hbars(genres.map((g) => ({ label: g.genre, value: g.count })),
          { title: "Genres des films vus", kind: "movie" })
          : html`<p>Genres pas encore connus (ils arrivent avec la mise à jour).</p>`}</div>
      <div><h2>Par décennie de sortie</h2>
        ${decades.length ? hbars(decades.map((d) => ({ label: d.label, value: d.count })),
          { title: "Films vus par décennie de sortie", kind: "movie" })
          : html`<p>Années de sortie inconnues.</p>`}</div>
    </div>
    <h2>Films vus (${movies.length})</h2>
    ${movies.length ? html`${table([{ label: "Film" }, { label: "Vu le" }, { label: "Note" },
      { label: "Durée" }, { label: "Sortie" }, { label: "Genres" }, { label: "Vu" }], movies.map((m) => {
      const rating = getRating(store, MOVIE, m.ref)?.rating;
      return [html`<a href="${movieHref(m.ref)}">${m.title}</a>`, m.day ? fmtDate(m.day) : "",
        rating ? html`<span aria-hidden="true">${ratingStars(rating)}</span><span
          class="sr-only">${formatRating(rating)} sur 5</span>` : "",
        m.runtime ? formatDuration(m.runtime) : "", m.releaseYear || "", m.genres || "",
        m.plays > 1 ? `${m.plays} fois` : ""];
    }), { label: "Films vus" })}
      <p class="caption">Date inconnue : vide (modifiable dans la fiche).</p>`
      : html`<p>Aucun film vu pour l'instant.</p>`}`;
}

function notesTab(store) {
  const notes = ratingDistribution(store);
  const best = topRatings(store);
  return html`<div class="charts-2">
    <div><h2>Répartition des notes</h2>
      ${notes.some((n) => n.count) ? hbars(notes.map((n) => ({ label: html`<span aria-hidden="true">${n.label}</span><span
        class="sr-only">${formatRating(n.rating)} sur 5</span>`, value: n.count })),
        { title: "Répartition des notes" })
        : html`<p>Aucune note pour l'instant.</p>`}
      <p class="caption">Noter tes séries et films affine aussi les suggestions.</p></div>
    <div><h2>Meilleures notes</h2>
      ${best.length ? table([{ label: "Titre" }, { label: "Type" }, { label: "Note" }], best.map((b) => [
        b.title, b.kind, html`<span aria-hidden="true">${ratingStars(b.rating)}</span><span
          class="sr-only">${formatRating(b.rating)} sur 5</span>`]), { label: "Meilleures notes" })
        : html`<p>Aucune note pour l'instant.</p>`}</div>
  </div>`;
}

export function render(app) {
  const day = today();
  const store = app.store;
  if (!store.watches.size) {
    return html`<h1>Statistiques</h1>${alert("info", "Rien de vu pour l'instant : marque des épisodes ou des "
      + "films comme vus pour voir tes statistiques.")}`;
  }
  const prefs = loadPrefs(PREFS, DEFAULTS);
  const frame = watchesFrame(store);
  const counts = globalCounts(store);
  const time = timeSpent(frame);
  const bulk = bulkDays(frame);
  const notes = ratingDistribution(store);
  const rated = notes.reduce((a, n) => a + n.count, 0);
  const average = rated ? notes.reduce((a, n) => a + n.rating * n.count, 0) / rated : null;
  const year = day.slice(0, 4);
  const thisYear = frame.filter((r) => r.day?.startsWith(year)).length;
  const missing = [time.episodesWithoutRuntime && plural(time.episodesWithoutRuntime, "épisode"),
    time.moviesWithoutRuntime && plural(time.moviesWithoutRuntime, "film")].filter(Boolean);
  const tab = currentTab(app, "stats", TABS);
  const body = { "Activité": () => activityTab(app, frame, bulk, day, prefs),
    "Séries": () => showsTab(store, frame, day), "Films": () => moviesTab(store, frame),
    "Notes": () => notesTab(store) }[tab]();

  return html`
  <h1>Statistiques</h1>
  ${metrics([
    metric("Séries suivies", counts.shows),
    metric("Séries terminées", counts.completedShows),
    metric("Épisodes vus", counts.episodesWatched.toLocaleString("fr-FR")),
    metric("Films suivis", counts.movies),
    metric("Films vus", counts.moviesWatched.toLocaleString("fr-FR")),
  ], { label: "Chiffres clés", five: true })}
  ${metrics([
    metric("Temps total", `${comma(time.totalMinutes / 1440)} jours`, { help: formatDuration(time.totalMinutes) }),
    metric("Temps en épisodes", hours(time.episodeMinutes), { help: formatDuration(time.episodeMinutes) }),
    metric("Temps en films", hours(time.movieMinutes), { help: formatDuration(time.movieMinutes) }),
    metric("Note moyenne", average ? `${comma(average)} / 5` : "—", { help: `Sur ${rated} note(s)` }),
    metric(`Vus en ${year}`, thisYear.toLocaleString("fr-FR"),
      { help: "Épisodes et films (premier visionnage)" }),
  ], { label: "Temps et notes", five: true })}
  ${missing.length ? html`<p class="caption">Temps : durée inconnue, non comptée pour
    ${missing.join(" et ")}.</p>` : ""}
  ${finishSection(store, frame, bulk, day, prefs.withLater)}
  ${tabs(app, "stats", TABS, "Statistiques détaillées")}
  ${tabPanel(app, "stats", TABS, body)}`;
}

export const actions = {
  "stats-pref"(app, el) { savePrefs(PREFS, { ...loadPrefs(PREFS, DEFAULTS), [el.name]: el.checked }); },
};
