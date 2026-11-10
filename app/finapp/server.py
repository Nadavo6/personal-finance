"""FastAPI app: JSON API under /api, UI from app/ui/ at /."""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sqlite3
import tempfile
import threading
import traceback
from datetime import date, datetime
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import analytics, config, db, freshness, ingest, insights, networth, rules

app = FastAPI(title="finapp", docs_url="/api/docs", openapi_url="/api/openapi.json")


def get_conn():
    conn = db.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _404(what: str = "not found"):
    raise HTTPException(status_code=404, detail=what)


def _num(v, name: str):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{name} must be a number")


def _insert(conn, table: str, allowed: tuple[str, ...], payload: dict, **extra) -> int:
    data = {k: payload[k] for k in allowed if k in payload}
    data.update(extra)
    cols = ",".join(data)
    cur = conn.execute(f"INSERT INTO {table}({cols}) VALUES ({','.join('?' * len(data))})",
                       tuple(data.values()))
    return cur.lastrowid


def _update(conn, table: str, id_: int, allowed: tuple[str, ...], payload: dict, **extra) -> None:
    data = {k: payload[k] for k in allowed if k in payload}
    data.update(extra)
    if not conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (id_,)).fetchone():
        _404(f"{table} {id_} not found")
    if data:
        sets = ",".join(f"{k}=?" for k in data)
        conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*data.values(), id_))


def _delete(conn, table: str, id_: int) -> dict:
    cur = conn.execute(f"DELETE FROM {table} WHERE id=?", (id_,))
    if cur.rowcount == 0:
        _404(f"{table} {id_} not found")
    return {"deleted": id_}


# ---------------------------------------------------------------- overview / month

@app.get("/api/overview")
def api_overview(conn=Depends(get_conn)):
    return analytics.overview(conn)


@app.get("/api/month/{m}")
def api_month(m: str, conn=Depends(get_conn)):
    if len(m) != 7 or m[4] != "-":
        raise HTTPException(422, "month must be YYYY-MM")
    return analytics.month_summary(conn, m)


@app.get("/api/trends")
def api_trends(months: int = 12, conn=Depends(get_conn)):
    return analytics.trends(conn, max(1, min(60, months)))


@app.get("/api/forecast")
def api_forecast(months: int = 6, conn=Depends(get_conn)):
    return analytics.forecast(conn, max(1, min(36, months)))


@app.get("/api/affordability")
def api_affordability(amount: float, month: str | None = None, conn=Depends(get_conn)):
    return analytics.affordability(conn, amount, month)


# ---------------------------------------------------------------- transactions

@app.get("/api/transactions")
def api_transactions(
    month: str | None = None,
    category_id: int | None = None,
    account: str | None = None,
    q: str | None = None,
    uncategorized: bool = False,
    include_transfers: bool = False,
    excluded: bool | None = None,
    ids: str | None = None,
    import_id: int | None = None,
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    conn=Depends(get_conn),
):
    where, params = [], []
    if ids is not None:
        # exactly these rows (other filters ignored); ids of merged rows resolve via txn_aliases
        want = [x.strip() for x in ids.split(",") if x.strip()]
        if want:
            ph = ",".join("?" * len(want))
            want += [r[0] for r in conn.execute(
                f"SELECT txn_id FROM txn_aliases WHERE alias_id IN ({ph})", want)]
        want = list(dict.fromkeys(want))
        if not want:
            return {"items": [], "total": 0}
        rs = conn.execute(db.TXN_SELECT + f" WHERE t.id IN ({','.join('?' * len(want))})"
                          " ORDER BY t.date DESC, t.rowid DESC", want).fetchall()
        return {"items": _with_suggestions(conn, rs), "total": len(rs)}
    if month:
        where.append("t.cashflow_month=?")
        params.append(month)
    if import_id is not None:
        where.append("t.import_id=?")
        params.append(import_id)
    if category_id is not None:
        where.append("t.category_id=?")
        params.append(category_id)
    if account:
        if account.isdigit():
            where.append("(a.id=? OR a.key=?)")
            params += [int(account), account]
        else:
            where.append("(a.key=? OR a.provider=? OR a.kind=?)")
            params += [account, account, account]
    if q:
        where.append("(t.description LIKE ? OR t.merchant LIKE ? OR t.notes LIKE ?)")
        like = f"%{q}%"
        params += [like, f"%{db.normalize_merchant(q) or q}%", like]
    if uncategorized:
        where.append(db.UNCATEGORIZED_WHERE)
    if not include_transfers:
        where.append("t.is_transfer=0")
    if excluded is not None:
        where.append("t.is_excluded=?")
        params.append(int(excluded))
    w = (" WHERE " + " AND ".join(where)) if where else ""
    base_from = (" FROM transactions t LEFT JOIN categories c ON c.id=t.category_id "
                 "LEFT JOIN accounts a ON a.id=t.account_id")
    total = conn.execute("SELECT COUNT(*)" + base_from + w, params).fetchone()[0]
    rs = conn.execute(db.TXN_SELECT + w + " ORDER BY t.date DESC, t.rowid DESC LIMIT ? OFFSET ?",
                      (*params, limit, offset)).fetchall()
    return {"items": _with_suggestions(conn, rs), "total": total}


