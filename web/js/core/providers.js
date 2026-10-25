// Plateformes en France (données JustWatch via TMDB), port de watchlog/core/providers.py.

export const REGION = "FR";
export const LOGO_BASE = "https://image.tmdb.org/t/p/w92";
export const ATTRIBUTION = "Disponibilités en France : JustWatch (via TMDB).";
// Types d'offre TMDB, dans l'ordre d'affichage
export const OFFER_LABELS = {
  flatrate: "Abonnement", free: "Gratuit", ads: "Gratuit avec pub", rent: "Location", buy: "Achat",
};
// Offres sans paiement à l'unité : « dispo en streaming », « sur mes plateformes »
export const STREAMING_OFFERS = ["Abonnement", "Gratuit", "Gratuit avec pub"];
const UNKNOWN_PRIORITY = 999;

/** Bloc d'un pays de la réponse TMDB -> {offers: {libellé: [{name, logo}]}, link}. */
export function parseProviders(regionData = {}) {
  const offers = {};
  for (const [key, label] of Object.entries(OFFER_LABELS)) {
    const entries = [...(regionData[key] || [])]
      .sort((a, b) => (a.display_priority ?? UNKNOWN_PRIORITY) - (b.display_priority ?? UNKNOWN_PRIORITY));
    const providers = entries.filter((e) => e.provider_name).map((e) => ({
      name: e.provider_name, logo: e.logo_path ? `${LOGO_BASE}${e.logo_path}` : null,
    }));
    if (providers.length) offers[label] = providers;
  }
  return { offers, link: regionData.link || null };
}

/** Plateformes où regarder en abonnement ou gratuitement, sans doublon. */
export function streamingNames(availability) {
  const names = [];
  for (const offer of STREAMING_OFFERS) {
    for (const p of availability.offers[offer] || []) if (!names.includes(p.name)) names.push(p.name);
  }
  return names;
}

/** Plateformes proposées pour « Mes plateformes » (les plus courantes d'abord) ; celles déjà
 *  choisies mais absentes de la liste sont gardées à la fin. */
export function platformChoices(lists, saved = []) {
  const priorities = new Map();
  for (const entry of lists.flat()) {
    const name = entry.provider_name;
    if (!name) continue;
    const priority = entry.display_priorities?.[REGION] ?? entry.display_priority ?? UNKNOWN_PRIORITY;
    priorities.set(name, Math.min(priority, priorities.get(name) ?? UNKNOWN_PRIORITY));
  }
  const names = [...priorities.keys()].sort((a, b) => (priorities.get(a) - priorities.get(b))
    || a.localeCompare(b, "fr", { sensitivity: "base" }));
  return [...names, ...saved.filter((n) => !priorities.has(n))];
}
