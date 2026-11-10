"""Net worth: assets (dated values), liabilities, property, planned items, equity comp."""

from __future__ import annotations

import calendar
import json
import re
import sqlite3
from collections import defaultdict
from datetime import date

from . import db
from .analytics import (
    _r,
    add_months,
    balance,
    current_month,
    expected_rent,
    month_range,
    rent_for_month,
    today,
)

STALE_DAYS = 45
HISTORY_MAX_MONTHS = 36
LIQUID_BY_DEFAULT = ("cash", "investment")


def default_liquid(kind: str | None) -> int:
    return 1 if kind in LIQUID_BY_DEFAULT else 0


def _month_end(m: str) -> str:
    y, mm = int(m[:4]), int(m[5:7])
    return f"{m}-{calendar.monthrange(y, mm)[1]:02d}"


def _days_since(as_of: str | None, on: date) -> int | None:
    if not as_of:
        return None
    try:
        return (on - date.fromisoformat(as_of[:10])).days
    except ValueError:
        return None


def _loads(s):
    if not s:
        return None
    try:
        return json.loads(s)
    except ValueError:
        return None


# ---------------------------------------------------------------- values

def latest_asset_value(conn: sqlite3.Connection, asset: dict, upto: str | None = None) -> dict | None:
    """{value, as_of, breakdown, source} — bank-tracked assets use the live bank balance."""
    if asset.get("track_from_bank"):
        b = balance(conn, upto)
        if b["amount"] is not None:
            return {"value": b["amount"], "as_of": b["as_of"], "breakdown": None, "source": "bank"}
    q = "SELECT * FROM asset_values WHERE asset_id=?" + (" AND as_of<=?" if upto else "")
    r = conn.execute(q + " ORDER BY as_of DESC LIMIT 1",
                     (asset["id"], upto) if upto else (asset["id"],)).fetchone()
    if r is None:
        return None
    return {"value": r["value"], "as_of": r["as_of"], "breakdown": _loads(r["breakdown_json"]),
            "source": r["source"]}


def latest_liability_balance(conn, liab: dict, upto: str | None = None) -> dict | None:
    q = "SELECT * FROM liability_values WHERE liability_id=?" + (" AND as_of<=?" if upto else "")
    r = conn.execute(q + " ORDER BY as_of DESC LIMIT 1",
                     (liab["id"], upto) if upto else (liab["id"],)).fetchone()
    return {"balance": r["balance"], "as_of": r["as_of"]} if r else None


def _is_liquid(a: dict) -> bool:
    return bool(a["liquid"] if a.get("liquid") is not None else default_liquid(a.get("kind")))


def asset_json(conn, a: dict, on: date) -> dict:
    v = latest_asset_value(conn, a) or {}
    return {
        "id": a["id"], "key": a["key"], "name": a["name"], "kind": a["kind"],
        "value": _r(v.get("value")), "as_of": v.get("as_of"), "breakdown": v.get("breakdown"),
        "value_source": v.get("source"), "stale_days": _days_since(v.get("as_of"), on),
        "stale": (_days_since(v.get("as_of"), on) or 0) > STALE_DAYS,
        "liquid": _is_liquid(a), "include_in_net_worth": bool(a.get("include_in_net_worth", 1)),
        "track_from_bank": bool(a.get("track_from_bank")), "notes": a.get("notes"),
        "source": a.get("source"),
    }


def liability_json(conn, liab: dict, on: date) -> dict:
    v = latest_liability_balance(conn, liab) or {}
    d = {k: liab.get(k) for k in ("id", "key", "name", "kind", "principal", "rate_pct",
                                  "start_month", "monthly_payment", "notes", "source")}
    d.update(balance=_r(v.get("balance")), as_of=v.get("as_of"),
             stale_days=_days_since(v.get("as_of"), on),
             include_in_net_worth=bool(liab.get("include_in_net_worth", 1)))
    return d


