// Page « Statistiques » : chiffres clés, temps pour finir la liste, activité, séries, films,
// notes. Les jours d'import (historique daté du jour de l'import) sont exclus des graphiques
// par date, et c'est dit.

import { fmtDate, fmtRuntime, today } from "../core/dates.js";
import { EPISODE, MOVIE } from "../core/model.js";
import {
  PACE_PERIODS, bulkDays, dailyCounts, finishEstimate, formatDuration, genreCounts,
  globalCounts, movieDecades, movieRuntimeStats, perMonth, perWeekday, perYear,
  ratingDistribution, records, remainingTime, showProgress, timeSpent, topRatings,
  topShowsByTime, watchPace, watchesFrame, withoutDays,
} from "../core/stats.js";
import { hbars, heatmap, monthLabel, stackedBars } from "./charts.js";
import { html, showHref } from "./dom.js";

const kpi = (value, label) => html`<li class="kpi"><strong>${value}</strong><span>${label}</span></li>`;
const plural = (n, word) => `${n.toLocaleString("fr-FR")} ${word}${n > 1 ? "s" : ""}`;

export function render(app) {
  const day = today();
  const store = app.store;
  if (!store.watches.size) {
    return html`<h1>Statistiques</h1><p class="empty">Rien de vu pour l'instant : marque des
      épisodes ou des films comme vus pour voir tes statistiques.</p>`;
  }
  const frame = watchesFrame(store);
  const counts = globalCounts(store);
  const time = timeSpent(frame);
  const bulk = bulkDays(frame);
  const dated = withoutDays(frame, bulk.keys());
  const remaining = remainingTime(store, day);
  const rec = records(dated);
  const monthly = perMonth(dated, 12, day).map((r) => ({ ...r, label: monthLabel(r.month) }));
  const hours = perMonth(dated, 12, day, "hours").map((r) => ({ ...r, label: monthLabel(r.month) }));
  const runtimes = movieRuntimeStats(frame);
  const progress = showProgress(store, day);
  const notes = ratingDistribution(store);
  const best = topRatings(store);
  const missing = [time.episodesWithoutRuntime && plural(time.episodesWithoutRuntime, "épisode"),
    time.moviesWithoutRuntime && plural(time.moviesWithoutRuntime, "film")].filter(Boolean);

  return html`
  <h1>Statistiques</h1>
  <ul class="kpis">
    ${kpi(counts.shows, "séries suivies")}
    ${kpi(counts.movies, "films suivis")}
    ${kpi(counts.episodesWatched.toLocaleString("fr-FR"), "épisodes vus")}
    ${kpi(counts.moviesWatched.toLocaleString("fr-FR"), "films vus")}
    ${kpi(formatDuration(time.totalMinutes), "devant l'écran")}
  </ul>
  ${missing.length ? html`<p class="muted small">Temps : durée inconnue, non comptée pour
    ${missing.join(" et ")}.</p>` : ""}

  <section><h2>Pour finir ta liste</h2>
    <p>${plural(remaining.episodes, "épisode")} et ${plural(remaining.movies, "film")} déjà
      sortis à voir (séries en cours et à voir) : <strong>${formatDuration(remaining.totalMinutes)}</strong>
      ${remaining.estimated ? html` <span class="muted">(${remaining.estimated} durée(s) estimée(s))</span>` : ""}.</p>
    <ul class="kpis">${PACE_PERIODS.map(([label, days]) => {
      const pace = watchPace(frame, days, bulk.keys(), day);
      const end = finishEstimate(remaining.totalMinutes, pace.perDay, day);
      return kpi(end ? fmtDate(end.date) : "—",
        `${label} (${formatDuration(Math.round(pace.perDay))} par jour)`);
    })}</ul>
    <p class="muted small">Un minimum : la liste grossit avec les nouvelles sorties.</p>
  </section>

  <section><h2>Activité</h2>
    ${bulk.size ? html`<p class="muted small">${plural([...bulk.values()].reduce((a, b) => a + b, 0),
      "visionnage")} datés d'un jour d'import (${[...bulk.keys()].map(fmtDate).join(", ")}) :
      exclus des graphiques par date.</p>` : ""}
    <h3>Les 12 derniers mois, jour par jour</h3>
    ${heatmap(dailyCounts(dated, 365, day), { title: "Visionnages par jour sur un an" })}
    <ul class="kpis">
      ${rec.bestDay ? kpi(fmtDate(rec.bestDay[0]), `jour record (${rec.bestDay[1]} visionnages)`) : ""}
      ${rec.streak ? kpi(plural(rec.streak[0], "jour"), "d'affilée, au plus long") : ""}
      ${rec.bestMonth ? kpi(monthLabel(rec.bestMonth[0]), `mois record (${rec.bestMonth[1]})`) : ""}
    </ul>
    <h3>Vus par mois</h3>
    ${stackedBars(monthly, { title: "Épisodes et films vus par mois, 12 derniers mois" })}
    <h3>Heures passées par mois</h3>
    ${stackedBars(hours, { title: "Heures passées par mois", unit: " h" })}
    <h3>Par jour de la semaine</h3>
    ${hbars(perWeekday(dated).map((d) => ({ label: d.label, value: d.count })),
      { title: "Visionnages par jour de la semaine" })}
    <h3>Par année</h3>
    ${stackedBars(perYear(dated).map((r) => ({ ...r, label: String(r.year) })),
      { title: "Épisodes et films vus par année" })}
  </section>

  <section><h2>Séries</h2>
    <h3>Où passe ton temps</h3>
    ${hbars(topShowsByTime(frame).map((s) => ({ label: s.title, value: s.minutes,
      text: `${formatDuration(s.minutes)} · ${s.episodes} ép.` })), { title: "Séries où tu as passé le plus de temps" })}
    <h3>Genres des séries regardées</h3>
    ${hbars(genreCounts(frame, EPISODE).map((g) => ({ label: g.genre, value: g.count })),
      { title: "Genres des séries regardées" })}
    <h3>Séries en cours (${progress.length})</h3>
    ${progress.length ? html`<ul class="hbars">${progress.map((p) => html`<li class="hbar">
      <a href="${showHref(p.show)}">${p.title}</a>
      <span class="track" aria-hidden="true"><span class="fill" style="width:${p.percent}%"></span></span>
      <span class="value">${p.seen}/${p.aired}</span></li>`)}</ul>` : html`<p class="muted">Aucune.</p>`}
    <p class="muted small">Épisodes vus sur épisodes déjà diffusés (hors spéciaux).
      ${counts.completedShows} série(s) terminée(s).</p>
  </section>

  <section><h2>Films</h2>
    ${runtimes ? html`<ul class="kpis">
      ${kpi(fmtRuntime(runtimes.average), "durée moyenne")}
      ${kpi(fmtRuntime(runtimes.longest[1]), `le plus long : ${runtimes.longest[0]}`)}
      ${kpi(fmtRuntime(runtimes.shortest[1]), `le plus court : ${runtimes.shortest[0]}`)}
    </ul>` : ""}
    <h3>Genres des films vus</h3>
    ${hbars(genreCounts(frame, MOVIE).map((g) => ({ label: g.genre, value: g.count })),
      { title: "Genres des films vus", kind: "movie" })}
    <h3>Par décennie de sortie</h3>
    ${hbars(movieDecades(frame).map((d) => ({ label: d.label, value: d.count })),
      { title: "Films vus par décennie de sortie", kind: "movie" })}
  </section>

  <section><h2>Notes</h2>
    ${notes.some((n) => n.count) ? html`
      ${hbars(notes.map((n) => ({ label: html`<span aria-label="${`${n.rating} sur 5`}">${n.label}</span>`,
        value: n.count })), { title: "Répartition des notes" })}
      <h3>Meilleures notes</h3>
      <ol>${best.map((b) => html`<li>${b.title} <span class="muted">(${b.kind}, ${b.rating}/5)</span></li>`)}</ol>`
      : html`<p class="muted">Aucune note pour l'instant. Noter tes séries et films affine aussi
        les suggestions.</p>`}
  </section>`;
}
