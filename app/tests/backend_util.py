"""Shared helpers for the backend tests. All data here is invented."""

from __future__ import annotations

from datetime import date

import pytest

from finapp import db
from finapp.importers.base import RawTxn

BANK = "leumi:000-00000000"  # fake account key
CARD = "max:0001"
CARD2 = "max:0002"
GIFT = "cal:0009"
RU_BANK = "riseup:leumiBank:aaaa"
RU_CARD = "riseup:leumicard:bbbb"


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("FINAPP_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("FINAPP_DATA", str(tmp_path / "data"))
    c = db.connect()
    yield c
    c.close()


def T(
    amount: float,
    desc: str,
    d: date | str,
    *,
    key: str = CARD,
    billing: date | str | None = None,
    **kw,
) -> RawTxn:
    if isinstance(d, str):
        d = date.fromisoformat(d)
    if isinstance(billing, str):
        billing = date.fromisoformat(billing)
    provider = key.split(":")[0]
    kind = kw.pop("kind", None)
    if kind is None:
        kind = {"leumi": "bank", "max": "card", "cal": "gift", "generic": "bank"}.get(provider)
        if provider == "riseup":
            kind = "bank" if "Bank" in key else "card"
    if billing is None and kind in ("card", "gift"):
        billing = d
    return RawTxn(provider=provider, account_key=key, account_kind=kind, date=d,
                  billing_date=billing, amount=amount, description=desc, **kw)


def txns(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(db.TXN_SELECT + " ORDER BY t.date, t.rowid")]


def cat(conn, name: str) -> int:
    return db.category_id(conn, name)