def assets_list(conn, on: date | None = None) -> list[dict]:
    on = on or today()
    return [asset_json(conn, dict(a), on) for a in
            conn.execute("SELECT * FROM assets ORDER BY include_in_net_worth DESC, kind, id")]


def liabilities_list(conn, on: date | None = None) -> list[dict]:
    on = on or today()
    return [liability_json(conn, dict(r), on) for r in
            conn.execute("SELECT * FROM liabilities ORDER BY id")]


def totals(assets: list[dict], liabilities: list[dict]) -> dict:
    inc = [a for a in assets if a["include_in_net_worth"] and a["value"] is not None]
    liab = sum(lb["balance"] or 0 for lb in liabilities if lb["include_in_net_worth"])
    by_kind: dict[str, float] = defaultdict(float)
    for a in inc:
        by_kind[a["kind"]] += a["value"]
    total = sum(a["value"] for a in inc) - liab
    liquid = sum(a["value"] for a in inc if a["liquid"])
    as_of = max((a["as_of"] for a in inc if a["as_of"]), default=None)
    kinds = [{"kind": k, "total": _r(v)} for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])]
    if liab:
        kinds.append({"kind": "liabilities", "total": _r(-liab)})
    return {"total": _r(total), "liquid": _r(liquid), "liabilities_total": _r(liab), "as_of": as_of,
            "by_kind": kinds}


def brief(conn, on: date | None = None) -> dict | None:
    """Overview tile: {total, liquid, as_of} or None when no assets exist."""
    if not conn.execute("SELECT 1 FROM assets LIMIT 1").fetchone():
        return None
    t = totals(assets_list(conn, on), liabilities_list(conn, on))
    return {"total": t["total"], "liquid": t["liquid"], "as_of": t["as_of"]}


def liquid_extra(conn, on: date | None = None) -> float:
    """Liquid assets on top of the bank balance (the forecast already starts from it): the
    'safety net' the affordability / forecast lines add to projected cash."""
    tot = 0.0
    for a in assets_list(conn, on):
        if a["include_in_net_worth"] and a["liquid"] and not a["track_from_bank"] and a["value"]:
            tot += a["value"]
    return tot


def history(conn, on: date | None = None) -> list[dict]:
    """Month-end net worth from the dated values (carry-forward). Starts at the first month
    that has a non-bank asset value (bank balances alone would understate earlier months)."""
    on = on or today()
    first = conn.execute(
        "SELECT MIN(v.as_of) FROM asset_values v JOIN assets a ON a.id=v.asset_id "
        "WHERE a.include_in_net_worth=1 AND COALESCE(a.track_from_bank,0)=0").fetchone()[0]
    if not first:
        return []
    cur = current_month(conn, on)
    start = max(first[:7], add_months(cur, -(HISTORY_MAX_MONTHS - 1)))
    assets = [dict(a) for a in conn.execute("SELECT * FROM assets WHERE include_in_net_worth=1")]
    liabs = [dict(r) for r in conn.execute("SELECT * FROM liabilities WHERE include_in_net_worth=1")]
    out = []
    for m in month_range(start, cur):
        end = min(_month_end(m), on.isoformat())
        tot = liq = 0.0
        for a in assets:
            v = latest_asset_value(conn, a, end)
            if v:
                tot += v["value"]
                liq += v["value"] if _is_liquid(a) else 0.0
        for lb in liabs:
            v = latest_liability_balance(conn, lb, end)
            tot -= v["balance"] if v else 0.0
        out.append({"month": m, "total": _r(tot), "liquid": _r(liq)})
    return out


# ---------------------------------------------------------------- planned items

def planned_json(p: dict, cur: str) -> dict:
    d = dict(p)
    d["done"] = bool(d.get("done"))
    d["signed_amount"] = _r(d["amount"] if d["kind"] == "income" else -d["amount"])
    d["overdue"] = bool(not d["done"] and d.get("when_month") and d["when_month"] < cur)
    return d


