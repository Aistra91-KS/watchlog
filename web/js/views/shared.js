// Morceaux d'interface et actions utilisés par plusieurs pages (bouton « Vu », statut,
// favori, note). Les actions lisent data-kind / data-ref sur l'élément cliqué.

import { fmtDate } from "../core/dates.js";
import {
  addRewatch, getLibraryItem, getRating, getWatch, markWatched, removeFromLibrary,
  removeRewatch, setFavorite, setRating, setStatus, setWatchDate, splitEmotions, unmarkWatched,
} from "../core/library.js";
import { EMOTION_LABELS, STATUSES, STATUS_LABELS } from "../core/model.js";
import { html, options, raw } from "./dom.js";

/** Sélecteur de statut (ajoute à la bibliothèque si besoin). */
export function statusSelect(store, kind, ref, id) {
  const item = getLibraryItem(store, kind, ref);
  const pairs = [...(item ? [] : [["", "Pas dans ma bibliothèque"]]),
    ...STATUSES.map((s) => [s, STATUS_LABELS[s]])];
  return html`<label class="field"><span>Statut</span>
    <select id="${id}" data-change="status" data-kind="${kind}" data-ref="${ref}">
      ${options(pairs, item?.status || "")}</select></label>`;
}

export function favoriteButton(store, kind, ref) {
  const on = Boolean(getLibraryItem(store, kind, ref)?.favorite);
  return html`<button type="button" class="btn btn-ghost" aria-pressed="${on ? "true" : "false"}" data-action="favorite"
    data-kind="${kind}" data-ref="${ref}" data-focus="fav-${ref}">
    <span aria-hidden="true">${on ? "★" : "☆"}</span> Favori</button>`;
}

/** Bouton « Vu » / « Pas vu » d'un épisode ou d'un film. */
export function watchButton(store, kind, ref, label = "") {
  const seen = Boolean(getWatch(store, kind, ref));
  const name = label ? ` : ${label}` : "";
  return seen
    ? html`<button type="button" class="btn btn-seen" data-action="unwatch" data-kind="${kind}"
        data-ref="${ref}" data-focus="w-${ref}" aria-label="${`Vu${name}. Retirer le visionnage`}">
        <span aria-hidden="true">✓</span> Vu</button>`
    : html`<button type="button" class="btn btn-watch" data-action="watch" data-kind="${kind}"
        data-ref="${ref}" data-focus="w-${ref}" aria-label="${`Vu ? Marquer comme vu${name}`}">Vu ?</button>`;
}

/** Note 1 à 5 en boutons radio (accessibles au clavier), émotions et commentaire. */
export function ratingForm(store, kind, ref) {
  const r = getRating(store, kind, ref) || {};
  const emotions = new Set(splitEmotions(r.emotions));
  return html`<form class="rating" data-submit="rating" data-kind="${kind}" data-ref="${ref}">
    <fieldset class="stars"><legend>Ma note</legend>
      ${[1, 2, 3, 4, 5].map((n) => html`<label><input type="radio" name="rating" value="${n}"
        ${r.rating === n ? raw("checked") : ""}><span aria-hidden="true">★</span>
        <span class="sr-only">${n} sur 5</span></label>`)}
      <label class="no-rating"><input type="radio" name="rating" value=""
        ${r.rating == null ? raw("checked") : ""}> Pas de note</label>
    </fieldset>
    <fieldset class="chips"><legend>Émotions</legend>
      ${Object.entries(EMOTION_LABELS).map(([code, label]) => html`<label class="chip">
        <input type="checkbox" name="emotions" value="${code}" ${emotions.has(code) ? raw("checked") : ""}>
        ${label}</label>`)}
    </fieldset>
    <label class="field"><span>Personnage préféré</span>
      <input name="character" value="${r.favorite_character || ""}" autocomplete="off"></label>
    <label class="field"><span>Commentaire</span>
      <textarea name="note" rows="2">${r.note || ""}</textarea></label>
    <button class="btn">Enregistrer l'avis</button>
  </form>`;
}

