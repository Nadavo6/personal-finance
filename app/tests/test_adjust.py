"""Household adjustments (invented household setup, see tests/conftest.py): home rent / education split, refund semantics, income
baseline, property P&L + arrears, goal ↔ asset link, fixed charges with an end.
All values are invented."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from finapp import analytics as an
from finapp import db, insights, networth, rules, server
from finapp.ingest import ingest
from tests.backend_util import BANK, RU_BANK, T, cat, conn, txns  # noqa: F401

pytestmark = pytest.mark.household

ON = date(2026, 11, 15)
HIST = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]


def _by_desc(conn) -> dict[str, dict]:
    return {t["description"]: t for t in txns(conn)}


def _household(conn, *, salary=9000, extra=()):
    """Six months: salary, a rent check (6,200 out), a roommate's share (3,100 in) as a
    standing order, groceries on the card."""
    rows = []
    for m in HIST:
        rows += [T(salary, "משכורת חברה", f"{m}-01", key=BANK),
                 T(3100, "הוראת קבע שותפה", f"{m}-01", key=BANK),
                 T(-6200, "שיק", f"{m}-10", key=BANK),
                 T(-1500, "סופר שכונתי", f"{m}-05", billing=f"{m}-10")]
    rules.create_rule(conn, {"pattern": "סופר שכונתי", "category_id": cat(conn, "Groceries")})
    ingest(rows + list(extra), conn=conn)


def _property(conn, plan, data=None):
    d = {"price": 1000000, "rental_income": plan, **(data or {})}
    conn.execute("INSERT INTO properties(key,name,data_json,updated_at) VALUES ('flat','Test flat',?,?)",
                 (json.dumps(d), db.now_iso()))


# ---------------------------------------------------------------- 1. categories + rules

def test_home_rent_and_education_rules(conn):
    names = {r["name"]: r["kind"] for r in conn.execute("SELECT name, kind FROM categories")}
    assert names["Home rent"] == "fixed" and names["Education"] == "fixed"
    assert "Rent & tuition" not in names
    ingest([
        T(-6400, "שיק", "2026-10-10", key=BANK),
        T(-450, "שיק", "2026-10-11", key=BANK),          # small check: not rent
        T(3200, "הוראת קבע שותפה", "2026-10-01", key=BANK),
        T(900, "הוראת קבע אחרת", "2026-10-02", key=BANK),  # outside the band
        T(-3200, "הוראת קבע", "2026-10-03", key=BANK),     # outgoing: not the roommate's share
        T(-4000, "מכללת בדיקה", "2026-10-04", key=RU_BANK, provider_category="שכירות ושכר לימוד"),
        T(-5000, "משכיר כלשהו", "2026-10-05", key=RU_BANK, provider_category="שכירות ושכר לימוד"),
    ], conn=conn)
    rows = _by_desc(conn)
    home, edu = cat(conn, "Home rent"), cat(conn, "Education")
    got = {(t["description"], t["amount"]): t["category_id"] for t in txns(conn)}
    assert got[("שיק", -6400)] == home
    assert got[("שיק", -450)] != home
    assert rows["הוראת קבע שותפה"]["category_id"] == home
    assert rows["הוראת קבע אחרת"]["category_id"] != home
    assert got[("הוראת קבע", -3200)] != home
    assert rows["מכללת בדיקה"]["category_id"] == edu      # Education rule beats RiseUp
    assert rows["משכיר כלשהו"]["category_id"] == home         # RiseUp rent+tuition -> Home rent


def test_migration_renames_rent_and_tuition(conn):
    home = cat(conn, "Home rent")
    conn.execute("UPDATE categories SET name='Rent & tuition', name_he='x' WHERE id=?", (home,))
    db._migrate_6(conn)
    db._migrate_6(conn)  # idempotent
    r = conn.execute("SELECT id, name_he FROM categories WHERE name='Home rent'").fetchone()
    assert r["id"] == home and r["name_he"] == "שכירות"
    assert conn.execute("SELECT COUNT(*) FROM rules WHERE pattern='שיק' AND direction='out'"
                        ).fetchone()[0] == 1


# ---------------------------------------------------------------- 2. refund semantics

def test_refund_in_expense_category_is_not_income(conn):
    _household(conn)
    led = an.Ledger(conn)
    home = cat(conn, "Home rent")
    for m in HIST:
        md = led.get(m)
        assert md["income"] == 9000                       # the roommate's share is not income
        assert led.cat_actual(m, home) == 6200 - 3100      # net rent
        assert md["expense"] == 3100 + 1500
        assert md["income_by"] == {"salary": 9000, "rental": 0, "reserve_duty": 0, "other": 0}
    ov = {m["month"]: m for m in an.overview(conn, ON)["months"]}
    assert ov["2026-10"]["income"] == 9000 and ov["2026-10"]["net"] == 9000 - 4600
    tr = an.trends(conn, 6, ON)
    assert set(tr["income"][:-1]) == {9000}
    s = an.month_summary(conn, "2026-10", ON)
    assert s["income"] == 9000
    assert next(c for c in s["categories"] if c["name"] == "Home rent")["actual"] == 3100
    assert s["income_breakdown"]["salary"] == 9000
    f = an.forecast(conn, 2, ON)
    assert f["income_baseline"]["salary"] == 9000 and f["months"][0]["income"] == 9000
    assert f["baseline"]["fixed_by_category"]["Home rent"] == 3100
    a = an.affordability(conn, 100, "2026-12", ON)
    assert a["income_baseline"]["total"] == 9000


def test_large_refund_is_no_unusual_inflow_but_uncategorized_inflow_is(conn):
    _household(conn, extra=[T(6100, "החזר ביטוח", "2026-10-20", key=BANK),
                            T(7000, "העברה מפלוני", "2026-10-21", key=BANK)])
    conn.execute("UPDATE transactions SET category_id=?, category_source='manual' "
                 "WHERE description='החזר ביטוח'", (cat(conn, "Insurance"),))
    led = an.Ledger(conn)
    # uncategorized inflow stays income; the refund with no Insurance charge that month
    # floors the category at 0 and the excess counts as other income
    assert led.get("2026-10")["income"] == 9000 + 7000 + 6100
    assert led.cat_actual("2026-10", cat(conn, "Insurance")) == 0
    flagged = {r["description"] for r in an.unusual_inflows(conn, ["2026-10"])}
    assert flagged == {"העברה מפלוני"}
    ins = insights.list_insights(conn, ON)["items"]
    assert not any(i["type"] == "unusual_inflow" and "החזר ביטוח" in i["title"] for i in ins)


def test_category_spend_floors_at_zero_excess_is_other_income(conn):
    """Refunds / inbound transfers beyond a category's outflows in a month never make its
    spend (or the month's expense) negative: the excess counts as other income."""
    _household(conn, extra=[T(-400, "חנות בגדים", "2026-10-03", billing="2026-10-10"),
                            T(1000, "חנות בגדים", "2026-10-04", billing="2026-10-10"),
                            T(-250, "חנות בגדים", "2026-09-03", billing="2026-09-10"),
                            T(100, "חנות בגדים", "2026-09-04", billing="2026-09-10")])
    shop = cat(conn, "Shopping")
    conn.execute("UPDATE transactions SET category_id=? WHERE description='חנות בגדים'", (shop,))
    led = an.Ledger(conn)
    oct_, sep = led.get("2026-10"), led.get("2026-09")
    assert led.cat_actual("2026-10", shop) == 0                 # 400 out, 1000 back
    assert oct_["expense"] == 3100 + 1500                       # unchanged by the refund
    assert oct_["income"] == 9000 + 600
    assert oct_["income_by"]["other"] == 600
    assert led.cat_actual("2026-09", shop) == 150               # partial refund: still spend
    assert sep["income"] == 9000 and sep["expense"] == 3100 + 1500 + 150
    for m in an.overview(conn, ON)["months"]:
        assert m["expense"] >= 0
    s = an.month_summary(conn, "2026-10", ON)
    assert all((c["actual"] or 0) >= 0 for c in s["categories"] if c["kind"] != "income")
    assert s["income_breakdown"]["other"] == 600
    tr = an.trends(conn, 6, ON)
    assert all(v is None or v >= 0 for c in tr["categories"] if c["kind"] in an.EXPENSE_KINDS
               for v in c["series"])


# ---------------------------------------------------------------- 3. income baseline

def test_income_baseline_setting_and_extras(conn):
    extra = [T(2400, 'מופ"ת מילואים', "2026-09-15", key=BANK),
             T(1200, "מענק מילואים", "2026-10-15", key=BANK)]
    _household(conn, extra=extra)
    # a bonus month
    ingest([T(1800, "משכורת בונוס", "2026-08-02", key=BANK)], conn=conn)
    f = an.forecast(conn, 3, ON)
    ib = f["income_baseline"]
    assert ib["salary_source"] == "median" and ib["salary"] == 9000
    assert f["months"][0]["income"] == 9000                 # reserve duty not in the baseline
    extras = {e["name"]: e["monthly_avg"] for e in ib["extra_income"]}
    assert extras["reserve_duty"] == pytest.approx(600)     # 3,600 over 6 months
    assert extras["bonus"] == 0                             # only measured against a set baseline

    db.set_setting(conn, "salary_net_baseline", "8500")
    f = an.forecast(conn, 3, ON)
    ib = f["income_baseline"]
    assert ib["salary_source"] == "setting" and ib["salary"] == 8500
    extras = {e["name"]: e["monthly_avg"] for e in ib["extra_income"]}
    assert extras["bonus"] == pytest.approx((500 * 6 + 1800) / 6)
    assert f["months"][0]["income"] == 8500

    db.set_setting(conn, "count_variable_income_in_forecast", "true")
    f = an.forecast(conn, 3, ON)
    assert f["income_baseline"]["extra_included"] is True
    assert f["months"][0]["income"] == pytest.approx(8500 + 600 + (3000 + 1800) / 6)

    s = an.month_summary(conn, "2026-10", ON)
    assert s["income_breakdown"] == {"salary": 9000, "rental": 0, "reserve_duty": 1200, "other": 0}


def test_income_baseline_adds_expected_rent(conn):
    _household(conn)
    _property(conn, [{"month": "2026-11", "amount": 4000}, {"month": "2027-01", "amount": 5000}])
    f = an.forecast(conn, 4, ON)
    by = {m["month"]: m for m in f["months"]}
    assert by["2026-12"]["rental"] == 0                   # not listed between plan months
    assert by["2027-01"]["rental"] == 5000 and by["2027-03"]["rental"] == 5000  # carried on
    assert by["2027-01"]["income"] == 9000 + 5000
    assert f["income_baseline"]["rental_expected"] == 4000  # current month
    s = an.month_summary(conn, "2026-11", ON)
    assert s["expected_income"] >= 9000 + 4000


# ---------------------------------------------------------------- 4. property P&L

def test_property_pnl_and_arrears(conn):
    plan = [{"month": "2026-08", "amount": 4000}, {"month": "2026-09", "amount": 4000},
            {"month": "2026-10", "amount": 6000}, {"month": "2026-11", "amount": 6000}]
    _property(conn, plan, {"fixed_monthly": [{"name": "Insurances (flat 40, life 30)", "amount": 70}]})
    ingest([T(8000, "שוכר", "2026-09-24", key=BANK),   # pays Aug + Sep
            T(1500, "שוכר נוסף", "2026-09-25", key=BANK),  # surplus: not applied to Oct
            T(-5000, "הלוואת משכנתא", "2026-09-10", key=BANK),
            T(-5100, "הלוואת משכנתא", "2026-10-10", key=BANK)], conn=conn)
    conn.execute("UPDATE transactions SET category_id=?, category_source='manual' "
                 "WHERE description LIKE 'שוכר%'", (cat(conn, "Rental income"),))
    p = networth.property_view(conn, ON)
    by = {r["month"]: r for r in p["pnl"]}
    assert list(by) == ["2026-08", "2026-09", "2026-10", "2026-11"]
    assert p["insurance_monthly"] == 40 and p["insurance_source"] == "sheet"
    sep = by["2026-09"]
    assert sep["expected"] == 4000 and sep["received"] == 9500 and sep["mortgage"] == 5000
    assert sep["tax_reserve"] == 400 and sep["insurance"] == 40
    assert sep["net"] == 9500 - 5000 - 40 - 400
    assert by["2026-10"]["net"] == 0 - 5100 - 40 - 600
    # cumulative: 20,000 due through Nov − 9,500 received; the 1,500 surplus carries into Oct
    assert p["rent_arrears"] == 10500 and p["rent_credit"] == 0
    assert [r["balance"] for r in p["pnl"]] == [-4000, 1500, -4500, -10500]
    assert p["rent_arrears_months"] == [{"month": "2026-10", "due": 4500},
                                        {"month": "2026-11", "due": 6000}]
    ins = [i for i in insights.list_insights(conn, ON)["items"] if i["type"] == "rent_overdue"]
    assert len(ins) == 1 and ins[0]["severity"] == "high"
    assert "10,500" in ins[0]["title"] and "Oct 2026" in ins[0]["title"]
    # a lump payment catches up and prepays: arrears clear, the rest is credit
    ingest([T(12000, "שוכר", "2026-11-12", key=BANK)], conn=conn)
    conn.execute("UPDATE transactions SET category_id=? WHERE description='שוכר'",
                 (cat(conn, "Rental income"),))
    p = networth.property_view(conn, ON)
    assert p["rent_arrears"] == 0 and p["rent_arrears_months"] == [] and p["rent_credit"] == 1500
    # upcoming months are never due
    assert all(r["month"] <= "2026-11" for r in p["pnl"])
    assert not any(i["type"] == "rent_overdue" for i in insights.list_insights(conn, ON)["items"])
    # rental income counts as rental income in the month breakdown
    assert an.month_summary(conn, "2026-11", ON)["income_breakdown"]["rental"] == 12000


def test_rent_lump_payment_covers_later_months(conn):
    plan = [{"month": m, "amount": 5000} for m in ("2026-08", "2026-09", "2026-10", "2026-11")]
    _property(conn, plan)
    ingest([T(15000, "שוכר", "2026-08-03", key=BANK)], conn=conn)  # prepays Aug–Oct
    conn.execute("UPDATE transactions SET category_id=?, category_source='manual'",
                 (cat(conn, "Rental income"),))
    p = networth.property_view(conn, date(2026, 10, 20))
    assert p["rent_arrears"] == 0 and p["rent_arrears_months"] == [] and p["rent_credit"] == 0
    assert [r["balance"] for r in p["pnl"]] == [10000, 5000, 0]
    p = networth.property_view(conn, date(2026, 9, 5))
    assert p["rent_arrears"] == 0 and p["rent_credit"] == 5000   # Oct already paid
    p = networth.property_view(conn, ON)
    assert p["rent_arrears"] == 5000
    assert p["rent_arrears_months"] == [{"month": "2026-11", "due": 5000}]  # Oct is covered


def test_insurance_from_fixed_lines():
    f = networth.insurance_from_fixed
    assert f([{"name": "Insurances (flat 12, life 3)", "amount": 15}]) == 12
    assert f([{"name": "Flat insurance", "amount": 33}]) == 33
    assert f([{"name": "ביטוח דירה", "amount": 21}]) == 21
    assert f([{"name": "Life insurance", "amount": 50}, {"name": "Loan", "amount": 9}]) is None


# ---------------------------------------------------------------- 5. goal ↔ asset

def _asset(conn, value, as_of, key="broker"):
    aid = conn.execute("SELECT id FROM assets WHERE key=?", (key,)).fetchone()
    if aid is None:
        aid = conn.execute("INSERT INTO assets(key,name,kind,created_at) VALUES (?,?,?,?)",
                           (key, "Broker", "investment", db.now_iso())).lastrowid
    else:
        aid = aid[0]
    networth.set_asset_value(conn, aid, as_of, value)
    return aid


def test_goal_linked_to_asset(conn):
    aid = _asset(conn, 50000, "2026-10-20")
    gid = conn.execute("INSERT INTO goals(name,target_amount,target_date,saved_amount,status,"
                       "created_at,asset_id) VALUES ('Flat',80000,'2027-11-30',1000,'active',"
                       "'2026-10-01',?)", (aid,)).lastrowid
    g = an.goal_fields(dict(conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()),
                       ON, conn=conn)
    assert g["saved_amount"] == 50000 and g["saved_source"] == "asset"
    assert g["linked_asset"]["as_of"] == "2026-10-20" and g["manual_saved_amount"] == 1000
    assert g["months_left"] == 12 and g["monthly_need"] == 2500
    assert g["progress_pct"] == 62.5
    assert an.goals_reserved(conn, ON) == 2500
    # this month: a savings deposit of 1,200 and the asset rose by 2,000 → 2,000 (no double count)
    ingest([T(-1200, "ברוקר דוגמה הפקדה", "2026-11-05", key=BANK)], conn=conn)
    _asset(conn, 52000, "2026-11-10")
    g = an.goal_fields(dict(conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()),
                       ON, conn=conn)
    put = g["put_away_this_month"]
    assert put["savings_outflows"] == 1200 and put["asset_change"] == 2000 and put["amount"] == 2000
    gp = an.month_summary(conn, "2026-11", ON)["goal_progress"]
    assert gp[0]["put_away"] == 2000 and gp[0]["monthly_need"] == pytest.approx(28000 / 12, abs=0.01)
    # no newer asset value in the month: savings outflows only
    gp = an.month_summary(conn, "2026-09", ON)["goal_progress"]
    assert gp[0]["put_away"] == 0 and gp[0]["detail"]["asset_change"] is None


# ---------------------------------------------------------------- 6. fixed charges with an end

def test_forecast_drops_fixed_after_end(conn):
    rows = []
    for m in HIST:
        rows += [T(9000, "משכורת חברה", f"{m}-01", key=BANK),
                 T(-2000, "מכללת בדיקה", f"{m}-03", key=BANK),
                 T(-300, "פרעון הלוואה", f"{m}-27", key=BANK),
                 T(-100, "עמלת הלוואה", f"{m}-27", key=BANK)]
    ingest(rows, conn=conn)
    loan = cat(conn, "Loan")
    conn.execute("UPDATE transactions SET category_id=?, category_source='manual' "
                 "WHERE description LIKE '%הלוואה%'", (loan,))
    conn.execute("INSERT INTO fixed_ends(category_id,last_month,created_at) VALUES (?,?,?)",
                 (cat(conn, "Education"), "2026-12", db.now_iso()))
    conn.execute("INSERT INTO fixed_ends(category_id,merchant,last_month,created_at) "
                 "VALUES (?,?,?,?)", (loan, "פרעון הלוואה", "2027-01", db.now_iso()))
    f = an.forecast(conn, 4, ON)
    ends = {e["what"]: e for e in f["fixed_ends"]}
    assert ends["Education"]["monthly_amount"] == 2000
    assert ends["פרעון הלוואה"]["monthly_amount"] == 300     # merchant share, not the category
    by = {m["month"]: m for m in f["months"]}
    assert by["2026-12"]["fixed"] == 2400 and by["2026-12"]["fixed_ended"] == 0
    assert by["2027-01"]["fixed"] == 400                    # education ended
    assert by["2027-02"]["fixed"] == 100 and by["2027-02"]["fixed_ended"] == 2300
    a = an.affordability(conn, 0, "2027-02", ON)
    a0 = [p["free"] for p in a["path"]]
    assert a0[2] - a0[1] == pytest.approx(a0[1] - a0[0] + 2000)  # Jan frees the tuition


# ---------------------------------------------------------------- rental tax + overdraft risk

def test_forecast_counts_rental_tax_and_flat_insurance(conn):
    _household(conn)
    _property(conn, [{"month": "2026-12", "amount": 4000}], {"insurance_monthly": 30})
    f = an.forecast(conn, 3, ON)
    by = {m["month"]: m for m in f["months"]}
    assert by["2026-12"]["rental_tax"] == 400 and by["2026-12"]["flat_insurance"] == 30
    assert by["2026-12"]["fixed"] == 3100 + 400 + 30
    assert by["2026-12"]["net"] == pytest.approx(9000 + 4000 - by["2026-12"]["expense"])
    assert f["income_baseline"]["rental_tax_reserve"] == 0   # no rent expected in Nov
    a = an.affordability(conn, 0, "2026-12", ON)
    step = a["path"][1]["free"] - a["path"][0]["free"]
    assert step == pytest.approx(9000 + 4000 - (3100 + 430) - a["assumptions"]["median_variable"])
    db.set_setting(conn, "rental_tax_pct", "25")
    assert {m["month"]: m for m in an.forecast(conn, 3, ON)["months"]}["2026-12"]["rental_tax"] == 1000
    # insurance already charged as a fixed category: not added twice
    ingest([T(-60, "ביטוח כלשהו", f"{m}-12", key=BANK) for m in HIST], conn=conn)
    conn.execute("UPDATE transactions SET category_id=?, category_source='manual' "
                 "WHERE description='ביטוח כלשהו'", (cat(conn, "Insurance"),))
    f = an.forecast(conn, 3, ON)
    assert f["months"][0]["flat_insurance"] == 0 and f["income_baseline"]["flat_insurance_in_fixed"]


def test_overdraft_risk(conn):
    _household(conn, extra=[T(-100, "עמלה", "2026-11-02", key=BANK, balance_after=1000)])
    assert an.forecast(conn, 1, ON)["end_of_current_month"] >= 0
    assert an.overdraft_risk(conn, ON) is None
    assert an.month_summary(conn, "2026-11", ON)["overdraft_risk"] is None
    conn.execute("INSERT INTO goals(name,target_amount,monthly_contribution,status,created_at) "
                 "VALUES ('Big',100000,20000,'active','2026-01-01')")
    aid = _asset(conn, 50000, "2026-11-01")
    networth.set_asset_value(conn, aid, "2026-11-01", 50000,
                             [{"name": "Money market (כספית)", "value": 3000},
                              {"name": "Stocks", "value": 47000}])
    r = an.overdraft_risk(conn, ON)
    end = an.forecast(conn, 1, ON)["end_of_current_month"]
    assert end < 0 and r["projected"] == end
    assert r["move_amount"] == 3000 and r["source"]["name"].startswith("Money market")  # capped
    assert "Nov 2026" in r["text"] and "money-market" in r["text"]
    assert an.month_summary(conn, "2026-11", ON)["overdraft_risk"]["move_amount"] == 3000
    assert "overdraft_risk" not in an.month_summary(conn, "2026-10", ON)
    ins = [i for i in insights.list_insights(conn, ON)["items"] if i["type"] == "overdraft_risk"]
    assert len(ins) == 1 and ins[0]["severity"] == "high"
    # a bigger fund: move the shortfall rounded up to ₪500
    networth.set_asset_value(conn, aid, "2026-11-01", 500000,
                             [{"name": "Money market (כספית)", "value": 400000}])
    r = an.overdraft_risk(conn, ON)
    assert r["move_amount"] % 500 == 0 and 0 <= r["move_amount"] + r["projected"] < 500
    # rent arrears are mentioned when there are any
    _property(conn, [{"month": "2026-10", "amount": 2000}])
    assert "rent arrears ₪4,000" in an.overdraft_risk(conn, ON)["text"]


# ---------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINAPP_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FINAPP_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return TestClient(server.app)


def test_api_settings_goal_link_fixed_ends(client):
    s = client.get("/api/settings").json()
    assert s["salary_net_baseline"] is None and s["count_variable_income_in_forecast"] == "false"
    assert client.patch("/api/settings", json={"salary_net_baseline": "abc"}).status_code == 422
    s = client.patch("/api/settings", json={"salary_net_baseline": 7000,
                                            "count_variable_income_in_forecast": True}).json()
    assert s["salary_net_baseline"] == "7000" and s["count_variable_income_in_forecast"] == "true"
    assert client.patch("/api/settings", json={"salary_net_baseline": None}).json()[
        "salary_net_baseline"] is None

    a = client.post("/api/assets", json={"name": "Broker", "kind": "investment"}).json()
    client.post(f"/api/assets/{a['id']}/value", json={"as_of": "2026-01-05", "value": 4000})
    g = client.post("/api/goals", json={"name": "G", "target_amount": 10000,
                                        "target_date": "2030-01-01"}).json()
    assert g["saved_source"] == "manual" and g["linked_asset"] is None
    assert client.patch(f"/api/goals/{g['id']}", json={"asset_id": 999}).status_code == 422
    g = client.patch(f"/api/goals/{g['id']}", json={"asset_id": a["id"]}).json()
    assert g["saved_amount"] == 4000 and g["linked_asset"]["id"] == a["id"]
    assert "put_away_this_month" in g
    g = client.patch(f"/api/goals/{g['id']}", json={"asset_id": None}).json()
    assert g["saved_source"] == "manual"

    cats = {c["name"]: c["id"] for c in client.get("/api/categories").json()}
    assert client.post("/api/fixed-ends", json={"last_month": "2027-01"}).status_code == 422
    assert client.post("/api/fixed-ends", json={"category_id": cats["Education"],
                                                "last_month": "bad"}).status_code == 422
    fe = client.post("/api/fixed-ends", json={"category_id": cats["Education"],
                                              "last_month": "2027-01"}).json()
    assert fe["what"] == "Education" and fe["monthly_amount"] == 0
    fe = client.patch(f"/api/fixed-ends/{fe['id']}", json={"last_month": "2027-03"}).json()
    assert fe["last_month"] == "2027-03"
    assert len(client.get("/api/fixed-ends").json()) == 1
    for path in ("/api/forecast", "/api/affordability?amount=100", "/api/networth",
                 "/api/month/2026-01", "/api/insights", "/api/overview", "/api/goals"):
        assert client.get(path).status_code == 200, path
    assert client.delete(f"/api/fixed-ends/{fe['id']}").status_code == 200
    assert client.get("/api/fixed-ends").json() == []