def planned_list(conn, on: date | None = None) -> list[dict]:
    cur = current_month(conn, on)
    return [planned_json(dict(p), cur) for p in conn.execute(
        "SELECT * FROM planned_items ORDER BY done, when_month IS NULL, when_month, id")]


def planned_by_month(conn, start: str) -> dict[str, float]:
    """Net planned amount (income +, expense −) per month ≥ start, open items only."""
    out: dict[str, float] = defaultdict(float)
    for p in conn.execute("SELECT kind, when_month, amount FROM planned_items "
                          "WHERE done=0 AND when_month IS NOT NULL AND when_month>=?", (start,)):
        out[p["when_month"][:7]] += p["amount"] if p["kind"] == "income" else -p["amount"]
    return dict(out)


# ---------------------------------------------------------------- property

def rent_received(conn) -> dict[str, float]:
    return {r[0]: r[1] for r in conn.execute(
        """SELECT t.cashflow_month, SUM(t.amount) FROM transactions t
           JOIN categories c ON c.id=t.category_id
           WHERE c.name='Rental income' AND t.amount>0 AND t.is_transfer=0
           GROUP BY 1 ORDER BY 1""")}


# flat/building insurance charges, when they show up as their own rows
PROPERTY_INSURANCE_PATTERNS = ("ביטוח מבנה", "ביטוח דירה", "ביטוח נכס", "ביטוח משכנתא")


def _cat_by_month(conn, name: str) -> dict[str, float]:
    """Net spend (positive) per cashflow month in category `name`."""
    return {r[0]: -r[1] for r in conn.execute(
        """SELECT t.cashflow_month, SUM(t.amount) FROM transactions t
           JOIN categories c ON c.id=t.category_id
           WHERE c.name=? AND t.is_transfer=0 AND t.is_excluded=0
           GROUP BY 1""", (name,))}


def _insurance_by_month(conn) -> dict[str, float]:
    where = " OR ".join("t.description LIKE ?" for _ in PROPERTY_INSURANCE_PATTERNS)
    return {r[0]: -r[1] for r in conn.execute(
        f"""SELECT t.cashflow_month, SUM(t.amount) FROM transactions t
            WHERE t.is_transfer=0 AND t.amount<0 AND ({where}) GROUP BY 1""",
        [f"%{p}%" for p in PROPERTY_INSURANCE_PATTERNS])}


def insurance_from_fixed(fixed_monthly: list[dict]) -> float | None:
    """The flat's insurance from the sheet's fixed-monthly lines: an explicit 'flat N' part of
    an insurance line ('Insurances (flat 59, life 41)'), else a whole line about flat/building
    insurance. None if nothing identifies it."""
    for it in fixed_monthly or []:
        name = str(it.get("name") or "")
        low = name.lower()
        if not ("insur" in low or "ביטוח" in name):
            continue
        m = re.search(r"(?:flat|apartment|building|דירה|מבנה)\s*[:=]?\s*₪?\s*(\d+(?:\.\d+)?)",
                      name, re.IGNORECASE)
        if m:
            return float(m.group(1))
        if any(w in low for w in ("flat", "apartment", "building")) or any(
                w in name for w in ("דירה", "מבנה")):
            amt = it.get("amount")
            return float(amt) if isinstance(amt, (int, float)) else None
    return None


