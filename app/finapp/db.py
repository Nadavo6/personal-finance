"""SQLite schema, migrations, seeds and small helpers."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import config

# ---------------------------------------------------------------- normalizer

_BIDI = re.compile("[‎‏‪-‮⁦-⁩﻿]")
_YOD_SUFFIX = re.compile(r"-\s*י(?=\s|$)")
_NON_LETTER = re.compile(r"[^א-תa-zA-Z]+")


def strip_bidi(s: str) -> str:
    return _BIDI.sub("", s or "")


def normalize_merchant(desc: str | None) -> str:
    """Normalize a merchant/description for matching.

    Strips bidi marks, the `-י` suffix (e.g. `פיננ-י`), digits / branch numbers, `/` and all
    other punctuation, collapses spaces, lower-cases latin. Keeps Hebrew and latin letters.
    """
    s = strip_bidi(desc or "")
    s = _YOD_SUFFIX.sub(" ", s)
    s = _NON_LETTER.sub(" ", s)
    s = " ".join(s.split()).lower()
    return s


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- schema

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,
  provider TEXT,
  kind TEXT CHECK (kind IN ('bank','card','gift')),
  name TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  name_he TEXT,
  kind TEXT CHECK (kind IN ('income','fixed','variable','tracking','savings','transfer')),
  monthly_budget REAL,
  color TEXT,
  archived INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
  id TEXT PRIMARY KEY,
  account_id INTEGER REFERENCES accounts(id),
  date TEXT NOT NULL,
  billing_date TEXT,
  cashflow_month TEXT,
  amount REAL NOT NULL,
  original_amount REAL,
  original_currency TEXT,
  description TEXT,
  merchant TEXT,
  category_id INTEGER REFERENCES categories(id),
  category_source TEXT,
  is_transfer INTEGER DEFAULT 0,
  is_excluded INTEGER DEFAULT 0,
  installment_no INTEGER,
  installment_total INTEGER,
  provider_category TEXT,
  notes TEXT,
  import_id INTEGER,
  created_at TEXT,
  updated_at TEXT,
  -- extensions (not in the API shape)
  riseup_category TEXT,       -- RiseUp category (own rows, or carried over from a merged RiseUp row)
  base_excluded INTEGER DEFAULT 0,  -- excluded by source (gift account / RiseUp one-off)
  flags_manual INTEGER DEFAULT 0,   -- is_transfer / is_excluded set by the user
  balance_after REAL,
  external_id TEXT,
  merged_riseup_id TEXT       -- id of the RiseUp row this native row replaced
);
CREATE INDEX IF NOT EXISTS ix_txn_month ON transactions(cashflow_month);
CREATE INDEX IF NOT EXISTS ix_txn_date ON transactions(date);
CREATE INDEX IF NOT EXISTS ix_txn_amount ON transactions(amount);
CREATE INDEX IF NOT EXISTS ix_txn_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS ix_txn_merchant ON transactions(merchant);

-- ids of rows collapsed into another row (cross-source dedupe), so re-imports stay dups
CREATE TABLE IF NOT EXISTS txn_aliases (alias_id TEXT PRIMARY KEY, txn_id TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS rules (
  id INTEGER PRIMARY KEY,
  pattern TEXT NOT NULL,
  match_type TEXT CHECK (match_type IN ('contains','equals','regex')) DEFAULT 'contains',
  account_kind TEXT,
  amount_min REAL,
  amount_max REAL,
  category_id INTEGER REFERENCES categories(id),
  set_transfer INTEGER,
  set_excluded INTEGER,
  priority INTEGER DEFAULT 0,
  hits INTEGER DEFAULT 0,
  created_at TEXT,
  enabled INTEGER DEFAULT 1,
  system INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  target_amount REAL NOT NULL,
  target_date TEXT,
  saved_amount REAL DEFAULT 0,
  monthly_contribution REAL,
  priority INTEGER DEFAULT 0,
  status TEXT CHECK (status IN ('active','done','archived')) DEFAULT 'active',
  notes TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS freebies (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  source TEXT DEFAULT 'other',
  value REAL,
  unit TEXT DEFAULT 'ILS',
  received_date TEXT,
  expiry_date TEXT,
  used_amount REAL DEFAULT 0,
  status TEXT CHECK (status IN ('active','used','expired')) DEFAULT 'active',
  where_to_use TEXT,
  notes TEXT,
  created_at TEXT,
  account_id INTEGER REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY,
  file_name TEXT,
  provider TEXT,
  rows INTEGER,
  new_rows INTEGER,
  dup_rows INTEGER,
  imported_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  notes TEXT,
  due_date TEXT,
  status TEXT CHECK (status IN ('open','done','dropped')) DEFAULT 'open',
  priority TEXT CHECK (priority IN ('high','med','low')) DEFAULT 'med',
  est_monthly_saving REAL,
  actual_monthly_saving REAL,
  merchant TEXT,
  category_id INTEGER,
  insight_key TEXT,
  created_at TEXT,
  done_at TEXT
);

CREATE TABLE IF NOT EXISTS insight_dismissals (
  insight_key TEXT PRIMARY KEY,
  dismissed_at TEXT,
  reason TEXT
);

CREATE TABLE IF NOT EXISTS manual_insights (
  key TEXT PRIMARY KEY,
  type TEXT DEFAULT 'ai_review',
  severity TEXT DEFAULT 'med',
  title TEXT NOT NULL,
  detail TEXT,
  est_monthly_saving REAL,
  est_yearly_saving REAL,
  txn_ids TEXT,          -- JSON array
  merchant TEXT,
  category_id INTEGER,
  suggested_task TEXT,   -- JSON object or plain title
  created_at TEXT
);
"""

