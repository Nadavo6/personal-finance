"""RawTxn list -> dedupe -> transfer marking / categorisation -> insert."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from . import config, db, rules

NATIVE_PROVIDERS = {"leumi", "max", "cal", "generic"}
DATE_WINDOW_DAYS = 2
MERCHANT_SIM_MIN = 0.5


# ---------------------------------------------------------------- helpers

def _d(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes", "y", "t"}
    return bool(v)


def cashflow_month(d: date, start_day: int = 1) -> str:
    """Cashflow month of a date (rule 6).

    start_day 1 = calendar month. Otherwise the month runs from `start_day` to the day before
    the next `start_day`. A period starting in the first half of a month is named after that
    month; one starting after the 15th (salary at month end) is named after the next month.
    """
    start_day = max(1, min(28, int(start_day or 1)))
    y, m = d.year, d.month
    if start_day == 1:
        return f"{y:04d}-{m:02d}"
    if d.day < start_day:  # belongs to the period that started last month
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    if start_day > 15:  # name it after the month the period mostly covers
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return f"{y:04d}-{m:02d}"


def txn_cashflow_date(account_kind: str, d: date, billing: date | None) -> date:
    if account_kind in ("card", "gift") and billing:
        return billing
    return d


def txn_id(account_key: str, d: date, amount: float, desc_norm: str, occ: int) -> str:
    s = f"{account_key}|{d.isoformat()}|{amount:.2f}|{desc_norm}|{occ}"
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def merchant_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return max(jac, SequenceMatcher(None, a, b).ratio())


def _default_account_name(provider: str, kind: str, key: str) -> str:
    tail = key.split(":")[-1]
    if provider == "leumi":
        return "Leumi checking"
    if provider == "max":
        return f"Max card •{tail[-4:]}"
    if provider == "cal":
        return f"Cal {'gift card' if kind == 'gift' else 'card'} •{tail[-4:]}"
    if provider == "riseup":
        src = key.split(":")[1] if key.count(":") >= 2 else "riseup"
        return f"RiseUp {src} {tail[:4]}"
    return f"{provider} {kind} {tail[-6:]}"


def upsert_account(conn: sqlite3.Connection, key: str, provider: str, kind: str) -> int:
    r = conn.execute("SELECT id, kind FROM accounts WHERE key=?", (key,)).fetchone()
    if r:
        if r["kind"] != kind and kind in ("bank", "card", "gift"):
            conn.execute("UPDATE accounts SET kind=? WHERE id=?", (kind, r["id"]))
        return r["id"]
    cur = conn.execute(
        "INSERT INTO accounts(key,provider,kind,name,created_at) VALUES (?,?,?,?,?)",
        (key, provider, kind, _default_account_name(provider, kind, key), db.now_iso()),
    )
    return cur.lastrowid


def _amount_tol(amount: float, fx: bool) -> float:
    return max(0.02, abs(amount) * 0.02) if fx else 0.02


def _find_counterpart(
    conn: sqlite3.Connection,
    *,
    want_riseup: bool,
    kind: str,
    d: date,
    amount: float,
    merchant: str,
    fx: bool,
    inst_no: int | None = None,
    inst_total: int | None = None,
) -> dict | None:
    """Cross-source dedupe candidate (rule 4): same account kind, same abs amount,
    date within ±2 days, similar merchant.

    Installments: Max dates every installment with the purchase date while RiseUp dates
    each with its own billing month, so an installment N/M matches on (N, M, amount ±1,
    similar merchant) within a year, regardless of the ±2-day window."""
    prov_cond = "a.provider='riseup'" if want_riseup else "a.provider<>'riseup'"
    if inst_total and inst_total > 1 and inst_no:
        lo = (d - timedelta(days=400)).isoformat()
        hi = (d + timedelta(days=400)).isoformat()
        q = (
            "SELECT t.*, a.kind AS account_kind, a.provider AS account_provider FROM transactions t "
            f"JOIN accounts a ON a.id=t.account_id WHERE {prov_cond} AND a.kind=? "
            "AND t.installment_no=? AND t.installment_total=? AND t.date BETWEEN ? AND ? "
            "AND ABS(ABS(t.amount) - ?) <= 1.0"
            + ("" if want_riseup else " AND t.merged_riseup_id IS NULL")
        )
        for c in (dict(r) for r in conn.execute(q, (kind, inst_no, inst_total, lo, hi, abs(amount)))):
            if merchant_similarity(merchant, c["merchant"] or "") >= MERCHANT_SIM_MIN:
                return c
    lo = (d - timedelta(days=DATE_WINDOW_DAYS)).isoformat()
    hi = (d + timedelta(days=DATE_WINDOW_DAYS)).isoformat()
    tol = _amount_tol(amount, fx)
    extra = "" if want_riseup else " AND t.merged_riseup_id IS NULL"
    q = (
        "SELECT t.*, a.kind AS account_kind, a.provider AS account_provider FROM transactions t "
        f"JOIN accounts a ON a.id=t.account_id WHERE {prov_cond} AND a.kind=? "
        "AND t.date BETWEEN ? AND ? AND ABS(ABS(t.amount) - ?) <= ?" + extra
    )
    cands = [dict(r) for r in conn.execute(q, (kind, lo, hi, abs(amount), tol))]
    if not cands:
        return None
    scored = []
    for c in cands:
        sim = merchant_similarity(merchant, c["merchant"] or "")
        dd = abs((_d(c["date"]) - d).days)
        scored.append((sim, -dd, c))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    sim, neg_dd, best = scored[0]
    if sim >= MERCHANT_SIM_MIN:
        return best
    if len(cands) == 1 and neg_dd == 0:
        return best
    return None


# ---------------------------------------------------------------- ingest

def ingest(
    raw_txns: Iterable,
    import_meta: dict | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Insert RawTxns. Returns {rows, new_rows, dup_rows, merged_rows, import_id, provider,
    first_date, last_date} (the date range the file covers)."""
    own = conn is None
    conn = conn or db.connect()
    meta = import_meta or {}
    raw_list = list(raw_txns)
    try:
        with conn:
            res = _ingest(conn, raw_list, meta)
    finally:
        if own:
            conn.close()
    return res


