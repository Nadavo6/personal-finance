"""Importer contract, file-type detection and shared parsing helpers.

Every importer turns one source file into ``list[RawTxn]`` (see docs/ARCHITECTURE.md).
Descriptions are returned in logical-order Hebrew with bidi marks stripped.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------- contract


@dataclass
class RawTxn:
    provider: str  # 'leumi' | 'max' | 'cal' | 'riseup' | 'generic'
    account_key: str  # 'leumi:<no>', 'max:1234', 'cal:5678', 'riseup:<source>:<hash>'
    account_kind: str  # 'bank' | 'card' | 'gift'
    date: date
    billing_date: date | None  # when charged (cards) / value date (bank)
    amount: float  # ILS, signed: expense < 0, income > 0
    description: str  # logical-order Hebrew, bidi marks stripped
    original_amount: float | None = None
    original_currency: str | None = None
    installment_no: int | None = None
    installment_total: int | None = None
    provider_category: str | None = None
    external_id: str | None = None
    balance_after: float | None = None
    raw: dict = field(default_factory=dict)


class Importer:
    provider: str = ""

    def parse(self, path: Path) -> list[RawTxn]:  # pragma: no cover - interface
        raise NotImplementedError

    @classmethod
    def sniff(cls, path: Path, info: "SniffInfo") -> bool:  # pragma: no cover - interface
        return False


# --------------------------------------------------------------------------- text helpers

BIDI_RE = re.compile("[‎‏‪-‮⁦-⁩؜﻿]")
HEBREW_RE = re.compile("[֐-׿]")
# A left-to-right run: latin letters/digits, possibly with inner spaces/punctuation, no Hebrew.
_LTR_RUN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9 .,:/%@&+_'*#\-]*[A-Za-z0-9])?")
_MIRROR = str.maketrans("()[]{}<>", ")(][}{><")


def strip_bidi(s: Any) -> str:
    if s is None:
        return ""
    return BIDI_RE.sub("", str(s))


def norm_text(s: Any) -> str:
    """Strip bidi marks, normalise Hebrew quote marks and whitespace."""
    s = strip_bidi(s).replace("״", '"').replace("׳", "'").replace("”", '"')
    s = s.replace("“", '"').replace("’", "'").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def has_hebrew(s: str) -> bool:
    return bool(HEBREW_RE.search(s or ""))


def visual_to_logical(s: str) -> str:
    """Convert a visually-ordered RTL line (as PDF text extraction yields it) to logical order.

    The whole string is reversed (which fixes Hebrew letters and word order), then each
    left-to-right run (digits / latin, incl. inner spaces and punctuation) is reversed back
    so numbers and English stay readable. Brackets are mirrored.
    """
    s = strip_bidi(s)
    if not has_hebrew(s):
        return s
    rev = s[::-1].translate(_MIRROR)
    return _LTR_RUN_RE.sub(lambda m: m.group(0)[::-1], rev)


# --------------------------------------------------------------------------- values

_EXCEL_EPOCH = datetime(1899, 12, 30)
_DATE_PATTERNS = [
    (re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$"), "dmy"),
    (re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2})$"), "dmy2"),
    (re.compile(r"^(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})$"), "ymd"),
]


def parse_date(v: Any) -> date | None:
    """dd/mm/yyyy, dd-mm-yyyy, dd.mm.yyyy (also 2-digit year), ISO, Excel serial, datetime."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 <= v <= 80000:  # Excel serial (1954..2119)
            return (_EXCEL_EPOCH + timedelta(days=float(v))).date()
        return None
    s = norm_text(v)
    s = s.split(" ")[0] if re.match(r"^\S+ \d{1,2}:\d{2}", s) else s  # drop a time part
    s = s.split("T")[0] if re.match(r"^\d{4}-\d{2}-\d{2}T", s) else s
    if re.fullmatch(r"\d{5}(\.\d+)?", s):
        return parse_date(float(s))
    for rx, kind in _DATE_PATTERNS:
        m = rx.match(s)
        if not m:
            continue
        a, b, c = (int(x) for x in m.groups())
        try:
            if kind == "dmy":
                return date(c, b, a)
            if kind == "dmy2":
                return date(2000 + c if c < 70 else 1900 + c, b, a)
            return date(a, b, c)
        except ValueError:
            return None
    return None


