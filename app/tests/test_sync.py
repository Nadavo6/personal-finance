"""Live-sync tests on synthetic scraper JSON. Never runs the real scraper or touches the Keychain:
run_sync is exercised with a fake 'node' (a Python script) that writes fixture output."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from finapp import sync
from finapp.sync import account_key_for, map_result, map_txn, run_sync

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[2]
SCRAPER_JSON = FIX / "scraper_output.json"


@pytest.fixture(scope="module")
def entries():
    if not SCRAPER_JSON.exists():
        import importlib.util

        spec = importlib.util.spec_from_file_location("make_fixtures", FIX / "make_fixtures.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.main()
    return {e["provider"]: e for e in json.loads(SCRAPER_JSON.read_text(encoding="utf-8"))}


# ============================================================================ mapping


def test_map_max(entries):
    txns = map_result(entries["max"])
    assert len(txns) == 4
    assert all(t.provider == "max" and t.account_kind == "card" and t.account_key == "max:1111" for t in txns)
    sup, inst, usd, pending = txns
    # UTC timestamps are Israel midnight -> local dates
    assert sup.date == date(2026, 10, 3) and sup.billing_date == date(2026, 11, 10)
    assert sup.amount == -100.0 and sup.description == "סופר השכונה" and sup.external_id == "5551"
    assert (inst.installment_no, inst.installment_total, inst.amount, inst.original_amount) == (2, 3, -300.0, -900.0)
    assert usd.original_currency == "USD" and usd.original_amount == -20.0 and usd.amount == -74.5
    assert usd.external_id is None
    assert pending.raw["pending"] is True and pending.amount == -55.0  # falls back to ILS original
    assert sup.raw["status"] == "completed" and "pending" not in sup.raw


def test_map_leumi(entries):
    (t,) = map_result(entries["leumi"])
    assert t.provider == "leumi" and t.account_kind == "bank" and t.account_key == "leumi:999-11112222"
    assert t.date == date(2026, 11, 1) and t.amount == 15000.0 and t.description == "העברת משכורת"


def test_map_failed_provider_is_empty(entries):
    assert map_result(entries["visaCal"]) == []


def test_account_keys_and_cal_is_gift():
    assert account_key_for("max", "1234") == "max:1234"
    assert account_key_for("visaCal", "4580-****-1234") == "cal:1234"
    t = map_txn("visaCal", "9999", {"date": "2026-10-01T21:00:00.000Z", "chargedAmount": -5, "description": "x"})
    assert t.provider == "cal" and t.account_kind == "gift" and t.date == date(2026, 10, 2)


def test_map_txn_skips_unusable():
    assert map_txn("max", "1", {"description": "no date", "chargedAmount": -1}) is None
    assert map_txn("max", "1", {"date": "2026-10-01T00:00:00Z", "description": "no amount"}) is None


# ============================================================================ run_sync plumbing

FAKE_NODE = r'''
import json, sys, time
args = sys.argv[1:]
opts = dict(zip(args[::2], args[1::2]))
log = __import__("pathlib").Path(opts["--out"]).with_suffix(".args")
log.write_text(json.dumps(args))
provider = opts["--providers"]
if provider == "slow":
    time.sleep(5)
if provider == "crash":
    sys.stderr.write("boom\n"); sys.exit(1)
entries = json.load(open(FIXTURE, encoding="utf-8"))
entry = next((e for e in entries if e["provider"] == provider),
             {"provider": provider, "ok": False, "error": "unknown", "accounts": []})
json.dump([entry], open(opts["--out"], "w", encoding="utf-8"), ensure_ascii=False)
'''


@pytest.fixture
def fake_node(tmp_path, entries):
    script = tmp_path / "fake_scrape.py"
    script.write_text(f"FIXTURE = {str(SCRAPER_JSON)!r}\n" + FAKE_NODE, encoding="utf-8")
    return script


def test_run_sync_default_providers(tmp_path, fake_node):
    res = run_sync(node=sys.executable, script=fake_node, out_dir=tmp_path / "out")
    assert [r["name"] for r in res] == ["leumi", "max"]
    assert all(r["ok"] and r["error"] is None for r in res)
    assert len(res[0]["txns"]) == 1 and len(res[1]["txns"]) == 4
    args_files = sorted((tmp_path / "out").glob("*.args"))
    assert len(args_files) == 2  # one process per provider, sequential
    args = json.loads(args_files[0].read_text())
    start = date.fromisoformat(args[args.index("--start") + 1])
    assert start == date.today() - timedelta(days=45)


def test_run_sync_failure_and_explicit_start(tmp_path, fake_node):
    res = run_sync(["visaCal"], start=date(2026, 1, 1), node=sys.executable, script=fake_node, out_dir=tmp_path)
    assert res == [{"name": "visaCal", "ok": False, "error": "INVALID_PASSWORD: bad login", "txns": []}]
    args = json.loads(next(tmp_path.glob("*.args")).read_text())
    assert args[args.index("--start") + 1] == "2026-01-01"


def test_run_sync_timeout_and_crash(tmp_path, fake_node):
    res = run_sync(["slow", "crash", "max"], node=sys.executable, script=fake_node, out_dir=tmp_path, timeout=1)
    assert res[0]["ok"] is False and "timeout" in res[0]["error"]
    assert res[1]["ok"] is False and "boom" in res[1]["error"]
    assert res[2]["ok"] is True and len(res[2]["txns"]) == 4  # later providers still run


def test_run_sync_node_missing(tmp_path):
    res = run_sync(["max"], node="/nonexistent/node", out_dir=tmp_path)
    assert res[0]["ok"] is False and "node not found" in res[0]["error"]


# ============================================================================ sync/ files


def test_package_json_pins_scraper():
    pkg = json.loads((ROOT / "sync" / "package.json").read_text())
    assert pkg["dependencies"]["israeli-bank-scrapers"] == "6.13.0"
    assert pkg["type"] == "module"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_scrape_mjs_syntax():
    p = subprocess.run(["node", "--check", str(ROOT / "sync" / "scrape.mjs")], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_scrape_mjs_contract():
    src = (ROOT / "sync" / "scrape.mjs").read_text()
    assert "combineInstallments: false" in src
    assert "finapp-${provider}" in src and "find-generic-password" in src
    assert "console.log" not in src  # nothing (credentials included) printed besides the JSON result
    assert sync.SCRIPT == ROOT / "sync" / "scrape.mjs"


def test_setup_credentials_script():
    src = (ROOT / "sync" / "setup_credentials.sh").read_text()
    assert "providers=(leumi max)" in src
    # password must be typed into security's own prompt (-w last), never passed in argv
    assert '-l "finapp $p password" -w\n' in src and '"$pass"' not in src
    p = subprocess.run(["bash", "-n", str(ROOT / "sync" / "setup_credentials.sh")], capture_output=True)
    assert p.returncode == 0
