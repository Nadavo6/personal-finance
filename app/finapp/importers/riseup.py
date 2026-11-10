"""RiseUp history export (data/riseup_transactions.csv, written by scripts/ RiseUp export).

`amount_ils` is already signed. `source` leumiBank (checkingAccount) / leumicard (creditCard).
"""

from __future__ import annotations

import csv
from pathlib import Path

from .base import Importer, RawTxn, SniffInfo, norm_text, parse_amount, parse_date

PROVIDER = "riseup"
REQUIRED = {"id", "date", "amount_ils", "business_name", "riseup_category", "source", "account_hash"}
RAW_FIELDS = [
    "is_excluded", "envelope_type", "cashflow_month", "expense_type", "category_label", "category_type",
    "actual_type", "is_income", "is_postponed", "source", "source_type", "account_hash", "account_nickname",
    "sequence_id", "commitment_id", "payment_app_addressee", "customer_comment", "origin", "original_amount",
]
KIND_BY_SOURCE_TYPE = {"checkingAccount": "bank", "creditCard": "card"}


def _bool(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("true", "1", "yes")


def _int(v: str | None) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except ValueError:
        return None


class RiseUpImporter(Importer):
    provider = PROVIDER

    @classmethod
    def sniff(cls, path: Path, info: SniffInfo) -> bool:
        return info.kind == "csv" and "amount_ils" in info.header_text and "riseup_category" in info.header_text

    def parse(self, path: Path) -> list[RawTxn]:
        out: list[RawTxn] = []
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            missing = REQUIRED - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"not a RiseUp export, missing columns: {sorted(missing)}")
            for row in reader:
                d = parse_date(row.get("date"))
                amount = parse_amount(row.get("amount_ils"))
                if d is None or amount is None:
                    continue
                source = row.get("source") or "unknown"
                kind = KIND_BY_SOURCE_TYPE.get(row.get("source_type") or "", "bank")
                is_inst = _bool(row.get("is_installment"))
                currency = (row.get("original_currency") or "").strip() or None
                raw = {k: row.get(k) for k in RAW_FIELDS if k in row}
                raw["is_excluded"] = _bool(row.get("is_excluded"))
                raw["is_income"] = _bool(row.get("is_income"))
                out.append(
                    RawTxn(
                        provider=PROVIDER,
                        account_key=f"{PROVIDER}:{source}:{row.get('account_hash') or 'unknown'}",
                        account_kind=kind,
                        date=d,
                        billing_date=parse_date(row.get("billing_date")),
                        amount=float(amount),
                        description=norm_text(row.get("business_name")),
                        original_amount=parse_amount(row.get("original_amount")) if currency else None,
                        original_currency=currency,
                        installment_no=_int(row.get("installment_number")) if is_inst else None,
                        installment_total=_int(row.get("installments_total")) if is_inst else None,
                        provider_category=norm_text(row.get("riseup_category")) or None,
                        external_id=row.get("id") or None,
                        raw=raw,
                    )
                )
        return out