def _ingest(conn: sqlite3.Connection, raw_list: list, meta: dict) -> dict:
    start_day = int(db.get_setting(conn, "cashflow_start_day", "1") or 1)
    provider = meta.get("provider") or (raw_list[0].provider if raw_list else None)
    cur = conn.execute(
        "INSERT INTO imports(file_name,provider,rows,new_rows,dup_rows,imported_at) "
        "VALUES (?,?,?,?,?,?)",
        (meta.get("file_name"), provider, len(raw_list), 0, 0, db.now_iso()),
    )
    import_id = cur.lastrowid
    accounts: dict[str, int] = {}
    occ: dict[tuple, int] = {}
    new_ids: list[str] = []
    touched_native: set[str] = set()
    dup = merged = 0
    ts = db.now_iso()

    cal_kind = db.get_setting(conn, "cal_account_kind") or "card"
    for rt in raw_list:
        kind = rt.account_kind if rt.account_kind in ("bank", "card", "gift") else "card"
        if rt.provider == "cal" and cal_kind in ("card", "gift"):
            kind = cal_kind  # importers/sync mark Cal as 'gift'; the setting decides
        if rt.account_key not in accounts:
            accounts[rt.account_key] = upsert_account(conn, rt.account_key, rt.provider, kind)
        acc_id = accounts[rt.account_key]
        d = _d(rt.date)
        bd = _d(rt.billing_date)
        amount = round(float(rt.amount), 2)
        desc = db.strip_bidi(rt.description or "").strip()
        norm = db.normalize_merchant(desc)
        merchant = norm or desc.lower()
        okey = (rt.account_key, d, amount, norm)
        n = occ.get(okey, 0)
        occ[okey] = n + 1
        tid = txn_id(rt.account_key, d, amount, norm, n)

        if conn.execute("SELECT 1 FROM transactions WHERE id=?", (tid,)).fetchone() or \
                conn.execute("SELECT 1 FROM txn_aliases WHERE alias_id=?", (tid,)).fetchone():
            dup += 1
            continue

        raw = rt.raw or {}
        is_riseup = rt.provider == "riseup"
        fx = bool(rt.original_currency and rt.original_currency.upper() not in ("ILS", "NIS", "₪"))
        base_excluded = 1 if kind == "gift" else 0
        riseup_cat = None
        if is_riseup:
            riseup_cat = rt.provider_category
            if _truthy(raw.get("is_excluded")):
                base_excluded = 1
        cfm = None
        if is_riseup and raw.get("cashflow_month"):
            cfm = str(raw["cashflow_month"])[:7]
        if not cfm:
            cfm = cashflow_month(txn_cashflow_date(kind, d, bd), start_day)

        row = {
            "id": tid, "account_id": acc_id, "date": d.isoformat(),
            "billing_date": bd.isoformat() if bd else None, "cashflow_month": cfm,
            "amount": amount, "original_amount": rt.original_amount,
            "original_currency": rt.original_currency, "description": desc,
            "merchant": merchant, "category_id": None, "category_source": None,
            "is_transfer": 0, "is_excluded": base_excluded,
            "installment_no": rt.installment_no, "installment_total": rt.installment_total,
            "provider_category": rt.provider_category, "notes": None, "import_id": import_id,
            "created_at": ts, "updated_at": ts, "riseup_category": riseup_cat,
            "base_excluded": base_excluded, "flags_manual": 0,
            "balance_after": rt.balance_after, "external_id": rt.external_id,
            "merged_riseup_id": None,
        }

        # ---- cross-source dedupe (rule 4)
        if kind in ("bank", "card"):
            if is_riseup:
                native = _find_counterpart(conn, want_riseup=False, kind=kind, d=d,
                                           amount=amount, merchant=merchant, fx=fx,
                                           inst_no=rt.installment_no, inst_total=rt.installment_total)
                if native:
                    _absorb_riseup_into(conn, native, row)
                    conn.execute("INSERT OR IGNORE INTO txn_aliases(alias_id,txn_id,account_id) "
                                 "VALUES(?,?,?)", (tid, native["id"], acc_id))
                    touched_native.add(native["id"])
                    dup += 1
                    continue
            elif rt.provider in NATIVE_PROVIDERS:
                ru = _find_counterpart(conn, want_riseup=True, kind=kind, d=d,
                                       amount=amount, merchant=merchant, fx=fx,
                                       inst_no=rt.installment_no, inst_total=rt.installment_total)
                if ru:
                    _absorb_riseup_into(conn, row, ru, row_is_new=True)
                    row["merged_riseup_id"] = ru["id"]
                    conn.execute("DELETE FROM transactions WHERE id=?", (ru["id"],))
                    conn.execute("UPDATE txn_aliases SET txn_id=? WHERE txn_id=?", (tid, ru["id"]))
                    conn.execute("INSERT OR REPLACE INTO txn_aliases(alias_id,txn_id,account_id) "
                                 "VALUES(?,?,?)", (ru["id"], tid, ru.get("account_id")))
                    merged += 1

        cols = ",".join(row)
        conn.execute(f"INSERT INTO transactions({cols}) VALUES ({','.join('?' * len(row))})",
                     tuple(row.values()))
        new_ids.append(tid)

    rules.apply_rules(conn, only_non_manual=True, txn_ids=new_ids + sorted(touched_native))
    if merged or touched_native:
        refresh_riseup_account_names(conn)
    conn.execute("UPDATE imports SET new_rows=?, dup_rows=? WHERE id=?",
                 (len(new_ids), dup, import_id))
    dates = sorted(rt.date for rt in raw_list if rt.date)
    return {"rows": len(raw_list), "new_rows": len(new_ids), "dup_rows": dup,
            "merged_rows": merged, "import_id": import_id, "provider": provider,
            "first_date": dates[0].isoformat() if dates else None,
            "last_date": dates[-1].isoformat() if dates else None}


