"""Max (max.co.il) "פירוט עסקאות" xlsx export.

Up to three sheets (`עסקאות במועד החיוב`, `עסקאות חו"ל ומט"ח`, `עסקאות בחיוב מיידי`), title rows,
then a 16-column header (found by keyword score). One export covers every card of the login;
each row carries the card's last 4 digits -> account_key ``max:<last4>``.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from .base import (
    Importer,
    RawTxn,
    SniffInfo,
    cell,
    currency_code,
    find_header_row,
    map_columns,
    norm_text,
    parse_amount,
    parse_date,
    parse_installments,
    read_tables,
)

log = logging.getLogger(__name__)

PROVIDER = "max"
KEYWORDS = ["תאריך עסקה", "שם בית העסק", "קטגוריה", "4 ספרות", "סוג עסקה", "סכום חיוב", "מטבע חיוב",
            "סכום עסקה מקורי", "תאריך חיוב", "הערות"]
COLUMNS = {
    "date": ["תאריך עסקה"],
    "merchant": ["שם בית העסק", "שם בית עסק"],
    "category": ["קטגוריה"],
    "card": ["4 ספרות אחרונות של כרטיס האשראי", "4 ספרות"],
    "txn_type": ["סוג עסקה"],
    "charged": ["סכום חיוב"],
    "charged_cur": ["מטבע חיוב"],
    "orig_amount": ["סכום עסקה מקורי", "סכום עסקה"],
    "orig_cur": ["מטבע עסקה מקורי", "מטבע עסקה"],
    "billing_date": ["תאריך חיוב"],
    "notes": ["הערות"],
    "tags": ["תיוגים"],
    "club": ["מועדון הנחות"],
    "discount_key": ["מפתח דיסקונט"],
    "method": ["אופן ביצוע ההעסקה", "אופן ביצוע"],
    "rate": ['שער המרה ממטבע מקור/התחשבנות לש"ח', "שער המרה"],
}
IMMEDIATE_SHEET = "חיוב מיידי"
RATE_RE = re.compile(r"(\d+(?:\.\d+)?)")
RATE_CUR = {"דולר": "USD", "אירו": "EUR", "יורו": "EUR", "ליש": "GBP", "פרנק": "CHF"}


def _rate(text) -> tuple[float | None, str | None]:
    """'שער המרה מדולר לש"ח 3.6512' -> (3.6512, 'USD'); a bare number -> (n, None)."""
    if text in (None, ""):
        return None, None
    if isinstance(text, (int, float)):
        return float(text), None
    s = norm_text(text)
    m = RATE_RE.findall(s)
    if not m:
        return None, None
    cur = next((code for word, code in RATE_CUR.items() if word in s), None)
    return float(m[-1]), cur


def _last4(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = re.sub(r"\D", "", norm_text(v))
    return s.zfill(4)[-4:] if s else "0000"


class MaxImporter(Importer):
    provider = PROVIDER

    def __init__(self) -> None:
        self.report: dict = {}

    @classmethod
    def sniff(cls, path: Path, info: SniffInfo) -> bool:
        if info.kind not in ("xlsx", "xls", "html", "csv"):
            return False
        t = info.header_text
        return "4 ספרות" in t or ("שם בית העסק" in t and "סכום חיוב" in t)

    def parse(self, path: Path) -> list[RawTxn]:
        out: list[RawTxn] = []
        self.report = {"sheets": {}}
        for sheet, rows in read_tables(Path(path)):
            hi, _ = find_header_row(rows, KEYWORDS, min_score=4)
            if hi < 0:
                continue
            cmap = map_columns(rows[hi], COLUMNS)
            if not {"date", "merchant", "charged"} <= cmap.keys():
                continue
            out.extend(self._parse_sheet(sheet, rows, hi, cmap))
        return out

    def _parse_sheet(self, sheet: str, rows: list, hi: int, cmap: dict[str, int]) -> list[RawTxn]:
        immediate = IMMEDIATE_SHEET in norm_text(sheet)
        parsed: list[tuple[dict, list]] = []
        total_line = None
        for i, r in enumerate(rows[hi + 1 :], start=hi + 1):
            first = norm_text(cell(r, 0))
            if first.startswith("סך הכל") or first.startswith('סה"כ'):
                nxt = rows[i + 1] if i + 1 < len(rows) else []
                total_line = norm_text(cell(nxt, 0)) or first
                break
            d = parse_date(cell(r, cmap["date"]))
            charged = parse_amount(cell(r, cmap["charged"]))
            if d is None or charged is None:
                continue
            parsed.append(({"date": d, "charged": charged}, r))

        # file-level effective FX rates per currency, implied by rows charged in ILS for a
        # foreign purchase (charged / original). Fallback for rows charged abroad w/o a rate.
        known_rates: dict[str, list[tuple[date, float]]] = {}
        for p, r in parsed:
            ccur = currency_code(cell(r, cmap.get("charged_cur"))) or "ILS"
            ocur = currency_code(cell(r, cmap.get("orig_cur")))
            oamt = parse_amount(cell(r, cmap.get("orig_amount")))
            if ccur == "ILS" and ocur and ocur != "ILS" and oamt and p["charged"]:
                implied = p["charged"] / oamt
                if implied > 0:
                    known_rates.setdefault(ocur, []).append((p["date"], round(implied, 4)))

        out: list[RawTxn] = []
        sums: dict[str, float] = {}
        with_rate = without_rate = 0
        for p, r in parsed:
            d, charged = p["date"], p["charged"]
            cur = currency_code(cell(r, cmap.get("charged_cur"))) or "ILS"
            sums[cur] = round(sums.get(cur, 0.0) + charged, 2)
            orig_amount = parse_amount(cell(r, cmap.get("orig_amount")))
            orig_cur = currency_code(cell(r, cmap.get("orig_cur")))
            rate, rate_cur = _rate(cell(r, cmap.get("rate")))
            notes = norm_text(cell(r, cmap.get("notes")))
            raw = {
                "sheet": norm_text(sheet),
                "txn_type": norm_text(cell(r, cmap.get("txn_type"))),
                "notes": notes,
                "tags": norm_text(cell(r, cmap.get("tags"))),
                "method": norm_text(cell(r, cmap.get("method"))),
                "charged_amount": charged,
                "charged_currency": cur,
            }
            if rate is not None:
                raw["fx_rate"] = rate
                if rate_cur:
                    raw["fx_rate_currency"] = rate_cur
            if immediate:
                raw["immediate"] = True
            if cur == "ILS":
                amount = -charged
            else:
                if orig_amount is None:
                    orig_amount, orig_cur = charged, cur
                if rate is not None and (rate_cur in (None, cur)):
                    amount = -round(charged * rate, 2)
                    with_rate += 1
                else:
                    without_rate += 1
                    raw["needs_fx"] = True
                    est = _nearest_rate(known_rates.get(cur, []), d)
                    if est is not None:
                        raw["fx_rate_estimated"] = est
                        amount = -round(charged * est, 2)
                    else:
                        amount = -charged  # unconverted; flagged
            inst_no, inst_total = parse_installments(notes)
            billing = parse_date(cell(r, cmap.get("billing_date")))
            out.append(
                RawTxn(
                    provider=PROVIDER,
                    account_key=f"{PROVIDER}:{_last4(cell(r, cmap.get('card')))}",
                    account_kind="card",
                    date=d,
                    billing_date=billing or (d if immediate else None),
                    amount=float(amount),
                    description=norm_text(cell(r, cmap["merchant"])),
                    original_amount=orig_amount,
                    original_currency=orig_cur,
                    installment_no=inst_no,
                    installment_total=inst_total,
                    provider_category=norm_text(cell(r, cmap.get("category"))) or None,
                    raw=raw,
                )
            )
        self.report["sheets"][norm_text(sheet)] = {
            "rows": len(out),
            "sums": sums,
            "total_line": total_line,
            "checksum_ok": _check_total(total_line, sums),
            "fx_with_rate": with_rate,
            "fx_without_rate": without_rate,
        }
        return out


def _nearest_rate(rates: list[tuple[date, float]], d: date) -> float | None:
    if not rates:
        return None
    return min(rates, key=lambda dr: abs((dr[0] - d).days))[1]


def _check_total(line: str | None, sums: dict[str, float]) -> bool | None:
    """Compare a 'סך הכל' line like '688.05₪ + 12.30€' with per-currency sums."""
    if not line:
        return None
    found: dict[str, float] = {}
    for part in line.split("+"):
        amt = parse_amount(part)
        if amt is None:
            continue
        found[currency_code(re.sub(r"[\d.,\s\-]", "", part)) or "ILS"] = amt
    if not found:
        return None
    return all(abs(found.get(c, 0.0) - sums.get(c, 0.0)) < 0.05 for c in set(found) | set(sums))
