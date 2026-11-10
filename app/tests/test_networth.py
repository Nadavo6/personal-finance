"""Net worth, sheet snapshot import, planned items. All data is invented."""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from finapp import analytics, db, networth, server
from finapp.importers.base import detect
from finapp.ingest import ingest
from finapp.sheet_import import import_sheet_snapshot
from tests.backend_util import BANK, T, conn  # noqa: F401  (fixture)

ON = date(2026, 3, 10)


def snapshot(**over) -> dict:
    s = {
        "source": "test", "sheet_last_updated": "2026-01-20",
        "assets": [
            {"key": "checking", "name": "Checking", "kind": "cash", "value": 1000,
             "as_of": "2026-01-20", "track_from_bank": True},
            {"key": "broker", "name": "Broker", "kind": "investment", "value": 50000,
             "as_of": "2026-01-20",
             "breakdown": [{"name": "Index", "value": 40000}, {"name": "Cash fund", "value": 10000}]},
            {"key": "pension", "name": "Pension", "kind": "pension", "value": 80000,
             "as_of": "2026-01-20"},
            {"key": "flat_equity", "name": "Flat equity", "kind": "property_equity",
             "value": 120000, "as_of": "2026-01-20"},
        ],
        "property": {
            "name": "Test flat", "asking_price_incl_renovation": 1200000, "bank_financed_pct": 70,
            "mortgage_approved": 770000,
            "purchase_costs": [{"item": "Tax", "amount": 9000, "note": ""}],
            "total_purchase_and_renovation_costs": 250000,
            "contributions": {"me": 130000, "family": 120000},
            "rental_income": [{"month": "2026-02", "amount": 5000},
                              {"month": "2026-03", "amount": 6000},
                              {"month": "2026-05", "amount": 6000}],
            "rental_tax_note": "tax note",
        },
        "fixed_monthly": [{"name": "Mortgage - Bank", "amount": 4000, "note": ""},
                          {"name": "Insurance", "amount": 100}],
        "future_income": [{"when": "2026-05", "amount": 7000, "source": "Refund"}],
        "future_expenses": [{"when": "2026-04-15", "amount": 2000, "what": "Tax payment"},
                            {"when": None, "amount": 9000, "what": "Undated thing"}],
        "equity_comp": [{"vests": "2027-06", "qty": 100, "type": "options", "source": "grant"}],
        "tasks": [{"title": "Call the bank", "est_monthly_saving": 20, "notes": "n"},
                  {"title": "Existing task", "est_monthly_saving": None, "notes": ""}],
        "freebies": [{"name": "Voucher A", "source": "work", "value": 300, "unit": "ILS"},
                     {"name": "Refund B", "source": "other", "value": 100, "unit": "USD"},
                     {"name": "Points C", "source": "other", "value": None, "unit": "points"},
                     {"name": "voucher a", "source": "work", "value": 1, "unit": "ILS"}],
    }
    s.update(over)
    return s


@pytest.fixture()
def snap_path(tmp_path):
    d = tmp_path / "data" / "sheet"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "snapshot_2026-01-20.json"
    p.write_text(json.dumps(snapshot()), encoding="utf-8")
    return p


def _counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("assets", "asset_values", "liabilities", "liability_values",
                      "planned_items", "properties", "equity_comp", "tasks", "freebies")}


def test_import_is_idempotent_and_dedupes(conn, snap_path):
    conn.execute("INSERT INTO tasks(title,status) VALUES ('EXISTING TASK','open')")
    conn.commit()
    r1 = import_sheet_snapshot(snap_path, conn=conn)
    assert r1["assets_added"] == 4 and r1["tasks_added"] == 1 and r1["tasks_skipped"] == 1
    assert r1["freebies_added"] == 3 and r1["freebies_skipped"] == 1  # 'voucher a' is a dup
    first = _counts(conn)
    r2 = import_sheet_snapshot(snap_path, conn=conn)
    assert _counts(conn) == first
    assert r2["values_written"] == 0 and r2["planned_added"] == 0 and r2["tasks_added"] == 0
    assert first["planned_items"] == 3 and first["liabilities"] == 1

    f = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM freebies")}
    assert f["Points C"]["value"] is None and f["Refund B"]["unit"] == "USD"
    m = dict(conn.execute("SELECT * FROM liabilities").fetchone())
    assert (m["kind"], m["principal"], m["monthly_payment"]) == ("mortgage", 770000, 4000)
    assert m["include_in_net_worth"] == 0
    p = {r["what"]: dict(r) for r in conn.execute("SELECT * FROM planned_items")}
    assert p["Tax payment"]["when_month"] == "2026-04" and p["Undated thing"]["when_month"] is None


