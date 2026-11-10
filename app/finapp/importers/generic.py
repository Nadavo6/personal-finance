"""Any CSV with a date, a description and either one signed amount or debit/credit columns.

Hebrew or English headers. account_key = ``generic:<file stem>``. The amount column is taken
as signed (expense < 0); debit/credit columns give credit − debit.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import (
    Importer,
    RawTxn,
    SniffInfo,
    cell,
    currency_code,
    find_header_row,
    norm_text,
    parse_amount,
    parse_date,
    read_csv_rows,
)

PROVIDER = "generic"
COLUMNS: dict[str, list[str]] = {
    "date": ["transaction date", "date", "תאריך עסקה", "תאריך"],
    "description": ["description", "merchant", "payee", "details", "name", "memo", "narrative",
                    "שם בית העסק", "שם בית עסק", "תיאור", "פרטים", "בית עסק"],
    "amount": ["amount", "sum", "value", "סכום חיוב", "סכום"],
    "debit": ["debit", "withdrawal", "out", "בחובה", "חובה"],
    "credit": ["credit", "deposit", "in", "בזכות", "זכות"],
    "category": ["category", "קטגוריה", "ענף"],
    "currency": ["currency", "מטבע"],
    "balance": ["balance", "יתרה"],
    "billing_date": ["billing date", "value date", "posted", "תאריך חיוב", "תאריך ערך"],
}
KEYWORDS = sorted({c for cands in COLUMNS.values() for c in cands if len(c) > 2})


def _map(header: list) -> dict[str, int]:
    cells = [norm_text(c).lower() for c in header]
    out: dict[str, int] = {}
    used: set[int] = set()
    for exact in (True, False):
        for name, cands in COLUMNS.items():
            if name in out:
                continue
            for cand in cands:
                for i, c in enumerate(cells):
                    if i in used or not c:
                        continue
                    ok = c == cand if exact else (len(cand) > 2 and re.search(rf"(^|\W){re.escape(cand)}", c))
                    if ok:
                        out[name] = i
                        used.add(i)
                        break
                if name in out:
                    break
    return out


def _header(rows: list) -> tuple[int, dict[str, int]]:
    lowered = [[norm_text(c).lower() for c in r] for r in rows]
    hi, _ = find_header_row(lowered, KEYWORDS, min_score=2)
    if hi < 0:
        return -1, {}
    cmap = _map(rows[hi])
    if "date" in cmap and "description" in cmap and ("amount" in cmap or {"debit", "credit"} & cmap.keys()):
        return hi, cmap
    return -1, {}


class GenericCsvImporter(Importer):
    provider = PROVIDER

    @classmethod
    def sniff(cls, path: Path, info: SniffInfo) -> bool:
        if info.kind != "csv":
            return False
        try:
            return _header(read_csv_rows(path)[:25])[0] >= 0
        except Exception:
            return False

    def parse(self, path: Path) -> list[RawTxn]:
        path = Path(path)
        rows = read_csv_rows(path)
        hi, cmap = _header(rows)
        if hi < 0:
            raise ValueError("CSV needs date, description and amount (or debit/credit) columns")
        key = f"{PROVIDER}:{re.sub(r'[^A-Za-z0-9א-ת_-]+', '_', path.stem)}"
        out: list[RawTxn] = []
        for r in rows[hi + 1 :]:
            d = parse_date(cell(r, cmap["date"]))
            if d is None:
                continue
            if "amount" in cmap:
                amount = parse_amount(cell(r, cmap["amount"]))
            else:
                debit = parse_amount(cell(r, cmap.get("debit"))) or 0.0
                credit = parse_amount(cell(r, cmap.get("credit"))) or 0.0
                amount = abs(credit) - abs(debit) if (debit or credit) else None
            if amount is None:
                continue
            cur = currency_code(cell(r, cmap.get("currency")))
            out.append(
                RawTxn(
                    provider=PROVIDER,
                    account_key=key,
                    account_kind="bank",
                    date=d,
                    billing_date=parse_date(cell(r, cmap.get("billing_date"))),
                    amount=round(float(amount), 2),
                    description=norm_text(cell(r, cmap["description"])),
                    original_currency=cur if cur and cur != "ILS" else None,
                    original_amount=round(float(amount), 2) if cur and cur != "ILS" else None,
                    provider_category=norm_text(cell(r, cmap.get("category"))) or None,
                    balance_after=parse_amount(cell(r, cmap.get("balance"))),
                    raw={"file": path.name, **({"needs_fx": True} if cur and cur != "ILS" else {})},
                )
            )
        return out
