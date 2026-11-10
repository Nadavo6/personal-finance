import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from finapp import server
from tests.backend_util import CARD, T

CSV = ("date,description,amount\n"
       "2026-10-03,חנות דוגמה,-120.50\n"
       "2026-10-04,חנות דוגמה,-80\n"
       "2026-10-05,משכורת דוגמה,9000\n")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINAPP_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FINAPP_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return TestClient(server.app)


def _import(client):
    r = client.post("/api/import", files={"file": ("bank.csv", CSV.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def test_empty_db_endpoints(client):
    ov = client.get("/api/overview").json()
    assert set(ov) >= {"current_month", "months", "balance", "freebies_expiring",
                       "uncategorized_count", "last_import", "last_sync",
                       "open_tasks_count", "insights_count"}
    assert ov["open_tasks_count"] == 0 and ov["insights_count"] == 0
    for url in ["/api/month/2026-10", "/api/transactions", "/api/categories", "/api/rules",
                "/api/goals", "/api/freebies", "/api/trends?months=6", "/api/forecast?months=3",
                "/api/affordability?amount=100", "/api/insights", "/api/tasks", "/api/accounts",
                "/api/settings", "/api/sync/status", "/api/export.csv", "/api/health"]:
        assert client.get(url).status_code == 200, url
    assert client.get("/api/month/bad").status_code == 422


def test_import_transactions_patch_and_rules(client):
    res = _import(client)
    assert (res["rows"], res["new_rows"], res["dup_rows"]) == (3, 3, 0)
    assert _import(client)["dup_rows"] == 3
    tx = client.get("/api/transactions", params={"month": "2026-10"}).json()
    assert tx["total"] == 3
    t = next(i for i in tx["items"] if i["amount"] == -120.5)
    assert set(t) >= {"id", "date", "billing_date", "cashflow_month", "amount", "description",
                      "merchant", "category_id", "category_name", "category_source", "account",
                      "is_transfer", "is_excluded", "installment_no", "installment_total",
                      "original_amount", "original_currency", "notes"}
    assert t["account"]["kind"] == "bank"
    cats = {c["name"]: c["id"] for c in client.get("/api/categories").json()}
    unc = client.get("/api/transactions", params={"uncategorized": True}).json()
    assert unc["total"] >= 2

    pv = client.post("/api/rules/preview", json={"pattern": t["merchant"],
                                                 "match_type": "contains"}).json()
    assert pv["count"] == 2

    r = client.patch(f"/api/transactions/{t['id']}",
                     json={"category_id": cats["Shopping"], "remember": True,
                           "apply_to_history": True}).json()
    assert r["txn"]["category_name"] == "Shopping" and r["txn"]["category_source"] == "manual"
    assert r["rule"]["pattern"] == t["merchant"] and r["updated_count"] == 2
    other = [i for i in client.get("/api/transactions", params={"q": "חנות"}).json()["items"]
             if i["id"] != t["id"]][0]
    assert other["category_name"] == "Shopping" and other["category_source"] == "rule"

    r = client.patch(f"/api/transactions/{other['id']}",
                     json={"is_excluded": True, "notes": "one-off"}).json()
    assert r["txn"]["is_excluded"] is True and r["txn"]["notes"] == "one-off"
    r = client.patch(f"/api/transactions/{other['id']}", json={"category_id": None}).json()
    assert r["txn"]["category_source"] == "rule"
    assert client.patch("/api/transactions/nope", json={}).status_code == 404

    b = client.post("/api/transactions/bulk", json={"ids": [t["id"], other["id"]],
                                                    "category_id": cats["Gifts"]}).json()
    assert b["updated_count"] == 2

    rules_ = client.get("/api/rules").json()
    user_rule = next(x for x in rules_ if x["pattern"] == t["merchant"])
    assert user_rule["category_name"] == "Shopping"
    nr = client.post("/api/rules", json={"pattern": "משכורת דוגמה",
                                         "category_id": cats["Other income"]})
    assert nr.status_code == 200 and nr.json()["updated_count"] >= 1
    rid = nr.json()["id"]
    assert client.patch(f"/api/rules/{rid}", json={"priority": 300}).json()["priority"] == 300
    assert client.delete(f"/api/rules/{rid}").json()["deleted"] == rid
    assert client.post("/api/rules", json={"pattern": "(", "match_type": "regex"}).status_code \
        == 422

    csv_text = client.get("/api/export.csv").text
    assert csv_text.lstrip("﻿").startswith("id,date") and csv_text.count("\n") == 4


def test_categories_crud(client):
    c = client.post("/api/categories", json={"name": "Hobby", "name_he": "תחביב",
                                             "kind": "variable", "monthly_budget": 300}).json()
    assert c["id"] and c["kind"] == "variable"
    assert client.patch(f"/api/categories/{c['id']}",
                        json={"monthly_budget": 400}).json()["monthly_budget"] == 400
    assert client.delete(f"/api/categories/{c['id']}").json() == {"deleted": c["id"]}
    assert client.post("/api/categories", json={}).status_code == 422


def test_goals_freebies_tasks(client):
    g = client.post("/api/goals", json={"name": "trip", "target_amount": 4000,
                                        "target_date": "2030-01-01", "saved_amount": 1000}).json()
    assert {"monthly_need", "months_left", "progress_pct", "on_track"} <= set(g)
    assert g["progress_pct"] == 25
    g2 = client.patch(f"/api/goals/{g['id']}", json={"saved_amount": 4000}).json()
    assert g2["progress_pct"] == 100 and g2["on_track"] is True
    assert len(client.get("/api/goals").json()) == 1
    assert client.delete(f"/api/goals/{g['id']}").status_code == 200

    f = client.post("/api/freebies", json={"name": "voucher", "source": "reserve", "value": 200,
                                           "expiry_date": "2030-01-01"}).json()
    assert f["days_left"] > 0 and f["remaining"] == 200
    f = client.patch(f"/api/freebies/{f['id']}", json={"used_amount": 50}).json()
    assert f["remaining"] == 150
    assert client.get("/api/freebies").json()[0]["name"] == "voucher"
    assert client.delete(f"/api/freebies/{f['id']}").status_code == 200

    t1 = client.post("/api/tasks", json={"title": "call bank", "priority": "high",
                                         "est_monthly_saving": 30}).json()
    client.post("/api/tasks", json={"title": "cancel gym", "est_monthly_saving": 100})
    ts = client.get("/api/tasks", params={"status": "open"}).json()
    assert len(ts["items"]) == 2 and ts["total_est_monthly_saving"] == 130
    done = client.patch(f"/api/tasks/{t1['id']}", json={"status": "done",
                                                        "actual_monthly_saving": 25}).json()
    assert done["done_at"]
    ts = client.get("/api/tasks").json()
    assert ts["total_est_monthly_saving"] == 100 and ts["total_actual_monthly_saving"] == 25
    assert client.delete(f"/api/tasks/{t1['id']}").status_code == 200


def test_insights_api(client):
    assert client.post("/api/insights/manual", json={
        "key": "ai:x", "title": "Switch plan", "est_monthly_saving": 10,
        "suggested_task": {"title": "Switch plan now"}}).json()["saved"]
    items = client.get("/api/insights").json()["items"]
    assert items[0]["key"] == "ai:x"
    task = client.post("/api/insights/ai:x/task").json()
    assert task["insight_key"] == "ai:x" and task["title"] == "Switch plan now"
    assert client.post("/api/insights/ai:x/dismiss", json={"reason": "n/a"}).json()["dismissed"]
    assert client.get("/api/insights").json()["items"] == []
    assert client.post("/api/insights/nope/task").status_code == 404


def test_accounts_settings_and_analytics(client):
    _import(client)
    acc = client.get("/api/accounts").json()
    assert len(acc) == 1 and acc[0]["txn_count"] == 3 and acc[0]["kind"] == "bank"
    assert client.patch(f"/api/accounts/{acc[0]['id']}", json={"name": "Main"}).json()["name"] \
        == "Main"
    s = client.patch("/api/settings", json={"cashflow_start_day": 5, "overspend_pct": 130}).json()
    assert s["cashflow_start_day"] == "5" and s["overspend_pct"] == "130"
    months = {i["cashflow_month"] for i in client.get("/api/transactions").json()["items"]}
    assert months == {"2026-09", "2026-10"}  # Oct 3/4 now belong to the Sept period
    assert client.patch("/api/settings", json={"cashflow_start_day": 40}).status_code == 422
    assert client.get("/api/trends", params={"months": 3}).json()["months"]
    assert len(client.get("/api/forecast", params={"months": 4}).json()["months"]) == 4
    a = client.get("/api/affordability", params={"amount": 500, "month": "2030-01"}).json()
    assert a["verdict"] in ("yes", "tight", "no") and a["path"][-1]["month"] == "2030-01"
    m = client.get("/api/month/2026-10").json()
    assert {"left_to_spend", "goals_reserved", "categories", "top_merchants", "anomalies",
            "fixed"} <= set(m)


def test_sync_background_and_guard(client, monkeypatch):
    from finapp import sync

    calls = []

    def fake_run_sync(providers=None, start=None):
        calls.append(providers)
        return [{"name": "max", "ok": True, "error": None,
                 "txns": [T(-42, "סנכרון דוגמה", date(2026, 10, 9), key=CARD)]},
                {"name": "leumi", "ok": False, "error": "login failed", "txns": []}]

    monkeypatch.setattr(sync, "run_sync", fake_run_sync)
    r = client.post("/api/sync", json={"providers": "max,leumi"})
    assert r.status_code == 200 and r.json()["started"]
    for _ in range(100):
        st = client.get("/api/sync/status").json()
        if not st["running"]:
            break
        time.sleep(0.05)
    assert not st["running"] and calls == [["max", "leumi"]]
    by = {p["name"]: p for p in st["providers"]}
    assert by["max"]["ok"] and by["max"]["new_rows"] == 1
    assert not by["leumi"]["ok"] and by["leumi"]["error"] == "login failed"
    assert client.get("/api/overview").json()["last_sync"]["providers"]

    server._sync_lock.acquire()
    try:
        assert client.post("/api/sync").status_code == 409
    finally:
        server._sync_lock.release()


def test_import_inbox_endpoint(client, tmp_path):
    inbox = tmp_path / "data" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "x.csv").write_text(CSV, encoding="utf-8")
    r = client.post("/api/import/inbox").json()
    assert r["new_rows"] == 3 and r["files"][0]["ok"]
    assert not (inbox / "x.csv").exists()


def test_overview_counts(client):
    _import(client)
    client.post("/api/tasks", json={"title": "call bank"})
    done = client.post("/api/tasks", json={"title": "old"}).json()
    client.patch(f"/api/tasks/{done['id']}", json={"status": "done"})
    ov = client.get("/api/overview").json()
    assert ov["open_tasks_count"] == 1
    assert ov["insights_count"] == len(client.get("/api/insights").json()["items"])


@pytest.mark.household
def test_one_offs_ids_suggestions_and_freebie_alias(client):
    from finapp import db, rules
    from finapp.ingest import ingest
    from tests.backend_util import BANK
    conn = db.connect()
    rows = [T(-50, "מאפייה טובה", f"2026-0{m}-03") for m in (6, 7, 8)]
    rows += [T(-60, "מאפייה טובה", "2026-10-03"),
             T(-70, "חנות לא מוכרת", "2026-10-04", provider_category="אופנה"),
             T(-80, "חנות אחרת", "2026-10-05"),
             T(5000, "העברה מבנק אחר", "2026-10-06", key=BANK)]
    ingest(rows, conn=conn)
    groc = db.category_id(conn, "Groceries")
    old = [r[0] for r in conn.execute(
        "SELECT id FROM transactions WHERE merchant='מאפייה טובה' AND date<'2026-10-01'")]
    conn.executemany("UPDATE transactions SET category_id=?, category_source='manual' WHERE id=?",
                     [(groc, i) for i in old])
    # a provider-mapped row the user put back to Uncategorized: provider fallback applies
    conn.execute("UPDATE transactions SET category_id=?, category_source='manual' "
                 "WHERE description='חנות לא מוכרת'", (db.uncategorized_id(conn),))
    conn.commit()
    cats = {c["name"]: c for c in client.get("/api/categories").json()}
    assert cats["Renovation"]["one_off"] == 1 and cats["Rental income"]["kind"] == "income"

    unc = {t["description"]: t for t in
           client.get("/api/transactions", params={"uncategorized": True}).json()["items"]}
    assert unc["מאפייה טובה"]["suggested_category_id"] == groc            # history wins
    assert unc["חנות לא מוכרת"]["suggested_category_id"] == cats["Clothing"]["id"]  # provider
    assert unc["חנות אחרת"]["suggested_category_id"] is None

    # PATCH into a one-off category excludes the row; month view reports it apart
    inflow = next(t for t in client.get("/api/transactions", params={"month": "2026-10"})
                  .json()["items"] if t["amount"] == 5000)
    r = client.patch(f"/api/transactions/{inflow['id']}",
                     json={"category_id": cats["Renovation"]["id"]}).json()
    assert r["txn"]["is_excluded"] is True and r["txn"]["category_source"] == "manual"
    mo = client.get("/api/month/2026-10").json()
    assert mo["one_offs"] == {"income": 5000, "expense": 0, "count": 1}
    assert mo["income"] == 0

    # ids=: exactly those rows, aliases resolved, transfer filter ignored
    conn.execute("INSERT INTO txn_aliases(alias_id, txn_id) VALUES ('old-alias', ?)",
                 (inflow["id"],))
    conn.commit()
    got = client.get("/api/transactions", params={"ids": f"old-alias,{old[0]},nope"}).json()
    assert got["total"] == 2 and {t["id"] for t in got["items"]} == {inflow["id"], old[0]}
    assert client.get("/api/transactions", params={"ids": ""}).json() == {"items": [], "total": 0}

    f = client.post("/api/freebies", json={"name": "v", "value": 100}).json()
    assert f["linked_used"] == f["linked_used_amount"] == 0
    conn.close()


def test_uncategorized_count_matches_list(client):
    from finapp import db
    from finapp.ingest import ingest
    from tests.backend_util import BANK
    from datetime import date
    d = date.today().replace(day=1).isoformat()  # inside the 4-month uncategorized window
    conn = db.connect()
    ingest([T(-11, "חנות א", d), T(-12, "חנות ב", d),
            T(-13, "חנות ג", d), T(-14, "מקס איט פיננ-י", d, key=BANK)],
           conn=conn)
    conn.execute("UPDATE transactions SET is_excluded=1 WHERE description='חנות ב'")
    conn.execute("UPDATE transactions SET category_id=NULL WHERE description='חנות ג'")
    conn.commit()
    ov = client.get("/api/overview").json()
    lst = client.get("/api/transactions", params={"uncategorized": 1}).json()
    assert ov["uncategorized_count"] == lst["total"] == 2  # excluded + transfer rows left out
    assert {t["description"] for t in lst["items"]} == {"חנות א", "חנות ג"}
    conn.close()
