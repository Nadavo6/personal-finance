from datetime import date

from finapp import config, db, ingest as ing
from finapp.ingest import cashflow_month, ingest
from tests.backend_util import BANK, CARD, GIFT, RU_BANK, RU_CARD, T, cat, conn, txns  # noqa: F401


def test_occurrence_index_and_reimport(conn):
    batch = [T(-18, "קפה ג'ו", "2026-04-01"), T(-18, "קפה ג'ו", "2026-04-01"),
             T(-25, "פלאפל", "2026-04-01")]
    r1 = ingest(batch, {"file_name": "a.xlsx"}, conn=conn)
    assert (r1["rows"], r1["new_rows"], r1["dup_rows"]) == (3, 3, 0)
    ids1 = {t["id"] for t in txns(conn)}
    assert len(ids1) == 3  # two identical coffees both survive
    r2 = ingest(batch, {"file_name": "a.xlsx"}, conn=conn)
    assert (r2["new_rows"], r2["dup_rows"]) == (0, 3)
    # overlapping file with one extra row
    r3 = ingest(batch + [T(-18, "קפה ג'ו", "2026-04-01")], conn=conn)
    assert (r3["new_rows"], r3["dup_rows"]) == (1, 3)
    assert conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 3


def test_id_is_stable_hash():
    a = ing.txn_id("max:1", date(2026, 1, 1), -10.0, "x", 0)
    assert a == ing.txn_id("max:1", date(2026, 1, 1), -10.0, "x", 0)
    assert a != ing.txn_id("max:1", date(2026, 1, 1), -10.0, "x", 1)
    assert len(a) == 40


def test_accounts_upserted(conn):
    from finapp import db as _db
    _db.set_setting(conn, "cal_account_kind", "gift")  # GIFT = prepaid Cal gift card
    ingest([T(-1, "a", "2026-01-01"), T(-1, "b", "2026-01-01", key=BANK),
            T(-1, "c", "2026-01-01", key=GIFT)], conn=conn)
    acc = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM accounts")}
    assert acc[CARD]["kind"] == "card" and acc[BANK]["kind"] == "bank"
    assert acc[GIFT]["kind"] == "gift" and acc[GIFT]["provider"] == "cal"


def test_riseup_then_native_collapses_and_carries(conn):
    ingest([T(-120, "שופרסל דיל", "2026-05-03", key=RU_CARD, provider_category="סופר",
              raw={"is_excluded": "false", "cashflow_month": "2026-06"}),
            T(-80, "מסעדת הים", "2026-05-04", key=RU_CARD, provider_category="אוכל בחוץ")],
           conn=conn)
    ru = {t["description"]: t for t in txns(conn)}
    # user had manually categorised one RiseUp row
    conn.execute("UPDATE transactions SET category_id=?, category_source='manual' WHERE id=?",
                 (cat(conn, "Gifts"), ru["מסעדת הים"]["id"]))
    res = ingest([T(-120, "שופרסל דיל 077", "2026-05-04", billing="2026-06-02"),
                  T(-80, "מסעדת הים בעמ", "2026-05-05", billing="2026-06-02"),
                  T(-33, "חדש לגמרי", "2026-05-05", billing="2026-06-02")], conn=conn)
    assert res["merged_rows"] == 2 and res["new_rows"] == 3
    rows = txns(conn)
    assert len(rows) == 3
    assert all(r["account_provider"] == "max" for r in rows)
    by = {r["description"]: r for r in rows}
    assert by["שופרסל דיל 077"]["category_name"] == "Groceries"
    assert by["שופרסל דיל 077"]["category_source"] == "riseup"  # carried RiseUp category
    assert by["מסעדת הים בעמ"]["category_name"] == "Gifts"  # carried manual category
    assert by["מסעדת הים בעמ"]["category_source"] == "manual"
    # re-importing the RiseUp file must not bring the collapsed rows back
    r2 = ingest([T(-120, "שופרסל דיל", "2026-05-03", key=RU_CARD, provider_category="סופר",
                   raw={"cashflow_month": "2026-06"}),
                 T(-80, "מסעדת הים", "2026-05-04", key=RU_CARD, provider_category="אוכל בחוץ")],
                conn=conn)
    assert r2["new_rows"] == 0 and r2["dup_rows"] == 2
    assert len(txns(conn)) == 3


def test_native_then_riseup_is_dup(conn):
    ingest([T(-250, "לאומי ויזה", "2026-05-02", key=BANK, balance_after=1000)], conn=conn)
    r = ingest([T(-250, "לאומי ויזה", "2026-05-03", key=RU_BANK, provider_category="העברות",
                  raw={"is_excluded": "true"})], conn=conn)
    assert r["new_rows"] == 0 and r["dup_rows"] == 1
    rows = txns(conn)
    assert len(rows) == 1 and rows[0]["account_provider"] == "leumi"
    assert rows[0]["riseup_category"] == "העברות"
    assert rows[0]["is_excluded"] == 1  # RiseUp one-off exclusion carried over


