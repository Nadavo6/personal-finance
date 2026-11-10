"""Paths and default settings."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # finance/
APP_DIR = ROOT / "app"
UI_DIR = APP_DIR / "ui"


def data_dir() -> Path:
    return Path(os.environ.get("FINAPP_DATA", ROOT / "data"))


def db_path() -> Path:
    env = os.environ.get("FINAPP_DB")
    return Path(env) if env else data_dir() / "finance.db"


def inbox_dir() -> Path:
    return data_dir() / "inbox"


def imported_dir() -> Path:
    return data_dir() / "imported"


def backups_dir() -> Path:
    return data_dir() / "backups"


HOST = "127.0.0.1"
PORT = 8747

DEFAULT_SETTINGS: dict[str, str | None] = {
    "cashflow_start_day": "1",
    "buffer_months": "1",
    "overspend_pct": "120",
    "sync_providers": "leumi,max",
    "freebie_warn_days": "30",
    # forecast income baseline: net monthly salary (None -> median Salary of recent months)
    "salary_net_baseline": None,
    # add reserve duty / bonuses / other income to the forecast baseline
    "count_variable_income_in_forecast": "false",
    # share of expected rent set aside for rental-income tax (property P&L)
    "rental_tax_pct": "10",
    # Visa Cal imports: 'gift' = a prepaid gift card (rows excluded: paid by the gift), or 'card'
    "cal_account_kind": "card",
}

BACKUP_KEEP = 30