def _with_suggestions(conn, rs) -> list[dict]:
    """txn_json + suggested_category_id for Uncategorized rows: the category most often given
    (manual or rule) to the same merchant, else the mapped provider category, else null."""
    items = [db.txn_json(r) for r in rs]
    unc = [(it, r) for it, r in zip(items, rs)
           if it["category_name"] in ("Uncategorized", None)]
    best: dict[str, int] = {}
    merchants = sorted({r["merchant"] for _, r in unc if r["merchant"]})
    if merchants:
        q = (f"SELECT t.merchant, t.category_id, COUNT(*) n FROM transactions t "
             f"JOIN categories c ON c.id=t.category_id "
             f"WHERE t.merchant IN ({','.join('?' * len(merchants))}) "
             f"AND t.category_source IN ('manual','rule') AND c.name != 'Uncategorized' "
             f"GROUP BY 1, 2 ORDER BY n DESC, t.category_id")
        for r in conn.execute(q, merchants):
            best.setdefault(r["merchant"], r["category_id"])
    for it in items:
        it["suggested_category_id"] = None
    for it, r in unc:
        it["suggested_category_id"] = (best.get(r["merchant"])
                                       or rules.map_provider_category(conn, r["provider_category"]))
    return items


def _patch_txn(conn, tid: str, payload: dict) -> tuple[dict | None, int]:
    t = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    if t is None:
        _404(f"transaction {tid} not found")
    ts = db.now_iso()
    recompute = False
    if "category_id" in payload:
        cid = payload["category_id"]
        if cid is None:
            conn.execute("UPDATE transactions SET category_source=NULL, updated_at=? WHERE id=?",
                         (ts, tid))
            recompute = True
        else:
            if not conn.execute("SELECT 1 FROM categories WHERE id=?", (cid,)).fetchone():
                raise HTTPException(422, f"category {cid} does not exist")
            conn.execute("UPDATE transactions SET category_id=?, category_source='manual', "
                         "updated_at=? WHERE id=?", (cid, ts, tid))
            kind, one_off = conn.execute("SELECT kind, one_off FROM categories WHERE id=?",
                                         (cid,)).fetchone()
            if "is_transfer" not in payload and not t["flags_manual"]:
                conn.execute("UPDATE transactions SET is_transfer=? WHERE id=?",
                             (int(kind == "transfer"), tid))
            if "is_excluded" not in payload and not t["flags_manual"]:
                # one-off event categories (e.g. a renovation) exclude their rows
                conn.execute("UPDATE transactions SET is_excluded=? WHERE id=?",
                             (int(bool(one_off) or bool(t["base_excluded"])), tid))
    flags = {k: int(bool(payload[k])) for k in ("is_excluded", "is_transfer") if k in payload}
    if flags:
        sets = ",".join(f"{k}=?" for k in flags)
        conn.execute(f"UPDATE transactions SET {sets}, flags_manual=1, updated_at=? WHERE id=?",
                     (*flags.values(), ts, tid))
    if "notes" in payload:
        conn.execute("UPDATE transactions SET notes=?, updated_at=? WHERE id=?",
                     (payload["notes"], ts, tid))
    if recompute:
        rules.apply_rules(conn, txn_ids=[tid])
    rule, updated = None, 0
    if payload.get("remember") and (payload.get("category_id") is not None or flags):
        rule, updated = rules.create_rule_from_txn(
            conn, tid,
            category_id_=payload.get("category_id"),
            set_transfer=flags.get("is_transfer"),
            set_excluded=flags.get("is_excluded"),
            apply_to_history=bool(payload.get("apply_to_history")),
        )
    return rule, updated


@app.patch("/api/transactions/{tid}")
def api_patch_txn(tid: str, payload: dict = Body(...), conn=Depends(get_conn)):
    rule, updated = _patch_txn(conn, tid, payload)
    return {"txn": db.get_txn(conn, tid), "rule": rule, "updated_count": updated + 1}


@app.post("/api/transactions/bulk")
def api_bulk(payload: dict = Body(...), conn=Depends(get_conn)):
    ids = payload.get("ids") or []
    sub = {k: payload[k] for k in ("category_id", "is_excluded", "is_transfer", "notes")
           if k in payload}
    remember = bool(payload.get("remember"))
    rules_made, updated, seen_merchants = [], 0, set()
    for tid in ids:
        p = dict(sub)
        m = conn.execute("SELECT merchant FROM transactions WHERE id=?", (tid,)).fetchone()
        if m is None:
            continue
        if remember and m[0] not in seen_merchants:
            p["remember"] = True
            p["apply_to_history"] = bool(payload.get("apply_to_history"))
            seen_merchants.add(m[0])
        rule, n = _patch_txn(conn, tid, p)
        updated += 1 + n
        if rule:
            rules_made.append(rule)
    return {"updated_count": updated, "rules": rules_made}


# ---------------------------------------------------------------- accounts

@app.get("/api/accounts")
def api_accounts(conn=Depends(get_conn)):
    q = """SELECT a.id, a.key, a.provider, a.kind, a.name, COUNT(t.id) txn_count,
                  MIN(t.date) first_date, MAX(t.date) last_date
           FROM accounts a LEFT JOIN transactions t ON t.account_id=a.id
           GROUP BY a.id ORDER BY a.kind, a.provider, a.key"""
    return [dict(r) for r in conn.execute(q)]


@app.get("/api/freshness")
def api_freshness(conn=Depends(get_conn)):
    return freshness.freshness(conn)


