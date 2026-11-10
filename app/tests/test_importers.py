"""Importer tests: helpers + every importer on synthetic fixtures + shape-only checks on real files.

Fixtures are invented (tests/fixtures/make_fixtures.py). Real-file tests skip when the files are
absent and assert shapes/patterns only — never real values.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pytest

from finapp.importers import RawTxn, detect
from finapp.importers.base import (
    find_header_row,
    map_columns,
    parse_amount,
    parse_date,
    parse_installments,
    strip_bidi,
    visual_to_logical,
    currency_code,
)
from finapp.importers.cal import CalImporter
from finapp.importers.generic import GenericCsvImporter
from finapp.importers.leumi import LeumiImporter
from finapp.importers.max import MaxImporter
from finapp.importers.riseup import RiseUpImporter

FIX = Path(__file__).parent / "fixtures"
DATA = Path(__file__).resolve().parents[2] / "data"
LEUMI_PDFS = sorted((DATA / "inbox").glob("leumi_statement_*.pdf"))
MAX_REAL = DATA / "inbox" / "max_transactions_18m_2026-11-10.xlsx"
RISEUP_REAL = DATA / "riseup_transactions.csv"


@pytest.fixture(scope="session", autouse=True)
def _fixtures_exist():
    needed = ["leumi_synthetic.pdf", "max_synthetic.xlsx", "cal_synthetic.xlsx", "riseup_synthetic.csv"]
    if not all((FIX / n).exists() for n in needed):
        import importlib.util

        spec = importlib.util.spec_from_file_location("make_fixtures", FIX / "make_fixtures.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.main()


# ============================================================================ helpers


class TestHelpers:
    @pytest.mark.parametrize(
        "visual,logical",
        [
            ("הזיו ימואל", "לאומי ויזה"),
            ("י-נניפ טיא סקמ", "מקס איט פיננ-י"),
            ("14 שי ץורע.למע", "עמל.ערוץ יש 14"),
            ('ת"פומ', 'מופ"ת'),
            ("ןב YELLOW זפ", "פז YELLOW בן"),
            ("BLUE SKY הפק", "קפה BLUE SKY"),
            ("(ןמוזמ) הכישמ", "משיכה (מזומן)"),
            ("ABC 123", "ABC 123"),  # no Hebrew: unchanged
        ],
    )
    def test_visual_to_logical(self, visual, logical):
        assert visual_to_logical(visual) == logical

    def test_strip_bidi(self):
        assert strip_bidi("‏שלום‎ ‫עולם‬") == "שלום עולם"

    @pytest.mark.parametrize(
        "v,expected",
        [
            ("02.11.2026", date(2026, 11, 2)),
            ("02/11/2026", date(2026, 11, 2)),
            ("02-11-2026", date(2026, 11, 2)),
            ("2/1/26", date(2026, 1, 2)),
            ("2026-11-02", date(2026, 11, 2)),
            ("2026-11-02T00:00:00", date(2026, 11, 2)),
            (46328, date(2026, 11, 2)),  # Excel serial
            (datetime(2026, 11, 2, 13, 5), date(2026, 11, 2)),
            (date(2026, 11, 2), date(2026, 11, 2)),
            ("‏02/11/2026", date(2026, 11, 2)),
            ("31/02/2026", None),
            ("סך הכל", None),
            (None, None),
            ("", None),
        ],
    )
    def test_parse_date(self, v, expected):
        assert parse_date(v) == expected

    @pytest.mark.parametrize(
        "v,expected",
        [
            ("₪1,234.50", 1234.5),
            ("₪-1,234.50", -1234.5),
            ("-₪5", -5.0),
            ("$ 3.20", 3.2),
            ("(12.00)", -12.0),
            ("12.00-", -12.0),
            ("1,000", 1000.0),
            ('100 ש"ח', 100.0),
            ("€7", 7.0),
            (12, 12.0),
            (-3.5, -3.5),
            ("", None),
            (None, None),
            ("abc", None),
        ],
    )
    def test_parse_amount(self, v, expected):
        assert parse_amount(v) == expected

    def test_installments_and_currency(self):
        assert parse_installments("תשלום 2 מתוך 12") == (2, 12)
        assert parse_installments("הוראת קבע") == (None, None)
        assert currency_code("₪") == "ILS" and currency_code("$") == "USD" and currency_code("€") == "EUR"

    def test_header_detection(self):
        rows = [["כל המשתמשים"], ["כל הכרטיסים"], [None], ["תאריך עסקה", "שם בית העסק", "סכום חיוב", "הערות"], ["x"]]
        assert find_header_row(rows, ["תאריך", "בית העסק", "סכום חיוב", "הערות"]) == (3, 4)
        assert find_header_row(rows[:3], ["תאריך", "סכום"]) == (-1, 0)
        cmap = map_columns(rows[3], {"date": ["תאריך עסקה"], "amount": ["סכום חיוב"], "x": ["לא קיים"]})
        assert cmap == {"date": 0, "amount": 2}

    def test_map_columns_prefers_exact(self):
        cmap = map_columns(["תאריך ערך", "תאריך", "בחובה"], {"date": ["תאריך"], "value": ["תאריך ערך"]})
        assert cmap == {"date": 1, "value": 0}


# ============================================================================ detection


@pytest.mark.parametrize(
    "name,cls",
    [
        ("leumi_synthetic.pdf", LeumiImporter),
        ("leumi_export.xls", LeumiImporter),
        ("leumi_export.xlsx", LeumiImporter),
        ("max_synthetic.xlsx", MaxImporter),
        ("cal_synthetic.xlsx", CalImporter),
        ("riseup_synthetic.csv", RiseUpImporter),
        ("generic_en.csv", GenericCsvImporter),
        ("generic_he_debit_credit.csv", LeumiImporter),  # bank-shaped CSV (חובה/זכות/יתרה) -> Leumi table
    ],
)
def test_detect(name, cls):
    assert isinstance(detect(FIX / name), cls)


def test_detect_unknown(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x00\x01\x02 nothing")
    with pytest.raises(ValueError):
        detect(p)


def _valid(t: RawTxn) -> None:
    assert isinstance(t.date, date) and isinstance(t.amount, float)
    assert t.account_kind in ("bank", "card", "gift")
    assert "‏" not in t.description and "‎" not in t.description


# ============================================================================ Leumi


class TestLeumiPdf:
    def setup_method(self):
        self.imp = LeumiImporter()
        self.txns = self.imp.parse(FIX / "leumi_synthetic.pdf")

    def test_rows_and_signs(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("mf", FIX / "make_fixtures.py")
        mf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mf)
        expected = mf.leumi_rows_with_balance()
        assert len(self.txns) == len(expected)
        for t, (d, desc, amt, bal) in zip(self.txns, expected):
            _valid(t)
            assert t.date == parse_date(d)
            assert t.description == desc  # logical order
            assert t.amount == pytest.approx(amt)
            assert t.balance_after == pytest.approx(bal)
            assert t.provider == "leumi" and t.account_kind == "bank"

    def test_account_and_reconcile(self):
        assert {t.account_key for t in self.txns} == {"leumi:999-11112222"}
        r = self.imp.report
        assert r.ok and r.mismatches == []
        assert r.period == (date(2026, 9, 1), date(2026, 11, 10))
        assert r.checked == len(self.txns) - 1  # oldest row is seeded from x-position
        assert self.txns[-1].raw["sign_from"] == "seed"

    def test_out_of_order_same_day_rows_reconcile(self):
        # rows 2/3 are printed out of balance order; the per-day chain must still sign them
        assert [t.raw["sign_from"] for t in self.txns[:4]] == ["balance"] * 4

    def test_skips_headers_and_footers(self):
        assert not any("תאריך" in t.description or "/" in t.description for t in self.txns)


@pytest.mark.parametrize("name", ["leumi_export.xls", "leumi_export.xlsx"])
def test_leumi_table(name):
    imp = LeumiImporter()
    txns = imp.parse(FIX / name)
    assert [t.amount for t in txns] == [-2300.0, 13000.0, -12.5]
    assert [t.description for t in txns] == ["מקס איט פיננסים", "העברת משכורת", "עמלת ערוץ ישיר"]
    assert {t.account_key for t in txns} == {"leumi:999-11112222"}
    assert txns[2].billing_date == date(2026, 11, 2)  # value date
    assert txns[0].balance_after == 10700.0
    assert txns[0].external_id == "1001"
    for t in txns:
        _valid(t)


# ============================================================================ Max


class TestMax:
    def setup_method(self):
        self.imp = MaxImporter()
        self.txns = self.imp.parse(FIX / "max_synthetic.xlsx")
        self.by = {t.description: t for t in self.txns}

    def test_counts_and_accounts(self):
        assert len(self.txns) == 8  # 4 local + 3 abroad + 1 immediate; totals skipped
        assert {t.account_key for t in self.txns} == {"max:1111", "max:2222", "max:3333"}
        for t in self.txns:
            _valid(t)
            assert t.account_kind == "card" and t.provider == "max"

    def test_signs_and_fields(self):
        sup = self.by["סופר השכונה"]
        assert sup.amount == -123.45 and sup.billing_date == date(2026, 11, 10) and sup.date == date(2026, 10, 3)
        assert sup.provider_category == "מזון וצריכה"
        assert self.by["חנות בגדים"].amount == 80.0  # refund
        inst = self.by["חנות רהיטים"]
        assert (inst.installment_no, inst.installment_total, inst.amount, inst.original_amount) == (2, 3, -250.0, 750.0)

    def test_fx(self):
        hotel = self.by["HOTEL EXAMPLE PARIS FR"]  # charged in ILS for a EUR purchase
        assert hotel.amount == -380.0 and hotel.original_currency == "EUR" and hotel.original_amount == 100.0
        app = self.by["APP STORE"]  # charged in $ with a rate
        assert app.amount == pytest.approx(-37.0) and app.raw["fx_rate"] == 3.7 and "needs_fx" not in app.raw
        museum = self.by["MUSEUM SHOP FR"]  # charged in € without a rate
        assert museum.raw["needs_fx"] is True
        assert museum.raw["fx_rate_estimated"] == pytest.approx(3.8)  # implied by the hotel row
        assert museum.amount == pytest.approx(-76.0)
        assert museum.original_currency == "EUR" and museum.original_amount == 20.0

    def test_immediate_sheet_and_checksums(self):
        cafe = self.by["קפה פינתי"]
        assert cafe.raw["immediate"] is True and cafe.billing_date == cafe.date
        sheets = self.imp.report["sheets"]
        assert len(sheets) == 3
        assert all(s["checksum_ok"] for s in sheets.values())


# ============================================================================ Cal


def test_cal():
    txns = CalImporter().parse(FIX / "cal_synthetic.xlsx")
    assert len(txns) == 3
    assert {t.account_key for t in txns} == {"cal:4444"}
    assert all(t.account_kind == "gift" and t.provider == "cal" for t in txns)
    assert [t.amount for t in txns] == [-200.0, -30.0, 25.0]
    assert (txns[1].installment_no, txns[1].installment_total, txns[1].original_amount) == (1, 3, 90.0)
    assert txns[0].provider_category == "מזון"
    for t in txns:
        _valid(t)


# ============================================================================ RiseUp


def test_riseup():
    txns = RiseUpImporter().parse(FIX / "riseup_synthetic.csv")
    assert len(txns) == 4
    sal, sup, inst, excl = txns
    assert sal.account_key == "riseup:leumiBank:aaa111" and sal.account_kind == "bank" and sal.amount == 15000.0
    assert sup.account_key == "riseup:leumicard:bbb222" and sup.account_kind == "card" and sup.amount == -123.45
    assert sup.billing_date == date(2026, 11, 10) and sup.provider_category == "סופר"
    assert sup.external_id == "00000000-0000-4000-8000-000000000002"
    assert sup.raw["cashflow_month"] == "2026-11" and sup.raw["envelope_type"] == "trackingCategory"
    assert sup.raw["expense_type"] == "סופר"
    assert (inst.installment_no, inst.installment_total) == (2, 3)
    assert sup.installment_no is None
    assert excl.raw["is_excluded"] is True and sup.raw["is_excluded"] is False
    assert sup.original_currency is None and sup.original_amount is None
    for t in txns:
        _valid(t)


def test_riseup_rejects_other_csv():
    with pytest.raises(ValueError):
        RiseUpImporter().parse(FIX / "generic_en.csv")


# ============================================================================ generic


def test_generic_en():
    txns = GenericCsvImporter().parse(FIX / "generic_en.csv")
    assert [t.amount for t in txns] == [-12.5, 30.0, -1250.0]
    assert [t.date for t in txns] == [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)]
    assert txns[2].description == "Big, Purchase" and txns[0].provider_category == "Food"
    assert {t.account_key for t in txns} == {"generic:generic_en"}


def test_generic_he_debit_credit():
    txns = GenericCsvImporter().parse(FIX / "generic_he_debit_credit.csv")
    assert [t.amount for t in txns] == [-200.0, 1000.0]
    assert txns[0].description == "משיכת מזומן" and txns[1].balance_after == 1800.0


# ============================================================================ real files (shape only)


@pytest.mark.skipif(not LEUMI_PDFS, reason="real Leumi PDFs not present")
@pytest.mark.parametrize("pdf", LEUMI_PDFS, ids=lambda p: p.name)
def test_real_leumi_pdf(pdf):
    imp = LeumiImporter()
    txns = imp.parse(pdf)
    r = imp.report
    assert len(txns) > 0
    assert r.account and re.fullmatch(r"\d{3}-\d{8}", r.account)
    assert {t.account_key for t in txns} == {f"leumi:{r.account}"}
    assert r.period is not None
    assert all(r.period[0] <= t.date <= r.period[1] for t in txns)
    assert r.ok, f"{len(r.mismatches)} balance mismatches"
    assert all(t.balance_after is not None for t in txns)
    assert any("לאומי ויזה" in t.description for t in txns)  # logical-order Hebrew
    assert any(t.amount > 0 for t in txns) and any(t.amount < 0 for t in txns)
    # x-position column agrees with the balance-derived sign
    assert all((t.amount > 0) == (t.raw["column"] == "credit") for t in txns if t.raw.get("column"))
    assert isinstance(detect(pdf), LeumiImporter)


@pytest.mark.skipif(not MAX_REAL.exists(), reason="real Max export not present")
def test_real_max():
    imp = MaxImporter()
    txns = imp.parse(MAX_REAL)
    assert 770 <= len(txns) <= 810
    assert all(isinstance(t.amount, float) for t in txns)
    assert all(date(2025, 5, 1) <= t.date <= date(2026, 11, 30) for t in txns)
    cards = {t.account_key for t in txns}
    assert len(cards) == 3 and all(re.fullmatch(r"max:\d{4}", k) for k in cards)
    assert all(s["checksum_ok"] for s in imp.report["sheets"].values())
    # every non-ILS charge is converted (rate) or flagged
    for t in txns:
        if t.raw["charged_currency"] != "ILS":
            assert "fx_rate" in t.raw or t.raw.get("needs_fx")
    assert isinstance(detect(MAX_REAL), MaxImporter)


@pytest.mark.skipif(not RISEUP_REAL.exists(), reason="real RiseUp CSV not present")
def test_real_riseup():
    txns = RiseUpImporter().parse(RISEUP_REAL)
    assert len(txns) > 0
    assert {t.account_kind for t in txns} == {"bank", "card"}
    assert all(re.fullmatch(r"riseup:(leumiBank|leumicard):\w+", t.account_key) for t in txns)
    assert all(t.external_id for t in txns)
    assert isinstance(detect(RISEUP_REAL), RiseUpImporter)