# (name, name_he, kind, color)
SEED_CATEGORIES: list[tuple[str, str, str, str]] = [
    # income
    ("Salary", "משכורת", "income", "#2e7d32"),
    ("Reserve duty income", "מילואים", "income", "#558b2f"),
    ("Refunds", "החזרים", "income", "#7cb342"),
    ("Other income", "הכנסה אחרת", "income", "#9ccc65"),
    # fixed
    ("Rent & tuition", "שכירות ושכר לימוד", "fixed", "#5d4037"),  # renamed "Home rent" in v6
    ("Mortgage", "משכנתא", "fixed", "#6d4c41"),
    ("Arnona", "ארנונה", "fixed", "#795548"),
    ("Electricity & gas", "חשמל וגז", "fixed", "#8d6e63"),
    ("Water", "מים", "fixed", "#4fc3f7"),
    ("Internet & mobile", "תקשורת", "fixed", "#0288d1"),
    ("Insurance", "ביטוח", "fixed", "#3949ab"),
    ("Subscriptions", "מנויים", "fixed", "#5e35b1"),
    ("Digital", "דיגיטל", "fixed", "#7e57c2"),
    ("Loan", "הלוואה", "fixed", "#8e24aa"),
    ("Education", "חינוך", "fixed", "#ab47bc"),
    ("Health", "בריאות", "fixed", "#ec407a"),
    ("Fees", "עמלות", "fixed", "#b0bec5"),
    # variable
    ("Groceries", "סופר", "variable", "#43a047"),
    ("Dining out", "אוכל בחוץ", "variable", "#fb8c00"),
    ("Car & fuel", "רכב ודלק", "variable", "#f4511e"),
    ("Public transport", "תחבורה ציבורית", "variable", "#ff7043"),
    ("Shopping", "קניות", "variable", "#e53935"),
    ("Entertainment", "פנאי ובילויים", "variable", "#d81b60"),
    ("Sports", "ספורט", "variable", "#00897b"),
    ("Personal care", "טיפוח", "variable", "#f06292"),
    ("Gifts", "מתנות", "variable", "#ffb300"),
    ("Donations", "תרומה", "variable", "#fdd835"),
    ("Travel", "תיירות", "variable", "#26c6da"),
    ("Pets", "חיות מחמד", "variable", "#a1887f"),
    ("Home", "בית", "variable", "#8d6e63"),
    ("Cash withdrawal", "מזומן", "variable", "#9e9e9e"),
    ("Checks", "שיק", "variable", "#757575"),
    ("Transfers to people", "העברות", "variable", "#90a4ae"),
    ("Payments", "תשלומים", "variable", "#78909c"),
    ("General", "כללי", "tracking", "#bdbdbd"),
    ("Other", "אחר", "tracking", "#9e9e9e"),
    # savings / transfers
    ("Savings & investments", "השקעה וחיסכון", "savings", "#1e88e5"),
    ("Card payment", "חיוב כרטיס אשראי", "transfer", "#cfd8dc"),
    ("Internal transfer", "העברה בין חשבונות", "transfer", "#cfd8dc"),
    ("Cash deposit", "הפקדת מזומן", "transfer", "#cfd8dc"),
    ("Uncategorized", "לא מסווג", "tracking", "#e0e0e0"),
]