_CURRENCY_TOKENS = re.compile(r'(₪|\$|€|£|ש"ח|ש״ח|NIS|ILS|USD|EUR)', re.I)


def parse_amount(v: Any) -> float | None:
    """Parse '₪1,234.50', '(12.00)', '12.00-', '-₪5', '$ 3' ... Returns None if empty/unparseable."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = norm_text(v)
    if not s:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    s = _CURRENCY_TOKENS.sub("", s).replace(",", "").replace(" ", "")
    if s.endswith("-"):
        neg, s = True, s[:-1]
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.startswith("-"):
        neg, s = not neg, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    if not re.fullmatch(r"\d+(\.\d+)?|\.\d+", s):
        return None
    val = float(s)
    return -val if neg else val


def currency_code(v: Any) -> str | None:
    s = norm_text(v)
    if not s:
        return None
    table = {"₪": "ILS", 'ש"ח': "ILS", "NIS": "ILS", "ILS": "ILS", "$": "USD", "USD": "USD",
             "€": "EUR", "EUR": "EUR", "£": "GBP", "GBP": "GBP", "דולר": "USD", "אירו": "EUR",
             "יורו": "EUR", "ליש\"ט": "GBP", "שקל": "ILS"}
    if s in table:
        return table[s]
    for k, code in table.items():
        if k in s:
            return code
    return s.upper()[:3] if re.fullmatch(r"[A-Za-z]{3}", s) else s


_INSTALLMENT_RE = re.compile(r"תשלום\s*(\d+)\s*(?:מתוך|מ-|/)\s*(\d+)")


def parse_installments(text: Any) -> tuple[int | None, int | None]:
    m = _INSTALLMENT_RE.search(norm_text(text))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


# --------------------------------------------------------------------------- tables

Row = list[Any]


def find_header_row(
    rows: list[Row], keywords: Iterable[str], max_rows: int = 25, min_score: int = 3
) -> tuple[int, int]:
    """Return (row_index, score) of the row with most cells matching a keyword; (-1, 0) if none
    reaches ``min_score``. Each keyword counts once per row."""
    kws = list(keywords)
    best, best_score = -1, 0
    for i, row in enumerate(rows[:max_rows]):
        cells = [norm_text(c) for c in row if c not in (None, "")]
        score = sum(1 for kw in kws if any(kw in c for c in cells))
        if score > best_score:
            best, best_score = i, score
    return (best, best_score) if best_score >= min_score else (-1, 0)


def map_columns(header: Row, spec: dict[str, list[str]]) -> dict[str, int]:
    """Map logical names to column indexes. ``spec`` values are candidate header strings, tried
    first as exact matches, then as substrings. A column is used at most once."""
    cells = [norm_text(c) for c in header]
    used: set[int] = set()
    out: dict[str, int] = {}
    for exact in (True, False):
        for name, cands in spec.items():
            if name in out:
                continue
            for cand in cands:
                hit = next(
                    (
                        i
                        for i, c in enumerate(cells)
                        if i not in used and c and (c == cand if exact else cand in c)
                    ),
                    None,
                )
                if hit is not None:
                    out[name] = hit
                    used.add(hit)
                    break
    return out


def cell(row: Row, idx: int | None) -> Any:
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def read_head(path: Path, n: int = 4096) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def is_html_bytes(head: bytes) -> bool:
    h = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()[:2048]
    return h.startswith((b"<html", b"<!doctype", b"<table", b"<?xml", b"<meta")) or b"<table" in h


def decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1255", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_csv_rows(path: Path) -> list[Row]:
    text = decode_text(Path(path).read_bytes())
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [list(r) for r in csv.reader(io.StringIO(text), dialect)]


def _read_html_tables(path: Path) -> list[tuple[str, list[Row]]]:
    import lxml.html

    doc = lxml.html.fromstring(decode_text(Path(path).read_bytes()))
    out = []
    for ti, table in enumerate(doc.iter("table")):
        rows = []
        for tr in table.iter("tr"):
            # skip rows of nested tables (they are handled as their own table)
            if tr.getparent() is not None and _closest_table(tr) is not table:
                continue
            rows.append([norm_text(td.text_content()) for td in tr if td.tag in ("td", "th")])
        if rows:
            out.append((f"table{ti}", rows))
    return out


def _closest_table(el):
    p = el.getparent()
    while p is not None and p.tag != "table":
        p = p.getparent()
    return p


def read_tables(path: Path) -> list[tuple[str, list[Row]]]:
    """Read any tabular file into [(sheet_name, rows)]: xlsx, xls (binary), HTML-as-xls, CSV."""
    path = Path(path)
    head = read_head(path)
    if head.startswith(b"PK"):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            return [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
        finally:
            wb.close()
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        import xlrd

        book = xlrd.open_workbook(str(path))
        out = []
        for sh in book.sheets():
            rows = []
            for r in range(sh.nrows):
                vals = []
                for c in range(sh.ncols):
                    ce = sh.cell(r, c)
                    if ce.ctype == xlrd.XL_CELL_DATE:
                        vals.append(xlrd.xldate_as_datetime(ce.value, book.datemode))
                    else:
                        vals.append(ce.value)
                rows.append(vals)
            out.append((sh.name, rows))
        return out
    if is_html_bytes(head):
        return _read_html_tables(path)
    return [("csv", read_csv_rows(path))]


# --------------------------------------------------------------------------- detection


@dataclass
class SniffInfo:
    ext: str
    head: bytes
    kind: str  # 'pdf' | 'xlsx' | 'xls' | 'html' | 'csv' | 'unknown'
    header_text: str = ""  # normalised text of the first rows of every sheet / first CSV lines


def sniff_info(path: Path) -> SniffInfo:
    path = Path(path)
    head = read_head(path)
    ext = path.suffix.lower()
    if head.startswith(b"%PDF"):
        return SniffInfo(ext, head, "pdf")
    if ext == ".json" or head.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] in (b"{", b"["):
        return SniffInfo(ext, head, "json")  # sheet snapshots: `import-sheet`, not a statement
    if head.startswith(b"PK"):
        kind = "xlsx"
    elif head.startswith(b"\xd0\xcf\x11\xe0"):
        kind = "xls"
    elif is_html_bytes(head):
        kind = "html"
    elif ext in (".csv", ".txt", ".tsv") or b"," in head:
        kind = "csv"
    else:
        return SniffInfo(ext, head, "unknown")
    try:
        tables = read_tables(path)
    except Exception:  # unreadable -> let detection fail cleanly
        return SniffInfo(ext, head, kind)
    parts = []
    for _name, rows in tables:
        for row in rows[:25]:
            parts.append(" | ".join(norm_text(c) for c in row if c not in (None, "")))
    return SniffInfo(ext, head, kind, "\n".join(parts))


def all_importers() -> list[type[Importer]]:
    from .cal import CalImporter
    from .generic import GenericCsvImporter
    from .leumi import LeumiImporter
    from .max import MaxImporter
    from .riseup import RiseUpImporter

    # order matters: most specific first, generic last
    return [RiseUpImporter, LeumiImporter, MaxImporter, CalImporter, GenericCsvImporter]


def detect(path: Path) -> Importer:
    """Sniff the file type/headers and return an importer instance. Raises ValueError."""
    info = sniff_info(Path(path))
    if info.kind == "json":
        raise ValueError(f"{Path(path).name} is JSON, not a statement. A sheet snapshot is "
                         "imported with `./finance import-sheet` (or the Import & Sync button)")
    for cls in all_importers():
        if cls.sniff(Path(path), info):
            return cls()
    raise ValueError(f"unrecognised file format: {Path(path).name}")
