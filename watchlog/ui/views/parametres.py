"""Page « Paramètres » : TMDB, mes plateformes, correspondances à vérifier, import, synchro,
export, sauvegardes."""

import io
import logging
from datetime import datetime

import pandas as pd
import streamlit as st
from sqlmodel import func, select

from watchlog.core import exchange, importer, providers, settings
from watchlog.core.bingers_export import export_bingers_zip
from watchlog.core.backup import backup_date, list_backups, make_backup
from watchlog.core.config import BACKUP_DIR, WEB_APP_URL
from watchlog.core.db import get_engine
from watchlog.core.export import export_all_csv, export_queue_excel
from watchlog.core.http import ApiError
from watchlog.core.models import (MATCH_NOT_FOUND, MATCH_NUMBERING, MATCH_UNCERTAIN,
                                  PendingWatch, Show)
from watchlog.core.queue import episode_code
from watchlog.core.sync import sync
from watchlog.ui import common

log = logging.getLogger(__name__)

MATCH_LABELS = {
    MATCH_UNCERTAIN: "à confirmer",
    MATCH_NOT_FOUND: "introuvable sur TVMaze",
    MATCH_NUMBERING: "numérotation incohérente",
}


def progress_bar(text: str):
    """Barre de progression Streamlit, au format attendu par l'import et la synchro."""
    bar = st.progress(0.0, text=text)

    def update(done: int, total: int, label: str) -> None:
        bar.progress(min(done / total, 1.0) if total else 1.0, text=label)

    return update


def remember(key: str, value) -> None:
    """Garde un message ou un rapport pour l'afficher après le rechargement de la page."""
    st.session_state[key] = value
    st.rerun()


def show_report(key: str) -> None:
    """Affiche le rapport gardé sous `key` (liste de lignes), s'il y en a un."""
    lines = st.session_state.get(key)
    if lines:
        st.text("\n".join(lines))


st.title("Paramètres")

# --- Connexion TMDB --------------------------------------------------------------
st.header("Connexion TMDB")
connected, message = common.tmdb_status()
if connected:
    st.success("Connecté")
else:
    st.warning("Non connecté")
st.caption(message)
if st.button("Retester"):
    common.tmdb_status.clear()
    st.rerun()

# --- Mes plateformes -----------------------------------------------------------------
st.header("Mes plateformes")
st.write("Choisis les plateformes auxquelles tu as accès : la case « Sur mes plateformes » des "
         "pages À regarder et Suggestions ne garde alors que les titres disponibles chez toi en "
         "France (abonnement, gratuit ou gratuit avec pub ; pas la location ni l'achat).")
if "platforms_message" in st.session_state:
    st.success(st.session_state.pop("platforms_message"))
with common.db() as session:
    saved_platforms = settings.my_platforms(session)
platform_options = list(saved_platforms)
if connected:
    try:
        platform_options = providers.platform_choices(common.tmdb_client(), saved_platforms)
    except ApiError as exc:
        st.warning(f"Liste des plateformes indisponible ({exc}) : seules tes plateformes déjà "
                   "enregistrées sont proposées. Réessaie plus tard.")
else:
    st.info("TMDB n'est pas connecté : la liste des plateformes ne peut pas être chargée. "
            "Tes plateformes déjà enregistrées restent modifiables.")
with st.form("my_platforms_form", border=False):
    chosen_platforms = st.multiselect("Plateformes", platform_options, default=saved_platforms,
                                      placeholder="Aucune plateforme choisie")
    if st.form_submit_button("Enregistrer mes plateformes"):
        with common.db() as session:
            settings.set_my_platforms(session, chosen_platforms)
            session.commit()
        count = len(chosen_platforms)
        remember("platforms_message", f"{count} plateforme(s) enregistrée(s)." if count
                 else "Plus aucune plateforme choisie : le filtre est désactivé.")


# --- Correspondances à vérifier ----------------------------------------------------
def search_results(query: str) -> list[dict]:
    """Recherche TVMaze, réduite à ce qu'on affiche : id, nom, année, chaîne."""
    results = []
    for hit in common.tvmaze_client().search(query):
        show = hit.get("show") or {}
        channel = show.get("network") or show.get("webChannel") or {}
        results.append({"id": show.get("id"), "name": show.get("name") or "?",
                        "year": (show.get("premiered") or "")[:4],
                        "channel": channel.get("name") or ""})
    return [r for r in results if r["id"] is not None]