# System rules (seeded, editable). Patterns are matched against the normalized merchant.
# (pattern, match_type, account_kind, category name, set_transfer)
SEED_RULES: list[tuple[str, str, str | None, str, int | None]] = [
    # card bill payments on the bank account -> transfers (rule 1)
    ("מקס איט", "contains", "bank", "Card payment", 1),
    ("לאומי ויזה", "contains", "bank", "Card payment", 1),
    ("לאומי קארד", "contains", "bank", "Card payment", 1),
    (r"\bויזה\b", "regex", "bank", "Card payment", 1),
    (r"\bכאל\b", "regex", "bank", "Card payment", 1),
    ("ישראכרט", "contains", "bank", "Card payment", 1),
    ("אמריקן אקספרס", "contains", "bank", "Card payment", 1),
    ("דיינרס", "contains", "bank", "Card payment", 1),
    # internal transfers between own accounts
    ("העברה בין חשבונות", "contains", "bank", "Internal transfer", 1),
    ("העברה עצמית", "contains", "bank", "Internal transfer", 1),
    # savings deposits / withdrawals -> savings (not expense)
    ("הפקדה לפיקדון", "contains", "bank", "Savings & investments", None),
    ("הפקדה לפקדון", "contains", "bank", "Savings & investments", None),
    ("העברה לחסכון", "contains", "bank", "Savings & investments", None),
    ("העברה לחיסכון", "contains", "bank", "Savings & investments", None),
    ("משיכה מפיקדון", "contains", "bank", "Savings & investments", None),
    ("משיכת פקדון", "contains", "bank", "Savings & investments", None),
    ("פירעון פיקדון", "contains", "bank", "Savings & investments", None),
]
SYSTEM_RULE_PRIORITY = 100


def _migrate_1(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_V1)
    for name, he, kind, color in SEED_CATEGORIES:
        conn.execute(
            "INSERT OR IGNORE INTO categories(name,name_he,kind,color) VALUES (?,?,?,?)",
            (name, he, kind, color),
        )
    ts = now_iso()
    for pattern, mt, ak, cat, st in SEED_RULES:
        cid = conn.execute("SELECT id FROM categories WHERE name=?", (cat,)).fetchone()[0]
        conn.execute(
            "INSERT INTO rules(pattern,match_type,account_kind,category_id,set_transfer,priority,"
            "hits,created_at,enabled,system) VALUES (?,?,?,?,?,?,0,?,1,1)",
            (pattern, mt, ak, cid, st, SYSTEM_RULE_PRIORITY, ts),
        )
    for k, v in config.DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))


# Added in schema v2: categories the Max provider taxonomy needs. Leisure, home and beauty map
# onto the existing Entertainment / Home / Personal care categories instead of duplicating them.
SEED_CATEGORIES_V2: list[tuple[str, str, str, str]] = [
    ("Clothing", "אופנה", "variable", "#c2185b"),
    ("Electronics", "חשמל ומחשבים", "variable", "#546e7a"),
    ("Books & print", "ספרים ודפוס", "variable", "#8e7cc3"),
    ("Government & municipal", "עירייה וממשלה", "variable", "#607d8b"),
]

# Default description rules (v2), seeded only when no rule with the same pattern exists.
# (pattern, match_type, account_kind, category name)
SEED_RULES_V2: list[tuple[str, str, str | None, str]] = [
    ("מענק מילואים", "contains", "bank", "Reserve duty income"),
    ('מופ"ת מילואי', "contains", "bank", "Reserve duty income"),
    ("משכורת", "contains", "bank", "Salary"),
]


def _migrate_2(conn: sqlite3.Connection) -> None:
    for name, he, kind, color in SEED_CATEGORIES_V2:
        conn.execute(
            "INSERT OR IGNORE INTO categories(name,name_he,kind,color) VALUES (?,?,?,?)",
            (name, he, kind, color),
        )
    conn.execute("UPDATE categories SET kind='fixed' WHERE name='Education'")
    ts = now_iso()
    for pattern, mt, ak, cat in SEED_RULES_V2:
        if conn.execute("SELECT 1 FROM rules WHERE pattern=?", (pattern,)).fetchone():
            continue
        cid = conn.execute("SELECT id FROM categories WHERE name=?", (cat,)).fetchone()
        if cid is None:
            continue
        conn.execute(
            "INSERT INTO rules(pattern,match_type,account_kind,category_id,priority,"
            "hits,created_at,enabled,system) VALUES (?,?,?,?,?,0,?,1,1)",
            (pattern, mt, ak, cid[0], SYSTEM_RULE_PRIORITY, ts),
        )


