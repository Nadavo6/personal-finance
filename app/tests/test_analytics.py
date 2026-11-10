from datetime import date

import pytest

from finapp import analytics as an
from finapp import rules
from finapp.ingest import ingest
from tests.backend_util import BANK, CARD, GIFT, T, cat, conn, txns  # noqa: F401

ON = date(2026, 11, 15)
HIST = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]


def _base(conn, *, dining=None, extra=()):
    """Six complete months: salary 10k, rent 3k (fixed), groceries 2k, dining 500."""
    rows = []
    for m in HIST:
        rows += [T(10000, "משכורת חברה", f"{m}-01", key=BANK),
                 T(-3000, "שכר דירה", f"{m}-02", key=BANK),
                 T(-2000, "סופר שכונתי", f"{m}-05", billing=f"{m}-10"),
                 T(-(dining or {}).get(m, 500), "מסעדה טובה", f"{m}-06", billing=f"{m}-10")]
    rows += list(extra)
    rules.create_rule(conn, {"pattern": "שכר דירה", "category_id": cat(conn, "Home rent")})
    rules.create_rule(conn, {"pattern": "סופר שכונתי", "category_id": cat(conn, "Groceries")})
    rules.create_rule(conn, {"pattern": "מסעדה טובה", "category_id": cat(conn, "Dining out")})
    ingest(rows, conn=conn)


def test_ledger_excludes_transfers_and_excluded(conn):
    from finapp import db as _db
    _db.set_setting(conn, "cal_account_kind", "gift")  # GIFT = prepaid Cal gift card
    _base(conn, extra=[T(-5000, "מקס איט פיננ-י", "2026-10-02", key=BANK),
                       T(-999, "מתנה", "2026-10-03", key=GIFT, billing="2026-10-10"),
                       T(-1000, "הפקדה לפיקדון", "2026-10-04", key=BANK),
                       T(50, "זיכוי סופר שכונתי", "2026-10-07", billing="2026-10-10")])
    led = an.Ledger(conn)
    oct_ = led.get("2026-10")
    assert oct_["income"] == 10000
    assert oct_["expense"] == 3000 + 2000 + 500 - 50  # card refund reduces spend
    assert oct_["savings"] == 1000
    assert oct_["fixed"] == 3000


def test_overspend_detection(conn):
    _base(conn, dining={"2026-10": 1500},
          extra=[T(-9000, "חנות רהיטים", "2026-09-07", billing="2026-09-10")])
    ov = an.overview(conn, ON)
    by = {m["month"]: m for m in ov["months"]}
    assert ov["current_month"] == "2026-11"
    assert by["2026-08"]["overspend"] is False
    assert by["2026-09"]["overspend"] is True
    assert any("more than income" in r for r in by["2026-09"]["reasons"])
    assert by["2026-10"]["overspend"] is True  # (b): 3,500 day-to-day vs 2,500 median
    assert by["2026-10"]["reasons"][0].startswith("Day-to-day spending")
    assert any(r.startswith("Dining out") and "300%" in r for r in by["2026-10"]["attention"])
    assert by["2026-08"]["attention"] == []
    assert by["2026-10"]["net"] == 10000 - 3000 - 2000 - 1500


def test_overspend_explicit_budget_and_pct_setting(conn):
    _base(conn, dining={"2026-08": 900, "2026-09": 1000})
    conn.execute("UPDATE categories SET monthly_budget=450 WHERE name='Dining out'")
    led = an.Ledger(conn)
    assert an.attention(led, "2026-07", 110) == []  # 500: over % but only ₪50 over
    assert an.attention(led, "2026-08", 120) == []  # ₪450 over < ₪500
    att = an.attention(led, "2026-09", 120)         # ₪550 over and > 540
    assert att[0].startswith("Dining out: ₪1,000 vs budget ₪450")
    # a single category never turns the month red on its own
    assert an.overspend(led, "2026-09", 120) == (False, [])
    # a higher overspend_pct silences it
    assert an.attention(led, "2026-09", 250) == []