def choose(show_id: int, tvmaze_id: int) -> None:
    result = importer.confirm_match(get_engine(), common.tvmaze_client(), show_id, tvmaze_id)
    st.session_state.pop(f"results_{show_id}", None)
    remember("match_message", result)


def search_section(show: Show) -> None:
    """Champ de recherche TVMaze et résultats avec un bouton « Choisir »."""
    query = st.text_input("Rechercher sur TVMaze", value=show.original_title or show.title,
                          key=f"query_{show.id}")
    if st.button("Rechercher", key=f"search_{show.id}") and query.strip():
        try:
            st.session_state[f"results_{show.id}"] = search_results(query.strip())
        except ApiError as exc:
            st.error(str(exc))
    results = st.session_state.get(f"results_{show.id}")
    if results is None:
        return
    if not results:
        st.write("Aucun résultat.")
    for result in results:
        text, button = st.columns([5, 1])
        details = " · ".join(x for x in (result["year"], result["channel"]) if x)
        text.write(f"**{common.md_escape(result['name'])}**"
                   + (f" ({common.md_escape(details)})" if details else ""))
        if button.button("Choisir", key=f"pick_{show.id}_{result['id']}"):
            choose(show.id, result["id"])


MAPPING_METHODS = {
    "absolute": ("Ordre absolu",
                 "le n-ième épisode vu devient le n-ième épisode TVMaze (dans l'ordre des "
                 "saisons et des numéros, hors spéciaux). Adapté quand les saisons sont "
                 "découpées autrement (ex. Lupin : 2 saisons d'un côté, 3 parties de l'autre)."),
    "direct": ("Numéros identiques seulement",
               "SxxEyy est rattaché au même SxxEyy chez TVMaze s'il existe ; le reste reste "
               "en attente. Adapté quand seuls quelques épisodes diffèrent (ex. un épisode "
               "de plus dans une saison) : l'ordre absolu décalerait alors tout ce qui suit."),
}


def mapping_section(show: Show) -> None:
    """Aperçu et application d'une correspondance (ordre absolu ou numéros identiques)."""
    method = st.radio("Méthode", list(MAPPING_METHODS), key=f"method_{show.id}",
                      format_func=lambda m: MAPPING_METHODS[m][0], horizontal=True)
    st.write(f"{MAPPING_METHODS[method][0]} : {MAPPING_METHODS[method][1]} "
             "Vérifie l'aperçu avant d'appliquer.")
    preview = (importer.preview_absolute_mapping if method == "absolute"
               else importer.preview_direct_mapping)
    with common.db() as session:
        pairs = preview(session, show.id)
        rows = [{"Bingers": episode_code(p.season, p.number),
                 "TVMaze": episode_code(e.season, e.number) if e else "aucun",
                 "Titre de l'épisode TVMaze": (e.title or "") if e else ""}
                for p, e in pairs]
    if not rows:
        st.write("Aucun visionnage en attente à recaler.")
        return
    matched = sum(1 for _, e in pairs if e is not None)
    st.caption(f"{matched} rattaché(s), {len(rows) - matched} sans équivalent (resteront en attente).")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=300)
    if st.button("Appliquer cette correspondance", key=f"apply_{show.id}"):
        apply = (importer.apply_absolute_mapping if method == "absolute"
                 else importer.apply_direct_mapping)
        remember("match_message", apply(get_engine(), show.id))


def leftover_section(show: Show) -> None:
    """Visionnages restés en attente après une correspondance : les lister, ou les ignorer."""
    with common.db() as session:
        pendings = session.exec(select(PendingWatch).where(PendingWatch.show_id == show.id)
                                .order_by(PendingWatch.season, PendingWatch.number)).all()
        codes = [episode_code(p.season, p.number) for p in pendings]
    st.write("Ces épisodes vus dans Bingers n'ont pas d'équivalent chez TVMaze : "
             + ", ".join(codes) + ".")
    if st.button("Ignorer ces visionnages", key=f"discard_{show.id}"):
        remember("match_message", importer.discard_pending(get_engine(), show.id))


st.header("Correspondances à vérifier")
if "match_message" in st.session_state:
    st.info(st.session_state.pop("match_message"))