# Added in schema v3. Categories with one_off=1 are one-off events: their rows are excluded
# (is_excluded=1) from monthly income/expense/overspend but stay visible. One-off event
# categories (and household-specific rules) come from data/personal_setup.json, not from here.
# (name, name_he, kind, color, one_off)
SEED_CATEGORIES_V3: list[tuple[str, str, str, str, int]] = [
    ("Rental income", "הכנסה משכירות", "income", "#33691e", 0),
]

# (pattern, match_type, account_kind, direction, amount_min, amount_max, category name)
# direction: 'in' = amount > 0, 'out' = amount < 0; amount_min/max bound the absolute amount.
SEED_RULES_V3: list[tuple] = []


def _migrate_3(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(categories)")}
    if "one_off" not in cols:
        conn.execute("ALTER TABLE categories ADD COLUMN one_off INTEGER DEFAULT 0")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(rules)")}
    if "direction" not in cols:
        conn.execute("ALTER TABLE rules ADD COLUMN direction TEXT")
    for name, he, kind, color, one_off in SEED_CATEGORIES_V3:
        conn.execute(
            "INSERT OR IGNORE INTO categories(name,name_he,kind,color,one_off) VALUES (?,?,?,?,?)",
            (name, he, kind, color, one_off),
        )
    ts = now_iso()
    for pattern, mt, ak, direction, amin, amax, cat in SEED_RULES_V3:
        if conn.execute("SELECT 1 FROM rules WHERE pattern=? AND direction IS ?",
                        (pattern, direction)).fetchone():
            continue
        cid = conn.execute("SELECT id FROM categories WHERE name=?", (cat,)).fetchone()
        if cid is None:
            continue
        conn.execute(
            "INSERT INTO rules(pattern,match_type,account_kind,direction,amount_min,amount_max,"
            "category_id,priority,hits,created_at,enabled,system) "
            "VALUES (?,?,?,?,?,?,?,?,0,?,1,1)",
            (pattern, mt, ak, direction, amin, amax, cid[0], SYSTEM_RULE_PRIORITY, ts),
        )


def _migrate_4(conn: sqlite3.Connection) -> None:
    # which account the aliased (merged-away) row belonged to, e.g. a RiseUp card account
    cols = {r[1] for r in conn.execute("PRAGMA table_info(txn_aliases)")}
    if "account_id" not in cols:
        conn.execute("ALTER TABLE txn_aliases ADD COLUMN account_id INTEGER")


ASSET_KINDS = ("cash", "investment", "pension", "study_fund", "property_equity", "property",
               "equity_comp", "other")
LIABILITY_KINDS = ("mortgage", "loan", "other")

SCHEMA_V5 = f"""
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  kind TEXT CHECK (kind IN ({",".join(repr(k) for k in ASSET_KINDS)})) DEFAULT 'other',
  include_in_net_worth INTEGER DEFAULT 1,
  liquid INTEGER,
  notes TEXT,
  source TEXT CHECK (source IN ('sheet','manual','bank')) DEFAULT 'manual',
  track_from_bank INTEGER DEFAULT 0,   -- value = latest bank balance (transactions.balance_after)
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS asset_values (
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  as_of TEXT NOT NULL,
  value REAL NOT NULL,
  breakdown_json TEXT,
  source TEXT,
  PRIMARY KEY (asset_id, as_of)
);
CREATE TABLE IF NOT EXISTS liabilities (
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  kind TEXT CHECK (kind IN ({",".join(repr(k) for k in LIABILITY_KINDS)})) DEFAULT 'other',
  principal REAL,
  rate_pct REAL,
  start_month TEXT,
  monthly_payment REAL,
  notes TEXT,
  include_in_net_worth INTEGER DEFAULT 1,
  source TEXT DEFAULT 'manual',
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS liability_values (
  liability_id INTEGER NOT NULL REFERENCES liabilities(id) ON DELETE CASCADE,
  as_of TEXT NOT NULL,
  balance REAL NOT NULL,
  source TEXT,
  PRIMARY KEY (liability_id, as_of)
);
CREATE TABLE IF NOT EXISTS planned_items (
  id INTEGER PRIMARY KEY,
  kind TEXT CHECK (kind IN ('income','expense')) NOT NULL,
  when_month TEXT,          -- 'YYYY-MM' or NULL (undated)
  amount REAL NOT NULL,     -- positive; the sign comes from kind
  what TEXT NOT NULL,
  notes TEXT,
  done INTEGER DEFAULT 0,
  source TEXT DEFAULT 'manual',
  created_at TEXT
);
-- one row per property; purchase costs / contributions / expected rent live in data_json
CREATE TABLE IF NOT EXISTS properties (
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,
  name TEXT,
  data_json TEXT,
  source TEXT DEFAULT 'sheet',
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS equity_comp (
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,  -- '<type>:<vests>'
  vests TEXT,                -- 'YYYY-MM' or 'YYYY-MM-DD'
  qty REAL,
  type TEXT,                 -- 'shares' | 'options' | ...
  notes TEXT,
  value REAL,                -- optional ILS estimate (not counted in net worth)
  source TEXT DEFAULT 'sheet',
  created_at TEXT
);
"""


def _migrate_5(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_V5)


# Added in schema v6: "Rent & tuition" becomes "Home rent" (tuition lives in Education). Rent
# rules that match a check or standing order by amount band are household-specific, so they
# live in data/personal_setup.json. goals.asset_id links a goal to an asset; fixed_ends lists
# fixed charges with a known last month (the forecast drops them afterwards).
# (pattern, match_type, account_kind, direction, amount_min, amount_max, category name)
SEED_RULES_V6: list[tuple] = []

SCHEMA_V6 = """
CREATE TABLE IF NOT EXISTS fixed_ends (
  id INTEGER PRIMARY KEY,
  category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
  merchant TEXT,             -- normalized-merchant substring; NULL = the whole category
  last_month TEXT NOT NULL,  -- 'YYYY-MM': last month the charge is expected
  notes TEXT,
  created_at TEXT
);
"""


def _migrate_6(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_V6)
    has_home = conn.execute("SELECT 1 FROM categories WHERE name='Home rent'").fetchone()
    if not has_home and conn.execute(
            "SELECT 1 FROM categories WHERE name='Rent & tuition'").fetchone():
        conn.execute("UPDATE categories SET name='Home rent', name_he='שכירות', kind='fixed' "
                     "WHERE name='Rent & tuition'")
    else:
        conn.execute("INSERT OR IGNORE INTO categories(name,name_he,kind,color) "
                     "VALUES ('Home rent','שכירות','fixed','#5d4037')")
    conn.execute("INSERT OR IGNORE INTO categories(name,name_he,kind,color) "
                 "VALUES ('Education','חינוך','fixed','#ab47bc')")
    conn.execute("UPDATE categories SET kind='fixed' WHERE name='Education'")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(goals)")}
    if "asset_id" not in cols:
        conn.execute("ALTER TABLE goals ADD COLUMN asset_id INTEGER "
                     "REFERENCES assets(id) ON DELETE SET NULL")
    ts = now_iso()
    for pattern, mt, ak, direction, amin, amax, cat in SEED_RULES_V6:
        if conn.execute("SELECT 1 FROM rules WHERE pattern=? AND direction IS ? "
                        "AND amount_min IS ?", (pattern, direction, amin)).fetchone():
            continue
        cid = conn.execute("SELECT id FROM categories WHERE name=?", (cat,)).fetchone()
        conn.execute(
            "INSERT INTO rules(pattern,match_type,account_kind,direction,amount_min,amount_max,"
            "category_id,priority,hits,created_at,enabled,system) "
            "VALUES (?,?,?,?,?,?,?,?,0,?,1,1)",
            (pattern, mt, ak, direction, amin, amax, cid[0], SYSTEM_RULE_PRIORITY, ts),
        )


def _migrate_7(conn: sqlite3.Connection) -> None:
    """goals.step_ups: JSON list of {from_month 'YYYY-MM', add_amount, reason, fixed_end_id}."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(goals)")}
    if "step_ups" not in cols:
        conn.execute("ALTER TABLE goals ADD COLUMN step_ups TEXT")


MIGRATIONS = [_migrate_1, _migrate_2, _migrate_3, _migrate_4, _migrate_5, _migrate_6,
              _migrate_7]
SCHEMA_VERSION = len(MIGRATIONS)


def migrate(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
    version = int(row[0]) if row else 0
    fresh = version == 0 and not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transactions'").fetchone()
    for i, m in enumerate(MIGRATIONS[version:], start=version + 1):
        with conn:
            m(conn)
            conn.execute(
                "INSERT INTO settings(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(i),),
            )
    # settings added later get their defaults without a migration
    with conn:
        for k, v in config.DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))
    if fresh:  # a brand-new database picks up the local personal setup, if there is one
        from . import personal_setup
        personal_setup.apply_if_present(conn)
    return SCHEMA_VERSION


# ---------------------------------------------------------------- connections

_init_lock = threading.Lock()
_initialized: set[str] = set()


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else config.db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    key = str(p.resolve())
    with _init_lock:
        if key not in _initialized:
            migrate(conn)
            _initialized.add(key)
    return conn


# ---------------------------------------------------------------- helpers

def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key,value FROM settings")}


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if r is not None:
        return r[0]
    return config.DEFAULT_SETTINGS.get(key, default)


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    if not isinstance(value, str):
        value = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def category_id(conn: sqlite3.Connection, name: str) -> int | None:
    r = conn.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()
    return r[0] if r else None


def uncategorized_id(conn: sqlite3.Connection) -> int:
    cid = category_id(conn, "Uncategorized")
    if cid is None:
        cur = conn.execute(
            "INSERT INTO categories(name,name_he,kind,color) VALUES ('Uncategorized','לא מסווג',"
            "'tracking','#e0e0e0')"
        )
        cid = cur.lastrowid
    return cid


# The one "uncategorized" predicate (overview count and /api/transactions?uncategorized=1).
# Needs `t` = transactions and `c` = LEFT JOINed categories.
# Only the last 4 months (current + 3 back) count: older history isn't worth reviewing.
UNCATEGORIZED_WHERE = ("((c.name='Uncategorized' OR t.category_id IS NULL) "
                       "AND t.is_transfer=0 AND t.is_excluded=0 "
                       "AND t.date >= date('now','localtime','start of month','-3 months'))")


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur]


TXN_SELECT = """
SELECT t.*, c.name AS category_name, c.kind AS category_kind,
       a.key AS account_key, a.provider AS account_provider, a.kind AS account_kind,
       a.name AS account_name
FROM transactions t
LEFT JOIN categories c ON c.id = t.category_id
LEFT JOIN accounts a ON a.id = t.account_id
"""


def txn_json(r) -> dict:
    """Transaction JSON per the contract."""
    d = dict(r)
    return {
        "id": d["id"],
        "date": d["date"],
        "billing_date": d.get("billing_date"),
        "cashflow_month": d.get("cashflow_month"),
        "amount": d["amount"],
        "description": d.get("description"),
        "merchant": d.get("merchant"),
        "category_id": d.get("category_id"),
        "category_name": d.get("category_name"),
        "category_kind": d.get("category_kind"),
        "category_source": d.get("category_source"),
        "account": {
            "key": d.get("account_key"),
            "provider": d.get("account_provider"),
            "kind": d.get("account_kind"),
            "name": d.get("account_name"),
        },
        "is_transfer": bool(d.get("is_transfer")),
        "is_excluded": bool(d.get("is_excluded")),
        "installment_no": d.get("installment_no"),
        "installment_total": d.get("installment_total"),
        "original_amount": d.get("original_amount"),
        "original_currency": d.get("original_currency"),
        "provider_category": d.get("provider_category"),
        "notes": d.get("notes"),
    }


def backup(dest_dir: str | Path | None = None, keep: int | None = None) -> Path:
    """SQLite online backup to data/backups/finance-YYYYMMDD-HHMM.db; keeps the newest `keep`."""
    dest = Path(dest_dir) if dest_dir else config.backups_dir()
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"finance-{datetime.now():%Y%m%d-%H%M}.db"
    src = connect()
    try:
        out = sqlite3.connect(str(target))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    files = sorted(dest.glob("finance-*.db"))
    for old in files[: max(0, len(files) - (keep or config.BACKUP_KEEP))]:
        old.unlink(missing_ok=True)
    return target


def last_backup_date(dest_dir: str | Path | None = None) -> str | None:
    dest = Path(dest_dir) if dest_dir else config.backups_dir()
    files = sorted(dest.glob("finance-*.db")) if dest.exists() else []
    return files[-1].name[8:16] if files else None


def get_txn(conn: sqlite3.Connection, txn_id: str) -> dict | None:
    r = conn.execute(TXN_SELECT + " WHERE t.id=?", (txn_id,)).fetchone()
    return txn_json(r) if r else None
