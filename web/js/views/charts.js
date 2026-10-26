// Graphiques en SVG, sans bibliothèque : barres empilées (épisodes en bleu, films en orange),
// barres horizontales, calendrier de chaleur. Chaque marque a une infobulle (<title>) et
// chaque graphique un tableau des données (lecteurs d'écran, daltonisme).

import { fmtDate } from "../core/dates.js";
import { esc, html, raw } from "./dom.js";

const MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.",
  "nov.", "déc."];

export function monthLabel(month) {
  const [y, m] = month.split("-");
  return `${MONTHS[Number(m) - 1]} ${y.slice(2)}`;
}

const fmt = (n) => (Number.isInteger(n) ? String(n) : n.toLocaleString("fr-FR"));

/** Graduation « ronde » au-dessus du maximum (1, 2, 5 × 10^n). */
function niceMax(max) {
  if (max <= 0) return 1;
  const step = 10 ** Math.floor(Math.log10(max));
  return [1, 2, 5, 10].map((k) => k * step).find((v) => v >= max);
}

export function legend() {
  return html`<p class="legend" aria-hidden="true"><span><span class="swatch bar-show-bg"></span>Épisodes</span>
    <span><span class="swatch bar-movie-bg"></span>Films</span></p>`;
}

function dataTable(caption, headers, rows) {
  return html`<details class="data-table" data-keep="${`table-${caption}`}"><summary>Voir les données
    <span class="sr-only">: ${caption}</span></summary>
    <div class="chart-wrap"><table><caption>${caption}</caption>
      <thead><tr>${headers.map((h) => html`<th scope="col">${h}</th>`)}</tr></thead>
      <tbody>${rows.map((r) => html`<tr>${r.map((c, i) => (i === 0 ? html`<th scope="row">${c}</th>`
        : html`<td>${c}</td>`))}</tr>`)}</tbody></table></div></details>`;
}

// Largeur des graphiques : celle de la colonne qui les reçoit (mesurée par main.js), bornée
// pour que le texte garde sa taille ; plus étroit, le graphique défile horizontalement.
const MIN_WIDTH = 480;
const MAX_WIDTH = 1600;
const chartWidth = (width) => Math.round(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, width || 640)));

/** Barres empilées par catégorie : rows = [{label, episodes, movies}]. */
export function stackedBars(rows, { title, unit = "", width: wanted = 640, height = 260 }) {
  const width = chartWidth(wanted);
  const pad = { top: 12, right: 8, bottom: 28, left: 36 };
  const max = niceMax(Math.max(0, ...rows.map((r) => r.episodes + r.movies)));
  const plotH = height - pad.top - pad.bottom;
  const slot = (width - pad.left - pad.right) / rows.length;
  const barW = Math.max(4, Math.min(56, slot * 0.6));
  const y = (v) => pad.top + plotH - (v / max) * plotH;
  const ticks = [0, max / 2, max];
  // Barres à bouts arrondis seulement côté données ; 2 px d'écart entre les deux segments.
  const bars = rows.map((r, i) => {
    const x = pad.left + i * slot + (slot - barW) / 2;
    const hE = (r.episodes / max) * plotH;
    const hM = (r.movies / max) * plotH;
    const gap = hE && hM ? 2 : 0;
    const tip = esc(`${r.label} : ${fmt(r.episodes)}${unit} épisodes, ${fmt(r.movies)}${unit} films`);
    return raw(`<g><title>${tip}</title>
      <rect x="${x}" y="${pad.top}" width="${barW}" height="${plotH}" fill="transparent"/>
      ${hE ? `<rect class="bar-show" x="${x}" y="${y(r.episodes)}" width="${barW}" height="${hE}" rx="2"/>` : ""}
      ${hM ? `<rect class="bar-movie" x="${x}" y="${y(r.episodes) - hM - gap}" width="${barW}" height="${hM}" rx="2"/>` : ""}
      <text x="${x + barW / 2}" y="${height - 8}" text-anchor="middle">${esc(r.label)}</text></g>`);
  });
  return html`<figure class="chart-block">
    ${legend()}
    <div class="chart-wrap" data-scroll-end tabindex="0" role="region" aria-label="${title}"><svg class="chart" viewBox="0 0 ${width} ${height}" role="img"
      aria-label="${title}" preserveAspectRatio="xMinYMin meet">
      ${ticks.map((t) => raw(`<line class="grid" x1="${pad.left}" x2="${width - pad.right}"
        y1="${y(t)}" y2="${y(t)}"/><text x="${pad.left - 6}" y="${y(t) + 4}" text-anchor="end">${fmt(t)}</text>`))}
      ${bars}
    </svg></div>
    ${dataTable(title, ["", `Épisodes${unit}`, `Films${unit}`], rows.map((r) => [r.label, fmt(r.episodes), fmt(r.movies)]))}
  </figure>`;
}

