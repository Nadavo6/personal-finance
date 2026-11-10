"""Data freshness: how far each real source (Leumi file, Max file, sheet snapshot) reaches.

The user uploads once a month, so "fresh" is judged on the date of the newest row, not on
when the app last ran an import. RiseUp history accounts and accounts without rows are left
out; a Max export covers every card, so a card with no rows while a sibling card is current
is "idle" (likely replaced or unused), not stale.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from . import db

OK_DAYS = 10  # newest row at most this old: up to date
STALE_DAYS = 35  # beyond this the source is missing a month
SHEET_OK_DAYS = 35
PROVIDER_LABELS = {"leumi": "Leumi checking", "max": "Max cards", "cal": "Visa Cal"}
MONTHLY = ("leumi", "max")  # sources on the monthly checklist; others are occasional


def _status(days: int | None, ok: int = OK_DAYS, stale: int = STALE_DAYS) -> str:
    if days is None:
        return "missing"
    return "ok" if days <= ok else "stale" if days <= stale else "missing"


def _months_after(last: str, today: date) -> list[str]:
    """Calendar months after the newest row's month up to the current month (rows missing)."""
    y, m = int(last[:4]), int(last[5:7])
    out = []
    while True:
        m += 1
        if m > 12:
            y, m = y + 1, 1
        if (y, m) > (today.year, today.month):
            return out
        out.append(f"{y:04d}-{m:02d}")


def _days(d: str | None, today: date) -> int | None:
    return (today - date.fromisoformat(d[:10])).days if d else None


def freshness(conn: sqlite3.Connection, on: date | None = None) -> dict:
    today = on or date.today()
    accts = [dict(r) for r in conn.execute(
        """SELECT a.id, a.key, a.provider, a.kind, a.name, COUNT(t.id) txn_count,
                  MIN(t.date) first_date, MAX(t.date) last_date
           FROM accounts a JOIN transactions t ON t.account_id=a.id
           WHERE a.provider != 'riseup'
           GROUP BY a.id ORDER BY a.provider, a.key""")]
    sources = []
    for prov in dict.fromkeys(a["provider"] for a in accts):
        group = [a for a in accts if a["provider"] == prov]
        last = max(a["last_date"] for a in group)
        days = _days(last, today)
        for a in group:
            a["days"] = _days(a["last_date"], today)
            st = _status(a["days"])
            a["status"] = "idle" if st != "ok" and a["last_date"] < last else st
        li = conn.execute("SELECT file_name, imported_at, rows, new_rows FROM imports "
                          "WHERE provider=? ORDER BY id DESC LIMIT 1", (prov,)).fetchone()
        occasional = prov not in MONTHLY or all(a["kind"] == "gift" for a in group)
        sources.append({
            "key": prov, "label": PROVIDER_LABELS.get(prov, prov.title()),
            "monthly": not occasional, "last_date": last, "days": days,
            "status": "optional" if occasional else _status(days),
            "missing_months": [] if occasional else _months_after(last, today),
            "last_import": dict(li) if li else None, "accounts": group,
        })
    for prov in MONTHLY:  # a monthly source that was never imported
        if not any(s["key"] == prov for s in sources):
            sources.append({"key": prov, "label": PROVIDER_LABELS[prov], "monthly": True,
                            "last_date": None, "days": None, "status": "missing",
                            "missing_months": [], "last_import": None, "accounts": []})
    sources.sort(key=lambda s: (not s["monthly"], MONTHLY.index(s["key"])
                                if s["key"] in MONTHLY else 9, s["key"]))

    raw = db.get_setting(conn, "last_sheet_import")
    try:
        imp = json.loads(raw) if raw else None
    except ValueError:
        imp = None
    sheet_date = (imp or {}).get("sheet_last_updated")
    sheet_days = _days(sheet_date, today) if sheet_date else None
    sheet = {"key": "sheet", "label": "Sheet snapshot", "monthly": True,
             "last_date": sheet_date, "days": sheet_days,
             "status": ("missing" if not imp else "ok" if sheet_days is None
                        else _status(sheet_days, SHEET_OK_DAYS, 10 ** 6)),
             "last_import": {"file_name": imp.get("file"), "imported_at": imp.get("at")}
             if imp else None}
    checklist = [s for s in sources if s["monthly"]] + [sheet]
    need = [s for s in checklist if s["status"] != "ok"]
    return {"today": today.isoformat(), "sources": sources, "sheet": sheet,
            "status": "ok" if not need else
            "missing" if any(s["status"] == "missing" for s in need) else "stale",
            "needs_update": [s["key"] for s in need]}
