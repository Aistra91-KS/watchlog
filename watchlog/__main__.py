"""Ligne de commande : python -m watchlog <commande>.

Commandes : import, sync, check, export, run (voir python -m watchlog --help).
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from watchlog.core.config import ROOT, setup_logging


def print_progress(done: int, total: int, label: str) -> None:
    """Barre de progression texte sur une seule ligne : [ 42/570] Titre."""
    width = len(str(total))
    label = label[:60]
    # \r revient en début de ligne ; les espaces effacent un titre précédent plus long.
    print(f"\r[{done:>{width}}/{total}] {label:<60}", end="", flush=True)
    if done >= total:
        print()


def print_lines(lines: list[str]) -> None:
    for line in lines:
        print(line)


def backup_first() -> None:
    """Sauvegarde de la base avant une opération qui la modifie beaucoup (import, sync)."""
    from watchlog.core.backup import make_backup

    dest = make_backup()
    if dest is not None:
        print(f"Sauvegarde : {dest.name}")
    else:
        print("Sauvegarde impossible (détail dans data/watchlog.log) : on continue sans copie.")


def tmdb_status(tmdb) -> None:
    """Affiche l'état de la connexion TMDB (jamais les identifiants)."""
    _ok, message = tmdb.check_connection()
    print(message)


# --- Commandes ---------------------------------------------------------------

def cmd_import(args, engine, tvmaze, tmdb) -> int:
    from watchlog.core.importer import import_bingers

    source = Path(args.zip)
    if not source.is_file():
        print(f"Fichier introuvable : {source}")
        return 1
    backup_first()
    if source.suffix.lower() == ".json":  # fichier de l'app web
        return import_exchange(source, engine)
    print(f"Import de {source.name}...")
    report = import_bingers(source, engine, tvmaze, tmdb, progress=print_progress,
                            download_posters=not args.no_posters)
    print_lines(report.lines())
    return 0


def import_exchange(source: Path, engine) -> int:
    from sqlmodel import Session

    from watchlog.core.exchange import import_json

    print(f"Import de {source.name} (app web)...")
    try:
        with Session(engine) as session:
            report = import_json(session, source)
            session.commit()
    except ValueError as exc:
        print(f"Import impossible : {exc}")
        return 1
    print_lines(report.lines())
    return 0


def cmd_sync(args, engine, tvmaze, tmdb) -> int:
    from watchlog.core.sync import sync

    backup_first()
    print("Synchronisation...")
    report = sync(engine, tvmaze, tmdb, progress=print_progress,
                  download_posters=not args.no_posters)
    print_lines(report.lines())
    return 1 if report.tvmaze_down else 0


def cmd_check(args, engine, tvmaze, tmdb) -> int:
    # Le statut TMDB est affiché par main() avant cette commande.
    return 0 if tmdb.available else 1


def cmd_export(args, engine, tvmaze, tmdb) -> int:
    from datetime import datetime

    from sqlmodel import Session

    from watchlog.core.bingers_export import export_bingers_zip
    from watchlog.core.export import export_all_csv, export_queue_excel

    # Sans option : les trois exports, dans exports/ avec la date et l'heure dans le nom.
    excel, csv, bingers = args.excel, args.csv, getattr(args, "bingers", None)
    as_json = getattr(args, "json", None)
    if as_json is not None:
        from watchlog.core.exchange import write_json

        dest = Path(as_json)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Session(engine) as session:
            count = write_json(session, dest)
        print(f"App web : {count} série(s) et film(s) -> {dest}")
        if excel is None and csv is None and bingers is None:
            return 0
    if excel is None and csv is None and bingers is None:
        stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
        excel = ROOT / "exports" / f"a-regarder-{stamp}.xlsx"
        csv = ROOT / "exports" / f"watchlog-{stamp}.zip"
        bingers = ROOT / "exports" / f"watchlog-bingers-{stamp}.zip"

    if excel is not None:
        excel = Path(excel)
        excel.parent.mkdir(parents=True, exist_ok=True)
        with Session(engine) as session:
            count = export_queue_excel(session, excel)
        print(f"« À regarder » : {count} ligne(s) -> {excel}")
    if csv is not None:
        csv = Path(csv)
        csv.parent.mkdir(parents=True, exist_ok=True)
        names = export_all_csv(engine, csv)
        print(f"Base complète : {len(names)} fichier(s) CSV -> {csv}")
    if bingers is not None:
        bingers = Path(bingers)
        bingers.parent.mkdir(parents=True, exist_ok=True)
        with Session(engine) as session:
            report = export_bingers_zip(session, bingers)
        print(f"Format Bingers -> {bingers}")
        print_lines(report.lines())
    return 0


def cmd_run(args, engine, tvmaze, tmdb) -> int:
    command = [sys.executable, "-m", "streamlit", "run", "watchlog/ui/app.py",
               "--server.port", "8501"]
    try:
        result = subprocess.run(command, cwd=ROOT)
    except KeyboardInterrupt:  # Ctrl+C : arrêt normal
        return 0
    return 0 if result.returncode == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m watchlog",
                                     description="Watchlog : suivi perso de séries et films.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="commande")

    p = sub.add_parser("import", help="importer un export Bingers (.zip) ou de l'app web (.json)")
    p.add_argument("zip", metavar="fichier",
                   help="export Bingers (.zip) ou fichier exporté par l'app web (.json)")
    p.add_argument("--no-posters", action="store_true", help="ne pas télécharger les affiches")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("sync", help="mettre à jour séries et films depuis TVMaze et TMDB")
    p.add_argument("--no-posters", action="store_true", help="ne pas télécharger les affiches")
    p.set_defaults(func=cmd_sync)

    sub.add_parser("check", help="vérifier la connexion à TMDB").set_defaults(func=cmd_check)
    p = sub.add_parser("export", help="exporter « À regarder » (Excel), la base (CSV en .zip) "
                                      "et un .zip réimportable dans Bingers",
                       description="Sans option, les trois exports sont créés dans exports/.")
    p.add_argument("--excel", metavar="CHEMIN", help="fichier Excel « À regarder » (.xlsx)")
    p.add_argument("--csv", metavar="CHEMIN", help="fichier .zip des CSV de la base")
    p.add_argument("--bingers", metavar="CHEMIN", help="fichier .zip au format Bingers")
    p.add_argument("--json", metavar="CHEMIN", help="fichier .json pour l'app web (seul, si "
                                                     "aucune autre option)")
    p.set_defaults(func=cmd_export)
    sub.add_parser("run", help="lancer l'interface (Streamlit)").set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Évite un plantage si la console ne sait pas afficher un caractère.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)

    setup_logging()
    from watchlog.core.db import get_engine
    from watchlog.core.services import make_clients

    engine = get_engine()
    tvmaze, tmdb = make_clients(engine)
    # Seules ces commandes utilisent TMDB : export et run n'ont pas besoin du réseau.
    if args.command in ("import", "sync", "check"):
        tmdb_status(tmdb)
    try:
        return args.func(args, engine, tvmaze, tmdb)
    except KeyboardInterrupt:
        print("\nInterrompu.")
        return 1
    except Exception as exc:  # noqa: BLE001  (message clair plutôt qu'une trace)
        logging.getLogger("watchlog").exception("Commande %s", args.command)
        print(f"\nErreur : {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
