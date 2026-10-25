// Petits outils d'affichage : HTML échappé, affiches, liens. Toute donnée venant d'une API
// ou de l'utilisateur passe par `html` (échappement) : pas d'injection de HTML.

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/** Marque un morceau de HTML déjà sûr (produit par `html`). */
export class Raw {
  constructor(text) { this.text = text; }

  toString() { return this.text; }
}

export const raw = (text) => new Raw(text);

function render(value) {
  if (value instanceof Raw) return value.text;
  if (Array.isArray(value)) return value.map(render).join("");
  if (value === null || value === undefined || value === false) return "";
  return esc(value);
}

/** Gabarit HTML : chaque valeur insérée est échappée, sauf `raw(...)` et les tableaux de `html`. */
export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((value, i) => { out += render(value) + strings[i + 1]; });
  return raw(out);
}

/** URL externe utilisable (https seulement : un fichier importé pourrait contenir
 *  « javascript:... »). Sinon null. */
export function safeUrl(url) {
  return typeof url === "string" && /^https:\/\/[^\s"'<>]+$/i.test(url) ? url : null;
}

export const href =(...parts) => `#/${parts.map((p) => encodeURIComponent(p)).join("/")}`;

export function showHref(key) { return href("serie", key); }

export function movieHref(key) { return href("film", key); }

/** Affiche (ou un cadre avec l'initiale si elle manque). Texte alternatif vide : le titre
 *  est toujours écrit à côté, l'image est décorative. */
export function poster(url, title, size = "md") {
  if (safeUrl(url)) {
    return html`<img class="poster poster-${size}" src="${url}" alt="" loading="lazy"
      decoding="async" referrerpolicy="no-referrer">`;
  }
  return html`<span class="poster poster-${size} poster-empty" aria-hidden="true">${(title || "?")[0]}</span>`;
}

export function kindBadge(kind) {
  return kind === "movie" ? html`<span class="badge badge-movie">Film</span>`
    : html`<span class="badge badge-show">Série</span>`;
}

/** Options d'un <select>, avec la valeur choisie. */
export function options(pairs, selected) {
  return pairs.map(([value, label]) => html`<option value="${value}" ${value === selected
    ? raw("selected") : ""}>${label}</option>`);
}