with common.db() as session:
    pending = dict(session.exec(select(PendingWatch.show_id, func.count(PendingWatch.id))
                                .group_by(PendingWatch.show_id)).all())
    # Séries à vérifier, ou déjà recalées mais avec des visionnages restés en attente.
    to_check = session.exec(select(Show)
                            .where(Show.match_status.in_(list(MATCH_LABELS))
                                   | Show.id.in_(list(pending)))
                            .order_by(Show.title)).all()

if not to_check:
    st.write("Aucune série à vérifier.")
for show in to_check:
    count = pending.get(show.id, 0)
    label = MATCH_LABELS.get(show.match_status, "visionnages sans équivalent")
    with st.expander(f"{common.md_escape(show.title)} : {label}, "
                     f"{count} visionnage(s) en attente"):
        if show.match_note:
            st.caption(show.match_note)
        if show.match_status == MATCH_UNCERTAIN and show.candidate_tvmaze_id:
            st.write(f"Proposition : [fiche TVMaze n° {show.candidate_tvmaze_id}]"
                     f"(https://www.tvmaze.com/shows/{show.candidate_tvmaze_id})")
            if st.button("Confirmer la proposition", key=f"confirm_{show.id}"):
                choose(show.id, show.candidate_tvmaze_id)
        if show.match_status in (MATCH_UNCERTAIN, MATCH_NOT_FOUND):
            search_section(show)
        if show.match_status == MATCH_NUMBERING:
            mapping_section(show)
        elif show.match_status not in MATCH_LABELS and count:
            leftover_section(show)

# --- Import Bingers --------------------------------------------------------------
st.header("Import Bingers")
st.write("Dans Bingers, exporte tes données (fichier .zip), puis choisis le fichier ici. "
         "L'import peut être relancé sans créer de doublons. "
         "Une sauvegarde de la base est faite juste avant.")
uploaded = st.file_uploader("Export Bingers (.zip)", type=["zip"])
def backup_or_warn() -> None:
    """Sauvegarde avant une grosse opération ; si elle rate, on le dit (l'opération continue)."""
    if make_backup() is None:
        st.warning("Sauvegarde impossible (détail dans data/watchlog.log) : l'opération continue "
                   "sans copie de sécurité.")


if st.button("Importer", disabled=uploaded is None):
    backup_or_warn()
    try:
        report = importer.import_bingers(uploaded, get_engine(), common.tvmaze_client(),
                                         common.tmdb_client(), progress=progress_bar("Import..."))
    except Exception as exc:  # noqa: BLE001  (fichier invalide, etc. : message clair)
        log.exception("Import Bingers depuis l'interface")
        st.error(f"Import impossible : {exc}")
    else:
        remember("import_report", report.lines())
show_report("import_report")

# --- Synchronisation -------------------------------------------------------------
st.header("Synchronisation")
st.write("Met à jour les séries (TVMaze) et les films (TMDB) de la bibliothèque. "
         "Une sauvegarde de la base est faite juste avant. Elle se lance aussi toute seule "
         "à l'ouverture de Watchlog si la dernière date de plus de 24 h.")
auto_running = common.auto_sync()["running"]
if auto_running:
    st.caption("Mise à jour automatique en cours : attends qu'elle soit finie (voir le menu).")
if st.button("Synchroniser maintenant", disabled=auto_running):
    backup_or_warn()
    report = sync(get_engine(), common.tvmaze_client(), common.tmdb_client(),
                  progress=progress_bar("Synchronisation..."))
    remember("sync_report", report.lines())
show_report("sync_report")


# --- Export ------------------------------------------------------------------------
def excel_bytes() -> bytes:
    buffer = io.BytesIO()
    with common.db() as session:
        export_queue_excel(session, buffer)
    return buffer.getvalue()


def csv_zip_bytes() -> bytes:
    buffer = io.BytesIO()
    export_all_csv(get_engine(), buffer)
    return buffer.getvalue()


def bingers_zip() -> tuple[bytes, list[str]]:
    """Le .zip et son rapport (dates inventées, titres omis : à montrer avant l'import)."""
    buffer = io.BytesIO()
    with common.db() as session:
        report = export_bingers_zip(session, buffer)
    return buffer.getvalue(), report.lines()


