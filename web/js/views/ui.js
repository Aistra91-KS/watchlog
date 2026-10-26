// Composants d'interface qui reprennent ceux de l'app Python (Streamlit) : métrique, pastille,
// alerte, expander, onglets, contrôle segmenté. Même aspect des deux côtés (css/style.css).

import { html, raw } from "./dom.js";
import { icon } from "./icons.js";

/** st.metric : libellé, grand chiffre, pastille grise facultative (delta), aide en dessous. */
export function metric(label, value, { delta = "", help = "", bordered = false } = {}) {
  return html`<li class="metric ${bordered ? "bordered" : ""}">
    <span class="metric-label">${label}${help ? helpTip(help) : ""}</span>
    <span class="metric-value" title="${String(value)}">${value}</span>
    ${delta ? html`<span class="metric-delta">${delta}</span>` : ""}
  </li>`;
}

/** Icône (?) avec infobulle (help= de Streamlit) : visible au survol et au clavier, lue par
 *  les lecteurs d'écran. */
export function helpTip(text) {
  return html`<span class="tip" tabindex="0" data-tip="${text}">${icon("help")}<span
    class="sr-only">Aide : ${text}</span></span>`;
}

/** Rangée de métriques (st.columns + st.metric). */
export function metrics(items, { label = "", five = false } = {}) {
  return html`<ul class="metrics ${five ? "metrics-5" : ""}" role="list"
    ${label ? html`aria-label="${label}"` : ""}>${items}</ul>`;
}

/** :blue-badge[...] / :orange-badge[...] / :green-badge[...] / :gray-badge[...] */
export function badge(text, color = "blue", iconName = "") {
  return html`<span class="badge badge-${color}">${iconName ? icon(iconName) : ""}${text}</span>`;
}

/** st.info / st.warning / st.success / st.error */
export function alert(kind, content, { role = "" } = {}) {
  const roleAttr = role ? html`role="${role}"` : "";
  return html`<div class="alert alert-${kind}" ${roleAttr}>${content}</div>`;
}

/** st.expander : bloc dépliable bordé ; `keep` garde son état (ouvert / fermé) entre deux
 *  rendus. */
export function expander(label, body, { open = false, keep = "", iconName = "" } = {}) {
  const keepAttr = keep ? html`data-keep="${keep}"` : "";
  return html`<details class="expander" ${keepAttr} ${open ? raw("open") : ""}>
    <summary><span class="chevron">${icon("chevron_right")}</span>${iconName ? icon(iconName) : ""}
      <span>${label}</span></summary>
    <div class="expander-body">${body}</div>
  </details>`;
}

/** st.tabs : onglets (role="tab", flèches du clavier gérées par main.js). L'onglet choisi
 *  est gardé dans app.ui.tabs[name] ; seul son contenu est construit par la page. */
export function tabs(app, name, labels, title) {
  const current = currentTab(app, name, labels);
  const tab = (label, i) => html`<button type="button" role="tab" id="${`tab-${name}-${i}`}"
    data-action="tab" data-tabs="${name}" data-value="${label}"
    aria-selected="${label === current ? "true" : "false"}" aria-controls="${`panel-${name}`}"
    tabindex="${label === current ? "0" : "-1"}">${label}</button>`;
  return html`<div class="tabs" role="tablist" aria-label="${title}">${labels.map(tab)}</div>`;
}

export function currentTab(app, name, labels) {
  const chosen = app.ui.tabs?.[name];
  return labels.includes(chosen) ? chosen : labels[0];
}

/** Zone de l'onglet choisi (à placer juste après `tabs`). */
export function tabPanel(app, name, labels, body) {
  const index = labels.indexOf(currentTab(app, name, labels));
  return html`<div role="tabpanel" id="${`panel-${name}`}"
    aria-labelledby="${`tab-${name}-${index}`}">${body}</div>`;
}

/** st.segmented_control : boutons radio collés. pairs = [[valeur, libellé], ...]. */
export function segmented(legend, name, pairs, value, change) {
  return html`<fieldset class="segmented"><legend>${legend}</legend><div class="segments">
    ${pairs.map(([v, label]) => html`<label><input type="radio" name="${name}" value="${v}"
      data-change="${change}" ${v === value ? raw("checked") : ""}>${label}</label>`)}
  </div></fieldset>`;
}

export const actions = {
  tab(app, el) {
    app.ui.tabs = { ...app.ui.tabs, [el.dataset.tabs]: el.dataset.value };
    app.ui.focusAfter = el.id;
  },
};

/** st.multiselect : liste repliée qui affiche le choix (ou `placeholder`), cases à cocher
 *  dedans (au clavier et au doigt). pairs = [[valeur, libellé], ...]. */
export function multiSelect(label, name, pairs, chosen, { placeholder = "", change, help = "" } = {}) {
  const picked = pairs.filter(([v]) => chosen.includes(v)).map(([, l]) => l);
  return html`<div class="field ms-field"><span id="${`ms-${name}`}">${label}${help ? helpTip(help) : ""}</span>
    <details class="ms" data-keep="${`ms-${name}`}">
      <summary aria-describedby="${`ms-${name}`}"><span class="ms-value ${picked.length ? "" : "is-empty"}">${
        picked.length ? picked.join(", ") : placeholder}</span>${icon("expand_more")}</summary>
      <fieldset class="ms-panel"><legend class="sr-only">${label}</legend>
        ${pairs.length ? pairs.map(([v, l]) => html`<label class="check"><input type="checkbox" name="${name}"
          value="${v}" data-change="${change}" ${chosen.includes(v) ? raw("checked") : ""}> ${l}</label>`)
          : html`<p class="caption">Aucun choix possible.</p>`}
      </fieldset>
    </details></div>`;
}

/** st.slider : curseur avec sa valeur affichée. Deux bornes (plage) si `value` est un tableau. */
export function slider(label, name, { min, max, step = 1, value, change, format = String }) {
  if (Array.isArray(value)) {
    const [lo, hi] = value;
    return html`<fieldset class="slider"><legend>${label} <span class="slider-value">${format(lo)} – ${format(hi)}</span></legend>
      <input type="range" name="${`${name}-min`}" min="${min}" max="${max}" step="${step}" value="${lo}"
        data-change="${change}" aria-label="${`${label} : début`}" aria-valuetext="${format(lo)}">
      <input type="range" name="${`${name}-max`}" min="${min}" max="${max}" step="${step}" value="${hi}"
        data-change="${change}" aria-label="${`${label} : fin`}" aria-valuetext="${format(hi)}">
    </fieldset>`;
  }
  return html`<label class="slider"><span>${label} <span class="slider-value">${format(value)}</span></span>
    <input type="range" name="${name}" min="${min}" max="${max}" step="${step}" value="${value}"
      data-change="${change}" aria-valuetext="${format(value)}"></label>`;
}