/** Ligne « Vu le … · 2 visionnages » + date modifiable + revisionnage, pour une fiche film. */
export function watchDetails(store, kind, ref) {
  const w = getWatch(store, kind, ref);
  if (!w) return "";
  const day = w.watched_at ? w.watched_at.slice(0, 10) : "";
  return html`<div class="watch-details">
    <p>${w.watched_at ? `Vu le ${fmtDate(w.watched_at)}` : "Vu (date inconnue)"}
      ${w.plays > 1 ? ` · ${w.plays} visionnages` : ""}</p>
    <label class="field field-inline"><span>Date de visionnage</span>
      <input type="date" value="${day}" data-change="watch-date" data-kind="${kind}" data-ref="${ref}"></label>
    <button type="button" class="btn btn-ghost" data-action="rewatch" data-kind="${kind}"
      data-ref="${ref}">+1 revisionnage</button>
    ${w.plays > 1 ? html`<button type="button" class="btn btn-ghost" data-action="unrewatch"
      data-kind="${kind}" data-ref="${ref}">−1 revisionnage</button>` : ""}
  </div>`;
}

/** « Retirer de la bibliothèque » avec confirmation dans la page. */
export function removeButton(kind, ref) {
  return html`<div class="confirm" data-confirm>
    <button type="button" class="btn btn-ghost btn-danger" data-action="remove-library"
      data-kind="${kind}" data-ref="${ref}" id="remove-ask">Retirer de la bibliothèque</button>
    <span class="confirm-box" role="group" aria-label="Confirmer le retrait">
      Historique et notes gardés.
      <button type="button" class="btn btn-danger" data-action="remove-library" data-confirm-yes
        data-kind="${kind}" data-ref="${ref}">Oui, retirer</button>
      <button type="button" class="btn btn-ghost" data-action="cancel-confirm">Annuler</button>
    </span></div>`;
}

const target = (el) => [el.dataset.kind, el.dataset.ref];

export const actions = {
  watch(app, el) {
    markWatched(app.store, ...target(el));
  },
  unwatch(app, el) {
    unmarkWatched(app.store, ...target(el));
  },
  rewatch(app, el) {
    addRewatch(app.store, ...target(el));
  },
  unrewatch(app, el) {
    removeRewatch(app.store, ...target(el));
  },
  "watch-date"(app, el) {
    setWatchDate(app.store, ...target(el), el.value || null);
  },
  status(app, el) {
    if (!el.value) return false;
    setStatus(app.store, ...target(el), el.value);
    app.toast(`Statut : ${STATUS_LABELS[el.value]}.`);
    return true;
  },
  favorite(app, el) {
    const [kind, ref] = target(el);
    setFavorite(app.store, kind, ref, !getLibraryItem(app.store, kind, ref)?.favorite);
  },
  "remove-library"(app, el) {
    // Premier bouton : demande confirmation (pas de confirm(), bloqué dans certains cadres).
    if (!el.hasAttribute("data-confirm-yes")) {
      const box = el.closest("[data-confirm]");
      box.classList.add("confirming");
      box.querySelector("[data-confirm-yes]")?.focus();
      return false;
    }
    removeFromLibrary(app.store, ...target(el));
    app.toast("Retiré de la bibliothèque (historique et notes gardés).");
    return true;
  },
  "cancel-confirm"(app, el) {
    const box = el.closest("[data-confirm]");
    box?.classList.remove("confirming");
    box?.querySelector("button")?.focus();   // retour au bouton qui a ouvert la confirmation
    return false;
  },
  rating(app, form) {
    const data = new FormData(form);
    const value = data.get("rating");
    setRating(app.store, form.dataset.kind, form.dataset.ref, {
      rating: value ? Number(value) : null, note: data.get("note"),
      emotions: data.getAll("emotions"), favoriteCharacter: data.get("character"),
    });
    app.toast("Avis enregistré.");
  },
};