/** Barres horizontales : items = [{label, value, text?}], kind = "show" | "movie". */
export function hbars(items, { title, kind = "show" }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return html`<figure class="chart-block">
    <ul class="hbars" role="list" aria-label="${title}">${items.map((i) => html`<li class="hbar">
      <span>${i.label}</span>
      <span class="track" aria-hidden="true"><span class="fill ${kind === "movie" ? "is-movie" : ""}"
        style="width:${((i.value / max) * 100).toFixed(1)}%"></span></span>
      <span class="value">${i.text ?? fmt(i.value)}</span></li>`)}</ul>
  </figure>`;
}

/** Calendrier de chaleur des 12 derniers mois : une case par jour, une colonne par semaine.
 *  Les cases remplissent la largeur disponible (comme le graphique de l'app Python). */
export function heatmap(days, { title, width: wanted = 0 }) {
  const gapPx = 2;
  const first = new Date(`${days[0].day}T12:00:00Z`);
  const offset = (first.getUTCDay() + 6) % 7;          // lundi = 0
  const max = Math.max(1, ...days.map((d) => d.count));
  const level = (n) => (n === 0 ? 0 : Math.min(5, Math.ceil((n / max) * 5)));
  const weeks = Math.ceil((days.length + offset) / 7);
  const cell = Math.max(9, Math.min(22, Math.floor((wanted - 28) / weeks) - gapPx));
  const width = 28 + weeks * (cell + gapPx);
  const height = 7 * (cell + gapPx) + 4;
  const cells = days.map((d, i) => {
    const pos = i + offset;
    const x = 28 + Math.floor(pos / 7) * (cell + gapPx);
    const y = (pos % 7) * (cell + gapPx);
    const tip = `${fmtDate(d.day)} : ${d.count} visionnage${d.count > 1 ? "s" : ""}`;
    return `<rect class="heat-${level(d.count)}" x="${x}" y="${y}" width="${cell}" height="${cell}"
      rx="2"><title>${tip}</title></rect>`;
  }).join("");
  const labels = [[0, "lun."], [2, "mer."], [4, "ven."]]
    .map(([row, t]) => `<text x="0" y="${row * (cell + gapPx) + cell * 0.8}">${t}</text>`).join("");
  const active = days.filter((d) => d.count);
  return html`<figure class="chart-block">
    <div class="chart-wrap" data-scroll-end tabindex="0" role="region" aria-label="${title}"><svg class="chart heatmap" viewBox="0 0 ${width} ${height}" width="${width}"
      height="${height}" role="img" aria-label="${`${title} : ${active.length} jours avec au moins un visionnage`}">
      ${raw(labels + cells)}</svg></div>
    <p class="legend" aria-hidden="true">Moins ${[0, 1, 2, 3, 4, 5].map((n) => html`<span
      class="swatch heat-bg-${n}"></span>`)} Plus</p>
    ${dataTable(`${title} : les jours les plus chargés`, ["Jour", "Visionnages"],
      [...active].sort((a, b) => b.count - a.count || b.day.localeCompare(a.day)).slice(0, 15)
        .map((d) => [fmtDate(d.day), d.count]))}
  </figure>`;
}
