"""Bank Leumi checking account: PDF statement ("דף חשבון") and Excel / HTML-saved-as-.xls exports.

PDF text comes out in *visual* order (reversed Hebrew). Each row has a date (rightmost), a
description, one amount in the credit (זכות) or debit (חובה) column and the running balance
(leftmost). Rows are newest first. The sign comes from the running-balance delta to the next
(older) row; the oldest row, and rows whose delta does not reconcile, fall back to the word
x-position (credit vs debit column).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .base import (
    Importer,
    RawTxn,
    SniffInfo,
    cell,
    find_header_row,
    map_columns,
    norm_text,
    parse_amount,
    parse_date,
    read_tables,
    visual_to_logical,
)

log = logging.getLogger(__name__)

PROVIDER = "leumi"
ACCOUNT_RE = re.compile(r"(?<![\d-])(\d{2,3}-\d{5,9}(?:[/-]\d{1,3})?)(?![\d])")
DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
PERIOD_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})")
AMOUNT_WORD_RE = re.compile(r"^₪?-?₪?[\d,]+\.\d{2}-?$")

# visual (reversed) header words of the PDF table
V_CREDIT, V_DEBIT, V_BALANCE, V_DATE = "תוכז", "הבוח", "הרתי", "ךיראת"
V_ACCOUNT_LABEL = "ןובשח רפסמ"  # "מספר חשבון" reversed
V_PERIOD_LABEL = "הפוקתל"  # "לתקופה" reversed

TABLE_KEYWORDS = ["תאריך", "תאריך ערך", "תיאור", "אסמכתא", "בחובה", "בזכות", "יתרה", "חובה", "זכות", "סוג תנועה"]
TABLE_COLUMNS = {
    "date": ["תאריך", "תאריך תנועה"],
    "value_date": ["תאריך ערך"],
    "description": ["תיאור", "סוג תנועה", "פרטים", "תאור"],
    "reference": ["אסמכתא"],
    "debit": ["בחובה", "חובה"],
    "credit": ["בזכות", "זכות"],
    "balance": ['היתרה בש"ח', "יתרה מצטברת", "יתרה", "היתרה"],
    "extra": ["תיאור מורחב", "הערה", "הערות"],
}


@dataclass
class _PdfRow:
    date: date
    description: str
    amount_abs: float | None
    column: str | None  # 'credit' | 'debit' | None
    balance: float | None
    page: int


@dataclass
class ReconcileReport:
    account: str | None = None
    period: tuple[date, date] | None = None
    rows: int = 0
    checked: int = 0
    mismatches: list[dict] = field(default_factory=list)
    sign_from_x: int = 0

    @property
    def ok(self) -> bool:
        return not self.mismatches


def account_key(number: str | None) -> str:
    return f"{PROVIDER}:{number}" if number else f"{PROVIDER}:unknown"


class LeumiImporter(Importer):
    provider = PROVIDER

    def __init__(self) -> None:
        self.report = ReconcileReport()

    @classmethod
    def sniff(cls, path: Path, info: SniffInfo) -> bool:
        if info.kind == "pdf":
            return True
        if info.kind not in ("xlsx", "xls", "html", "csv"):
            return False
        t = info.header_text
        return "תאריך" in t and ("בחובה" in t or "חובה" in t) and ("בזכות" in t or "זכות" in t) and "יתרה" in t

    def parse(self, path: Path) -> list[RawTxn]:
        path = Path(path)
        self.report = ReconcileReport()
        with open(path, "rb") as f:
            is_pdf = f.read(5).startswith(b"%PDF")
        txns = self._parse_pdf(path) if is_pdf else self._parse_table(path)
        if self.report.mismatches:
            log.warning("leumi %s: %d balance mismatches", path.name, len(self.report.mismatches))
        return txns

    # ------------------------------------------------------------------ PDF

    def _parse_pdf(self, path: Path) -> list[RawTxn]:
        import pdfplumber

        rows: list[_PdfRow] = []
        account = None
        period = None
        cols: dict[str, float] = {}
        with pdfplumber.open(path) as pdf:
            for pno, page in enumerate(pdf.pages, start=1):
                words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
                for line in _group_lines(words):
                    text = " ".join(w["text"] for w in line)
                    if account is None and V_ACCOUNT_LABEL in text:
                        m = ACCOUNT_RE.search(text)
                        account = m.group(1) if m else None
                    if period is None and V_PERIOD_LABEL in text:
                        m = PERIOD_RE.search(text)
                        if m:
                            d1, d2 = parse_date(m.group(1)), parse_date(m.group(2))
                            if d1 and d2:
                                period = (min(d1, d2), max(d1, d2))
                    texts = [w["text"] for w in line]
                    if V_CREDIT in texts and V_DEBIT in texts and V_DATE in texts:
                        cols = {
                            "credit": _center(line[texts.index(V_CREDIT)]),
                            "debit": _center(line[texts.index(V_DEBIT)]),
                        }
                        if V_BALANCE in texts:
                            cols["balance"] = _center(line[texts.index(V_BALANCE)])
                        continue
                    row = _parse_pdf_line(line, cols, pno)
                    if row:
                        rows.append(row)

        self.report.account = account
        self.report.period = period
        self.report.rows = len(rows)
        key = account_key(account)
        out: list[RawTxn] = []
        signs = self._assign_signs(rows)
        for i, r in enumerate(rows):
            amount, source = signs[i]
            if amount is None:
                continue
            out.append(
                RawTxn(
                    provider=PROVIDER,
                    account_key=key,
                    account_kind="bank",
                    date=r.date,
                    billing_date=None,
                    amount=amount,
                    description=r.description,
                    balance_after=r.balance,
                    raw={"source": "pdf", "page": r.page, "column": r.column, "sign_from": source},
                )
            )
        return out

    def _assign_signs(self, rows: list[_PdfRow]) -> list[tuple[float | None, str]]:
        """Sign each movement by chaining running balances day by day.

        Rows are newest first, but rows of the same day are not printed in the order the
        bank applied them, so adjacent deltas are unreliable. Per day (oldest day first),
        starting from the previous day's closing balance, repeatedly pick the row whose
        balance equals ``current ± amount``. Rows left over are reported as mismatches and
        signed from their x-position (credit / debit column). The oldest day has no known
        opening balance; it is seeded from a row's x-signed amount.
        """
        result: list[tuple[float | None, str]] = [(None, "none")] * len(rows)
        groups: list[list[int]] = []
        for i, r in enumerate(rows):
            if groups and rows[groups[-1][0]].date == r.date:
                groups[-1].append(i)
            else:
                groups.append([i])

        def x_signed(i: int) -> float | None:
            r = rows[i]
            if r.amount_abs is None:
                return None
            return r.amount_abs * {"credit": 1, "debit": -1}.get(r.column or "", -1)

        def chain(start: float, idxs: list[int]) -> tuple[dict[int, float], float]:
            cur, left, signed = start, list(idxs), {}
            while left:
                hit = None
                for i in reversed(left):  # prefer document order oldest-first
                    r = rows[i]
                    if r.balance is None or r.amount_abs is None:
                        continue
                    if abs(abs(r.balance - cur) - r.amount_abs) < 0.005:
                        hit = i
                        break
                if hit is None:
                    break
                r = rows[hit]
                signed[hit] = r.amount_abs if r.balance > cur else -r.amount_abs
                cur = r.balance
                left.remove(hit)
            return signed, cur

        def seed_chain(idxs: list[int]) -> tuple[dict[int, float], float]:
            best: tuple[dict[int, float], float] = ({}, 0.0)
            for i in reversed(idxs):
                xs = x_signed(i)
                if xs is None or rows[i].balance is None:
                    continue
                got = chain(round(rows[i].balance - xs, 2), idxs)
                if len(got[0]) > len(best[0]):
                    best = got
                if len(got[0]) == len(idxs):
                    break
            return best

        def unmatched(i: int) -> None:
            r = rows[i]
            self.report.mismatches.append(
                {"index": i, "date": r.date.isoformat(), "kind": "balance_chain",
                 "amount": r.amount_abs, "balance": r.balance}
            )
            self.report.sign_from_x += 1
            result[i] = (x_signed(i), "x")

        prev: float | None = None
        carry: list[int] = []  # previous day's unmatched rows (e.g. quarter-end fees booked late)
        for idxs in reversed(groups):
            pool = carry + idxs
            seeded = prev is None
            if not seeded:
                signed, closing = chain(prev, pool)
                if not signed:
                    if not carry:  # whole day applied after the next day's rows: retry tomorrow
                        carry = list(idxs)
                        continue
                    seeded = True  # gap in the statement: re-seed this day
            if seeded:
                signed, closing = seed_chain(pool)
            for i, amt in signed.items():
                r = rows[i]
                xs = x_signed(i)
                if not seeded:
                    self.report.checked += 1
                if r.column and xs is not None and (xs > 0) != (amt > 0):
                    self.report.mismatches.append(
                        {"index": i, "date": r.date.isoformat(), "kind": "sign_conflict"}
                    )
                result[i] = (amt, "seed" if seeded else "balance")
            left = [i for i in pool if i not in signed]
            for i in left:
                if i in carry:
                    unmatched(i)
            carry = [i for i in left if i not in carry]
            if signed:
                prev = closing
        for i in carry:
            unmatched(i)
        return result

    # ------------------------------------------------------------------ Excel / HTML

    def _parse_table(self, path: Path) -> list[RawTxn]:
        out: list[RawTxn] = []
        tables = read_tables(path)
        # title text (e.g. "תנועות בחשבון <no>") may sit in the header's sheet or, in HTML
        # exports, in a separate small table
        title_account = None
        for _sheet, rows in tables:
            hi, _ = find_header_row(rows, TABLE_KEYWORDS, min_score=4)
            for r in rows[: hi if hi >= 0 else 25]:
                m = ACCOUNT_RE.search(" ".join(norm_text(c) for c in r if c not in (None, "")))
                if m:
                    title_account = m.group(1)
                    break
            if title_account:
                break
        for _sheet, rows in tables:
            hi, _ = find_header_row(rows, TABLE_KEYWORDS, min_score=4)
            if hi < 0:
                continue
            cmap = map_columns(rows[hi], TABLE_COLUMNS)
            if "date" not in cmap or not ({"debit", "credit"} & cmap.keys()):
                continue
            account = title_account
            self.report.account = self.report.account or account
            key = account_key(account)
            for r in rows[hi + 1 :]:
                first = norm_text(cell(r, 0))
                if first.startswith("סך הכל") or first.startswith('סה"כ'):
                    break
                d = parse_date(cell(r, cmap["date"]))
                debit = parse_amount(cell(r, cmap.get("debit"))) or 0.0
                credit = parse_amount(cell(r, cmap.get("credit"))) or 0.0
                if d is None or (debit == 0 and credit == 0):
                    continue
                desc = norm_text(cell(r, cmap.get("description")))
                extra = norm_text(cell(r, cmap.get("extra")))
                ref = norm_text(cell(r, cmap.get("reference")))
                out.append(
                    RawTxn(
                        provider=PROVIDER,
                        account_key=key,
                        account_kind="bank",
                        date=d,
                        billing_date=parse_date(cell(r, cmap.get("value_date"))),
                        amount=round(abs(credit) - abs(debit), 2),
                        description=desc,
                        external_id=ref or None,
                        balance_after=parse_amount(cell(r, cmap.get("balance"))),
                        raw={"source": "table", "reference": ref, "extra": extra},
                    )
                )
        self.report.rows = len(out)
        return out


# ---------------------------------------------------------------------- PDF helpers


def _center(w: dict) -> float:
    return (w["x0"] + w["x1"]) / 2


def _group_lines(words: list[dict], tol: float = 2.5) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def _parse_pdf_line(line: list[dict], cols: dict[str, float], page: int) -> _PdfRow | None:
    if len(line) < 3 or not DATE_RE.match(line[-1]["text"]):
        return None
    d = parse_date(line[-1]["text"])
    if d is None:
        return None
    amounts = [(w, parse_amount(w["text"])) for w in line[:-1] if AMOUNT_WORD_RE.match(w["text"])]
    amounts = [(w, v) for w, v in amounts if v is not None]
    if not amounts:
        return None
    amount_words = {id(w) for w, _ in amounts}
    desc_words = [w for w in line[:-1] if id(w) not in amount_words]
    description = norm_text(visual_to_logical(" ".join(w["text"] for w in desc_words)))

    balance = None
    amount_abs = None
    column = None
    if cols:
        bal_x = cols.get("balance", min(_center(w) for w, _ in amounts))
        # nearest to the balance column = balance; the remaining one is the movement
        by_role = sorted(amounts, key=lambda wv: abs(_center(wv[0]) - bal_x))
        if len(amounts) >= 2 or abs(_center(by_role[0][0]) - bal_x) < abs(
            _center(by_role[0][0]) - min(cols["credit"], cols["debit"])
        ):
            balance = by_role[0][1]
            rest = by_role[1:]
        else:
            rest = by_role
        if rest:
            w, v = rest[0]
            amount_abs = abs(v)
            cx = _center(w)
            column = "credit" if abs(cx - cols["credit"]) < abs(cx - cols["debit"]) else "debit"
    else:  # no header seen: leftmost = balance, next = movement
        balance = amounts[0][1]
        if len(amounts) > 1:
            amount_abs = abs(amounts[1][1])
    return _PdfRow(d, description, amount_abs, column, balance, page)
