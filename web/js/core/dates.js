// Dates : en mémoire et dans le fichier d'échange, un instant est une chaîne ISO UTC
// (« 2026-10-25T18:30:00Z ») et un jour une chaîne « AAAA-MM-JJ ». Affichage en heure de Paris,
// comme l'app Python. Les chaînes « AAAA-MM-JJ » se comparent directement (ordre lexical).

export const TIME_ZONE = "Europe/Paris";

// Format « AAAA-MM-JJ » dans le fuseau de Paris (en-CA écrit les dates dans cet ordre).
const DAY_FORMAT = new Intl.DateTimeFormat("en-CA", {
  timeZone: TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit",
});
const PARTS_FORMAT = new Intl.DateTimeFormat("en-US", {
  timeZone: TIME_ZONE, hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric",
  hour: "numeric", minute: "numeric", second: "numeric",
});

/** Instant (Date ou ms) -> « 2026-10-25T18:30:00Z » (secondes, sans millisecondes). */
export function isoUtc(when = new Date()) {
  return new Date(when).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** Jour à Paris d'un instant ISO UTC (ou d'une Date). */
export function parisDay(instant) {
  return DAY_FORMAT.format(new Date(instant));
}

/** Aujourd'hui à Paris, « AAAA-MM-JJ ». */
export function today(now = new Date()) {
  return parisDay(now);
}

/** Date de diffusion en heure française (airstamp UTC), sinon la date TVMaze. */
export function localDate(airstamp, airdate) {
  return airstamp ? parisDay(airstamp) : (airdate || null);
}

/** « AAAA-MM-JJ » + n jours. */
export function addDays(day, n) {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Nombre de jours de `a` à `b` (b - a). */
export function daysBetween(a, b) {
  return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000);
}

/** Lundi de la semaine d'un jour (pour grouper le calendrier). */
export function weekStart(day) {
  const weekday = (new Date(`${day}T00:00:00Z`).getUTCDay() + 6) % 7; // lundi = 0
  return addDays(day, -weekday);
}

/** Écart (minutes) entre l'heure de Paris et UTC à un instant donné. */
function parisOffsetMinutes(ms) {
  const p = Object.fromEntries(PARTS_FORMAT.formatToParts(new Date(ms))
    .filter((x) => x.type !== "literal").map((x) => [x.type, Number(x.value)]));
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return Math.round((asUtc - Math.floor(ms / 1000) * 1000) / 60_000);
}

/** Jour saisi -> instant ISO UTC à midi heure de Paris (la date affichée ne bouge jamais). */
export function localDayToUtc(day) {
  const [y, m, d] = day.split("-").map(Number);
  const guess = Date.UTC(y, m - 1, d, 12);
  return isoUtc(guess - parisOffsetMinutes(guess) * 60_000);
}

/** « 25/10/2026 » ; chaîne vide sans date. Accepte un jour ou un instant ISO. */
export function fmtDate(value) {
  if (!value) return "";
  const day = value.length > 10 ? parisDay(value) : value;
  const [y, m, d] = day.split("-");
  return `${d}/${m}/${y}`;
}

/** « samedi 25 octobre » (jour de la semaine et mois en toutes lettres). */
export function fmtLongDay(day) {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString("fr-FR", {
    weekday: "long", day: "numeric", month: "long", timeZone: "UTC",
  });
}

/** Durée en minutes -> « 2 h 05 », « 45 min ». */
export function fmtRuntime(minutes) {
  if (!minutes) return "";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")}` : `${m} min`;
}