@app.patch("/api/accounts/{aid}")
def api_patch_account(aid: int, payload: dict = Body(...), conn=Depends(get_conn)):
    _update(conn, "accounts", aid, ("name",), payload)
    if "kind" in payload and payload["kind"] in ("bank", "card", "gift"):
        conn.execute("UPDATE accounts SET kind=? WHERE id=?", (payload["kind"], aid))
        conn.execute("UPDATE transactions SET base_excluded=? WHERE account_id=?",
                     (int(payload["kind"] == "gift"), aid))
        ids = [r[0] for r in conn.execute("SELECT id FROM transactions WHERE account_id=?", (aid,))]
        rules.apply_rules(conn, txn_ids=ids)
    return dict(conn.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone())


# ---------------------------------------------------------------- categories

CAT_FIELDS = ("name", "name_he", "kind", "monthly_budget", "color", "archived", "one_off")


@app.get("/api/categories")
def api_categories(conn=Depends(get_conn)):
    q = """SELECT c.*, COUNT(t.id) txn_count FROM categories c
           LEFT JOIN transactions t ON t.category_id=c.id GROUP BY c.id ORDER BY c.kind, c.name"""
    return [dict(r) for r in conn.execute(q)]


@app.post("/api/categories")
def api_create_category(payload: dict = Body(...), conn=Depends(get_conn)):
    if not payload.get("name"):
        raise HTTPException(422, "name is required")
    payload.setdefault("kind", "variable")
    try:
        cid = _insert(conn, "categories", CAT_FIELDS, payload)
    except Exception as e:
        raise HTTPException(422, str(e))
    return dict(conn.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone())


@app.patch("/api/categories/{cid}")
def api_patch_category(cid: int, payload: dict = Body(...), conn=Depends(get_conn)):
    old = conn.execute("SELECT kind, one_off FROM categories WHERE id=?", (cid,)).fetchone()
    try:
        _update(conn, "categories", cid, CAT_FIELDS, payload)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, str(e))
    if old and (("kind" in payload and payload["kind"] != old[0])
                or ("one_off" in payload and int(bool(payload["one_off"])) != (old[1] or 0))):
        ids = [r[0] for r in conn.execute("SELECT id FROM transactions WHERE category_id=?",
                                          (cid,))]
        rules.apply_rules(conn, txn_ids=ids)
    return dict(conn.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone())


@app.delete("/api/categories/{cid}")
def api_delete_category(cid: int, conn=Depends(get_conn)):
    r = conn.execute("SELECT name FROM categories WHERE id=?", (cid,)).fetchone()
    if r is None:
        _404()
    if r[0] == "Uncategorized":
        raise HTTPException(422, "Uncategorized cannot be deleted")
    used = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_id=?", (cid,)).fetchone()[0]
    if used:
        conn.execute("UPDATE categories SET archived=1 WHERE id=?", (cid,))
        return {"archived": cid, "txn_count": used}
    conn.execute("DELETE FROM rules WHERE category_id=?", (cid,))
    conn.execute("DELETE FROM categories WHERE id=?", (cid,))
    return {"deleted": cid}


# ---------------------------------------------------------------- rules

def _rule_out(conn, r) -> dict:
    d = dict(r)
    c = conn.execute("SELECT name FROM categories WHERE id=?", (d.get("category_id"),)).fetchone()
    d["category_name"] = c[0] if c else None
    return d


@app.get("/api/rules")
def api_rules(conn=Depends(get_conn)):
    rs = [dict(r) for r in conn.execute("SELECT * FROM rules")]
    rs.sort(key=lambda r: (-(r["priority"] or 0), -rules._specificity(r), r["id"]))
    return [_rule_out(conn, r) for r in rs]


@app.post("/api/rules")
def api_create_rule(payload: dict = Body(...), conn=Depends(get_conn)):
    try:
        rule = rules.create_rule(conn, payload)
    except ValueError as e:
        raise HTTPException(422, str(e))
    updated = 0
    if payload.get("apply_to_history", True):
        updated = rules.apply_rules(conn)
    out = _rule_out(conn, conn.execute("SELECT * FROM rules WHERE id=?", (rule["id"],)).fetchone())
    out["updated_count"] = updated
    return out


@app.post("/api/rules/preview")
def api_rule_preview(payload: dict = Body(...), conn=Depends(get_conn)):
    try:
        return rules.preview(conn, payload)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/rules/apply")
def api_rules_apply(conn=Depends(get_conn)):
    return {"updated_count": rules.apply_rules(conn)}


@app.patch("/api/rules/{rid}")
def api_patch_rule(rid: int, payload: dict = Body(...), conn=Depends(get_conn)):
    if payload.get("match_type") == "regex" and payload.get("pattern") and \
            rules._compile(payload["pattern"]) is None:
        raise HTTPException(422, "invalid regex")
    _update(conn, "rules", rid, rules.RULE_FIELDS, payload)
    updated = rules.apply_rules(conn) if payload.get("apply_to_history", True) else 0
    out = _rule_out(conn, conn.execute("SELECT * FROM rules WHERE id=?", (rid,)).fetchone())
    out["updated_count"] = updated
    return out


@app.delete("/api/rules/{rid}")
def api_delete_rule(rid: int, conn=Depends(get_conn)):
    out = _delete(conn, "rules", rid)
    out["updated_count"] = rules.apply_rules(conn)
    return out


# ---------------------------------------------------------------- goals

