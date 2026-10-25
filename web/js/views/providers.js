// Bloc « Où regarder » d'une fiche (plateformes en France, JustWatch via TMDB).

import { ATTRIBUTION, REGION, parseProviders } from "../core/providers.js";
import { html, safeUrl } from "./dom.js";

export async function providersBlock(app, media, tmdbId) {
  if (!app.tmdb.configured) return "";
  let availability;
  try {
    const results = await app.tmdb.watchProviders(media, tmdbId);
    availability = parseProviders(results[REGION] || {});
  } catch (error) {
    console.warn(`Plateformes non lues (${media} ${tmdbId})`, error);
    return html`<section><h2>Où regarder</h2><p class="muted">Plateformes indisponibles pour
      l'instant (${error.message}).</p></section>`;
  }
  const mine = new Set(app.store.setting("my_platforms", []));
  const offers = Object.entries(availability.offers);
  return html`<section class="providers"><h2>Où regarder</h2>
    ${offers.length ? html`<dl>${offers.map(([label, list]) => html`<div>
      <dt>${label}</dt>
      <dd>${list.map((p) => html`<span class="provider ${mine.has(p.name) ? "is-mine" : ""}">
        ${safeUrl(p.logo) ? html`<img src="${p.logo}" alt="" width="24" height="24" loading="lazy">` : ""}
        ${p.name}${mine.has(p.name) ? html`<span class="sr-only"> (une de mes plateformes)</span>` : ""}</span>`)}</dd>
    </div>`)}</dl>` : html`<p class="muted">Pas disponible en France pour l'instant.</p>`}
    <p class="credit">${ATTRIBUTION}${safeUrl(availability.link) ? html` <a href="${availability.link}"
      rel="noopener" target="_blank">Détail sur TMDB</a>` : ""}</p>
  </section>`;
}
