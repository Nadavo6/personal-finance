import pytest

from finapp import db, rules
from finapp.ingest import ingest
from tests.backend_util import BANK, CARD, RU_CARD, T, cat, conn, txns  # noqa: F401


def _one(conn, desc):
    return next(t for t in txns(conn) if t["description"] == desc)


def test_precedence_default_provider_riseup_rule_manual(conn):
    ingest([T(-50, "חנות אלפא", "2026-01-05", provider_category="מזון וצריכה"),
            T(-60, "חנות בטא", "2026-01-06"),
            T(-70, "חנות גמא", "2026-01-07", key=RU_CARD, provider_category="ספורט")],
           conn=conn)
    a, b, g = _one(conn, "חנות אלפא"), _one(conn, "חנות בטא"), _one(conn, "חנות גמא")
    assert (a["category_name"], a["category_source"]) == ("Groceries", "provider")
    assert (b["category_name"], b["category_source"]) == ("Uncategorized", "default")
    assert (g["category_name"], g["category_source"]) == ("Sports", "riseup")

    # rule beats riseup + provider
    rules.create_rule(conn, {"pattern": "חנות", "category_id": cat(conn, "Shopping")})
    rules.apply_rules(conn)
    for d in ("חנות אלפא", "חנות בטא", "חנות גמא"):
        t = _one(conn, d)
        assert (t["category_name"], t["category_source"]) == ("Shopping", "rule")

    # manual beats rule
    conn.execute("UPDATE transactions SET category_id=?, category_source='manual' WHERE id=?",
                 (cat(conn, "Gifts"), _one(conn, "חנות בטא")["id"]))
    rules.apply_rules(conn)
    assert _one(conn, "חנות בטא")["category_name"] == "Gifts"
    # ...unless explicitly overridden
    rules.apply_rules(conn, only_non_manual=False)
    assert _one(conn, "חנות בטא")["category_name"] == "Shopping"


def test_priority_then_longest_pattern(conn):
    ingest([T(-30, "קפה גרג רמת גן", "2026-02-01")], conn=conn)
    rules.create_rule(conn, {"pattern": "קפה", "category_id": cat(conn, "Groceries")})
    rules.create_rule(conn, {"pattern": "קפה גרג", "category_id": cat(conn, "Dining out")})
    rules.apply_rules(conn)
    assert txns(conn)[0]["category_name"] == "Dining out"  # longer pattern wins
    rules.create_rule(conn, {"pattern": "קפה", "category_id": cat(conn, "Health"),
                             "priority": 500})
    rules.apply_rules(conn)
    assert txns(conn)[0]["category_name"] == "Health"  # higher priority wins


def test_regex_equals_kind_and_amount_filters(conn):
    ingest([T(-20, "PAYPAL *SPOTIFY 12", "2026-02-01"),
            T(-500, "סופר יוחננוף", "2026-02-02"),
            T(-15, "סופר יוחננוף", "2026-02-03"),
            T(-99, "עירית חולון", "2026-02-04", key=BANK)], conn=conn)
    rules.create_rule(conn, {"pattern": r"spotify|netflix", "match_type": "regex",
                             "category_id": cat(conn, "Digital")})
    rules.create_rule(conn, {"pattern": "סופר יוחננוף", "match_type": "equals",
                             "amount_min": 100, "category_id": cat(conn, "Groceries")})
    rules.create_rule(conn, {"pattern": "עירית", "account_kind": "card",
                             "category_id": cat(conn, "Arnona")})
    rules.apply_rules(conn)
    by = {(t["description"], t["amount"]): t["category_name"] for t in txns(conn)}
    assert by[("PAYPAL *SPOTIFY 12", -20)] == "Digital"
    assert by[("סופר יוחננוף", -500)] == "Groceries"
    assert by[("סופר יוחננוף", -15)] == "Uncategorized"
    assert by[("עירית חולון", -99)] == "Uncategorized"  # account_kind filter


def test_rule_sets_flags(conn):
    ingest([T(-100, "העברה לחבר", "2026-02-01", key=BANK)], conn=conn)
    rules.create_rule(conn, {"pattern": "העברה לחבר", "set_excluded": 1})
    rules.apply_rules(conn)
    assert txns(conn)[0]["is_excluded"] == 1