def property_pnl(conn, d: dict, cur: str) -> dict:
    """Monthly P&L from the first expected-rent month to the current month, plus arrears.

    Arrears are cumulative: max(0, Σ expected rent for months ≤ current − Σ rent received to
    date). A lump payment carries forward to the following months (tenants catch up or prepay);
    upcoming months are not due. `balance` per month = Σ received − Σ expected so far (negative =
    owed); `rent_arrears_months` lists the months still unpaid once receipts cover the oldest
    months first; `rent_credit` = paid ahead of what is due."""
    plan = expected_rent(conn)
    first = plan[1]
    if not first:
        return {"pnl": [], "rent_arrears": 0.0, "rent_arrears_months": [], "rent_credit": 0.0,
                "insurance_monthly": None, "insurance_source": None, "tax_pct": None}
    pct = 10.0
    try:
        pct = float(db.get_setting(conn, "rental_tax_pct", "10") or 10)
    except ValueError:
        pass
    received = rent_received(conn)
    mortgage = _cat_by_month(conn, "Mortgage")
    ins_rows = _insurance_by_month(conn)
    ins_fixed = d.get("insurance_monthly")
    if ins_fixed is None:
        ins_fixed = insurance_from_fixed(d.get("fixed_monthly") or [])
    ins_source = "transactions" if ins_rows else ("sheet" if ins_fixed is not None else None)
    rows: list[dict] = []
    # receipts before the first rent month (e.g. a deposit) count toward it
    early = sum(v for m, v in received.items() if m < first)
    tot_exp = tot_got = 0.0
    for m in month_range(first, cur):
        exp = rent_for_month(plan, m)
        got = received.get(m, 0.0) + (early if m == first else 0.0)
        ins = ins_rows.get(m, 0.0) if ins_rows else float(ins_fixed or 0.0)
        tax = exp * pct / 100.0
        mort = mortgage.get(m, 0.0)
        tot_exp += exp
        tot_got += got
        rows.append({"month": m, "expected": _r(exp), "received": _r(got), "mortgage": _r(mort),
                     "insurance": _r(ins), "tax_reserve": _r(tax),
                     "net": _r(got - mort - ins - tax), "balance": _r(tot_got - tot_exp)})
    # which months are still open: cumulative receipts cover the oldest months first
    pay, open_months = tot_got, []
    for r in rows:
        take = min(pay, r["expected"] or 0.0)
        pay -= take
        left = (r["expected"] or 0.0) - take
        if left > 0.005:
            open_months.append({"month": r["month"], "due": _r(left)})
    return {"pnl": rows, "rent_arrears": _r(max(0.0, tot_exp - tot_got)),
            "rent_arrears_months": open_months, "rent_credit": _r(max(0.0, tot_got - tot_exp)),
            "insurance_monthly": _r(ins_fixed), "insurance_source": ins_source, "tax_pct": pct}


def property_view(conn, on: date | None = None) -> dict | None:
    on = on or today()
    r = conn.execute("SELECT * FROM properties ORDER BY id LIMIT 1").fetchone()
    if r is None:
        return None
    d = _loads(r["data_json"]) or {}
    cur = current_month(conn, on)
    price = d.get("price")
    mort = conn.execute("SELECT * FROM liabilities WHERE kind='mortgage' ORDER BY id LIMIT 1").fetchone()
    mortgage = None
    if mort:
        lj = liability_json(conn, dict(mort), on)
        mortgage = {k: lj[k] for k in ("id", "name", "principal", "balance", "as_of",
                                       "monthly_payment", "rate_pct", "notes")}
    bal = (mortgage or {}).get("balance")
    equity = price - bal if price is not None and bal is not None else None
    expected = {e["month"]: e for e in d.get("rental_income") or [] if e.get("month")}
    received = rent_received(conn)
    first_exp = min(expected) if expected else None
    months = sorted(set(expected) | {m for m in received if not first_exp or m >= add_months(first_exp, -3)})
    rental = []
    for m in months:
        e = expected.get(m) or {}
        rental.append({"month": m, "expected": _r(e.get("amount")), "received": _r(received.get(m)),
                       "note": e.get("note"), "future": m > cur})
    steady = None
    past_or_now = [x for x in rental if x["expected"] is not None]
    if past_or_now:
        steady = past_or_now[-1]["expected"]  # latest planned month = full-occupancy rent
    yield_pct = (steady * 12 / price * 100) if steady and price else None
    last12 = [m for m in received if m > add_months(cur, -12)]
    contrib = d.get("contributions") or {}
    return {
        "id": r["id"], "key": r["key"], "name": r["name"], "price": _r(price),
        "bank_financed_pct": d.get("bank_financed_pct"),
        "purchase_costs": d.get("purchase_costs") or [],
        "total_costs": _r(d.get("total_costs")),
        "contributions": contrib,
        "contributions_total": _r(sum(v for v in contrib.values() if isinstance(v, (int, float)))),
        "mortgage": mortgage, "equity_estimate": _r(equity),
        "rental_income": rental,
        "rent_expected_monthly": _r(steady),
        "rent_received_12m": _r(sum(received[m] for m in last12)),
        "rent_yield_pct": _r(yield_pct),
        "rental_tax_note": d.get("rental_tax_note"),
        "updated_at": r["updated_at"],
        **property_pnl(conn, d, cur),
    }


