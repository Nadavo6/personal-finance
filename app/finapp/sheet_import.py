"""Import a structured snapshot of a net-worth Google Sheet (data/sheet/snapshot_<date>.json).
Format: examples/sheet_snapshot.example.json.

Idempotent: re-importing the same snapshot changes nothing. Upserts assets (+ one dated value
per as_of), the mortgage liability, the property record, planned items (future income /
expenses), equity comp, and adds tasks / freebies that do not exist yet (title / name match,
case-insensitive).
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import config, db
from .networth import check_kind, default_liquid, set_asset_value, set_liability_balance

FREEBIE_SOURCES = ("work", "reserve", "gift", "other")
MORTGAGE_RE = re.compile(r"mortgage|משכנתא", re.I)


def _mortgage_approved(prop: dict):
    """Approved mortgage amount: `mortgage_approved`, or a lender-suffixed variant
    (`mortgage_approved_<lender>`) from older snapshots."""
    if prop.get("mortgage_approved") is not None:
        return prop["mortgage_approved"]
    return next((v for k, v in prop.items() if k.startswith("mortgage_approved_")), None)


def sheet_dir() -> Path:
    return config.data_dir() / "sheet"


def latest_snapshot() -> Path | None:
    files = sorted(sheet_dir().glob("snapshot_*.json")) if sheet_dir().exists() else []
    return files[-1] if files else None


def _month(s) -> str | None:
    if not s:
        return None
    m = re.match(r"(\d{4})-(\d{2})", str(s))
    return f"{m.group(1)}-{m.group(2)}" if m else None


def _norm(s) -> str:
    return " ".join(str(s or "").split()).casefold()


def _upsert_asset(conn, a: dict, as_of_default: str | None, stats: dict) -> None:
    key = a.get("key") or _norm(a.get("name")).replace(" ", "_")
    kind = check_kind(a.get("kind") or "other", db.ASSET_KINDS, "asset")
    track = 1 if a.get("track_from_bank") else 0
    row = conn.execute("SELECT id FROM assets WHERE key=?", (key,)).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO assets(key,name,kind,include_in_net_worth,liquid,notes,source,"
            "track_from_bank,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (key, a.get("name") or key, kind, 1, default_liquid(kind), a.get("notes"),
             "bank" if track else "sheet", track, db.now_iso()))
        aid = cur.lastrowid
        stats["assets_added"] += 1
    else:
        aid = row[0]
        # name/kind/notes follow the sheet; include/liquid flags stay as the user set them
        conn.execute("UPDATE assets SET name=?, kind=?, notes=?, track_from_bank=? WHERE id=?",
                     (a.get("name") or key, kind, a.get("notes"), track, aid))
        stats["assets_updated"] += 1
    as_of = a.get("as_of") or as_of_default
    if a.get("value") is None or not as_of:
        return
    old = conn.execute("SELECT value, breakdown_json, source FROM asset_values "
                       "WHERE asset_id=? AND as_of=?", (aid, as_of[:10])).fetchone()
    if old is not None and old["source"] not in (None, "sheet"):
        return  # a value the user entered by hand for that date wins
    bd = a.get("breakdown") or None
    bd_json = json.dumps(bd, ensure_ascii=False) if bd else None
    if old is not None and old["value"] == float(a["value"]) and old["breakdown_json"] == bd_json:
        return
    set_asset_value(conn, aid, as_of, a["value"], bd, source="sheet")
    stats["values_written"] += 1


def _upsert_mortgage(conn, prop: dict, fixed: list[dict], as_of: str | None, stats: dict) -> None:
    principal = _mortgage_approved(prop)
    if principal is None:
        return
    pay = next((f for f in fixed if MORTGAGE_RE.search(f.get("name") or "")), None)
    name = (pay or {}).get("name") or "Mortgage"
    notes = "; ".join(x for x in ((pay or {}).get("note"), "principal = approved amount from the sheet")
                      if x)
    row = conn.execute("SELECT id FROM liabilities WHERE key='mortgage'").fetchone()
    if row is None:
        # The flat counts in net worth as your equity (property_equity asset), so the mortgage
        # stays out of the total by default; the property card shows it against the price.
        cur = conn.execute(
            "INSERT INTO liabilities(key,name,kind,principal,monthly_payment,notes,"
            "include_in_net_worth,source,created_at) VALUES ('mortgage',?, 'mortgage',?,?,?,0,"
            "'sheet',?)",
            (name, principal, (pay or {}).get("amount"), notes, db.now_iso()))
        lid = cur.lastrowid
        stats["liabilities_added"] += 1
    else:
        lid = row[0]
        conn.execute("UPDATE liabilities SET name=?, principal=?, monthly_payment=?, notes=? "
                     "WHERE id=?", (name, principal, (pay or {}).get("amount"), notes, lid))
    if as_of and not conn.execute("SELECT 1 FROM liability_values WHERE liability_id=?",
                                  (lid,)).fetchone():
        set_liability_balance(conn, lid, as_of, principal, source="sheet")


def _upsert_property(conn, prop: dict, stats: dict, fixed: list[dict] | None = None) -> None:
    from .networth import insurance_from_fixed

    data = {
        "price": prop.get("asking_price_incl_renovation"),
        "bank_financed_pct": prop.get("bank_financed_pct"),
        "mortgage_approved": _mortgage_approved(prop),
        "purchase_costs": prop.get("purchase_costs") or [],
        "total_costs": prop.get("total_purchase_and_renovation_costs"),
        "contributions": prop.get("contributions") or {},
        "rental_income": prop.get("rental_income") or [],
        "rental_tax_note": prop.get("rental_tax_note"),
    }
    ins = prop.get("insurance_monthly")
    if ins is None:
        ins = insurance_from_fixed(fixed or [])
    if ins is not None:
        data["insurance_monthly"] = ins
    js = json.dumps(data, ensure_ascii=False, sort_keys=True)
    row = conn.execute("SELECT id, data_json FROM properties WHERE key='flat'").fetchone()
    if row is None:
        conn.execute("INSERT INTO properties(key,name,data_json,source,updated_at) "
                     "VALUES ('flat',?,?,'sheet',?)", (prop.get("name"), js, db.now_iso()))
        stats["property"] = "added"
    elif row["data_json"] != js:
        conn.execute("UPDATE properties SET name=?, data_json=?, updated_at=? WHERE id=?",
                     (prop.get("name"), js, db.now_iso(), row["id"]))
        stats["property"] = "updated"
    else:
        stats["property"] = "unchanged"


def _upsert_planned(conn, kind: str, what: str, when, amount, notes, stats: dict) -> None:
    if not what or amount is None:
        return
    wm = _month(when)
    row = conn.execute("SELECT id, when_month, amount, notes FROM planned_items "
                       "WHERE kind=? AND lower(trim(what))=?", (kind, _norm(what))).fetchone()
    if row is None:
        # fall back to a Python casefold match (sqlite lower() is ASCII-only)
        for r in conn.execute("SELECT id, what, when_month, amount, notes FROM planned_items "
                              "WHERE kind=?", (kind,)):
            if _norm(r["what"]) == _norm(what):
                row = r
                break
    if row is None:
        conn.execute("INSERT INTO planned_items(kind,when_month,amount,what,notes,done,source,"
                     "created_at) VALUES (?,?,?,?,?,0,'sheet',?)",
                     (kind, wm, abs(float(amount)), what, notes, db.now_iso()))
        stats["planned_added"] += 1
    elif (row["when_month"], row["amount"], row["notes"]) != (wm, abs(float(amount)), notes):
        conn.execute("UPDATE planned_items SET when_month=?, amount=?, notes=? WHERE id=?",
                     (wm, abs(float(amount)), notes, row["id"]))
        stats["planned_updated"] += 1


def _add_tasks(conn, tasks: list[dict], stats: dict) -> None:
    have = {_norm(r[0]) for r in conn.execute("SELECT title FROM tasks")}
    for t in tasks:
        title = (t.get("title") or "").strip()
        if not title:
            continue
        if _norm(title) in have:
            stats["tasks_skipped"] += 1
            continue
        conn.execute("INSERT INTO tasks(title,notes,status,priority,est_monthly_saving,created_at)"
                     " VALUES (?,?, 'open', 'med', ?, ?)",
                     (title, t.get("notes") or None, t.get("est_monthly_saving"), db.now_iso()))
        have.add(_norm(title))
        stats["tasks_added"] += 1


def _add_freebies(conn, items: list[dict], stats: dict) -> None:
    have = {_norm(r[0]) for r in conn.execute("SELECT name FROM freebies")}
    for f in items:
        name = (f.get("name") or "").strip()
        if not name:
            continue
        if _norm(name) in have:
            stats["freebies_skipped"] += 1
            continue
        src = f.get("source") if f.get("source") in FREEBIE_SOURCES else "other"
        conn.execute("INSERT INTO freebies(name,source,value,unit,status,notes,used_amount,"
                     "created_at) VALUES (?,?,?,?, 'active', ?, 0, ?)",
                     (name, src, f.get("value"), f.get("unit") or "ILS", f.get("notes") or None,
                      db.now_iso()))
        have.add(_norm(name))
        stats["freebies_added"] += 1


def _upsert_equity(conn, items: list[dict], stats: dict) -> None:
    for e in items:
        vests, typ = e.get("vests"), e.get("type") or "grant"
        key = f"{typ}:{vests}"
        row = conn.execute("SELECT id FROM equity_comp WHERE key=?", (key,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO equity_comp(key,vests,qty,type,notes,source,created_at) "
                         "VALUES (?,?,?,?,?,'sheet',?)",
                         (key, vests, e.get("qty"), typ, e.get("source"), db.now_iso()))
            stats["equity_comp_added"] += 1
        else:
            conn.execute("UPDATE equity_comp SET qty=?, notes=? WHERE id=?",
                         (e.get("qty"), e.get("source"), row[0]))


def import_sheet_snapshot(path: str | Path | None = None,
                          conn: sqlite3.Connection | None = None) -> dict:
    p = Path(path) if path else latest_snapshot()
    if p is None or not p.exists():
        raise FileNotFoundError(f"no sheet snapshot found in {sheet_dir()}")
    snap = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(snap, dict) or "assets" not in snap:
        raise ValueError(f"{p.name} is not a sheet snapshot (no 'assets')")
    own = conn is None
    conn = conn or db.connect()
    stats = {k: 0 for k in ("assets_added", "assets_updated", "values_written",
                            "liabilities_added", "planned_added", "planned_updated",
                            "tasks_added", "tasks_skipped", "freebies_added", "freebies_skipped",
                            "equity_comp_added")}
    as_of = snap.get("sheet_last_updated")
    try:
        with conn:
            for a in snap.get("assets") or []:
                _upsert_asset(conn, a, as_of, stats)
            prop = snap.get("property") or {}
            if prop:
                _upsert_property(conn, prop, stats, snap.get("fixed_monthly") or [])
                _upsert_mortgage(conn, prop, snap.get("fixed_monthly") or [], as_of, stats)
            for f in snap.get("future_income") or []:
                _upsert_planned(conn, "income", f.get("source") or f.get("what"), f.get("when"),
                                f.get("amount"), f.get("note"), stats)
            for f in snap.get("future_expenses") or []:
                _upsert_planned(conn, "expense", f.get("what") or f.get("source"), f.get("when"),
                                f.get("amount"), f.get("note"), stats)
            _upsert_equity(conn, snap.get("equity_comp") or [], stats)
            _add_tasks(conn, snap.get("tasks") or [], stats)
            _add_freebies(conn, snap.get("freebies") or [], stats)
            db.set_setting(conn, "last_sheet_import", {"at": db.now_iso(), "file": p.name,
                                                       "sheet_last_updated": as_of})
    finally:
        if own:
            conn.close()
    return {"file": p.name, "sheet_last_updated": as_of, **stats}
