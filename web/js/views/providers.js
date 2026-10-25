// Bloc « Où regarder » d'une fiche (plateformes en France, JustWatch via TMDB), présenté comme
// common.show_availability : logos par type d'offre (abonnement, location...), lien vers les prix.

import { REGION, parseProviders } from "../core/providers.js";
import { externalLink, html, safeImageUrl, safeUrl } from "./dom.js";

export async function providersBlock(app, media, tmdbId) {
  if (!app.tmdb.configured) return "";
  let availability;
  try {
    const results = await app.tmdb.watchProviders(media, tmdbId);
    availability = parseProviders(results?.[REGION] || {});
  } catch (error) {
    console.warn(`Plateformes non lues (${media} ${tmdbId})`, error);
    return html`<p><strong>Où regarder</strong></p><p class="caption">Plateformes indisponibles pour
      l'instant (${error.message}).</p>`;
  }
  const mine = new Set(app.store.setting("my_platforms", []));
  const offers = Object.entries(availability.offers);
  if (!offers.length) {
    return html`<p><strong>Où regarder</strong></p><p class="caption">Pas disponible en streaming,
      location ou achat en France pour l'instant.</p>`;
  }
  return html`<div class="providers"><p><strong>Où regarder</strong></p>
    <dl>${offers.map(([label, list]) => html`<div>
      <dt>${label}</dt>
      <dd>${list.map((p) => html`<span class="provider ${mine.has(p.name) ? "is-mine" : ""}"
        title="${p.name}">${safeImageUrl(p.logo) ? html`<img src="${p.logo}" alt="" width="32" height="32"
        loading="lazy">` : ""}<span class="name">${p.name}${mine.has(p.name)
        ? " (une de mes plateformes)" : ""}</span></span>`)}</dd>
    </div>`)}</dl>
    ${safeUrl(availability.link) ? html`<p class="caption">${externalLink(availability.link,
      "Voir les offres et les prix")}</p>` : ""}
  </div>`;
}