RISEUP_SUFFIX = "(RiseUp history)"


def backfill_alias_accounts(conn: sqlite3.Connection, riseup_csv: str | Path | None = None) -> int:
    """Fill txn_aliases.account_id for RiseUp rows merged away before it was recorded, by
    recomputing the RiseUp row ids from the history CSV (same id scheme as _ingest)."""
    path = Path(riseup_csv) if riseup_csv else config.data_dir() / "riseup_transactions.csv"
    if not path.exists() or not conn.execute(
            "SELECT 1 FROM txn_aliases WHERE account_id IS NULL LIMIT 1").fetchone():
        return 0
    from .importers.base import detect

    occ: dict[tuple, int] = {}
    accounts: dict[str, int | None] = {}
    filled = 0
    for rt in detect(path).parse(path):
        if rt.account_key not in accounts:
            r = conn.execute("SELECT id FROM accounts WHERE key=?", (rt.account_key,)).fetchone()
            accounts[rt.account_key] = r[0] if r else None
        d = _d(rt.date)
        amount = round(float(rt.amount), 2)
        norm = db.normalize_merchant(db.strip_bidi(rt.description or "").strip())
        okey = (rt.account_key, d, amount, norm)
        n = occ.get(okey, 0)
        occ[okey] = n + 1
        if accounts[rt.account_key] is None:
            continue
        filled += conn.execute(
            "UPDATE txn_aliases SET account_id=? WHERE alias_id=? AND account_id IS NULL",
            (accounts[rt.account_key], txn_id(rt.account_key, d, amount, norm, n))).rowcount
    return filled


