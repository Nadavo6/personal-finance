"""Goal contribution schedule: step-ups, fixed-end-linked amounts, projected completion,
per-month goals_reserved / forecast; and the uncategorized-count window. All data invented."""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from finapp import analytics as an
from finapp import db, server
from finapp.ingest import ingest
from tests.backend_util import BANK, CARD, T, cat, conn  # noqa: F401

ON = date(2026, 11, 15)
HIST = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]


def _goal(**kw) -> dict:
    g = {"name": "flat", "target_amount": 20000, "target_date": "2027-06-30",
         "saved_amount": 10000, "monthly_contribution": 1000, "status": "active",
         "created_at": "2026-10-01",
         "step_ups": json.dumps([{"from_month": "2027-01", "add_amount": 500,
                                  "reason": "loan ends", "fixed_end_id": None}])}
    g.update(kw)
    return g


# ---------------------------------------------------------------- schedule math

def test_step_up_schedule_and_projection():
    f = an.goal_fields(_goal(), ON)
    by = {x["month"]: x["amount"] for x in f["contribution_by_month"]}
    assert len(f["contribution_by_month"]) == 12
    assert f["contribution_by_month"][0]["month"] == "2026-11"
    assert f["contribution_now"] == 1000
    assert by["2026-12"] == 1000 and by["2027-01"] == 1500
    # 10,000 saved: 1,000 ×2, then 1,500 ×5 = 9,500 by May; June only needs the last 500
    assert by["2027-05"] == 1500 and by["2027-06"] == 500 and by["2027-07"] == 0
    assert f["projected_completion_month"] == "2027-06"
    assert f["projected_vs_target"] == "on time" and f["on_track"] is True
    assert f["step_ups"][0]["add_amount"] == 500 and f["step_ups"][0]["amount_source"] == "manual"

    late = an.goal_fields(_goal(target_date="2027-04-30"), ON)
    assert late["projected_vs_target"] == "2 months late" and late["on_track"] is False
    early = an.goal_fields(_goal(target_date="2027-07-31"), ON)
    assert early["projected_vs_target"] == "1 month early" and early["on_track"] is True

    # no plan at all: contributions = monthly need, on_track by the straight-line expectation
    plain = an.goal_fields(_goal(monthly_contribution=None, step_ups=None,
                                 target_date="2027-07-01", saved_amount=0, target_amount=4000,
                                 created_at="2026-11-01"), ON)
    assert plain["contribution_now"] == 500 and plain["has_plan"] is False
    assert plain["on_track"] is False

    # a plan that never gets there
    never = an.goal_fields(_goal(monthly_contribution=0, step_ups=None), ON)
    assert never["projected_completion_month"] is None
    assert never["projected_vs_target"] == "not reached" and never["on_track"] is False

    # reached already: nothing more to put away
    done = an.goal_fields(_goal(saved_amount=20000), ON)
    assert done["contribution_now"] == 0 and done["projected_completion_month"] == "2026-11"


# ---------------------------------------------------------------- fixed-end link + reserved

def _tuition_history(conn):
    rows = []
    for m in HIST:
        rows += [T(9000, "משכורת חברה", f"{m}-01", key=BANK),
                 T(-2000, "מכללת בדיקה", f"{m}-03", key=BANK)]
    ingest(rows, conn=conn)
    return conn.execute("INSERT INTO fixed_ends(category_id,last_month,created_at) VALUES (?,?,?)",
                        (cat(conn, "Education"), "2026-12", db.now_iso())).lastrowid


def _insert_goal(conn, fid, add_amount=None):
    steps = [{"from_month": "2027-01", "add_amount": add_amount, "fixed_end_id": fid,
              "reason": "tuition ends"}]
    return conn.execute(
        "INSERT INTO goals(name,target_amount,target_date,saved_amount,monthly_contribution,"
        "status,created_at,step_ups) VALUES ('flat',100000,'2027-11-30',50000,3000,'active',"
        "'2026-10-01',?)", (json.dumps(steps),)).lastrowid


@pytest.mark.household
def test_fixed_end_linked_step_up_and_reserved_per_month(conn):
    fid = _tuition_history(conn)
    gid = _insert_goal(conn, fid, add_amount=1234)  # stale snapshot: the live charge wins
    g = an.goal_fields(dict(conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()),
                       ON, conn=conn)
    st = g["step_ups"][0]
    assert st["add_amount"] == 2000 and st["amount_source"] == "fixed_end"
    assert st["fixed_end"]["what"] == "Education" and st["saved_add_amount"] == 1234
    by = {x["month"]: x["amount"] for x in g["contribution_by_month"]}
    assert g["contribution_now"] == 3000 and by["2026-12"] == 3000 and by["2027-01"] == 5000

    assert an.goals_reserved(conn, ON) == 3000
    assert an.goals_reserved(conn, ON, "2026-12") == 3000
    assert an.goals_reserved(conn, ON, "2027-01") == 5000
    assert an.goals_reserved(conn, ON, "2026-09") == 3000  # a past month counts as now
    s = an.month_summary(conn, "2026-11", ON)
    assert s["goals_reserved"] == 3000
    assert s["goal_progress"][0]["contribution_now"] == 3000
    assert an.month_summary(conn, "2027-01", ON)["goal_progress"][0]["contribution"] == 5000

    f = an.forecast(conn, 3, ON)
    fm = {m["month"]: m for m in f["months"]}
    assert fm["2026-12"]["goals"] == 3000 and fm["2027-01"]["goals"] == 5000
    # the freed tuition moves to the goal: Jan's net equals Dec's
    assert fm["2027-01"]["fixed_ended"] == 2000
    assert fm["2027-01"]["net"] == pytest.approx(fm["2026-12"]["net"])
    a = an.affordability(conn, 0, "2027-01", ON)
    assert a["assumptions"]["goal_contributions"] == 3000 and len(a["path"]) == 3