def test_create_rule_from_txn_and_history(conn):
    ingest([T(-40, "מאפיית השכונה 1", "2026-03-01"),
            T(-45, "מאפיית השכונה 2", "2026-03-08"),
            T(-50, "מאפיית השכונה 3", "2026-03-15")], conn=conn)
    ts = txns(conn)
    conn.execute("UPDATE transactions SET category_id=?, category_source='manual' WHERE id=?",
                 (cat(conn, "Gifts"), ts[2]["id"]))
    rule, n = rules.create_rule_from_txn(conn, ts[0]["id"], cat(conn, "Groceries"),
                                         apply_to_history=True)
    assert rule["pattern"] == "מאפיית השכונה" and rule["match_type"] == "contains"
    assert n == 2  # the manual row is not touched
    names = [t["category_name"] for t in txns(conn)]
    assert names == ["Groceries", "Groceries", "Gifts"]
    # same merchant again -> updates the same rule, no duplicate
    rule2, _ = rules.create_rule_from_txn(conn, ts[1]["id"], cat(conn, "Dining out"))
    assert rule2["id"] == rule["id"]
    assert conn.execute("SELECT COUNT(*) FROM rules WHERE system=0").fetchone()[0] == 1


def test_preview_and_hits(conn):
    ingest([T(-10, "פיצה האט", "2026-03-01"), T(-12, "פיצה האט", "2026-03-02"),
            T(-9, "בורגר", "2026-03-03")], conn=conn)
    p = rules.preview(conn, {"pattern": "פיצה", "match_type": "contains",
                             "category_id": cat(conn, "Dining out")})
    assert p["count"] == 2 and p["would_change"] == 2 and len(p["sample"]) == 2
    assert p["sample"][0]["merchant"] == "פיצה האט"
    r = rules.create_rule(conn, {"pattern": "פיצה", "category_id": cat(conn, "Dining out")})
    rules.apply_rules(conn)
    assert conn.execute("SELECT hits FROM rules WHERE id=?", (r["id"],)).fetchone()[0] == 2
    rules.apply_rules(conn)  # full run recounts, doesn't double
    assert conn.execute("SELECT hits FROM rules WHERE id=?", (r["id"],)).fetchone()[0] == 2


def test_bad_regex_rejected(conn):
    import pytest

    with pytest.raises(ValueError):
        rules.create_rule(conn, {"pattern": "(", "match_type": "regex"})
    with pytest.raises(ValueError):
        rules.preview(conn, {"pattern": "(", "match_type": "regex"})


def test_card_payment_rule_is_bank_only(conn):
    ingest([T(-3000, "לאומי ויזה", "2026-03-02", key=BANK),
            T(-25, "לאומי ויזה חיוב שנתי", "2026-03-03", key=CARD)], conn=conn)
    b, c = txns(conn)
    assert b["is_transfer"] == 1 and b["category_name"] == "Card payment"
    assert c["is_transfer"] == 0
    assert db.get_txn(conn, b["id"])["is_transfer"] is True


def test_generic_riseup_loses_to_specific_provider(conn):
    ingest([T(-11, "חנות אחת", "2026-03-01", provider_category="אופנה"),
            T(-12, "חנות שתיים", "2026-03-02", provider_category="אופנה"),
            T(-13, "חנות שלוש", "2026-03-03", provider_category="שונות"),
            T(-14, "חנות ארבע", "2026-03-04", provider_category="אחר"),
            T(-15, "חנות חמש", "2026-03-05", provider_category="חשמל ומחשבים"),
            T(-16, "חנות שש", "2026-03-06", provider_category="עיצוב הבית")], conn=conn)
    ru = {"חנות אחת": "אחר", "חנות שתיים": "סופר", "חנות שלוש": "כללי", "חנות ארבע": None,
          "חנות חמש": "RiseUp", "חנות שש": " "}
    for d, c in ru.items():
        conn.execute("UPDATE transactions SET riseup_category=? WHERE description=?", (c, d))
    rules.apply_rules(conn)
    got = {d: (_one(conn, d)["category_name"], _one(conn, d)["category_source"]) for d in ru}
    assert got["חנות אחת"] == ("Clothing", "provider")      # generic RiseUp < provider
    assert got["חנות שתיים"] == ("Groceries", "riseup")     # specific RiseUp > provider
    assert got["חנות שלוש"] == ("General", "riseup")        # generic provider -> generic RiseUp
    assert got["חנות ארבע"] == ("Uncategorized", "default")
    assert got["חנות חמש"] == ("Electronics", "provider")    # RiseUp internal is generic
    assert got["חנות שש"] == ("Home", "provider")            # blank RiseUp is generic
    assert rules.is_generic_riseup(None) and not rules.is_generic_riseup("סופר")


