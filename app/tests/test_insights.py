import json
from datetime import date

from finapp import insights as ins
from finapp import rules
from finapp.ingest import ingest
from finapp.analytics import add_months
from tests.backend_util import BANK, CARD, CARD2, T, cat, conn  # noqa: F401

ON = date(2026, 11, 15)
MONTHS = [add_months("2025-09", i) for i in range(14)]  # 2025-09 .. 2026-10


def _rule(conn, pattern, name):
    rules.create_rule(conn, {"pattern": pattern, "category_id": cat(conn, name)})


def _items(conn, typ=None):
    items = ins.list_insights(conn, ON)["items"]
    return [i for i in items if typ is None or i["type"] == typ]


def test_duplicate_charge(conn):
    ingest([T(-250, "חנות נעליים", "2026-10-01"), T(-250, "חנות נעליים", "2026-10-03"),
            T(-250, "חנות נעליים", "2026-10-20"),          # far apart: not a dup
            T(-250, "חנות נעליים", "2026-10-02", key=CARD2)],  # other card
           conn=conn)
    d = _items(conn, "duplicate_charge")
    assert len(d) == 1
    assert d[0]["severity"] == "high" and len(d[0]["txn_ids"]) == 2
    assert d[0]["key"].startswith("duplicate_charge:")


def test_fees(conn):
    rows = [T(-25, "דמי כרטיס", f"{m}-20") for m in MONTHS[-12:]]
    rows += [T(-15, "עמלת פעולה בסניף", f"{m}-07", key=BANK) for m in MONTHS[-10:]]
    rows += [T(-8, "עמלת המרה", f"{m}-09") for m in MONTHS[-3:]]
    ingest(rows, conn=conn)
    by = {i["key"]: i for i in _items(conn)}
    assert by["card_fee"]["type"] == "card_fee" and by["card_fee"]["est_yearly_saving"] == 300
    assert by["card_fee"]["severity"] == "high"
    assert by["bank_fee"]["type"] == "bank_fee" and by["bank_fee"]["est_yearly_saving"] == 150
    assert by["fx_fee"]["type"] == "card_fee" and by["fx_fee"]["est_yearly_saving"] == 24
    assert by["bank_fee"]["suggested_task"]["title"]


def _subs(conn, last_netflix=55):
    rows = [T(-55, "NETFLIX.COM", f"{m}-03") for m in MONTHS[-7:-1]]
    rows.append(T(-last_netflix, "NETFLIX.COM", f"{MONTHS[-1]}-03"))
    rows += [T(-20, "SPOTIFY P1", f"{m}-12") for m in MONTHS[-6:]]
    ingest(rows, conn=conn)
    _rule(conn, "netflix", "Digital")
    _rule(conn, "spotify", "Digital")
    rules.apply_rules(conn)


def test_subscriptions_and_overlap(conn):
    _subs(conn)
    subs = {i["key"]: i for i in _items(conn, "subscription")}
    assert "subscription:netflix com" in subs and "subscription:spotify p" in subs
    assert subs["subscription:netflix com"]["est_monthly_saving"] is None  # informational
    assert "subscription:overlap:digital" in subs
    assert subs["subscription:overlap:digital"]["est_monthly_saving"] == 20
    assert not _items(conn, "price_increase")


def test_price_increase(conn):
    _subs(conn, last_netflix=70)
    pi = _items(conn, "price_increase")
    assert len(pi) == 1 and pi[0]["merchant"] == "netflix com"
    assert pi[0]["est_monthly_saving"] == 15
    assert pi[0]["key"] == f"price_increase:netflix com:{MONTHS[-1]}"


def test_category_creep_and_top_discretionary(conn):
    rows = []
    for i, m in enumerate(MONTHS[-12:]):
        amt = 1000 if i >= 9 else 500  # last 3 complete months doubled
        rows.append(T(-amt, "מסעדה", f"{m}-15"))
    ingest(rows, conn=conn)
    _rule(conn, "מסעדה", "Dining out")
    rules.apply_rules(conn)
    creep = _items(conn, "category_creep")
    assert len(creep) == 1 and creep[0]["category_id"] == cat(conn, "Dining out")
    assert creep[0]["est_monthly_saving"] == 250  # 50% of the increase is recoverable
    top = _items(conn, "top_discretionary")
    assert top and top[0]["category_id"] == cat(conn, "Dining out")
    assert top[0]["est_monthly_saving"] == round(7500 / 12 * 0.15, 2)


def test_fx_heavy(conn):
    ingest([T(-100, f"SHOP{i}", f"2026-{(i % 9) + 1:02d}-1{i % 3}", original_amount=-27,
              original_currency="USD") for i in range(12)], conn=conn)
    fx = _items(conn, "fx_heavy")
    assert len(fx) == 1 and fx[0]["est_yearly_saving"] == 30


def test_insurance_review(conn):
    ingest([T(-150, "ביטוח ישיר", f"{m}-20") for m in MONTHS], conn=conn)
    _rule(conn, "ביטוח ישיר", "Insurance")
    rules.apply_rules(conn)
    ir = _items(conn, "insurance_review")
    assert len(ir) == 1 and ir[0]["est_monthly_saving"] == 22.5
    assert not _items(conn, "subscription")  # insurance isn't listed as a subscription


def test_excludes_transfers(conn):
    ingest([T(-4000, "מקס איט פיננ", "2026-10-02", key=BANK),
            T(-4000, "מקס איט פיננ", "2026-10-03", key=BANK)], conn=conn)
    assert _items(conn) == []


def test_manual_insights_dismiss_and_task(conn):
    conn.execute(
        "INSERT INTO manual_insights(key,type,severity,title,detail,est_monthly_saving,txn_ids,"
        "suggested_task) VALUES ('ai:1','ai_review','high','Switch mobile plan','Cheaper plan',"
        "40,?,?)", (json.dumps(["x"]), json.dumps({"title": "Call carrier"})))
    items = _items(conn)
    assert items[0]["key"] == "ai:1" and items[0]["est_yearly_saving"] == 480
    assert items[0]["txn_ids"] == ["x"] and items[0]["suggested_task"]["title"] == "Call carrier"
    t = ins.insight_to_task(conn, "ai:1", ON)
    assert t["title"] == "Call carrier" and t["priority"] == "high"
    assert t["est_monthly_saving"] == 40 and t["insight_key"] == "ai:1"
    assert ins.insight_to_task(conn, "ai:1", ON)["id"] == t["id"]  # idempotent
    assert _items(conn)[0]["task_id"] == t["id"]
    ins.dismiss(conn, "ai:1", "done already")
    assert _items(conn) == []
    assert len(ins.list_insights(conn, ON, include_dismissed=True)["items"]) == 1


def test_keys_stable(conn):
    _subs(conn)
    k1 = [i["key"] for i in _items(conn)]
    k2 = [i["key"] for i in _items(conn)]
    assert k1 == k2 and len(set(k1)) == len(k1)