def test_fixed_end_without_amount_falls_back_to_saved(conn):
    # no tuition history: the fixed end has no monthly amount, the saved snapshot applies
    fid = conn.execute("INSERT INTO fixed_ends(category_id,last_month,created_at) VALUES (?,?,?)",
                       (cat(conn, "Education"), "2026-12", db.now_iso())).lastrowid
    gid = _insert_goal(conn, fid, add_amount=800)
    g = an.goal_fields(dict(conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()),
                       ON, conn=conn)
    assert g["step_ups"][0]["add_amount"] == 800
    assert g["step_ups"][0]["amount_source"] == "fixed_end_saved"
    assert an.goals_reserved(conn, ON, "2027-02") == 3800


def test_goal_stops_contributing_once_reached_in_forecast(conn):
    conn.execute("INSERT INTO goals(name,target_amount,target_date,saved_amount,"
                 "monthly_contribution,status,created_at) VALUES ('small',2500,'2027-12-31',0,"
                 "1000,'active','2026-10-01')")
    f = an.forecast(conn, 4, ON)
    assert [m["goals"] for m in f["months"]] == [1000, 500, 0, 0]  # Nov 1,000 already counted


# ---------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINAPP_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FINAPP_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return TestClient(server.app)


@pytest.mark.household
def test_api_goal_step_ups(client):
    c = db.connect()
    cur = an.current_month(c)
    hist = [an.add_months(cur, -k) for k in range(1, 7)]
    ingest([T(-2000, "מכללת בדיקה", f"{m}-03", key=BANK) for m in hist], conn=c)
    c.commit()
    cats = {x["name"]: x["id"] for x in client.get("/api/categories").json()}
    end_m, step_m = an.add_months(cur, 1), an.add_months(cur, 2)
    fe = client.post("/api/fixed-ends", json={"category_id": cats["Education"],
                                              "last_month": end_m}).json()
    assert fe["monthly_amount"] == 2000
    body = {"name": "flat", "target_amount": 60000, "saved_amount": 40000,
            "monthly_contribution": 3000, "target_date": f"{an.add_months(cur, 6)}-28",
            "step_ups": [{"from_month": step_m, "fixed_end_id": fe["id"], "reason": "tuition"}]}
    g = client.post("/api/goals", json=body).json()
    stored = json.loads(c.execute("SELECT step_ups FROM goals WHERE id=?", (g["id"],)).fetchone()[0])
    assert stored[0]["add_amount"] == 2000 and stored[0]["fixed_end_id"] == fe["id"]
    assert g["contribution_now"] == 3000 and len(g["contribution_by_month"]) == 12
    assert g["contribution_by_month"][2] == {"month": step_m, "amount": 5000}
    # 20,000 to go: 3,000 ×2 then 5,000 ×3 = 21,000 → reached in the 5th month
    assert g["projected_completion_month"] == an.add_months(cur, 4)
    assert g["projected_vs_target"] == "2 months early" and g["on_track"] is True
    assert {"contribution_by_month", "contribution_now", "projected_completion_month",
            "projected_vs_target"} <= set(client.get("/api/goals").json()[0])

    bad = client.patch(f"/api/goals/{g['id']}", json={"step_ups": [{"from_month": "2027-13",
                                                                    "add_amount": 5}]})
    assert bad.status_code == 422
    assert client.patch(f"/api/goals/{g['id']}", json={"step_ups": [
        {"from_month": step_m, "fixed_end_id": 9999}]}).status_code == 422
    assert client.patch(f"/api/goals/{g['id']}", json={"step_ups": [
        {"from_month": step_m}]}).status_code == 422
    manual = client.patch(f"/api/goals/{g['id']}", json={"step_ups": [
        {"from_month": step_m, "add_amount": "750"}]}).json()
    assert manual["step_ups"][0]["add_amount"] == 750
    cleared = client.patch(f"/api/goals/{g['id']}", json={"step_ups": []}).json()
    assert cleared["step_ups"] == [] and cleared["contribution_by_month"][5]["amount"] == 3000
    c.close()


def test_uncategorized_count_ignores_rows_older_than_three_months(client):
    c = db.connect()
    cur = date.today().strftime("%Y-%m")
    inside = f"{an.add_months(cur, -3)}-01"   # first day of current month − 3: counted
    outside = f"{an.add_months(cur, -4)}-28"  # older: not counted
    ingest([T(-11, "חנות חדשה", f"{cur}-01"), T(-12, "חנות גבול", inside),
            T(-13, "חנות ישנה", outside)], conn=c)
    c.commit()
    ov = client.get("/api/overview").json()
    lst = client.get("/api/transactions", params={"uncategorized": 1}).json()
    assert ov["uncategorized_count"] == lst["total"] == 2
    assert {t["description"] for t in lst["items"]} == {"חנות חדשה", "חנות גבול"}
    c.close()
