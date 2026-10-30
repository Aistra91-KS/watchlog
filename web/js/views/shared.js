// Morceaux d'interface et actions utilisés par plusieurs pages (statut, favori, avis,
// retrait). Les actions lisent data-kind / data-ref sur l'élément cliqué.

import {
  RATING_VALUES, addRewatch, formatRating, getLibraryItem, getRating, markWatched,
  removeFromLibrary, removeRewatch, setFavorite, setRating, setStatus, setWatchDate,
  splitEmotions, unmarkWatched,
} from "../core/library.js";
import { characterLabel, movieCast, showCast } from "../core/cast.js";
import { EMOTION_LABELS, SHOW, STATUSES, STATUS_LABELS, refKey } from "../core/model.js";
import { html, options, raw, safeImageUrl } from "./dom.js";
import { expander } from "./ui.js";

// Réglage (IndexedDB) du dernier calcul de suggestions : page Suggestions, lu par l'accueil.
export const SAVED_SUGGESTIONS = "suggestions_result";

/** Dernier calcul des suggestions (sans les traits), ou null s'il n'y en a pas encore. */
export function savedSuggestions(app) {
  return app.ui.suggestions || app.store.setting(SAVED_SUGGESTIONS, null);
}

/** Sélecteur de statut (ajoute à la bibliothèque si besoin). */
export function statusSelect(store, kind, ref, id) {
  const item = getLibraryItem(store, kind, ref);
  const pairs = [...(item ? [] : [["", "Pas dans ma bibliothèque"]]),
    ...STATUSES.map((s) => [s, STATUS_LABELS[s]])];
  return html`<label class="field"><span>Statut</span>
    <select id="${id}" data-change="status" data-kind="${kind}" data-ref="${ref}">
      ${options(pairs, item?.status || "")}</select></label>`;
}

/** Case « Favori » (comme la fiche Python). */
export function favoriteCheck(store, kind, ref) {
  const on = Boolean(getLibraryItem(store, kind, ref)?.favorite);
  return html`<label class="check"><input type="checkbox" data-change="favorite-check"
    data-kind="${kind}" data-ref="${ref}" data-focus="fav-${ref}" ${on ? raw("checked") : ""}>
    Favori</label>`;
}

/** Personnages du titre pour « Personnage préféré » : {characters, errors}. Gardés pour la
 *  session (les clients d'API ont aussi leur cache) ; après une panne, nouvel essai au
 *  prochain affichage. */
export async function loadCast(app, kind, ref) {
  app.ui.cast ??= new Map();
  const key = refKey(kind, ref);
  if (app.ui.cast.has(key)) return app.ui.cast.get(key);
  const record = app.store.get(kind === SHOW ? "shows" : "movies", ref);
  if (!record) return { characters: [], errors: [] };
  const found = kind === SHOW ? await showCast(app.tvmaze, app.tmdb, record)
    : await movieCast(app.tmdb, record);
  if (!found.errors.length) app.ui.cast.set(key, found);
  return found;
}

/** « Personnage préféré » : saisie libre avec la liste « Personnage (Acteur) », et les
 *  photos des personnages (un clic remplit le champ), pour retrouver un nom oublié. */
function characterField(ref, value, cast) {
  const characters = cast?.characters || [];
  const withPhoto = characters.filter((c) => safeImageUrl(c.photoUrl));
  const listId = `cast-${ref}`;
  return html`<label class="field"><span>Personnage préféré</span>
      <input name="character" value="${value}" autocomplete="off"
        ${characters.length ? html`list="${listId}"` : ""}></label>
    ${characters.length ? html`<datalist id="${listId}">${characters.map((c) => html`<option
      value="${c.name}">${characterLabel(c)}</option>`)}</datalist>` : ""}
    ${withPhoto.length ? expander("Revoir les personnages (photos)", html`
      <p class="caption">Touche un personnage pour le choisir (puis « Enregistrer »).</p>
      <ul class="cast-grid" role="list">${withPhoto.map((c) => html`<li><button type="button"
        class="cast-pick" data-action="pick-character" data-name="${c.name}"
        aria-label="${`Choisir ${characterLabel(c)}`}"><img src="${c.photoUrl}" alt="" loading="lazy"
        referrerpolicy="no-referrer"><span class="cast-name">${c.name}</span>${c.actor
        ? html`<span class="cast-actor">(${c.actor})</span>` : ""}</button></li>`)}</ul>`,
    { keep: "cast-photos", iconName: "photo_library" }) : ""}
    ${cast?.errors?.length ? html`<p class="caption">Liste des personnages incomplète :
      ${cast.errors.join(" et ")} n'a pas répondu.</p>` : ""}`;
}

/** « Mon avis » (avis.review_form) : note, commentaire, émotions, personnage préféré.
 *  `cast` : personnages proposés (loadCast), facultatif. */
export function ratingForm(store, kind, ref, cast = null) {
  const r = getRating(store, kind, ref) || {};
  const emotions = new Set(splitEmotions(r.emotions));
  const notes = [["", "Pas de note"],
    ...RATING_VALUES.map((n) => [String(n), `${formatRating(n)} / 5`])];
  return html`<h2>Mon avis</h2>
  <form class="rating box stack" data-submit="rating" data-kind="${kind}" data-ref="${ref}">
    <label class="field"><span>Note</span><select name="rating">${options(notes,
      r.rating ? String(r.rating) : "")}</select></label>
    <label class="field"><span>Commentaire</span>
      <textarea name="note" rows="3">${r.note || ""}</textarea></label>
    <fieldset class="chips"><legend>Émotions</legend>
      ${Object.entries(EMOTION_LABELS).map(([code, label]) => html`<label class="chip">
        <input type="checkbox" name="emotions" value="${code}" ${emotions.has(code) ? raw("checked") : ""}>
        ${label}</label>`)}
    </fieldset>
    ${characterField(ref, r.favorite_character || "", cast)}
    <button class="btn">Enregistrer</button>
  </form>`;
}

/** « Retirer de la bibliothèque » avec confirmation dans la page (après un séparateur). */
export function removeButton(kind, ref) {
  return html`<hr><div class="confirm" data-confirm>
    <button type="button" class="btn" data-action="remove-library"
      data-kind="${kind}" data-ref="${ref}" id="remove-ask">Retirer de la bibliothèque</button>
    <span class="confirm-box" role="group" aria-label="Confirmer le retrait">
      Retirer de la bibliothèque ? L'historique et les notes sont gardés.
      <button type="button" class="btn" data-action="remove-library" data-confirm-yes
        data-kind="${kind}" data-ref="${ref}">Oui, retirer</button>
      <button type="button" class="btn" data-action="cancel-confirm">Annuler</button>
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
  "favorite-check"(app, el) {
    const [kind, ref] = target(el);
    setFavorite(app.store, kind, ref, el.checked);
    app.toast(el.checked ? "Ajouté aux favoris." : "Retiré des favoris.");
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
  "pick-character"(app, el) {
    // Remplit le champ sans redessiner la page : le reste du formulaire n'est pas enregistré.
    const input = el.closest("form").querySelector('input[name="character"]');
    input.value = el.dataset.name;
    input.focus();
    app.announce(`Personnage préféré : ${el.dataset.name}. Enregistre pour le garder.`);
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
