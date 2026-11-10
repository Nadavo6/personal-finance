"""Optional personal setup (data/personal_setup.json): apply, auto-apply, export. Invented data."""

import json
from pathlib import Path

from finapp import config, db, personal_setup, rules
from finapp.__main__ import main
from finapp.ingest import ingest
from finapp.sheet_import import _mortgage_approved
from tests.backend_util import BANK, T, cat, conn, txns  # noqa: F401

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "personal_setup.example.json"
SETUP = {
    "categories": [{"name": "Renovation", "name_he": "שיפוץ", "kind": "variable", "one_off": True},
                   {"name": "Dining out", "monthly_budget": 900}],
    "rules": [{"pattern": "שיק", "account_kind": "bank", "direction": "out",
               "amount_min": 4000, "amount_max": 4800, "category": "Home rent"},
              {"pattern": "חנות שיפוצים", "category": "Renovation"},
              {"pattern": "x", "category": "No such category"}],
    "settings": {"salary_net_baseline": "12500"},
}


def _rule_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]


def test_apply_is_idempotent_and_categorises(conn):
    before = _rule_count(conn)
    r = personal_setup.apply(conn, SETUP)
    assert (r["categories_added"], r["categories_updated"], r["rules_added"]) == (1, 1, 2)
    assert r["settings_set"] == 1 and len(r["warnings"]) == 1
    assert _rule_count(conn) == before + 2
    again = personal_setup.apply(conn, SETUP)
    assert (again["categories_added"], again["categories_updated"], again["rules_added"],
            again["rules_existing"], again["settings_set"]) == (0, 0, 0, 2, 0)
    assert _rule_count(conn) == before + 2
    row = conn.execute("SELECT * FROM categories WHERE name='Renovation'").fetchone()
    assert row["one_off"] == 1 and row["kind"] == "variable"
    assert conn.execute("SELECT monthly_budget FROM categories WHERE name='Dining out'"
                        ).fetchone()[0] == 900
    ingest([T(-4400, "שיק", "2026-10-10", key=BANK), T(-300, "שיק", "2026-10-11", key=BANK),
            T(-2500, "חנות שיפוצים", "2026-10-12")], conn=conn)
    got = {(t["description"], t["amount"]): (t["category_name"], t["is_excluded"])
           for t in txns(conn)}
    assert got[("שיק", -4400)] == ("Home rent", 0)
    assert got[("שיק", -300)][0] != "Home rent"
    assert got[("חנות שיפוצים", -2500)] == ("Renovation", 1)  # one-off: excluded


def test_settings_kept_unless_overwrite(conn):
    db.set_setting(conn, "salary_net_baseline", "9000")
    r = personal_setup.apply(conn, SETUP)
    assert r["settings_kept"] == ["salary_net_baseline"]
    assert db.get_setting(conn, "salary_net_baseline") == "9000"
    personal_setup.apply(conn, SETUP, overwrite_settings=True)
    assert db.get_setting(conn, "salary_net_baseline") == "12500"


def test_fresh_database_applies_setup_file_once(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "personal_setup.json").write_text(json.dumps(SETUP), encoding="utf-8")
    monkeypatch.delenv("FINAPP_SETUP")
    monkeypatch.setenv("FINAPP_DATA", str(data))
    monkeypatch.setenv("FINAPP_DB", str(data / "finance.db"))
    assert personal_setup.setup_path() == data / "personal_setup.json"
    c = db.connect()
    assert cat(c, "Renovation") is not None
    assert db.get_setting(c, "salary_net_baseline") == "12500"
    n = _rule_count(c)
    db.migrate(c)  # an existing database is never re-applied automatically
    assert _rule_count(c) == n
    c.close()


def test_existing_database_is_not_touched_by_a_new_setup_file(conn, tmp_path, monkeypatch):
    p = tmp_path / "setup.json"
    p.write_text(json.dumps(SETUP), encoding="utf-8")
    monkeypatch.setenv("FINAPP_SETUP", str(p))
    db.migrate(conn)
    assert cat(conn, "Renovation") is None


def test_export_round_trip(conn, tmp_path, monkeypatch):
    rules.create_rule(conn, {"pattern": "מאפייה טובה", "category_id": cat(conn, "Groceries")})
    personal_setup.apply(conn, SETUP)
    out = personal_setup.export(conn)
    assert {c["name"] for c in out["categories"]} == {"Renovation", "Dining out"}
    assert {r["pattern"] for r in out["rules"]} == {"שיק", "חנות שיפוצים", "מאפייה טובה"}
    assert out["settings"] == {"salary_net_baseline": "12500"}
    # a fresh database + the export reproduces the same rules, categories and settings
    p = tmp_path / "exported.json"
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("FINAPP_SETUP", str(p))
    fresh = db.connect(tmp_path / "fresh.db")
    q_r = ("SELECT r.pattern,r.match_type,r.account_kind,r.direction,r.amount_min,r.amount_max,"
           "c.name,r.set_transfer,r.priority FROM rules r LEFT JOIN categories c ON c.id=r.category_id")
    q_c = "SELECT name,name_he,kind,monthly_budget,one_off FROM categories"
    for q in (q_r, q_c):
        assert {tuple(r) for r in conn.execute(q)} == {tuple(r) for r in fresh.execute(q)}
    assert personal_setup.export(fresh) == out
    fresh.close()


def test_fresh_database_exports_nothing(conn):
    assert personal_setup.export(conn) == {"categories": [], "rules": [], "settings": {}}


def test_example_file_applies_cleanly(conn):
    r = personal_setup.apply_file(conn, EXAMPLE)
    assert r["warnings"] == [] and r["rules_added"] == 7 and r["categories_added"] == 2


def test_cli_apply_setup(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FINAPP_DATA", str(tmp_path))
    monkeypatch.setenv("FINAPP_DB", str(tmp_path / "cli.db"))
    assert main(["apply-setup", str(tmp_path / "missing.json")]) == 1
    p = tmp_path / "s.json"
    p.write_text(json.dumps(SETUP), encoding="utf-8")
    assert main(["apply-setup", str(p), "--no-backup"]) == 0
    assert '"rules_added": 2' in capsys.readouterr().out
    assert main(["apply-setup", str(p), "--no-backup"]) == 0
    assert '"rules_added": 0' in capsys.readouterr().out
    assert config.db_path() == tmp_path / "cli.db"


def test_riseup_custom_category_extends_a_builtin(conn):
    assert rules.map_riseup_category(conn, "משכנתא הדירה ברחוב הראשי") == cat(conn, "Mortgage")
    assert rules.map_riseup_category(conn, "משכנתאות") is None


def test_mortgage_approved_key_variants():
    assert _mortgage_approved({"mortgage_approved": 500000}) == 500000
    assert _mortgage_approved({"mortgage_approved_bank": 400000}) == 400000
    assert _mortgage_approved({}) is None