# ---------------------------------------------------------------- equity comp

def equity_comp_list(conn, on: date | None = None) -> list[dict]:
    on = on or today()
    cur = f"{on:%Y-%m}"
    out = []
    for r in conn.execute("SELECT * FROM equity_comp ORDER BY vests, id"):
        d = dict(r)
        v = (d.get("vests") or "")[:7]
        d["months_to_vest"] = ((int(v[:4]) * 12 + int(v[5:7])) - (on.year * 12 + on.month)
                               if len(v) == 7 and v[4] == "-" else None)
        d["vested"] = bool(v and v <= cur)
        out.append(d)
    return out


# ---------------------------------------------------------------- API payload

def summary(conn, on: date | None = None) -> dict:
    on = on or today()
    assets = assets_list(conn, on)
    liabs = liabilities_list(conn, on)
    t = totals(assets, liabs)
    imp = db.get_setting(conn, "last_sheet_import")
    return {
        **t,
        "assets": assets,
        "liabilities": liabs,
        "history": history(conn, on),
        "property": property_view(conn, on),
        "planned": planned_list(conn, on),
        "equity_comp": equity_comp_list(conn, on),
        "stale_after_days": STALE_DAYS,
        "last_sheet_import": _loads(imp) if imp else None,
    }


# ---------------------------------------------------------------- writes (validation)

def check_kind(kind, allowed: tuple[str, ...], what: str) -> str:
    if kind not in allowed:
        raise ValueError(f"{what} kind must be one of {', '.join(allowed)}")
    return kind


def check_month(m, name: str = "when_month", allow_none: bool = True) -> str | None:
    if m in (None, ""):
        if allow_none:
            return None
        raise ValueError(f"{name} is required")
    s = str(m)[:7]
    if len(s) != 7 or s[4] != "-" or not (s[:4].isdigit() and s[5:].isdigit()):
        raise ValueError(f"{name} must be YYYY-MM")
    return s


def check_date(d, name: str = "as_of") -> str:
    if not d:
        return today().isoformat()
    try:
        return date.fromisoformat(str(d)[:10]).isoformat()
    except ValueError:
        raise ValueError(f"{name} must be YYYY-MM-DD")


def set_asset_value(conn, asset_id: int, as_of, value, breakdown=None, source="manual") -> None:
    if value is None:
        raise ValueError("value is required")
    conn.execute(
        "INSERT INTO asset_values(asset_id,as_of,value,breakdown_json,source) VALUES (?,?,?,?,?) "
        "ON CONFLICT(asset_id,as_of) DO UPDATE SET value=excluded.value, "
        "breakdown_json=excluded.breakdown_json, source=excluded.source",
        (asset_id, check_date(as_of), float(value),
         json.dumps(breakdown, ensure_ascii=False) if breakdown else None, source))


def set_liability_balance(conn, liab_id: int, as_of, bal, source="manual") -> None:
    if bal is None:
        raise ValueError("balance is required")
    conn.execute(
        "INSERT INTO liability_values(liability_id,as_of,balance,source) VALUES (?,?,?,?) "
        "ON CONFLICT(liability_id,as_of) DO UPDATE SET balance=excluded.balance, "
        "source=excluded.source",
        (liab_id, check_date(as_of), float(bal), source))