def refresh_riseup_account_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Friendly names for RiseUp history accounts (unless the user renamed them):
    bank -> "Leumi checking (RiseUp history)"; card -> "Max •<last4> (RiseUp history)", the
    last4 being the Max card most of its rows merged into."""
    out = {}
    for a in conn.execute("SELECT * FROM accounts WHERE provider='riseup'").fetchall():
        default = _default_account_name(a["provider"], a["kind"], a["key"])
        if a["name"] not in (None, "", default) and not a["name"].endswith(RISEUP_SUFFIX):
            continue  # user-chosen name
        if a["kind"] == "bank":
            name = f"Leumi checking {RISEUP_SUFFIX}"
        else:
            top = conn.execute(
                """SELECT na.key, COUNT(*) n FROM txn_aliases al
                   JOIN transactions t ON t.id=al.txn_id
                   JOIN accounts na ON na.id=t.account_id
                   WHERE al.account_id=? AND na.provider='max'
                   GROUP BY na.key ORDER BY n DESC, na.key LIMIT 1""", (a["id"],)).fetchone()
            if top:
                name = f"Max •{top['key'].split(':')[-1][-4:]} {RISEUP_SUFFIX}"
            else:
                name = f"Card {a['key'].split(':')[-1][:4]} {RISEUP_SUFFIX}"
        if name != a["name"]:
            conn.execute("UPDATE accounts SET name=? WHERE id=?", (name, a["id"]))
        out[a["key"]] = name
    return out


def _absorb_riseup_into(conn, native: dict, ru: dict, row_is_new: bool = False) -> None:
    """Carry RiseUp-side info onto the native row: manual category, RiseUp category,
    one-off exclusion, notes, manual flags."""
    upd: dict[str, Any] = {}
    if ru.get("category_source") == "manual" and native.get("category_source") != "manual":
        upd["category_id"] = ru["category_id"]
        upd["category_source"] = "manual"
    if ru.get("riseup_category") and not native.get("riseup_category"):
        upd["riseup_category"] = ru["riseup_category"]
    if ru.get("base_excluded") and not native.get("base_excluded"):
        upd["base_excluded"] = 1
        if not native.get("flags_manual"):
            upd["is_excluded"] = 1
    if ru.get("flags_manual") and not native.get("flags_manual"):
        upd.update(flags_manual=1, is_transfer=ru["is_transfer"], is_excluded=ru["is_excluded"])
    if ru.get("notes") and not native.get("notes"):
        upd["notes"] = ru["notes"]
    if not upd:
        return
    if row_is_new:
        native.update(upd)
    else:
        sets = ",".join(f"{k}=?" for k in upd)
        conn.execute(f"UPDATE transactions SET {sets}, updated_at=? WHERE id=?",
                     (*upd.values(), db.now_iso(), native["id"]))


# ---------------------------------------------------------------- files

def import_file(path: str | Path, conn: sqlite3.Connection | None = None) -> dict:
    from .importers.base import detect

    path = Path(path)
    importer = detect(path)
    txns = importer.parse(path)
    res = ingest(txns, {"file_name": path.name, "provider": importer.provider}, conn=conn)
    res["provider"] = importer.provider
    res["file_name"] = path.name
    return res


def import_inbox(conn: sqlite3.Connection | None = None) -> dict:
    inbox = config.inbox_dir()
    dest = config.imported_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    files = []
    for p in sorted(inbox.iterdir()):
        if p.name.startswith(".") or not p.is_file():
            continue
        try:
            res = import_file(p, conn=conn)
            target = dest / f"{stamp}_{p.name}"
            i = 1
            while target.exists():
                target = dest / f"{stamp}_{i}_{p.name}"
                i += 1
            shutil.move(str(p), str(target))
            res["ok"] = True
            res["moved_to"] = target.name
        except Exception as e:  # keep the file in the inbox, report the error
            res = {"file_name": p.name, "ok": False, "error": f"{type(e).__name__}: {e}"}
        files.append(res)
    return {
        "files": files,
        "rows": sum(f.get("rows", 0) for f in files),
        "new_rows": sum(f.get("new_rows", 0) for f in files),
        "dup_rows": sum(f.get("dup_rows", 0) for f in files),
    }