def test_overspend_small_budgets_are_floored(conn):
    # tiny trailing medians (the "₪46 budget" case) are floored at ₪200
    small = {m: 40 for m in HIST}
    small["2026-10"] = 400  # 10x its median, but only ₪200 over the floor
    _base(conn, dining=small)
    led = an.Ledger(conn)
    did = cat(conn, "Dining out")
    assert led.spend_budget("2026-10", did) == 200
    assert an.overspend(led, "2026-10", 120)[0] is False  # total 2,400 < 120% of 2,040 too
    s = an.month_summary(conn, "2026-10", ON)
    d = next(c for c in s["categories"] if c["name"] == "Dining out")
    assert d["budget"] == 200 and d["over"] is False


def test_overspend_budget_uses_months_with_spend(conn):
    _base(conn, extra=[T(-800, "חנות", f"{m}-08", billing=f"{m}-10")
                       for m in ("2026-05", "2026-07")]
          + [T(-1500, "חנות", "2026-10-08", billing="2026-10-10")])
    rules.create_rule(conn, {"pattern": "חנות", "category_id": cat(conn, "Shopping")})
    rules.apply_rules(conn)
    led = an.Ledger(conn)
    # median over months that had spend (800, 800), not over all 6 months (0)
    assert led.spend_budget("2026-10", cat(conn, "Shopping")) == 800
    assert any(r.startswith("Shopping: ₪1,500 vs budget ₪800")
               for r in an.attention(led, "2026-10", 120))


def test_overspend_zero_budget_zero_spend_never_flags(conn):
    _base(conn)
    conn.execute("UPDATE categories SET monthly_budget=0 WHERE name='Donations'")
    ingest([T(-0.4, "תרומה קטנה", "2026-09-03", billing="2026-09-10"),
            T(0.4, "זיכוי תרומה קטנה", "2026-09-04", billing="2026-09-10")], conn=conn)
    rules.create_rule(conn, {"pattern": "תרומה קטנה", "category_id": cat(conn, "Donations")})
    rules.apply_rules(conn)
    led = an.Ledger(conn)
    for m in HIST:
        assert an.overspend(led, m, 120)[0] is False
        assert not any("Donations" in r for r in an.attention(led, m, 120)), m
    s = an.month_summary(conn, "2026-09", ON)
    don = next(c for c in s["categories"] if c["name"] == "Donations")
    assert don["over"] is False and don["pct"] is None
    assert an.category_over(0.0, 0.0, 120) is False


def test_overspend_total_variable_vs_median(conn):
    # no single category is ₪500 over, but day-to-day spend is > 120% of its median (2,500)
    _base(conn, dining={"2026-10": 900},
          extra=[T(-2400 + 2000, "סופר שכונתי", "2026-10-06", billing="2026-10-10"),
                 T(-400, "חנות", "2026-10-08", billing="2026-10-10")])
    led = an.Ledger(conn)
    assert led.get("2026-10")["variable"] == 3700
    over, reasons = an.overspend(led, "2026-10", 120)
    assert over and len(reasons) == 1 and reasons[0].startswith("Day-to-day spending ₪3,700")
    assert an.overspend(led, "2026-09", 120)[0] is False


def test_overspend_reasons_capped_and_sorted(conn):
    big = [T(-amt, name, "2026-10-08", billing="2026-10-10")
           for name, amt in (("חנות א", 1200), ("חנות ב", 3000), ("חנות ג", 2000),
                             ("חנות ד", 900))]
    _base(conn, extra=big)
    for name, c in (("חנות א", "Gifts"), ("חנות ב", "Travel"), ("חנות ג", "Home"),
                    ("חנות ד", "Pets")):
        rules.create_rule(conn, {"pattern": name, "category_id": cat(conn, c)})
    rules.apply_rules(conn)
    led = an.Ledger(conn)
    over, reasons = an.overspend(led, "2026-10", 120)
    # total-spend overage (₪7,100) outranks "more than income" (₪2,600)
    assert over and len(reasons) == 2
    assert reasons[0].startswith("Day-to-day spending")
    assert reasons[1].startswith("Spent ₪2,600")
    # four categories over budget: only the 3 biggest, biggest first
    att = an.attention(led, "2026-10", 120)
    assert [r.split(":")[0] for r in att] == ["Travel", "Home", "Gifts"]
    s = an.month_summary(conn, "2026-10", ON)
    assert s["attention"] == att and s["reasons"] == reasons