st.header("Export")
st.write("« À regarder » : la liste avec les filtres par défaut (séries en cours et à voir, films). "
         "Base complète : un fichier CSV par table, réunis dans un .zip (pour Excel, illisible "
         "par Bingers). **Pour Bingers** : un .zip au format d'export de Bingers, à importer dans "
         "l'app (Importer l'historique > Choisir le fichier .zip).")
stamp = f"{datetime.now():%Y-%m-%d}"
left, middle, right = st.columns(3)
# Les fichiers ne sont fabriqués qu'au clic (data = fonction).
left.download_button(
    "Télécharger « À regarder » (Excel)", data=excel_bytes, file_name=f"a-regarder-{stamp}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", on_click="ignore")
middle.download_button(
    "Télécharger la base complète (CSV)", data=csv_zip_bytes, file_name=f"watchlog-{stamp}.zip",
    mime="application/zip", on_click="ignore")
# Bingers : préparé d'abord, pour montrer le rapport (dates inventées, titres omis).
if right.button("Préparer l'export pour Bingers",
                help="Séries, films, épisodes vus avec leurs dates, statuts, favoris, notes et "
                     "listes. Les titres saisis à la main et les épisodes spéciaux ne sont pas "
                     "repris."):
    st.session_state["bingers_export"] = bingers_zip()
if "bingers_export" in st.session_state:
    bingers_data, bingers_lines = st.session_state["bingers_export"]
    right.download_button(
        "Télécharger pour Bingers (.zip)", data=bingers_data,
        file_name=f"watchlog-bingers-{stamp}.zip", mime="application/zip", on_click="ignore")
    right.text("\n".join(bingers_lines))


# --- App web (échange JSON) --------------------------------------------------------
def exchange_bytes() -> bytes:
    buffer = io.BytesIO()
    with common.db() as session:
        exchange.write_json(session, buffer)
    return buffer.getvalue()


st.header("App web")
st.write(f"La [version web de Watchlog]({WEB_APP_URL}) s'ouvre sur le téléphone, sans "
         "installation ; ses données restent dans le navigateur. Pour y retrouver ta "
         "bibliothèque, exporte-la ici puis importe le fichier dans la page Paramètres de l'app "
         "web (et inversement). À l'import, les visionnages s'ajoutent, et le fichier fait "
         "référence pour les statuts, favoris et notes. Ta clé TMDB n'est jamais dans le fichier.")
st.download_button("Exporter pour l'app web (.json)", data=exchange_bytes,
                   file_name=f"watchlog-{stamp}.json", mime="application/json", on_click="ignore")
uploaded_json = st.file_uploader("Fichier exporté par l'app web (.json)", type=["json"])
if st.button("Importer le fichier de l'app web", disabled=uploaded_json is None):
    backup_or_warn()
    try:
        with common.db() as session:
            json_report = exchange.import_json(session, uploaded_json)
            session.commit()
    except ValueError as exc:  # fichier qui n'est pas un export Watchlog : message clair
        st.error(f"Import impossible : {exc}")
    except Exception as exc:  # noqa: BLE001  (base verrouillée, etc. : message clair)
        log.exception("Import JSON depuis l'interface")
        st.error(f"Import impossible : {exc}")
    else:
        remember("exchange_report", json_report.lines())
show_report("exchange_report")

# --- Sauvegardes -------------------------------------------------------------------
st.header("Sauvegardes")
st.write("Une sauvegarde est faite à chaque lancement de l'outil, avant chaque import et "
         f"chaque synchronisation. Les 5 plus récentes sont gardées dans `{BACKUP_DIR}`.")
if "backup_message" in st.session_state:
    st.info(st.session_state.pop("backup_message"))


def size_label(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} Mo".replace(".", ",")
    return f"{max(size // 1024, 1)} Ko"


backups = list_backups()
if backups:
    st.dataframe(pd.DataFrame([
        {"Date": backup_date(p).strftime("%d/%m/%Y %H:%M") if backup_date(p) else "",
         "Taille": size_label(p.stat().st_size), "Fichier": p.name}
        for p in backups]), hide_index=True)
else:
    st.write("Aucune sauvegarde pour l'instant.")
if st.button("Sauvegarder maintenant"):
    dest = make_backup()
    remember("backup_message", f"Sauvegarde créée : {dest.name}." if dest
             else "Sauvegarde impossible (détail dans data/watchlog.log).")