def test_installments_dedupe_across_sources_despite_dates(conn):
    # RiseUp dates each installment in its billing month; Max dates all with the purchase date.
    ingest([T(-130, "ביטוח דוגמה חובה חדש", f"2026-0{n}-25", key=RU_CARD,
              installment_no=n, installment_total=12) for n in (2, 3, 4)], conn=conn)
    r = ingest([T(-130, "ביטוח דוגמה חובה", "2025-11-25", billing=f"2026-0{n}-02",
                  installment_no=n, installment_total=12) for n in (2, 3, 4)], conn=conn)
    rows = txns(conn)
    assert len(rows) == 3 and all(x["account_provider"] == "max" for x in rows)
    assert r["merged_rows"] == 3


def test_no_collapse_on_different_amount_kind_or_far_date(conn):
    ingest([T(-100, "דלק פז", "2026-05-01", key=RU_CARD),
            T(-100, "דלק פז", "2026-05-01", key=RU_BANK),
            T(-100, "קניון", "2026-06-01", key=RU_CARD)], conn=conn)
    r = ingest([T(-101, "דלק פז", "2026-05-01"),          # different amount
                T(-100, "דלק פז", "2026-05-10"),          # >2 days apart
                T(-100, "משהו אחר לגמרי", "2026-06-01"),  # different merchant ...
                T(-100, "משהו אחר", "2026-06-02")], conn=conn)
    # ... but exact date + unique candidate collapses one; the other stays
    assert r["merged_rows"] == 1
    assert len(txns(conn)) == 3 + 4 - 1


def test_transfer_and_savings_marking(conn):
    ingest([T(-4000, "מקס איט פיננ-י", "2026-06-02", key=BANK),
            T(-1500, "לאומי ויזה", "2026-06-10", key=BANK),
            T(-2000, "הפקדה לפיקדון", "2026-06-11", key=BANK),
            T(15000, "משכורת", "2026-06-01", key=BANK),
            T(-50, "מקס איט פיננ-י דמי כרטיס", "2026-06-05", key=CARD)], conn=conn)
    by = {r["description"]: r for r in txns(conn)}
    assert by["מקס איט פיננ-י"]["is_transfer"] == 1
    assert by["מקס איט פיננ-י"]["category_name"] == "Card payment"
    assert by["לאומי ויזה"]["is_transfer"] == 1
    sav = by["הפקדה לפיקדון"]
    assert sav["category_kind"] == "savings" and sav["is_transfer"] == 0
    assert by["משכורת"]["is_transfer"] == 0
    assert by["מקס איט פיננ-י דמי כרטיס"]["is_transfer"] == 0  # card line item, not a bill


def test_gift_account_excluded(conn):
    from finapp import db as _db
    _db.set_setting(conn, "cal_account_kind", "gift")  # GIFT = prepaid Cal gift card
    ingest([T(-200, "מתנה בחנות", "2026-06-03", key=GIFT)], conn=conn)
    t = txns(conn)[0]
    assert t["is_excluded"] == 1 and t["base_excluded"] == 1 and t["account_kind"] == "gift"


def test_cashflow_month_rules():
    assert cashflow_month(date(2026, 3, 31)) == "2026-03"
    assert cashflow_month(date(2026, 3, 9), 10) == "2026-02"
    assert cashflow_month(date(2026, 3, 10), 10) == "2026-03"
    assert cashflow_month(date(2026, 3, 27), 28) == "2026-03"  # period started Feb 28
    assert cashflow_month(date(2026, 3, 28), 28) == "2026-04"
    assert cashflow_month(date(2026, 12, 28), 28) == "2027-01"
    assert cashflow_month(date(2026, 1, 5), 10) == "2025-12"


def test_cashflow_month_card_by_billing_bank_by_date_riseup_kept(conn):
    ingest([T(-10, "a", "2026-01-25", billing="2026-02-02"),
            T(-10, "b", "2026-01-25", key=BANK),
            T(-11, "c", "2026-01-25", key=RU_CARD, raw={"cashflow_month": "2026-03"})],
           conn=conn)
    by = {t["description"]: t["cashflow_month"] for t in txns(conn)}
    assert by == {"a": "2026-02", "b": "2026-01", "c": "2026-03"}


def test_start_day_setting_used(conn):
    db.set_setting(conn, "cashflow_start_day", "10")
    ingest([T(-10, "x", "2026-01-05", key=BANK)], conn=conn)
    assert txns(conn)[0]["cashflow_month"] == "2025-12"