def test_unusual_inflow(conn):
    extra = [T(8000, "מכירת רכב", "2026-10-12", key=BANK),
             T(6000, "מענק מילואים", "2026-10-13", key=BANK),  # reserve duty: never flagged
             T(4000, "החזר קטן", "2026-10-14", key=BANK)]       # under ₪5,000
    extra += [T(6000, "שכירות דירה מושכרת", f"{m}-15", key=BANK)  # recurring inflow
              for m in ("2026-08", "2026-09", "2026-10")]
    _base(conn, extra=extra)
    a = [x for x in an.anomalies(conn, "2026-10") if x["type"] == "unusual_inflow"]
    assert len(a) == 1 and "one-off" in a[0]["text"] and len(a[0]["txn_ids"]) == 1
    from finapp import insights as ins
    items = [i for i in ins.list_insights(conn, ON)["items"] if i["type"] == "unusual_inflow"]
    assert [i["txn_ids"] for i in items] == [a[0]["txn_ids"]]
    assert items[0]["key"] == f"unusual_inflow:{a[0]['txn_ids'][0]}"
    # not auto-excluded
    assert conn.execute("SELECT is_excluded FROM transactions WHERE id=?",
                        (a[0]["txn_ids"][0],)).fetchone()[0] == 0
    # marking it excluded removes the finding
    conn.execute("UPDATE transactions SET is_excluded=1 WHERE id=?", (a[0]["txn_ids"][0],))
    assert not [x for x in an.anomalies(conn, "2026-10") if x["type"] == "unusual_inflow"]


def test_month_summary(conn):
    _base(conn, dining={"2026-10": 1500})
    s = an.month_summary(conn, "2026-10", ON)
    cats = {c["name"]: c for c in s["categories"]}
    assert cats["Dining out"]["actual"] == 1500
    assert cats["Dining out"]["median3"] == 500 and cats["Dining out"]["budget"] == 500
    assert cats["Dining out"]["over"] is True and cats["Dining out"]["pct"] == 300
    assert cats["Groceries"]["over"] is False
    assert s["top_merchants"][0]["merchant"] == "שכר דירה"
    assert {f["name"] for f in s["fixed"]} == {"Home rent"}
    assert s["income"] == 10000 and s["income"] - s["expense"] == 3500
    assert any(a["type"] == "large_txn" for a in s["anomalies"]) is False  # <5 samples/6mo? no
    assert "Card payment" not in cats


def test_month_summary_current_left_to_spend(conn):
    _base(conn, extra=[T(-3000, "שכר דירה", "2026-11-02", key=BANK),
                       T(-500, "סופר שכונתי", "2026-11-05", billing="2026-11-10")])
    conn.execute("INSERT INTO goals(name,target_amount,target_date,saved_amount,status,"
                 "created_at) VALUES ('trip',4000,'2027-07-01',0,'active','2026-11-01')")
    s = an.month_summary(conn, "2026-11", ON)
    assert s["is_current"] and s["expected_income"] == 10000  # salary not in yet -> median
    assert s["goals_reserved"] == 500
    # 10000 - 3500 spent - 500 goals - 0 remaining fixed
    assert s["left_to_spend"] == 6000
    assert s["days_left"] == 16 and s["daily_pace"] == 375


