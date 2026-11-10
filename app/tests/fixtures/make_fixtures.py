"""Generate the synthetic test fixtures in this directory. All values are invented.

Run: cd app && uv run python tests/fixtures/make_fixtures.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
FAKE_ACCOUNT = "999-11112222"  # invented
FAKE_CARDS = ("1111", "2222", "3333")
FAKE_GIFT = "4444"


# --------------------------------------------------------------------------- Leumi PDF
# A hand-written PDF that mimics the real statement: words are laid out in *visual* order
# (Hebrew reversed), columns at fixed x-positions, rows newest-first, two pages.
# Hebrew letters are encoded through a custom /Differences encoding with Adobe glyph names
# (afii57664.. = U+05D0..), which pdfminer maps back to Unicode.

_HEB_BASE = 192  # byte code of alef
_SHEQEL = 230  # byte code of ₪ (afii57636)
_CHAR_W = 500  # glyph width in 1/1000 em
_SIZE = 9


def _enc(s: str) -> bytes:
    out = bytearray()
    for ch in s:
        o = ord(ch)
        if 0x5D0 <= o <= 0x5EA:
            out.append(_HEB_BASE + (o - 0x5D0))
        elif ch == "₪":
            out.append(_SHEQEL)
        elif o < 128:
            out.append(o)
        else:
            raise ValueError(f"unsupported char {ch!r}")
    esc = bytearray()
    for b in out:
        if b in (40, 41, 92):  # ( ) \
            esc += b"\\" + bytes([b])
        elif b >= 128:
            esc += f"\\{b:03o}".encode()
        else:
            esc.append(b)
    return bytes(esc)


def _visual(logical: str) -> str:
    """Logical Hebrew -> visual (what a PDF extract shows): reverse, then re-reverse digit runs."""
    import re

    rev = logical[::-1]
    return re.sub(r"[A-Za-z0-9][A-Za-z0-9.,\-]*[A-Za-z0-9]|[A-Za-z0-9]", lambda m: m.group(0)[::-1], rev)


def _text_width(s: str) -> float:
    return len(s) * _CHAR_W / 1000 * _SIZE


def _word(x: float, y: float, s: str) -> bytes:
    return b"BT /F1 %d Tf %.2f %.2f Td (" % (_SIZE, x, y) + _enc(s) + b") Tj ET\n"


def _right(x_right: float, y: float, s: str) -> bytes:
    return _word(x_right - _text_width(s), y, s)


def _money(v: float) -> str:
    sign = "-" if v < 0 else ""
    return f"₪{sign}{abs(v):,.2f}"


# columns (x of right edge), like the real file: balance | debit | credit | description | date
X_BAL, X_DEBIT, X_CREDIT, X_DESC, X_DATE = 125, 235, 348, 484, 544
LEUMI_ROWS_LOGICAL = [  # newest first: (date, description, signed amount); balances computed
    ("02.11.2026", "לאומי ויזה", -1200.50),
    ("02.11.2026", "מקס איט פיננ-י", -2300.00),
    ("01.11.2026", "העברת משכורת", 15000.00),
    ("01.11.2026", "עמל.ערוץ יש 14", -12.30),
    ("30.10.2026", "הוראת קבע", -450.00),
    ("15.10.2026", "משיכת מזומן", -500.00),
    ("01.10.2026", "העברת משכורת", 15000.00),
    ("30.09.2026", "עמלת קנייה", -9.90),
]
LEUMI_OPENING = 1000.00  # balance before the oldest row


def leumi_rows_with_balance() -> list[tuple[str, str, float, float]]:
    rows = []
    bal = LEUMI_OPENING
    for d, desc, amt in reversed(LEUMI_ROWS_LOGICAL):
        bal = round(bal + amt, 2)
        rows.append((d, desc, amt, bal))
    rows.reverse()
    # same-day rows printed out of balance order (like the real statement): swap the two
    # 01.11 rows so adjacent deltas do not reconcile but the per-day chain does
    rows[2], rows[3] = rows[3], rows[2]
    return rows


def make_leumi_pdf(path: Path) -> None:
    rows = leumi_rows_with_balance()
    pages_rows = [rows[:5], rows[5:]]
    streams = []
    for pno, prow in enumerate(pages_rows, start=1):
        s = bytearray()
        y = 800
        s += _right(X_DESC + 60, y, "10.11.2026 " + _visual("תאריך הפקה:"))
        y -= 30
        s += _right(X_DATE, y, _visual("דף חשבון שם חשבון: ישראל ישראלי"))
        y -= 25
        s += _right(X_DATE, y, "10.11.2026-01.09.2026 " + _visual("לתקופה:"))
        s += _right(240, y, FAKE_ACCOUNT + " " + _visual("מספר חשבון:"))
        y -= 30
        for x, word in ((X_DATE, "ךיראת"), (459, "גוס"), (440, "העונת"), (X_CREDIT - 14, "תוכז"),
                        (X_DEBIT - 12, "הבוח"), (135, "הרתי"), (106, "תרבטצמ")):
            s += _right(x, y, word)
        for d, desc, amt, bal in prow:
            y -= 23
            s += _right(X_DATE, y, d)
            s += _right(X_DESC, y, _visual(desc))
            s += _right(X_CREDIT if amt > 0 else X_DEBIT, y, _money(abs(amt)))
            s += _right(X_BAL, y, _money(bal))
        y -= 30
        s += _right(X_DATE, y, _visual("לא כולל תנועות שבוצעו היום ותנועות הממתינות לביצוע"))
        s += _word(290, 30, f"{pno}/{len(pages_rows)}")
        streams.append(bytes(s))

    widths = " ".join(str(_CHAR_W) for _ in range(32, 256))
    diffs = " ".join(f"/afii{57664 + i}" for i in range(27))
    objs: list[bytes] = []
    n_pages = len(streams)
    # 1 catalog, 2 pages, 3 font, then (page, content) pairs
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objs.append(
        (
            "<< /Type /Font /Subtype /Type1 /BaseFont /FinappSynthetic /FirstChar 32 /LastChar 255 "
            f"/Widths [{widths}] /Encoding << /Type /Encoding /BaseEncoding /WinAnsiEncoding "
            f"/Differences [{_HEB_BASE} {diffs} {_SHEQEL} /afii57636] >> "
            "/FontDescriptor << /Type /FontDescriptor /FontName /FinappSynthetic /Flags 32 "
            "/FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 "
            "/StemV 80 >> >>"
        ).encode()
    )
    for i, st in enumerate(streams):
        content_id = 5 + 2 * i
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.3 841.9] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(st) + st + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


# --------------------------------------------------------------------------- Leumi Excel/HTML

LEUMI_TABLE = [  # date, value date, description, reference, debit, credit, balance
    ("05/11/2026", "05/11/2026", "מקס איט פיננסים", "1001", "2,300.00", "", "10,700.00"),
    ("03/11/2026", "03/11/2026", "העברת משכורת", "1002", "", "13,000.00", "13,000.00"),
    ("01.11.2026", "02.11.2026", "עמלת ערוץ ישיר", "1003", "₪ 12.50", "", "0.00"),
]
LEUMI_HEADER = ["תאריך", "תאריך ערך", "תיאור", "אסמכתא", "בחובה", "בזכות", 'היתרה בש"ח']


def make_leumi_html(path: Path) -> None:
    rows = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in LEUMI_TABLE
    )
    html = (
        '<html dir="rtl"><head><meta charset="utf-8"></head><body>'
        f"<table><tr><td>תנועות בחשבון {FAKE_ACCOUNT}</td></tr></table>"
        "<table><tr>" + "".join(f"<th>{h}</th>" for h in LEUMI_HEADER) + "</tr>" + rows + "</table>"
        "</body></html>"
    )
    path.write_text(html, encoding="utf-8")


def make_leumi_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "תנועות"
    ws.append([f"תנועות בחשבון {FAKE_ACCOUNT}"])
    ws.append([])
    ws.append(LEUMI_HEADER)
    for r in LEUMI_TABLE:
        ws.append(list(r))
    wb.save(path)


# --------------------------------------------------------------------------- Max xlsx

MAX_HEADER = ["תאריך עסקה", "שם בית העסק", "קטגוריה", "4 ספרות אחרונות של כרטיס האשראי", "סוג עסקה",
              "סכום חיוב", "מטבע חיוב", "סכום עסקה מקורי", "מטבע עסקה מקורי", "תאריך חיוב", "הערות",
              "תיוגים", "מועדון הנחות", "מפתח דיסקונט", "אופן ביצוע ההעסקה",
              'שער המרה ממטבע מקור/התחשבנות לש"ח']


def _max_row(d, merchant, cat, card, typ, charged, ccur, orig, ocur, bill, notes=None, rate=None):
    return [d, merchant, cat, card, typ, charged, ccur, orig, ocur, bill, notes, None, None, None,
            "בנוכחות כרטיס", rate]


MAX_LOCAL = [
    _max_row("03-10-2026", "סופר השכונה", "מזון וצריכה", FAKE_CARDS[0], "רגילה", 123.45, "₪", 123.45, "₪", "10-11-2026"),
    _max_row("05-10-2026", "חנות רהיטים", "ריהוט ובית", FAKE_CARDS[1], "תשלומים", 250, "₪", 750, "₪", "10-11-2026",
             "תשלום 2 מתוך 3"),
    _max_row("07-10-2026", "חנות בגדים", "אופנה", FAKE_CARDS[1], "זיכוי", -80, "₪", -80, "₪", "10-11-2026"),
    _max_row("09-10-2026", "STREAMING CO", "פנאי, בידור וספורט", FAKE_CARDS[2], "רגילה", 39.9, "₪", 39.9, "₪",
             "10-11-2026", "הוראת קבע"),
]
MAX_ABROAD = [
    _max_row("01-10-2026", "HOTEL EXAMPLE PARIS FR", "טיסות ותיירות", FAKE_CARDS[0], "חיוב חודשי", 380.0, "₪", 100.0,
             "€", "10-11-2026", 'חיוב עסקת חו"ל בש"ח', " 3.8"),
    _max_row("02-10-2026", "APP STORE", "שירותי תקשורת", FAKE_CARDS[2], "רגילה", 10.0, "$", 10.0, "$", "10-11-2026",
             None, "3.7"),
    _max_row("04-10-2026", "MUSEUM SHOP FR", "פנאי, בידור וספורט", FAKE_CARDS[0], "רגילה", 20.0, "€", 20.0, "€",
             "10-11-2026"),
]
MAX_IMMEDIATE = [
    _max_row("06-10-2026", "קפה פינתי", "מסעדות, קפה וברים", FAKE_CARDS[2], "חיוב עסקות מיידי", 18.0, "₪", 18.0,
             "₪", None),
]


def _max_sheet(ws, rows, total_text):
    ws.append(["כל המשתמשים (1)"])
    ws.append(["כל הכרטיסים (3)"])
    ws.append(["2026-10-01-2026-10-31"])
    ws.append(MAX_HEADER)
    for r in rows:
        ws.append(r)
    ws.append([])
    ws.append(["סך הכל"])
    ws.append([total_text])


def make_max_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "עסקאות במועד החיוב"
    _max_sheet(ws, MAX_LOCAL, f"{sum(r[5] for r in MAX_LOCAL):.2f}₪")
    _max_sheet(wb.create_sheet('עסקאות חו"ל ומט"ח'), MAX_ABROAD, "380.00₪ + 20.00€ + 10.00$")
    _max_sheet(wb.create_sheet("עסקאות בחיוב מיידי"), MAX_IMMEDIATE, "18.00₪")
    wb.save(path)


# --------------------------------------------------------------------------- Cal xlsx

CAL_HEADER = ["תאריך עסקה", "שם בית עסק", "סכום עסקה", "סכום חיוב", "סוג עסקה", "ענף", "הערות"]
CAL_ROWS = [
    ["12/10/2026", "רשת מזון לדוגמה", "₪ 200.00", "₪ 200.00", "רגילה", "מזון", ""],
    ["15/10/2026", "חנות ספרים", "₪ 90.00", "₪ 30.00", "תשלומים", "ספרים", "תשלום 1 מתוך 3"],
    ["20/10/2026", "חנות צעצועים", "₪ -25.00", "₪ -25.00", "זיכוי", "פנאי", ""],
]


def make_cal_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "פירוט עסקאות"
    ws.append([f"פירוט עסקאות לכרטיס המסתיים ב-{FAKE_GIFT}"])
    ws.append(["לתקופה 10/2026"])
    ws.append([])
    ws.append(CAL_HEADER)
    for r in CAL_ROWS:
        ws.append(r)
    ws.append(['סה"כ', "", "", "₪ 205.00"])
    wb.save(path)


# --------------------------------------------------------------------------- RiseUp CSV

RISEUP_HEADER = ["id", "date", "billing_date", "cashflow_month", "amount_ils", "is_income", "original_amount",
                 "original_currency", "business_name", "riseup_category", "category_label", "expense_type",
                 "category_type", "envelope_type", "actual_type", "is_excluded", "is_postponed", "source",
                 "source_type", "account_hash", "account_nickname", "is_installment", "installment_number",
                 "installments_total", "sequence_id", "commitment_id", "payment_app_addressee", "customer_comment",
                 "origin", "raw_json"]


def _ru(i, d, bill, cfm, amt, income, name, cat, env, excl, source, stype, ahash, inst=None):
    is_inst = inst is not None
    return {
        "id": f"00000000-0000-4000-8000-{i:012d}", "date": d, "billing_date": bill, "cashflow_month": cfm,
        "amount_ils": amt, "is_income": "true" if income else "false", "original_amount": str(abs(float(amt))),
        "original_currency": "", "business_name": name, "riseup_category": cat, "category_label": cat,
        "expense_type": cat, "category_type": "other", "envelope_type": env, "actual_type": env,
        "is_excluded": "true" if excl else "false", "is_postponed": "false", "source": source,
        "source_type": stype, "account_hash": ahash, "account_nickname": "", "is_installment":
        "true" if is_inst else "false", "installment_number": str(inst[0]) if is_inst else "",
        "installments_total": str(inst[1]) if is_inst else "", "sequence_id": "", "commitment_id": "",
        "payment_app_addressee": "", "customer_comment": "", "origin": "both", "raw_json": "{}",
    }


RISEUP_ROWS = [
    _ru(1, "2026-10-01", "2026-10-01", "2026-10", "15000", True, "משכורת לדוגמה", "משכורת", "variableIncome",
        False, "leumiBank", "checkingAccount", "aaa111"),
    _ru(2, "2026-10-03", "2026-11-10", "2026-11", "-123.45", False, "סופר השכונה", "סופר", "trackingCategory",
        False, "leumicard", "creditCard", "bbb222"),
    _ru(3, "2026-10-05", "2026-11-10", "2026-11", "-250", False, "חנות רהיטים", "קניות", "variable", False,
        "leumicard", "creditCard", "bbb222", inst=(2, 3)),
    _ru(4, "2026-10-08", "2026-10-08", "2026-10", "-999", False, "החזר חד פעמי", "אחר", "excluded", True,
        "leumiBank", "checkingAccount", "aaa111"),
]


def make_riseup_csv(path: Path) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RISEUP_HEADER)
        w.writeheader()
        w.writerows(RISEUP_ROWS)


# --------------------------------------------------------------------------- generic CSVs


def make_generic(dir_: Path) -> None:
    (dir_ / "generic_en.csv").write_text(
        "Date,Description,Amount,Category\n"
        "2026-10-01,Coffee Shop,-12.50,Food\n"
        "02/10/2026,Refund Store,30.00,Shopping\n"
        "03/10/2026,\"Big, Purchase\",\"-1,250.00\",Home\n",
        encoding="utf-8",
    )
    (dir_ / "generic_he_debit_credit.csv").write_text(
        "תאריך,תיאור,חובה,זכות,יתרה\n"
        "01/10/2026,משיכת מזומן,200.00,,800.00\n"
        "02/10/2026,הפקדה,,1000.00,1800.00\n",
        encoding="utf-8-sig",
    )


# --------------------------------------------------------------------------- scraper JSON

SCRAPER_OUTPUT = [
    {
        "provider": "max", "ok": True, "error": None, "errorType": None,
        "accounts": [{
            "accountNumber": FAKE_CARDS[0], "balance": None,
            "txns": [
                {"type": "normal", "identifier": 5551, "date": "2026-10-02T21:00:00.000Z",
                 "processedDate": "2026-11-09T22:00:00.000Z", "originalAmount": -100.0, "originalCurrency": "ILS",
                 "chargedAmount": -100.0, "description": "סופר השכונה", "memo": "", "status": "completed"},
                {"type": "installments", "identifier": 5552, "date": "2026-09-05T21:00:00.000Z",
                 "processedDate": "2026-11-09T22:00:00.000Z", "originalAmount": -900.0, "originalCurrency": "ILS",
                 "chargedAmount": -300.0, "description": "חנות רהיטים", "installments": {"number": 2, "total": 3},
                 "status": "completed"},
                {"type": "normal", "date": "2026-10-04T21:00:00.000Z", "processedDate": "2026-11-09T22:00:00.000Z",
                 "originalAmount": -20.0, "originalCurrency": "USD", "chargedAmount": -74.5,
                 "description": "APP STORE", "status": "completed"},
                {"type": "normal", "date": "2026-11-08T22:00:00.000Z", "processedDate": "2026-12-09T22:00:00.000Z",
                 "originalAmount": -55.0, "originalCurrency": "ILS", "chargedAmount": 0,
                 "description": "קפה פינתי", "status": "pending"},
            ],
        }],
    },
    {
        "provider": "leumi", "ok": True, "error": None,
        "accounts": [{
            "accountNumber": FAKE_ACCOUNT, "balance": 1234.5,
            "txns": [
                {"type": "normal", "identifier": "777", "date": "2026-11-01T00:00:00.000+02:00",
                 "processedDate": "2026-11-01T00:00:00.000+02:00", "originalAmount": 15000,
                 "originalCurrency": "ILS", "chargedAmount": 15000, "description": "העברת משכורת",
                 "status": "completed"},
            ],
        }],
    },
    {"provider": "visaCal", "ok": False, "error": "INVALID_PASSWORD: bad login", "errorType": "INVALID_PASSWORD",
     "accounts": []},
]


def make_scraper_json(path: Path) -> None:
    path.write_text(json.dumps(SCRAPER_OUTPUT, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    make_leumi_pdf(HERE / "leumi_synthetic.pdf")
    make_leumi_html(HERE / "leumi_export.xls")
    make_leumi_xlsx(HERE / "leumi_export.xlsx")
    make_max_xlsx(HERE / "max_synthetic.xlsx")
    make_cal_xlsx(HERE / "cal_synthetic.xlsx")
    make_riseup_csv(HERE / "riseup_synthetic.csv")
    make_generic(HERE)
    make_scraper_json(HERE / "scraper_output.json")
    print("fixtures written to", HERE)


if __name__ == "__main__":
    main()