GOAL_FIELDS = ("name", "target_amount", "target_date", "saved_amount", "monthly_contribution",
               "priority", "status", "notes", "asset_id", "step_ups")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _clean_step_ups(conn, payload: dict) -> None:
    """Validate payload['step_ups'] and store it as JSON text (NULL when empty). A step-up linked
    to a fixed end gets that charge's current monthly_amount as add_amount when none is given."""
    if "step_ups" not in payload:
        return
    raw = payload["step_ups"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else None
        except ValueError:
            raise HTTPException(422, "step_ups must be a JSON list")
    if not raw:
        payload["step_ups"] = None
        return
    if not isinstance(raw, list):
        raise HTTPException(422, "step_ups must be a list")
    ends = None
    out = []
    for x in raw:
        if not isinstance(x, dict):
            raise HTTPException(422, "each step-up must be an object")
        fm = str(x.get("from_month") or "")[:7]
        if not _MONTH_RE.match(fm):
            raise HTTPException(422, "step-up from_month must be YYYY-MM")
        fid = x.get("fixed_end_id") or None
        amt = x.get("add_amount")
        amt = None if amt in ("", None) else _num(amt, "add_amount")
        if fid is not None:
            fid = int(fid)
            if ends is None:
                ends = {e["id"]: e for e in _fixed_ends_out(conn)}
            if fid not in ends:
                raise HTTPException(422, f"fixed end {fid} not found")
            if amt is None:
                amt = ends[fid]["monthly_amount"]
        elif amt is None:
            raise HTTPException(422, "a step-up needs add_amount or fixed_end_id")
        out.append({"from_month": fm, "add_amount": amt, "reason": x.get("reason") or None,
                    "fixed_end_id": fid})
    out.sort(key=lambda s_: s_["from_month"])
    payload["step_ups"] = json.dumps(out, ensure_ascii=False)


def _goal_out(conn, gid: int) -> dict:
    r = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    if r is None:
        _404(f"goal {gid} not found")
    return analytics.goal_fields(dict(r), conn=conn)


def _check_goal_asset(conn, payload: dict) -> None:
    aid = payload.get("asset_id")
    if aid in ("", None):
        if "asset_id" in payload:
            payload["asset_id"] = None
        return
    if not conn.execute("SELECT 1 FROM assets WHERE id=?", (aid,)).fetchone():
        raise HTTPException(422, f"asset {aid} not found")


@app.get("/api/goals")
def api_goals(conn=Depends(get_conn)):
    return [analytics.goal_fields(dict(r), conn=conn) for r in
            conn.execute("SELECT * FROM goals ORDER BY status, priority DESC, target_date")]


@app.post("/api/goals")
def api_create_goal(payload: dict = Body(...), conn=Depends(get_conn)):
    if not payload.get("name") or payload.get("target_amount") is None:
        raise HTTPException(422, "name and target_amount are required")
    _check_goal_asset(conn, payload)
    _clean_step_ups(conn, payload)
    gid = _insert(conn, "goals", GOAL_FIELDS, payload, created_at=db.now_iso())
    return _goal_out(conn, gid)


@app.patch("/api/goals/{gid}")
def api_patch_goal(gid: int, payload: dict = Body(...), conn=Depends(get_conn)):
    _check_goal_asset(conn, payload)
    _clean_step_ups(conn, payload)
    _update(conn, "goals", gid, GOAL_FIELDS, payload)
    return _goal_out(conn, gid)


@app.delete("/api/goals/{gid}")
def api_delete_goal(gid: int, conn=Depends(get_conn)):
    return _delete(conn, "goals", gid)


# ---------------------------------------------------------------- freebies

FREEBIE_FIELDS = ("name", "source", "value", "unit", "received_date", "expiry_date",
                  "used_amount", "status", "where_to_use", "notes", "account_id")


@app.get("/api/freebies")
def api_freebies(conn=Depends(get_conn)):
    return analytics.list_freebies(conn)


@app.post("/api/freebies")
def api_create_freebie(payload: dict = Body(...), conn=Depends(get_conn)):
    if not payload.get("name"):
        raise HTTPException(422, "name is required")
    fid = _insert(conn, "freebies", FREEBIE_FIELDS, payload, created_at=db.now_iso())
    return analytics.freebie_fields(conn, dict(conn.execute("SELECT * FROM freebies WHERE id=?",
                                                            (fid,)).fetchone()))


@app.patch("/api/freebies/{fid}")
def api_patch_freebie(fid: int, payload: dict = Body(...), conn=Depends(get_conn)):
    _update(conn, "freebies", fid, FREEBIE_FIELDS, payload)
    return analytics.freebie_fields(conn, dict(conn.execute("SELECT * FROM freebies WHERE id=?",
                                                            (fid,)).fetchone()))


@app.delete("/api/freebies/{fid}")
def api_delete_freebie(fid: int, conn=Depends(get_conn)):
    return _delete(conn, "freebies", fid)


# ---------------------------------------------------------------- tasks

TASK_FIELDS = ("title", "notes", "due_date", "status", "priority", "est_monthly_saving",
               "actual_monthly_saving", "merchant", "category_id", "insight_key")


def _tasks_payload(conn, status: str | None) -> dict:
    q, params = "SELECT * FROM tasks", []
    if status:
        q += " WHERE status=?"
        params.append(status)
    q += (" ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'done' THEN 1 ELSE 2 END, "
          "CASE priority WHEN 'high' THEN 0 WHEN 'med' THEN 1 ELSE 2 END, due_date, id")
    items = [dict(r) for r in conn.execute(q, params)]
    est = conn.execute("SELECT COALESCE(SUM(est_monthly_saving),0) FROM tasks "
                       "WHERE status='open'").fetchone()[0]
    act = conn.execute("SELECT COALESCE(SUM(COALESCE(actual_monthly_saving, est_monthly_saving)),0)"
                       " FROM tasks WHERE status='done'").fetchone()[0]
    return {"items": items, "total_est_monthly_saving": round(est, 2),
            "total_actual_monthly_saving": round(act, 2)}


@app.get("/api/tasks")
def api_tasks(status: str | None = None, conn=Depends(get_conn)):
    return _tasks_payload(conn, status)


@app.post("/api/tasks")
def api_create_task(payload: dict = Body(...), conn=Depends(get_conn)):
    if not payload.get("title"):
        raise HTTPException(422, "title is required")
    tid = _insert(conn, "tasks", TASK_FIELDS, payload, created_at=db.now_iso())
    return dict(conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())


@app.patch("/api/tasks/{tid}")
def api_patch_task(tid: int, payload: dict = Body(...), conn=Depends(get_conn)):
    extra = {}
    if payload.get("status") == "done":
        extra["done_at"] = db.now_iso()
    elif payload.get("status") in ("open", "dropped"):
        extra["done_at"] = None
    _update(conn, "tasks", tid, TASK_FIELDS, payload, **extra)
    return dict(conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())


@app.delete("/api/tasks/{tid}")
def api_delete_task(tid: int, conn=Depends(get_conn)):
    return _delete(conn, "tasks", tid)


# ---------------------------------------------------------------- net worth

ASSET_FIELDS = ("key", "name", "kind", "include_in_net_worth", "liquid", "notes", "source",
                "track_from_bank")
LIAB_FIELDS = ("key", "name", "kind", "principal", "rate_pct", "start_month", "monthly_payment",
               "notes", "include_in_net_worth")
PLANNED_FIELDS = ("kind", "when_month", "amount", "what", "notes", "done")


def _bad(e: Exception):
    raise HTTPException(422, str(e))


def _bools(payload: dict, *keys: str) -> dict:
    for k in keys:
        if k in payload and payload[k] is not None:
            payload[k] = int(bool(payload[k]))
    return payload


def _new_key(conn, table: str, name: str) -> str:
    slug = "".join(ch if ch.isalnum() else " " for ch in (name or "").casefold())
    base = "_".join(slug.split()) or table[:-1]
    key, i = base, 2
    while conn.execute(f"SELECT 1 FROM {table} WHERE key=?", (key,)).fetchone():
        key, i = f"{base}_{i}", i + 1
    return key


def _asset_out(conn, aid: int) -> dict:
    r = conn.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
    if r is None:
        _404(f"asset {aid} not found")
    return networth.asset_json(conn, dict(r), analytics.today())


def _liab_out(conn, lid: int) -> dict:
    r = conn.execute("SELECT * FROM liabilities WHERE id=?", (lid,)).fetchone()
    if r is None:
        _404(f"liability {lid} not found")
    return networth.liability_json(conn, dict(r), analytics.today())


@app.get("/api/networth")
def api_networth(conn=Depends(get_conn)):
    return networth.summary(conn)


@app.post("/api/networth/import-sheet")
def api_import_sheet(payload: dict | None = Body(None), conn=Depends(get_conn)):
    from .sheet_import import import_sheet_snapshot, sheet_dir

    path = None
    name = (payload or {}).get("file")
    if name:  # only files inside data/sheet/
        path = sheet_dir() / Path(name).name
    try:
        return import_sheet_snapshot(path, conn=conn)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        _bad(e)


@app.get("/api/assets")
def api_assets(conn=Depends(get_conn)):
    return networth.assets_list(conn)


@app.post("/api/assets")
def api_create_asset(payload: dict = Body(...), conn=Depends(get_conn)):
    if not payload.get("name"):
        raise HTTPException(422, "name is required")
    try:
        kind = networth.check_kind(payload.get("kind") or "other", db.ASSET_KINDS, "asset")
        payload = _bools(dict(payload, kind=kind), "include_in_net_worth", "liquid", "track_from_bank")
        payload.setdefault("liquid", networth.default_liquid(kind))
        payload.setdefault("source", "manual")
        payload["key"] = payload.get("key") or _new_key(conn, "assets", payload["name"])
        aid = _insert(conn, "assets", ASSET_FIELDS, payload, created_at=db.now_iso())
        if payload.get("value") is not None:
            networth.set_asset_value(conn, aid, payload.get("as_of"), payload["value"],
                                     payload.get("breakdown"))
    except sqlite3.IntegrityError as e:
        _bad(e)
    except ValueError as e:
        _bad(e)
    return _asset_out(conn, aid)


@app.patch("/api/assets/{aid}")
def api_patch_asset(aid: int, payload: dict = Body(...), conn=Depends(get_conn)):
    try:
        if "kind" in payload:
            networth.check_kind(payload["kind"], db.ASSET_KINDS, "asset")
        _update(conn, "assets", aid, ASSET_FIELDS,
                _bools(dict(payload), "include_in_net_worth", "liquid", "track_from_bank"))
    except (sqlite3.IntegrityError, ValueError) as e:
        _bad(e)
    return _asset_out(conn, aid)


@app.delete("/api/assets/{aid}")
def api_delete_asset(aid: int, conn=Depends(get_conn)):
    conn.execute("DELETE FROM asset_values WHERE asset_id=?", (aid,))
    return _delete(conn, "assets", aid)


@app.post("/api/assets/{aid}/value")
def api_asset_value(aid: int, payload: dict = Body(...), conn=Depends(get_conn)):
    _asset_out(conn, aid)  # 404 if missing
    try:
        networth.set_asset_value(conn, aid, payload.get("as_of"), _num(payload.get("value"), "value"),
                                 payload.get("breakdown"))
    except ValueError as e:
        _bad(e)
    return _asset_out(conn, aid)


@app.get("/api/liabilities")
def api_liabilities(conn=Depends(get_conn)):
    return networth.liabilities_list(conn)


@app.post("/api/liabilities")
def api_create_liability(payload: dict = Body(...), conn=Depends(get_conn)):
    if not payload.get("name"):
        raise HTTPException(422, "name is required")
    try:
        kind = networth.check_kind(payload.get("kind") or "other", db.LIABILITY_KINDS, "liability")
        payload = _bools(dict(payload, kind=kind), "include_in_net_worth")
        payload["key"] = payload.get("key") or _new_key(conn, "liabilities", payload["name"])
        if payload.get("start_month"):
            payload["start_month"] = networth.check_month(payload["start_month"], "start_month")
        lid = _insert(conn, "liabilities", LIAB_FIELDS, payload, created_at=db.now_iso())
        bal = payload.get("balance", payload.get("principal"))
        if bal is not None:
            networth.set_liability_balance(conn, lid, payload.get("as_of"), bal)
    except (sqlite3.IntegrityError, ValueError) as e:
        _bad(e)
    return _liab_out(conn, lid)


@app.patch("/api/liabilities/{lid}")
def api_patch_liability(lid: int, payload: dict = Body(...), conn=Depends(get_conn)):
    try:
        if "kind" in payload:
            networth.check_kind(payload["kind"], db.LIABILITY_KINDS, "liability")
        if payload.get("start_month"):
            payload["start_month"] = networth.check_month(payload["start_month"], "start_month")
        _update(conn, "liabilities", lid, LIAB_FIELDS, _bools(dict(payload), "include_in_net_worth"))
    except (sqlite3.IntegrityError, ValueError) as e:
        _bad(e)
    return _liab_out(conn, lid)


@app.delete("/api/liabilities/{lid}")
def api_delete_liability(lid: int, conn=Depends(get_conn)):
    conn.execute("DELETE FROM liability_values WHERE liability_id=?", (lid,))
    return _delete(conn, "liabilities", lid)


@app.post("/api/liabilities/{lid}/value")
def api_liability_value(lid: int, payload: dict = Body(...), conn=Depends(get_conn)):
    _liab_out(conn, lid)
    try:
        networth.set_liability_balance(conn, lid, payload.get("as_of"),
                                       _num(payload.get("balance", payload.get("value")), "balance"))
    except ValueError as e:
        _bad(e)
    return _liab_out(conn, lid)


def _planned_clean(payload: dict, partial: bool) -> dict:
    p = dict(payload)
    if not partial or "kind" in p:
        p["kind"] = networth.check_kind(p.get("kind") or "expense", ("income", "expense"), "planned")
    if "when_month" in p:
        p["when_month"] = networth.check_month(p["when_month"])
    if "amount" in p:
        if p["amount"] is None:
            raise ValueError("amount is required")
        p["amount"] = abs(float(p["amount"]))
    if not partial and (not p.get("what") or p.get("amount") is None):
        raise ValueError("what and amount are required")
    return _bools(p, "done")


def _planned_out(conn, pid: int) -> dict:
    r = conn.execute("SELECT * FROM planned_items WHERE id=?", (pid,)).fetchone()
    if r is None:
        _404(f"planned item {pid} not found")
    return networth.planned_json(dict(r), analytics.current_month(conn))


@app.get("/api/planned")
def api_planned(conn=Depends(get_conn)):
    return networth.planned_list(conn)


@app.post("/api/planned")
def api_create_planned(payload: dict = Body(...), conn=Depends(get_conn)):
    try:
        p = _planned_clean(payload, partial=False)
    except (TypeError, ValueError) as e:
        _bad(e)
    pid = _insert(conn, "planned_items", PLANNED_FIELDS, p, source="manual", created_at=db.now_iso())
    return _planned_out(conn, pid)


@app.patch("/api/planned/{pid}")
def api_patch_planned(pid: int, payload: dict = Body(...), conn=Depends(get_conn)):
    try:
        p = _planned_clean(payload, partial=True)
    except (TypeError, ValueError) as e:
        _bad(e)
    _update(conn, "planned_items", pid, PLANNED_FIELDS, p)
    return _planned_out(conn, pid)


@app.delete("/api/planned/{pid}")
def api_delete_planned(pid: int, conn=Depends(get_conn)):
    return _delete(conn, "planned_items", pid)


# fixed charges with a known end (the forecast drops them after last_month)
FIXED_END_FIELDS = ("category_id", "merchant", "last_month", "notes")


def _fixed_end_clean(conn, payload: dict, partial: bool) -> dict:
    p = {k: payload[k] for k in FIXED_END_FIELDS if k in payload}
    if "last_month" in p or not partial:
        p["last_month"] = networth.check_month(p.get("last_month"), "last_month", allow_none=False)
    if p.get("merchant") == "":
        p["merchant"] = None
    if "category_id" in p:
        if p["category_id"] in ("", None):
            p["category_id"] = None
        elif not conn.execute("SELECT 1 FROM categories WHERE id=?",
                              (p["category_id"],)).fetchone():
            raise ValueError(f"category {p['category_id']} not found")
    if not partial and not p.get("category_id") and not p.get("merchant"):
        raise ValueError("category_id or merchant is required")
    return p


def _fixed_ends_out(conn) -> list[dict]:
    led = analytics.Ledger(conn)
    return analytics.fixed_ends(conn, led, analytics.current_month(conn))


@app.get("/api/fixed-ends")
def api_fixed_ends(conn=Depends(get_conn)):
    return _fixed_ends_out(conn)


@app.post("/api/fixed-ends")
def api_create_fixed_end(payload: dict = Body(...), conn=Depends(get_conn)):
    try:
        p = _fixed_end_clean(conn, payload, partial=False)
    except ValueError as e:
        _bad(e)
    fid = _insert(conn, "fixed_ends", FIXED_END_FIELDS, p, created_at=db.now_iso())
    return next(x for x in _fixed_ends_out(conn) if x["id"] == fid)


@app.patch("/api/fixed-ends/{fid}")
def api_patch_fixed_end(fid: int, payload: dict = Body(...), conn=Depends(get_conn)):
    try:
        p = _fixed_end_clean(conn, payload, partial=True)
    except ValueError as e:
        _bad(e)
    _update(conn, "fixed_ends", fid, FIXED_END_FIELDS, p)
    return next(x for x in _fixed_ends_out(conn) if x["id"] == fid)


@app.delete("/api/fixed-ends/{fid}")
def api_delete_fixed_end(fid: int, conn=Depends(get_conn)):
    return _delete(conn, "fixed_ends", fid)


# ---------------------------------------------------------------- insights

@app.get("/api/insights")
def api_insights(include_dismissed: bool = False, conn=Depends(get_conn)):
    return insights.list_insights(conn, include_dismissed=include_dismissed)


@app.post("/api/insights/{key:path}/dismiss")
def api_dismiss(key: str, payload: dict | None = Body(None), conn=Depends(get_conn)):
    return insights.dismiss(conn, key, (payload or {}).get("reason"))


@app.post("/api/insights/{key:path}/task")
def api_insight_task(key: str, conn=Depends(get_conn)):
    try:
        return insights.insight_to_task(conn, key)
    except KeyError:
        _404(f"insight {key} not found")


@app.post("/api/insights/manual")
def api_manual_insight(payload: dict = Body(...), conn=Depends(get_conn)):
    """Upsert an offline-review (ai_review) insight."""
    if not payload.get("key") or not payload.get("title"):
        raise HTTPException(422, "key and title are required")
    st = payload.get("suggested_task")
    conn.execute(
        "INSERT OR REPLACE INTO manual_insights(key,type,severity,title,detail,est_monthly_saving,"
        "est_yearly_saving,txn_ids,merchant,category_id,suggested_task,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (payload["key"], payload.get("type", "ai_review"), payload.get("severity", "med"),
         payload["title"], payload.get("detail"), payload.get("est_monthly_saving"),
         payload.get("est_yearly_saving"), json.dumps(payload.get("txn_ids") or []),
         payload.get("merchant"), payload.get("category_id"),
         json.dumps(st) if isinstance(st, dict) else st, db.now_iso()),
    )
    return {"key": payload["key"], "saved": True}