def test_anomalies_large_and_new_merchant(conn):
    extra = [T(-100, f"קפה {i}", f"2026-{5 + i // 2:02d}-1{i % 2}", billing=None)
             for i in range(10)]
    for e in extra:
        e.description = "בית קפה"
    extra += [T(-900, "בית קפה", "2026-10-20"), T(-800, "חנות חדשה", "2026-10-21")]
    _base(conn, extra=extra)
    rules.create_rule(conn, {"pattern": "בית קפה", "category_id": cat(conn, "Dining out")})
    rules.apply_rules(conn)
    an_ = an.anomalies(conn, "2026-10")
    types = {a["type"] for a in an_}
    assert "large_txn" in types and "new_merchant" in types
    big = next(a for a in an_ if a["type"] == "large_txn")
    assert len(big["txn_ids"]) == 1


def test_trends(conn):
    _base(conn)
    tr = an.trends(conn, 12, ON)
    assert len(tr["months"]) == 12 and tr["months"][-1] == "2026-11"
    groc = next(c for c in tr["categories"] if c["name"] == "Groceries")
    assert groc["series"][tr["months"].index("2026-07")] == 2000
    assert all(c["name"] != "Card payment" for c in tr["categories"])


def test_balance_picks_final_row_of_day(conn):
    ingest([T(-100, "a", "2026-11-02", key=BANK, balance_after=900),
            T(-50, "b", "2026-11-02", key=BANK, balance_after=850),
            T(1000, "c", "2026-11-01", key=BANK, balance_after=1000)], conn=conn)
    assert an.balance(conn) == {"amount": 850.0, "as_of": "2026-11-02"}


def _afford_setup(conn, goal_contrib=None):
    _base(conn, extra=[T(10000, "משכורת חברה", "2026-11-01", key=BANK),
                       T(-3000, "שכר דירה", "2026-11-02", key=BANK, balance_after=5000),
                       T(-500, "סופר שכונתי", "2026-11-05", billing="2026-11-10")])
    if goal_contrib:
        conn.execute("INSERT INTO goals(name,target_amount,target_date,saved_amount,"
                     "monthly_contribution,status,created_at) VALUES "
                     "('g',100000,'2030-01-01',0,?,'active','2026-11-01')", (goal_contrib,))


def test_affordability_verdicts(conn):
    _afford_setup(conn)
    # medians: income 10000, fixed 3000, variable 2500 -> +4500/month, buffer 5500.
    # free now = 5000 balance - 500 pending card - (5500 - 3500 spent) = 2500
    a = an.affordability(conn, 6000, "2027-01", ON)
    assert [p["free"] for p in a["path"]] == [2500, 7000, 11500]
    assert a["projected_free"] == 11500 and a["buffer"] == 5500
    assert a["verdict"] == "yes"
    assert an.affordability(conn, 8000, "2027-01", ON)["verdict"] == "tight"
    no = an.affordability(conn, 12000, "2027-01", ON)
    assert no["verdict"] == "no" and no["shortfall"] == 6000
    assert no["monthly_saving_needed"] == 2000
    assert a["p25"] <= a["remaining_after"] <= a["p75"]
    assert a["assumptions"]["pending_card_charges"] == 500


def test_affordability_goals_deducted(conn):
    _afford_setup(conn, goal_contrib=1000)
    a = an.affordability(conn, 8000, "2027-01", ON)
    assert [p["free"] for p in a["path"]] == [1500, 5000, 8500]
    assert a["verdict"] == "tight"


def test_forecast_known_installments(conn):
    _afford_setup(conn)
    ingest([T(-100, "ריהוט", "2026-10-20", billing="2026-11-10", installment_no=2,
              installment_total=6)], conn=conn)
    f = an.forecast(conn, 6, ON)
    assert len(f["months"]) == 6 and f["months"][0]["month"] == "2026-12"
    inst = [m["known_installments"] for m in f["months"]]
    assert inst == [100, 100, 100, 100, 0, 0]
    assert f["months"][0]["income"] == 10000
    assert f["months"][1]["balance"] > f["months"][0]["balance"]


