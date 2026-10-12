"""Exports : liste « À regarder » en Excel, et base complète en CSV (dans un .zip).

La table api_cache (copie des réponses des API) n'est jamais exportée :
elle est volumineuse et sans intérêt pour l'utilisateur.
"""

import io
import zipfile
from pathlib import Path
from typing import BinaryIO

import pandas as pd
from openpyxl.utils import get_column_letter
from sqlalchemy import Engine, inspect
from sqlmodel import Session

from watchlog.core.dates import fmt_date
from watchlog.core.models import STATUS_LABELS
from watchlog.core.queue import AVAILABILITY_LABELS, QueueItem, build_queue

# Colonnes de la page « À regarder », dans l'ordre.
QUEUE_COLUMNS = ["Date", "Type", "Série / Film", "Épisode", "Titre de l'épisode",
                 "Disponibilité", "Statut", "Durée (min)"]

# Tables jamais exportées (nom de la table SQLite de ApiCache : "apicache").
EXCLUDED_TABLES = {"apicache", "api_cache"}


def queue_dataframe(items: list[QueueItem]) -> pd.DataFrame:
    """Tableau « À regarder » : une ligne par épisode ou film, dans l'ordre reçu."""
    rows = []
    for item in items:
        rows.append({
            "Date": fmt_date(item.release_date),
            "Type": item.kind_label,
            "Série / Film": item.title,
            "Épisode": item.code,
            "Titre de l'épisode": item.episode_title or "",
            "Disponibilité": AVAILABILITY_LABELS.get(item.availability, item.availability),
            "Statut": STATUS_LABELS.get(item.status, item.status),
            "Durée (min)": item.runtime,
        })
    frame = pd.DataFrame(rows, columns=QUEUE_COLUMNS)
    # Entiers « à trous » : une durée inconnue reste vide (et non 0 ou 45.0).
    frame["Durée (min)"] = frame["Durée (min)"].astype("Int64")
    return frame


def export_queue_excel(session: Session, dest: Path | BinaryIO, **filters) -> int:
    """Écrit la liste « À regarder » dans un fichier Excel. Renvoie le nombre de lignes.

    `filters` est passé tel quel à build_queue (kinds, statuses, availabilities...).
    """
    frame = queue_dataframe(build_queue(session, **filters))
    with pd.ExcelWriter(dest, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="À regarder", index=False)
        sheet = writer.sheets["À regarder"]
        sheet.freeze_panes = "A2"  # ligne d'en-tête toujours visible
        # Largeur de chaque colonne : le texte le plus long, entre 8 et 50 caractères.
        for i, column in enumerate(frame.columns, start=1):
            longest = max([len(column)] + [len(str(v)) for v in frame[column] if pd.notna(v)])
            sheet.column_dimensions[get_column_letter(i)].width = min(max(longest + 2, 8), 50)
    return len(frame)


def export_all_csv(engine: Engine, dest: Path | BinaryIO) -> list[str]:
    """Écrit un .zip contenant un CSV par table (sauf api_cache). Renvoie les noms des fichiers.

    Les CSV sont en UTF-8 avec BOM : Excel les ouvre alors avec les bons accents.
    """
    tables = [name for name in inspect(engine).get_table_names() if name not in EXCLUDED_TABLES]
    names = []
    with engine.connect() as connection, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        for table in sorted(tables):
            frame = pd.read_sql_table(table, connection)
            buffer = io.StringIO()
            frame.to_csv(buffer, index=False)
            name = f"{table}.csv"
            archive.writestr(name, buffer.getvalue().encode("utf-8-sig"))
            names.append(name)
    return names