def test_reimport_updates_value_but_keeps_user_flags(conn, snap_path):
    import_sheet_snapshot(snap_path, conn=conn)
    conn.execute("UPDATE assets SET liquid=0 WHERE key='broker'")
    s = snapshot()
    s["assets"][1].update(value=55000, as_of="2026-02-20")
    snap_path.write_text(json.dumps(s), encoding="utf-8")
    import_sheet_snapshot(snap_path, conn=conn)
    a = next(x for x in networth.assets_list(conn, ON) if x["key"] == "broker")
    assert a["value"] == 55000 and a["as_of"] == "2026-02-20" and a["liquid"] is False
    assert conn.execute("SELECT COUNT(*) FROM asset_values v JOIN assets a ON a.id=v.asset_id "
                        "WHERE a.key='broker'").fetchone()[0] == 2


def test_bank_tracked_checking_uses_bank_balance(conn, snap_path):
    ingest([T(-100, "חנות", "2026-02-27", key=BANK, balance_after=4900),
            T(-50, "חנות", "2026-03-02", key=BANK, balance_after=4850)],
           {"file_name": "t", "provider": "leumi"}, conn=conn)
    import_sheet_snapshot(snap_path, conn=conn)
    chk = next(x for x in networth.assets_list(conn, ON) if x["key"] == "checking")
    assert chk["value"] == 4850 and chk["as_of"] == "2026-03-02" and chk["value_source"] == "bank"
    assert chk["stale_days"] == 8 and chk["stale"] is False


def test_totals_by_kind_and_history(conn, snap_path):
    import_sheet_snapshot(snap_path, conn=conn)
    s = networth.summary(conn, ON)
    # no bank rows: checking falls back to the sheet value
    assert s["total"] == 1000 + 50000 + 80000 + 120000  # mortgage not counted (equity asset)
    assert s["liquid"] == 1000 + 50000
    kinds = {k["kind"]: k["total"] for k in s["by_kind"]}
    assert kinds == {"property_equity": 120000, "pension": 80000, "investment": 50000, "cash": 1000}
    broker = next(a for a in s["assets"] if a["key"] == "broker")
    assert broker["breakdown"][0] == {"name": "Index", "value": 40000}
    assert broker["stale"] is True and broker["stale_days"] == 49
    assert [h["month"] for h in s["history"]] == ["2026-01", "2026-02", "2026-03"]
    assert all(h["total"] == s["total"] for h in s["history"])
    # a later manual value shows up in the history months after it
    aid = broker["id"]
    networth.set_asset_value(conn, aid, "2026-03-01", 60000)
    hist = {h["month"]: h["total"] for h in networth.history(conn, ON)}
    assert hist["2026-03"] - hist["2026-02"] == 10000
    # including the mortgage subtracts its balance
    conn.execute("UPDATE liabilities SET include_in_net_worth=1")
    t = networth.summary(conn, ON)
    assert t["total"] == s["total"] + 10000 - 770000
    assert t["by_kind"][-1] == {"kind": "liabilities", "total": -770000}


@pytest.mark.household
def test_property_rent_expected_vs_received(conn, snap_path):
    import_sheet_snapshot(snap_path, conn=conn)
    ingest([T(8000, "העברה דיגיטל", "2026-02-05", key=BANK)],
           {"file_name": "t", "provider": "leumi"}, conn=conn)  # household rule → Rental income
    p = networth.property_view(conn, ON)
    rows = {r["month"]: r for r in p["rental_income"]}
    assert rows["2026-02"]["expected"] == 5000 and rows["2026-02"]["received"] == 8000
    assert rows["2026-03"]["received"] is None and rows["2026-05"]["future"] is True
    assert p["equity_estimate"] == 1200000 - 770000
    assert p["rent_expected_monthly"] == 6000 and p["rent_yield_pct"] == 6.0
    assert p["contributions_total"] == 250000


def test_planned_items_in_forecast_and_affordability(conn, snap_path):
    base = analytics.forecast(conn, 4, on=ON)
    import_sheet_snapshot(snap_path, conn=conn)
    conn.execute("DELETE FROM properties")  # expected rent would add to income; isolate planned
    f = analytics.forecast(conn, 4, on=ON)
    by = {m["month"]: m for m in f["months"]}
    assert by["2026-04"]["planned"] == -2000 and by["2026-05"]["planned"] == 7000
    assert by["2026-06"]["balance"] - {m["month"]: m for m in base["months"]}["2026-06"]["balance"] == 5000
    assert by["2026-04"]["safety_net"] - by["2026-04"]["balance"] == 50000  # broker only
    a = analytics.affordability(conn, 1000, "2026-05", on=ON)
    assert a["planned_in_range"] == 5000
    assert a["safety_net"] - a["projected_free"] == 50000
    assert a["path"][-1]["safety_net"] == a["safety_net"]
    # done items drop out
    conn.execute("UPDATE planned_items SET done=1 WHERE what='Refund'")
    assert analytics.affordability(conn, 1000, "2026-05", on=ON)["planned_in_range"] == -2000