def test_goal_math():
    g = {"name": "flights", "target_amount": 4000, "target_date": "2027-07-01",
         "saved_amount": 0, "status": "active", "created_at": "2026-11-01"}
    f = an.goal_fields(g, ON)
    assert f["months_left"] == 8 and f["monthly_need"] == 500
    assert f["progress_pct"] == 0 and f["on_track"] is False
    f2 = an.goal_fields({**g, "monthly_contribution": 500}, ON)
    assert f2["on_track"] is True
    f3 = an.goal_fields({**g, "saved_amount": 1000}, ON)
    assert f3["progress_pct"] == 25 and f3["monthly_need"] == 375 and f3["on_track"] is True
    past = an.goal_fields({**g, "target_date": "2026-01-01"}, ON)
    assert past["months_left"] == 1 and past["monthly_need"] == 4000


def test_freebie_fields_expire_and_linked_gift(conn):
    from finapp import db as _db
    _db.set_setting(conn, "cal_account_kind", "gift")  # GIFT = prepaid Cal gift card
    ingest([T(-50, "לפני", "2026-09-20", key=GIFT), T(-120, "אחרי 1", "2026-10-05", key=GIFT),
            T(-80, "אחרי 2", "2026-10-20", key=GIFT)], conn=conn)
    gift_id = conn.execute("SELECT id FROM accounts WHERE kind='gift'").fetchone()[0]
    conn.execute("INSERT INTO freebies(name,value,unit,received_date,expiry_date,used_amount,"
                 "status,account_id) VALUES ('gift card',500,'ILS','2026-10-01','2027-10-01',30,"
                 "'active',?)", (gift_id,))
    conn.execute("INSERT INTO freebies(name,value,expiry_date,status) VALUES "
                 "('old voucher',100,'2026-11-14','active')")
    conn.execute("INSERT INTO freebies(name,value,expiry_date,status) VALUES "
                 "('soon',100,'2026-12-01','active')")
    fs = {f["name"]: f for f in an.list_freebies(conn, ON)}
    g = fs["gift card"]
    assert g["used_amount"] == 230 and g["remaining"] == 270 and g["status"] == "active"
    old = fs["old voucher"]
    assert old["status"] == "expired" and old["days_left"] == -1
    assert conn.execute("SELECT status FROM freebies WHERE name='old voucher'").fetchone()[0] \
        == "expired"
    assert [f["name"] for f in an.list_freebies(conn, ON)] == ["old voucher", "soon",
                                                               "gift card"]
    ov = an.overview(conn, ON)
    assert [f["name"] for f in ov["freebies_expiring"]] == ["soon"]


def test_overview_empty(conn):
    ov = an.overview(conn, ON)
    assert ov["months"] == [] and ov["balance"]["amount"] is None
    assert ov["uncategorized_count"] == 0 and ov["last_import"] is None


@pytest.mark.household
def test_forecast_includes_rent_and_mortgage(conn):
    _base(conn, extra=[T(-4000, "הלוואת משכנתא", f"{m}-03", key=BANK) for m in HIST])
    f = an.forecast(conn, 3, ON)
    assert f["baseline"]["fixed"] == 3000 + 4000
    assert all(m["fixed"] == 7000 for m in f["months"])


@pytest.mark.household
def test_forecast_counts_recently_started_fixed_charge(conn):
    # mortgage only in the last 3 of 6 baseline months: still counted in full next to rent
    _base(conn, extra=[T(-4000, "הלוואת משכנתא", f"{m}-03", key=BANK) for m in HIST[3:]]
          + [T(-600, "מכללת דוגמה", "2026-06-03", key=BANK)])  # stopped: not counted
    f = an.forecast(conn, 3, ON)
    assert f["baseline"]["fixed"] == 7000
    assert f["baseline"]["fixed_by_category"] == {"Home rent": 3000, "Mortgage": 4000}
    assert f["months"][0]["fixed"] == 7000
