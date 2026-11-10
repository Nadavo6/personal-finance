"""Visa Cal xlsx export. Rows come out as kind 'gift' (a prepaid gift card); ingest applies the
`cal_account_kind` setting ('gift' | 'card').

Header: תאריך עסקה | שם בית עסק | סכום עסקה | סכום חיוב | סוג עסקה | ענף | הערות (note: בית עסק, no ה).
Title rows above it carry the card's last 4 digits.
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
    map_columns,
    norm_text,
    parse_amount,
    parse_date,
    parse_installments,
    read_tables,
)

PROVIDER = "cal"
KEYWORDS = ["תאריך עסקה", "שם בית עסק", "סכום עסקה", "סכום חיוב", "סוג עסקה", "ענף", "הערות", 'סכום בש"ח',
            "פרטי חיוב", "תאריך חיוב"]
COLUMNS = {
    "date": ["תאריך עסקה", "תאריך"],
    "merchant": ["שם בית עסק", "שם בית העסק", "בית עסק"],
    "orig_amount": ["סכום עסקה"],
    "charged": ["סכום חיוב", 'סכום בש"ח'],
    "txn_type": ["סוג עסקה"],
    "category": ["ענף"],
    "notes": ["הערות", "פרטי חיוב"],
    "billing_date": ["תאריך חיוב", "מועד חיוב"],
    "orig_cur": ["מטבע עסקה", "מטבע"],
}
LAST4_PATTERNS = [
    re.compile(r"(?:מסתיים|המסתיים|מסתיימת|המסתיימת)\s*(?:ב-?|בספרות)?\s*(\d{4})"),
    re.compile(r"(?:כרטיס|לכרטיס)[^\d]{0,30}(\d{4})(?!\d)"),
    re.compile(r"\*{2,}\s*(\d{4})|(\d{4})\s*\*{2,}"),
]


def find_last4(rows: list) -> str | None:
    text = "\n".join(" ".join(norm_text(c) for c in r if c not in (None, "")) for r in rows)
    for rx in LAST4_PATTERNS:
        m = rx.search(text)
        if m:
            return next(g for g in m.groups() if g)
    return None


class CalImporter(Importer):
    provider = PROVIDER

    @classmethod
    def sniff(cls, path: Path, info: SniffInfo) -> bool:
        if info.kind not in ("xlsx", "xls", "html", "csv"):
            return False
        t = info.header_text
        return "שם בית עסק" in t and ("סכום חיוב" in t or "ענף" in t or 'סכום בש"ח' in t)

    def parse(self, path: Path) -> list[RawTxn]:
        out: list[RawTxn] = []
        for sheet, rows in read_tables(Path(path)):
            hi, _ = find_header_row(rows, KEYWORDS, min_score=3)
            if hi < 0:
                continue
            cmap = map_columns(rows[hi], COLUMNS)
            if "date" not in cmap or "merchant" not in cmap or not ({"charged", "orig_amount"} & cmap.keys()):
                continue
            last4 = find_last4(rows[:hi]) or "unknown"
            key = f"{PROVIDER}:{last4}"
            for r in rows[hi + 1 :]:
                first = norm_text(cell(r, 0))
                if first.startswith("סך הכל") or first.startswith('סה"כ'):
                    break
                d = parse_date(cell(r, cmap["date"]))
                charged = parse_amount(cell(r, cmap.get("charged")))
                orig_raw = cell(r, cmap.get("orig_amount"))
                orig = parse_amount(orig_raw)
                if charged is None:
                    charged = orig
                if d is None or charged is None:
                    continue
                notes = norm_text(cell(r, cmap.get("notes")))
                inst_no, inst_total = parse_installments(notes)
                orig_cur = currency_code(cell(r, cmap.get("orig_cur"))) or _currency_in(orig_raw)
                out.append(
                    RawTxn(
                        provider=PROVIDER,
                        account_key=key,
                        account_kind="gift",
                        date=d,
                        billing_date=parse_date(cell(r, cmap.get("billing_date"))),
                        amount=-float(charged),
                        description=norm_text(cell(r, cmap["merchant"])),
                        original_amount=orig,
                        original_currency=orig_cur,
                        installment_no=inst_no,
                        installment_total=inst_total,
                        provider_category=norm_text(cell(r, cmap.get("category"))) or None,
                        raw={"sheet": norm_text(sheet), "txn_type": norm_text(cell(r, cmap.get("txn_type"))),
                             "notes": notes},
                    )
                )
        return out


def _currency_in(v) -> str | None:
    if not isinstance(v, str):
        return None
    s = re.sub(r"[\d.,\s\-()]", "", norm_text(v))
    return currency_code(s) if s else None
