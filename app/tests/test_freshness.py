"""Data freshness per source + import date range / import_id filter. All values are invented."""

import json
from datetime import date

from fastapi.testclient import TestClient

from finapp import db, server
from finapp.freshness import freshness
from finapp.ingest import ingest
from tests.backend_util import BANK, CARD, CARD2, GIFT, RU_CARD, T, conn  # noqa: F401

ON = date(2026, 11, 10)


def test_freshness_per_source(conn):
    ingest([T(-50, "סופר", "2026-11-06", key=CARD), T(-20, "קפה", "2026-03-30", key=CARD2)],
           {"file_name": "max.xlsx", "provider": "max"}, conn=conn)
    ingest([T(-100, "ארנונה", "2026-09-20", key=BANK)],
           {"file_name": "leumi.pdf", "provider": "leumi"}, conn=conn)
    ingest([T(-30, "מתנה", "2026-01-05", key=GIFT)], conn=conn)
    ingest([T(-10, "היסטוריה", "2026-11-09", key=RU_CARD)], conn=conn)
    db.set_setting(conn, "last_sheet_import", json.dumps(
        {"file": "snapshot_x.json", "at": "2026-11-01T10:00:00", "sheet_last_updated": "2026-08-16"}))
    f = freshness(conn, ON)
    by = {s["key"]: s for s in f["sources"]}
    assert "riseup" not in by                           # history accounts are left out
    assert [s["key"] for s in f["sources"]][:2] == ["leumi", "max"]
    mx = by["max"]
    assert mx["status"] == "ok" and mx["last_date"] == "2026-11-06" and mx["days"] == 4
    assert mx["last_import"]["file_name"] == "max.xlsx"
    cards = {a["key"]: a["status"] for a in mx["accounts"]}
    assert cards == {CARD: "ok", CARD2: "idle"}          # sibling card is current: idle, not stale
    le = by["leumi"]
    assert le["status"] == "missing" and le["missing_months"] == ["2026-10", "2026-11"]
    assert by["cal"]["status"] == "optional" and not by["cal"]["monthly"]
    assert f["sheet"]["status"] == "stale" and f["sheet"]["days"] == 86
    assert f["status"] == "missing" and set(f["needs_update"]) == {"leumi", "sheet"}


def test_freshness_never_imported(conn):
    f = freshness(conn, ON)
    assert {s["key"]: s["status"] for s in f["sources"]} == {"leumi": "missing", "max": "missing"}
    assert f["sheet"]["status"] == "missing"


def test_import_reports_range_and_filters_by_import_id(tmp_path, monkeypatch):
    monkeypatch.setenv("FINAPP_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("FINAPP_DATA", str(tmp_path / "data"))
    c = db.connect()
    r1 = ingest([T(-5, "א", "2026-10-02"), T(-6, "ב", "2026-10-28")], conn=c)
    r2 = ingest([T(-7, "ג", "2026-11-01")], conn=c)
    c.close()
    assert (r1["first_date"], r1["last_date"]) == ("2026-10-02", "2026-10-28")
    cl = TestClient(server.app)
    items = cl.get("/api/transactions", params={"import_id": r2["import_id"]}).json()["items"]
    assert [t["description"] for t in items] == ["ג"]
    assert cl.get("/api/freshness").json()["sources"][0]["key"] == "leumi"