def test_detect_rejects_json(tmp_path):
    p = tmp_path / "snapshot_x.json"
    p.write_text('{"assets": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="import-sheet"):
        detect(p)


# ---------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINAPP_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FINAPP_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(exist_ok=True)
    return TestClient(server.app)


def test_api_smoke(client, snap_path):
    nw = client.get("/api/networth").json()
    assert nw["total"] == 0 and nw["assets"] == [] and nw["property"] is None
    assert client.get("/api/overview").json()["net_worth"] is None

    r = client.post("/api/networth/import-sheet", json={})
    assert r.status_code == 200 and r.json()["assets_added"] == 4
    nw = client.get("/api/networth").json()
    assert set(nw) >= {"total", "liquid", "by_kind", "assets", "liabilities", "history",
                       "property", "planned", "equity_comp"}
    assert nw["equity_comp"][0]["qty"] == 100
    assert isinstance(nw["liabilities"], list) and nw["liabilities_total"] == 0
    assert set(nw["assets"][0]) >= {"id", "key", "name", "kind", "value", "as_of", "breakdown",
                                    "stale_days", "liquid"}
    ov = client.get("/api/overview").json()["net_worth"]
    assert ov["total"] == nw["total"] and ov["liquid"] == nw["liquid"]

    # assets CRUD + dated value
    a = client.post("/api/assets", json={"name": "Savings box", "kind": "cash", "value": 700,
                                         "as_of": "2026-03-01"}).json()
    assert a["value"] == 700 and a["liquid"] is True and a["key"] == "savings_box"
    assert client.post("/api/assets", json={"name": "x", "kind": "boat"}).status_code == 422
    a = client.post(f"/api/assets/{a['id']}/value", json={"as_of": "2026-03-05", "value": 900}).json()
    assert a["value"] == 900 and a["as_of"] == "2026-03-05"
    a = client.patch(f"/api/assets/{a['id']}", json={"include_in_net_worth": False}).json()
    assert a["include_in_net_worth"] is False
    assert client.delete(f"/api/assets/{a['id']}").status_code == 200
    assert client.patch(f"/api/assets/{a['id']}", json={"name": "y"}).status_code == 404

    # liabilities
    lb = client.post("/api/liabilities", json={"name": "Car loan", "kind": "loan",
                                               "principal": 20000, "as_of": "2026-02-01"}).json()
    assert lb["balance"] == 20000
    lb = client.post(f"/api/liabilities/{lb['id']}/value", json={"as_of": "2026-03-01",
                                                                 "balance": 19000}).json()
    assert lb["balance"] == 19000
    total_with = client.get("/api/networth").json()["total"]
    assert total_with == nw["total"] - 19000
    assert client.delete(f"/api/liabilities/{lb['id']}").status_code == 200

    # planned items
    p = client.post("/api/planned", json={"kind": "expense", "what": "Trip", "amount": -3000,
                                          "when_month": "2027-02"}).json()
    assert p["amount"] == 3000 and p["signed_amount"] == -3000 and p["done"] is False
    assert client.post("/api/planned", json={"kind": "expense", "what": "x",
                                             "when_month": "Feb"}).status_code == 422
    p = client.patch(f"/api/planned/{p['id']}", json={"done": True}).json()
    assert p["done"] is True
    assert any(x["what"] == "Trip" for x in client.get("/api/planned").json())
    assert client.delete(f"/api/planned/{p['id']}").status_code == 200

    # forecast / affordability expose the safety-net line
    f = client.get("/api/forecast?months=3").json()
    assert "safety_net" in f["months"][0] and "safety_net_now" in f
    af = client.get("/api/affordability?amount=100").json()
    assert {"safety_net", "safety_net_after", "covered_by_safety_net"} <= set(af)

    # bad file name stays inside data/sheet
    assert client.post("/api/networth/import-sheet",
                       json={"file": "../../nope.json"}).status_code == 404
    # JSON uploaded as a statement is rejected with a pointer to import-sheet
    r = client.post("/api/import", files={"file": ("s.json", b'{"assets": []}', "application/json")})
    assert r.status_code == 422 and "import-sheet" in r.json()["detail"]


def test_example_snapshot_imports(conn, tmp_path):
    from pathlib import Path
    ex = Path(__file__).resolve().parents[2] / "examples" / "sheet_snapshot.example.json"
    res = import_sheet_snapshot(ex, conn=conn)
    assert res["sheet_last_updated"] == "2026-10-31"
    s = networth.summary(conn, ON)
    assert len(s["assets"]) == 5 and s["property"]["mortgage"]["principal"] == 980000
