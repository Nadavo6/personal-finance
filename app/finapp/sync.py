"""Live sync: run sync/scrape.mjs (israeli-bank-scrapers) per provider and map results to RawTxn.

``run_sync`` only fetches and maps; feeding the rows into ingest is the caller's job (server /
CLI). Providers run sequentially, one node process each, 5-minute timeout per provider. The raw
scraper JSON is kept under data/raw/sync/ for audit (data/ is gitignored).
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .importers.base import RawTxn, currency_code, norm_text

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
SYNC_DIR = ROOT / "sync"
SCRIPT = SYNC_DIR / "scrape.mjs"
DEFAULT_PROVIDERS = ["leumi", "max"]
DEFAULT_DAYS_BACK = 45
TIMEOUT_S = 300
TZ = ZoneInfo("Asia/Jerusalem")

# scraper company id -> (our provider, account kind)
PROVIDER_MAP = {
    "leumi": ("leumi", "bank"),
    "max": ("max", "card"),
    "visaCal": ("cal", "gift"),  # ingest applies the cal_account_kind setting
    "cal": ("cal", "gift"),
}


def _data_dir() -> Path:
    try:
        from . import config  # owned by the backend agent; optional here

        return Path(getattr(config, "DATA", ROOT / "data"))
    except Exception:
        return ROOT / "data"


def _to_local_date(v: Any) -> date | None:
    """Scraper dates are ISO timestamps, usually Israel midnight expressed in UTC
    ('2026-11-01T22:00:00.000Z' = 2 Nov local). Convert to Asia/Jerusalem first."""
    if not v:
        return None
    s = str(v)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(TZ).date()


def _num(v: Any) -> float | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def account_key_for(provider: str, account_number: Any) -> str:
    our, _kind = PROVIDER_MAP.get(provider, (provider, "bank"))
    num = norm_text(account_number) or "unknown"
    if our in ("max", "cal"):
        digits = "".join(ch for ch in num if ch.isdigit())
        num = digits[-4:] if len(digits) >= 4 else num
    return f"{our}:{num}"


def map_txn(provider: str, account_number: Any, t: dict) -> RawTxn | None:
    """Map one israeli-bank-scrapers transaction to RawTxn (None if it has no usable date/amount)."""
    our, kind = PROVIDER_MAP.get(provider, (provider, "bank"))
    d = _to_local_date(t.get("date"))
    processed = _to_local_date(t.get("processedDate"))
    charged = _num(t.get("chargedAmount"))
    orig = _num(t.get("originalAmount"))
    orig_cur = currency_code(t.get("originalCurrency")) if t.get("originalCurrency") else None
    status = t.get("status") or "completed"
    raw: dict[str, Any] = {
        "source": "scraper",
        "status": status,
        "type": t.get("type"),
        "memo": t.get("memo"),
        "identifier": t.get("identifier"),
        "account_number": str(account_number) if account_number is not None else None,
    }
    if charged is None or (charged == 0 and status == "pending" and orig):
        # pending rows often lack the charged amount; fall back to the ILS original
        if orig is not None and (orig_cur in (None, "ILS")):
            charged = orig
        elif orig is not None:
            charged = orig
            raw["needs_fx"] = True
    if d is None or charged is None:
        return None
    if status == "pending":
        raw["pending"] = True
    inst = t.get("installments") or {}
    ident = t.get("identifier")
    return RawTxn(
        provider=our,
        account_key=account_key_for(provider, account_number),
        account_kind=kind,
        date=d,
        billing_date=processed,
        amount=float(charged),
        description=norm_text(t.get("description")),
        original_amount=orig,
        original_currency=orig_cur,
        installment_no=int(inst["number"]) if inst.get("number") else None,
        installment_total=int(inst["total"]) if inst.get("total") else None,
        external_id=str(ident) if ident not in (None, "") else None,
        raw=raw,
    )


def map_result(entry: dict) -> list[RawTxn]:
    """Map one provider entry of scrape.mjs output to RawTxns."""
    provider = entry.get("provider") or ""
    out: list[RawTxn] = []
    for acct in entry.get("accounts") or []:
        for t in acct.get("txns") or []:
            r = map_txn(provider, acct.get("accountNumber"), t)
            if r is not None:
                out.append(r)
    return out


def _sanitize(msg: str, limit: int = 500) -> str:
    return norm_text(msg)[-limit:]


def run_sync(
    providers: list[str] | None = None,
    start: date | None = None,
    *,
    node: str = "node",
    script: Path = SCRIPT,
    timeout: int = TIMEOUT_S,
    out_dir: Path | None = None,
) -> list[dict]:
    """Scrape each provider sequentially. Returns [{name, ok, error, txns: list[RawTxn]}]."""
    providers = providers or list(DEFAULT_PROVIDERS)
    start = start or (date.today() - timedelta(days=DEFAULT_DAYS_BACK))
    out_dir = Path(out_dir) if out_dir else _data_dir() / "raw" / "sync"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results: list[dict] = []
    for p in providers:
        out_file = out_dir / f"{stamp}-{p}.json"
        res = {"name": p, "ok": False, "error": None, "txns": []}
        try:
            proc = subprocess.run(
                [node, str(script), "--providers", p, "--start", start.isoformat(), "--out", str(out_file)],
                cwd=str(Path(script).parent),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            res["error"] = f"timeout after {timeout}s"
            results.append(res)
            continue
        except FileNotFoundError:
            res["error"] = f"node not found ({node})"
            results.append(res)
            continue
        if not out_file.exists():
            res["error"] = _sanitize(proc.stderr or f"scrape.mjs exited {proc.returncode} without output")
            results.append(res)
            continue
        try:
            entries = json.loads(out_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            res["error"] = f"bad scraper output: {e}"
            results.append(res)
            continue
        entry = next((e for e in entries if e.get("provider") in (p, "visaCal" if p == "cal" else p)), None)
        if entry is None and entries:
            entry = entries[0]
        if entry is None:
            res["error"] = "empty scraper output"
        else:
            res["ok"] = bool(entry.get("ok"))
            res["error"] = entry.get("error")
            res["txns"] = map_result(entry) if res["ok"] else []
        log.info("sync %s: ok=%s txns=%d", p, res["ok"], len(res["txns"]))
        results.append(res)
    return results