def test_max_provider_categories_map(conn):
    cases = {"דלק, חשמל וגז": "Car & fuel", "פנאי, בידור וספורט": "Entertainment",
             "טיסות ותיירות": "Travel", "מזון וצריכה": "Groceries",
             "מסעדות, קפה וברים": "Dining out", "ביטוח ופיננסים": "Insurance",
             "רפואה ובתי מרקחת": "Health", "אופנה": "Clothing",
             "חשמל ומחשבים": "Electronics", "עירייה וממשלה": "Government & municipal",
             "מוצרי m_a_x": "Fees", "שונות": "Uncategorized", "אחר": "Uncategorized"}
    ingest([T(-10 - i, f"merchant{i}", "2026-01-01", provider_category=k)
            for i, k in enumerate(cases)], conn=conn)
    got = {t["provider_category"]: t["category_name"] for t in txns(conn)}
    for k, v in cases.items():
        assert got[k] == v, k


def test_installments_kept(conn):
    ingest([T(-100, "ריהוט", "2026-01-01", billing="2026-02-02", installment_no=2,
              installment_total=6, original_amount=-600, original_currency="ILS")], conn=conn)
    t = txns(conn)[0]
    assert (t["installment_no"], t["installment_total"]) == (2, 6)


def test_import_inbox_moves_files(conn, tmp_path):
    inbox = config.inbox_dir()
    inbox.mkdir(parents=True)
    (inbox / "bank.csv").write_text(
        "date,description,amount\n2026-01-03,חנות דוגמה,-12.50\n2026-01-04,משכורת דוגמה,1000\n",
        encoding="utf-8")
    (inbox / "junk.bin").write_bytes(b"\x00\x01garbage")
    res = ing.import_inbox(conn=conn)
    ok = [f for f in res["files"] if f.get("ok")]
    bad = [f for f in res["files"] if not f.get("ok")]
    assert len(ok) == 1 and ok[0]["new_rows"] == 2
    moved = list(config.imported_dir().iterdir())
    assert len(moved) == 1 and moved[0].name.endswith("_bank.csv")
    assert moved[0].name[:8].isdigit()
    assert len(bad) == 1 and (inbox / "junk.bin").exists()  # failures stay in the inbox
    assert not (inbox / "bank.csv").exists()


def test_riseup_account_names_from_merged_cards(conn):
    from pathlib import Path
    from finapp.ingest import (backfill_alias_accounts, import_file,
                               refresh_riseup_account_names)
    fix = Path(__file__).parent / "fixtures" / "riseup_synthetic.csv"
    import_file(fix, conn=conn)
    # native Max rows for the RiseUp card's purchases -> merged into card 0001
    ingest([T(-123.45, "סופר השכונה", "2026-10-03", billing="2026-11-10"),
            T(-250, "חנות רהיטים", "2026-10-05", billing="2026-11-10",
              installment_no=2, installment_total=3)], conn=conn)
    names = {r["key"]: r["name"] for r in conn.execute("SELECT key, name FROM accounts")}
    assert names["riseup:leumicard:bbb222"] == "Max •0001 (RiseUp history)"
    assert names["riseup:leumiBank:aaa111"] == "Leumi checking (RiseUp history)"
    # aliases recorded before account_id existed are backfilled from the CSV
    conn.execute("UPDATE txn_aliases SET account_id=NULL")
    conn.execute("UPDATE accounts SET name='RiseUp leumicard bbb2' WHERE key LIKE '%bbb222'")
    assert backfill_alias_accounts(conn, fix) >= 2
    assert refresh_riseup_account_names(conn)["riseup:leumicard:bbb222"] == \
        "Max •0001 (RiseUp history)"
    # a user-chosen name is kept
    conn.execute("UPDATE accounts SET name='My old card' WHERE key LIKE '%bbb222'")
    assert "riseup:leumicard:bbb222" not in refresh_riseup_account_names(conn)


def test_cal_account_kind_setting(conn):
    from finapp import db
    conn.execute("DELETE FROM settings WHERE key='cal_account_kind'")  # back to the app default
    ingest([T(-40, "קפה", "2026-10-01", key="cal:0020")], conn=conn)
    assert conn.execute("SELECT kind FROM accounts WHERE key='cal:0020'").fetchone()[0] == "card"
    db.set_setting(conn, "cal_account_kind", "gift")
    ingest([T(-50, "קפה", "2026-10-01", key=GIFT)], conn=conn)
    assert next(t for t in txns(conn) if t["amount"] == -50)["is_excluded"] == 1
    db.set_setting(conn, "cal_account_kind", "card")
    ingest([T(-60, "מאפה", "2026-10-02", key="cal:0010")], conn=conn)
    acc = conn.execute("SELECT kind, name FROM accounts WHERE key='cal:0010'").fetchone()
    assert tuple(acc) == ("card", "Cal card •0010")
    assert next(t for t in txns(conn) if t["amount"] == -60)["is_excluded"] == 0
