"""CLI: python -m finapp serve|import|import-inbox|import-sheet|sync|backup|export-csv|recategorize|
tag-event|apply-setup|export-setup."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from datetime import date
from pathlib import Path

from . import config, db


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def cmd_serve(args) -> int:
    import uvicorn

    conn = db.connect()
    conn.close()
    if db.last_backup_date() != f"{date.today():%Y%m%d}":
        try:
            p = db.backup()
            print(f"daily backup: {p.name}", flush=True)
        except Exception as e:  # never block serving on a backup failure
            print(f"backup failed: {e}", file=sys.stderr)
    import socket

    for port in range(args.port, args.port + 20):  # skip ports other local tools hold
        with socket.socket() as s:
            if s.connect_ex((config.HOST, port)) != 0:
                args.port = port
                break
    url = f"http://{config.HOST}:{args.port}/"
    if args.open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"serving on {url}", flush=True)
    uvicorn.run("finapp.server:app", host=config.HOST, port=args.port, log_level="warning")
    return 0


def cmd_import(args) -> int:
    from .ingest import import_file

    rc = 0
    for f in args.files:
        try:
            _print(import_file(Path(f)))
        except Exception as e:
            print(f"{f}: {type(e).__name__}: {e}", file=sys.stderr)
            rc = 1
    return rc


def cmd_import_inbox(args) -> int:
    from .ingest import import_inbox

    res = import_inbox()
    _print(res)
    return 0 if all(f.get("ok") for f in res["files"]) else 1


def cmd_import_sheet(args) -> int:
    from .sheet_import import import_sheet_snapshot

    try:
        _print(import_sheet_snapshot(Path(args.path) if args.path else None))
    except (FileNotFoundError, ValueError) as e:
        print(f"import-sheet: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_sync(args) -> int:
    from .server import run_sync_job

    providers = [p.strip() for p in args.providers.split(",")] if args.providers else None
    res = run_sync_job(providers, args.start)
    _print(res)
    return 0 if not res.get("error") and all(p.get("ok") for p in res.get("providers", [])) else 1


def cmd_backup(args) -> int:
    print(db.backup())
    return 0


def _source_counts(conn) -> dict:
    return {r[0] or "none": r[1] for r in conn.execute(
        "SELECT category_source, COUNT(*) FROM transactions GROUP BY 1 ORDER BY 1")}


def cmd_recategorize(args) -> int:
    """Re-run category resolution (rules + precedence) on every non-manual row, and refresh
    the RiseUp history account names."""
    from . import rules

    if not args.no_backup:
        print(f"backup: {db.backup()}", file=sys.stderr)
    conn = db.connect()
    try:
        before = _source_counts(conn)
        from .ingest import backfill_alias_accounts, refresh_riseup_account_names

        with conn:
            changed = rules.apply_rules(conn, only_non_manual=True)
            backfilled = backfill_alias_accounts(conn)
            names = refresh_riseup_account_names(conn)
        _print({"changed": changed, "by_source_before": before,
                "by_source_after": _source_counts(conn), "aliases_backfilled": backfilled,
                "riseup_accounts_named": len(names)})
    finally:
        conn.close()
    return 0


REGULAR_INCOME = ("Salary", "Reserve duty income", "Rental income")


def tag_event(conn, category: str, date_from: str, date_to: str, dry_run: bool = False) -> dict:
    """Tag a one-off event's inflows: positive bank rows dated [date_from, date_to] that are
    not transfers, savings or regular income (Salary / Reserve duty / Rental) get `category`
    (category_source manual). A one-off category also excludes them (see rules.decide)."""
    from . import rules

    cid = db.category_id(conn, category)
    if cid is None:
        raise SystemExit(f"no category named {category!r}")
    rs = conn.execute(
        f"""SELECT t.id, t.amount FROM transactions t
            JOIN accounts a ON a.id=t.account_id
            LEFT JOIN categories c ON c.id=t.category_id
            WHERE a.kind='bank' AND t.amount>0 AND t.is_transfer=0
              AND t.date BETWEEN ? AND ?
              AND COALESCE(c.kind,'') NOT IN ('transfer','savings')
              AND COALESCE(c.name,'') NOT IN ({','.join('?' * len(REGULAR_INCOME))})
              AND COALESCE(t.category_id,-1) != ?""",
        (date_from, date_to, *REGULAR_INCOME, cid)).fetchall()
    ids = [r["id"] for r in rs]
    if ids and not dry_run:
        ts = db.now_iso()
        conn.executemany("UPDATE transactions SET category_id=?, category_source='manual', "
                         "updated_at=? WHERE id=?", [(cid, ts, i) for i in ids])
        rules.apply_rules(conn, txn_ids=ids)  # sets is_excluded for one-off categories
    return {"category": category, "count": len(ids),
            "total": round(sum(r["amount"] for r in rs), 2), "dry_run": dry_run}


def cmd_tag_event(args) -> int:
    if not args.dry_run and not args.no_backup:
        print(f"backup: {db.backup()}", file=sys.stderr)
    conn = db.connect()
    try:
        with conn:
            _print(tag_event(conn, args.category, args.date_from, args.date_to, args.dry_run))
    finally:
        conn.close()
    return 0


def cmd_export_csv(args) -> int:
    from .server import export_csv_text

    conn = db.connect()
    try:
        text = export_csv_text(conn)
    finally:
        conn.close()
    if args.out:
        Path(args.out).write_text("﻿" + text, encoding="utf-8")
        print(args.out)
    else:
        sys.stdout.write(text)
    return 0


def cmd_apply_setup(args) -> int:
    from . import personal_setup

    p = Path(args.path) if args.path else personal_setup.setup_path()
    if not p.is_file():
        print(f"no setup file at {p} (copy examples/personal_setup.example.json there and edit it)",
              file=sys.stderr)
        return 1
    if config.db_path().exists() and not args.no_backup:
        print(f"backup: {db.backup()}", file=sys.stderr)
    conn = db.connect()
    try:
        _print(personal_setup.apply_file(conn, p, overwrite_settings=args.overwrite_settings))
    finally:
        conn.close()
    return 0


def cmd_export_setup(args) -> int:
    from . import personal_setup

    conn = db.connect()
    try:
        text = json.dumps(personal_setup.export(conn), ensure_ascii=False, indent=2) + "\n"
    finally:
        conn.close()
    if args.out:
        out = Path(args.out)
        if out.exists() and not args.force:
            print(f"{out} exists (use --force to overwrite)", file=sys.stderr)
            return 1
        out.write_text(text, encoding="utf-8")
        print(out)
    else:
        sys.stdout.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="finapp")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=config.PORT)
    s.add_argument("--open", action="store_true", help="open the browser")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("import")
    s.add_argument("files", nargs="+")
    s.set_defaults(fn=cmd_import)
    sub.add_parser("import-inbox").set_defaults(fn=cmd_import_inbox)
    s = sub.add_parser("import-sheet", help="import a sheet snapshot (default: latest in data/sheet/)")
    s.add_argument("path", nargs="?", default=None)
    s.set_defaults(fn=cmd_import_sheet)
    s = sub.add_parser("sync")
    s.add_argument("--providers", default=None, help="comma list, default settings.sync_providers")
    s.add_argument("--start", default=None, help="YYYY-MM-DD")
    s.set_defaults(fn=cmd_sync)
    sub.add_parser("backup").set_defaults(fn=cmd_backup)
    s = sub.add_parser("export-csv")
    s.add_argument("--out", default=None)
    s.set_defaults(fn=cmd_export_csv)
    s = sub.add_parser("recategorize", help="re-run categorisation on all non-manual rows")
    s.add_argument("--no-backup", action="store_true")
    s.set_defaults(fn=cmd_recategorize)
    s = sub.add_parser("tag-event", help="tag a one-off event's inflows (e.g. a big family event)")
    s.add_argument("--category", required=True, help="a one-off category's name")
    s.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    s.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--no-backup", action="store_true")
    s.set_defaults(fn=cmd_tag_event)
    s = sub.add_parser("apply-setup", help="apply data/personal_setup.json (idempotent)")
    s.add_argument("path", nargs="?", default=None)
    s.add_argument("--overwrite-settings", action="store_true",
                   help="also replace settings you already changed")
    s.add_argument("--no-backup", action="store_true")
    s.set_defaults(fn=cmd_apply_setup)
    s = sub.add_parser("export-setup", help="print (or --out) this database's personal setup")
    s.add_argument("--out", default=None)
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_export_setup)
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        args = ap.parse_args(["serve", *(argv or [])])
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