def test_seeded_default_rules(conn):
    ingest([T(5000, 'מופ"ת מילואים', "2026-03-01", key=BANK),
            T(3000, "מענק מילואים", "2026-03-02", key=BANK),
            T(12000, "משכורת חברה", "2026-03-03", key=BANK),
            T(-4000, "הלוואת משכנתא", "2026-03-04", key=BANK),
            T(-6300, "שיק", "2026-03-06", key=BANK),
            T(-900, "מכללת דוגמה", "2026-03-05")], conn=conn)
    got = {t["description"]: t["category_name"] for t in txns(conn)}
    # generic defaults only: household rules (mortgage lender, rent check, tuition) come from
    # a personal setup file
    assert got == {'מופ"ת מילואים': "Reserve duty income", "מענק מילואים": "Reserve duty income",
                   "משכורת חברה": "Salary", "הלוואת משכנתא": "Uncategorized",
                   "שיק": "Uncategorized", "מכללת דוגמה": "Uncategorized"}
    assert conn.execute("SELECT kind FROM categories WHERE name='Education'").fetchone()[0] \
        == "fixed"


def test_migration_2_skips_existing_patterns(conn):
    conn.execute("DELETE FROM rules WHERE system=1 AND pattern='משכורת'")
    rules.create_rule(conn, {"pattern": "משכורת", "category_id": cat(conn, "Other income")})
    conn.execute("UPDATE settings SET value='1' WHERE key='schema_version'")
    conn.commit()
    db.migrate(conn)
    rs = conn.execute("SELECT system FROM rules WHERE pattern='משכורת'").fetchall()
    assert [r[0] for r in rs] == [0]
    n = conn.execute("SELECT COUNT(*) FROM categories WHERE name='Clothing'").fetchone()[0]
    assert n == 1


@pytest.mark.household
def test_direction_rules_digital_transfer(conn):
    ingest([T(-20000, "העברה דיגיטל", "2026-04-01", key=BANK),
            T(-500, "העברה דיגיטל", "2026-04-02", key=BANK),
            T(9000, "העברה דיגיטל", "2026-04-03", key=BANK),
            T(20000, "העברה דיגיטל", "2026-04-04", key=BANK),
            T(-3000, "ברוקר דוגמה", "2026-04-05", key=BANK)], conn=conn)
    got = {t["amount"]: t["category_name"] for t in txns(conn)}
    assert got[-20000] == "Savings & investments"
    assert got[-500] == "Uncategorized"          # small outgoing transfer: not savings
    assert got[9000] == "Rental income"
    assert got[20000] == "Uncategorized"         # above the rental range
    assert got[-3000] == "Savings & investments"
    with __import__("pytest").raises(ValueError):
        rules.create_rule(conn, {"pattern": "x", "direction": "sideways"})


@pytest.mark.household
def test_one_off_category_excludes_rows(conn):
    ingest([T(2500, "מתנה מחבר", "2026-09-12", key=BANK)], conn=conn)
    t = txns(conn)[0]
    assert t["is_excluded"] == 0
    conn.execute("UPDATE transactions SET category_id=?, category_source='manual' WHERE id=?",
                 (cat(conn, "Renovation"), t["id"]))
    rules.apply_rules(conn)
    t = txns(conn)[0]
    assert (t["category_name"], t["is_excluded"]) == ("Renovation", 1)
    # moving it out of the one-off category re-includes it
    conn.execute("UPDATE transactions SET category_id=? WHERE id=?",
                 (cat(conn, "Other income"), t["id"]))
    rules.apply_rules(conn)
    assert txns(conn)[0]["is_excluded"] == 0


@pytest.mark.household
def test_tag_event(conn):
    from finapp.__main__ import tag_event
    ingest([T(3000, "העברה מבנק אחר", "2026-09-15", key=BANK),
            T(1800, "הפקדת שיק", "2026-09-20", key=BANK),
            T(12000, "משכורת חברה", "2026-09-25", key=BANK),     # salary: kept
            T(9000, "העברה דיגיטל", "2026-09-24", key=BANK),     # rental income: kept
            T(-400, "חנות", "2026-09-16", key=BANK),             # outgoing: kept
            T(700, "זיכוי", "2026-09-17"),                       # card row: kept
            T(2000, "העברה מבנק אחר", "2026-10-02", key=BANK)],  # outside the range
           conn=conn)
    dry = tag_event(conn, "Renovation", "2026-09-10", "2026-09-30", dry_run=True)
    assert (dry["count"], dry["total"]) == (2, 4800)
    assert all(t["category_name"] != "Renovation" for t in txns(conn))
    res = tag_event(conn, "Renovation", "2026-09-10", "2026-09-30")
    assert (res["count"], res["total"]) == (2, 4800)
    tagged = [t for t in txns(conn) if t["category_name"] == "Renovation"]
    assert {t["amount"] for t in tagged} == {3000, 1800}
    assert all(t["is_excluded"] == 1 and t["category_source"] == "manual" for t in tagged)
    assert tag_event(conn, "Renovation", "2026-09-10", "2026-09-30")["count"] == 0  # idempotent