# ---------------------------------------------------------------- import

@app.post("/api/import")
def api_import(file: UploadFile = File(...), conn=Depends(get_conn)):
    name = Path(file.filename or "upload").name
    tmpdir = Path(tempfile.mkdtemp(dir=config.data_dir()) if config.data_dir().exists()
                  else tempfile.mkdtemp())
    path = tmpdir / name
    try:
        with path.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        try:
            res = ingest.import_file(path, conn=conn)
        except Exception as e:
            raise HTTPException(422, f"{type(e).__name__}: {e}")
        dest = config.imported_dir()
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / f"{datetime.now():%Y%m%d}_{name}"
        i = 1
        while target.exists():
            target = dest / f"{datetime.now():%Y%m%d}_{i}_{name}"
            i += 1
        shutil.move(str(path), str(target))
        return res
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.post("/api/import/inbox")
def api_import_inbox(conn=Depends(get_conn)):
    return ingest.import_inbox(conn=conn)


# ---------------------------------------------------------------- sync

_sync_lock = threading.Lock()
_sync_state: dict = {"running": False, "providers": [], "started_at": None, "finished_at": None,
                     "error": None}


def run_sync_job(providers: list[str] | None = None, start: str | None = None) -> dict:
    """Run scrapers, ingest results. Guarded: returns immediately if a sync is running."""
    if not _sync_lock.acquire(blocking=False):
        return {"running": True, "skipped": True}
    try:
        _sync_state.update(running=True, started_at=db.now_iso(), finished_at=None, error=None,
                           providers=[])
        from .sync import run_sync

        conn = db.connect()
        try:
            if providers is None:
                providers = [p.strip() for p in
                             (db.get_setting(conn, "sync_providers", "leumi,max") or "").split(",")
                             if p.strip()]
            start_d = date.fromisoformat(start) if start else None
            results = run_sync(providers=providers, start=start_d)
            out = []
            for r in results:
                entry = {"name": r.get("name"), "ok": bool(r.get("ok")), "error": r.get("error"),
                         "new_rows": 0, "rows": 0, "at": db.now_iso()}
                if r.get("ok") and r.get("txns"):
                    try:
                        res = ingest.ingest(r["txns"], {"file_name": f"sync:{r.get('name')}",
                                                        "provider": r.get("name")}, conn=conn)
                        entry.update(new_rows=res["new_rows"], rows=res["rows"])
                    except Exception as e:
                        entry.update(ok=False, error=f"ingest: {type(e).__name__}: {e}")
                out.append(entry)
                _sync_state["providers"] = list(out)
            with conn:
                db.set_setting(conn, "last_sync", {"at": db.now_iso(), "providers": out})
        finally:
            conn.close()
    except Exception as e:
        _sync_state["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        _sync_state.update(running=False, finished_at=db.now_iso())
        _sync_lock.release()
    return dict(_sync_state)


@app.post("/api/sync")
def api_sync(payload: dict | None = Body(None)):
    payload = payload or {}
    if _sync_state["running"] or _sync_lock.locked():
        return JSONResponse({"started": False, "running": True}, status_code=409)
    providers = payload.get("providers")
    if isinstance(providers, str):
        providers = [p.strip() for p in providers.split(",") if p.strip()]
    _sync_state["running"] = True  # visible immediately to /status
    th = threading.Thread(target=run_sync_job, args=(providers, payload.get("start")),
                          daemon=True)
    th.start()
    return {"started": True, "running": True}


@app.get("/api/sync/status")
def api_sync_status():
    return dict(_sync_state)


# ---------------------------------------------------------------- settings / export

NUMERIC_SETTINGS = {"cashflow_start_day", "buffer_months", "overspend_pct", "freebie_warn_days",
                    "salary_net_baseline", "rental_tax_pct"}
NULLABLE_SETTINGS = {"salary_net_baseline"}
BOOL_SETTINGS = {"count_variable_income_in_forecast"}


@app.get("/api/settings")
def api_settings(conn=Depends(get_conn)):
    return db.get_settings(conn)


@app.patch("/api/settings")
def api_patch_settings(payload: dict = Body(...), conn=Depends(get_conn)):
    for k, v in payload.items():
        if k == "schema_version":
            continue
        if k in NULLABLE_SETTINGS and v in (None, ""):
            conn.execute("INSERT INTO settings(key,value) VALUES(?,NULL) "
                         "ON CONFLICT(key) DO UPDATE SET value=NULL", (k,))
            continue
        if k in NUMERIC_SETTINGS and _num(v, k) is None:
            raise HTTPException(422, f"{k} must be a number")
        if k in BOOL_SETTINGS:
            v = "true" if str(v).strip().lower() in ("1", "true", "yes", "on") else "false"
        if k == "cashflow_start_day" and not 1 <= int(float(v)) <= 28:
            raise HTTPException(422, "cashflow_start_day must be 1..28")
        if k == "cal_account_kind" and v not in ("gift", "card"):
            raise HTTPException(422, "cal_account_kind must be 'gift' or 'card'")
        if k == "cashflow_start_day":
            v = str(int(float(v)))
        db.set_setting(conn, k, v)
    if "cashflow_start_day" in payload:
        recompute_cashflow_months(conn)
    return db.get_settings(conn)


def recompute_cashflow_months(conn) -> int:
    sd = int(float(db.get_setting(conn, "cashflow_start_day", "1")))
    n = 0
    for r in conn.execute("""SELECT t.id, t.date, t.billing_date, a.kind, a.provider
                             FROM transactions t JOIN accounts a ON a.id=t.account_id
                             WHERE a.provider<>'riseup'""").fetchall():
        d = date.fromisoformat(r["date"])
        bd = date.fromisoformat(r["billing_date"]) if r["billing_date"] else None
        m = ingest.cashflow_month(ingest.txn_cashflow_date(r["kind"], d, bd), sd)
        conn.execute("UPDATE transactions SET cashflow_month=? WHERE id=?", (m, r["id"]))
        n += 1
    return n


EXPORT_COLS = ["id", "date", "billing_date", "cashflow_month", "amount", "original_amount",
               "original_currency", "description", "merchant", "category_name",
               "category_source", "account_key", "account_kind", "is_transfer", "is_excluded",
               "installment_no", "installment_total", "provider_category", "notes"]


def export_csv_text(conn) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLS)
    for r in conn.execute(db.TXN_SELECT + " ORDER BY t.date, t.rowid"):
        d = dict(r)
        w.writerow([d.get(c) for c in EXPORT_COLS])
    return buf.getvalue()


@app.get("/api/export.csv")
def api_export(conn=Depends(get_conn)):
    return Response("﻿" + export_csv_text(conn), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename=finance-{date.today():%Y%m%d}.csv"})


@app.post("/api/backup")
def api_backup():
    return {"path": str(db.backup())}


@app.get("/api/health")
def api_health():
    return {"ok": True, "time": db.now_iso()}


# ---------------------------------------------------------------- UI

if config.UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(config.UI_DIR), html=True), name="ui")
else:
    @app.get("/", response_class=HTMLResponse)
    def _no_ui():
        return "<h1>finapp</h1><p>UI not built yet (app/ui/index.html). API: /api/docs</p>"

