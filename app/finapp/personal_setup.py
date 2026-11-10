"""Optional local setup: categories, rules and settings that describe one household.

The defaults seeded by db.py are generic (card-bill payments, salary, reserve duty, bank fees...).
Anything that encodes *your* life — the rent check's amount band, a roommate's standing order, your
broker's name, a one-off event category — goes in a local JSON file, `data/personal_setup.json`
(gitignored with the rest of data/). See examples/personal_setup.example.json for the format.

`./finance apply-setup [file]` applies it; a brand-new database applies it automatically when the
file exists. Applying is idempotent: categories upsert by name, rules are added only when an
identical rule (pattern, match type, account kind, direction, amount band) is not there yet, and
settings are filled only while unset unless `overwrite_settings=True`.
`./finance export-setup [file]` writes the current database's non-default setup to a file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import config, db

CATEGORY_KINDS = ("income", "fixed", "variable", "tracking", "savings", "transfer")
CATEGORY_FIELDS = ("name_he", "kind", "color", "monthly_budget", "one_off", "archived")
RULE_KEY = ("pattern", "match_type", "account_kind", "direction", "amount_min", "amount_max")
# settings that are runtime state, not setup
_STATE_SETTINGS = {"schema_version", "last_sheet_import", "last_sync", "sheet_last_updated"}


def setup_path() -> Path:
    env = os.environ.get("FINAPP_SETUP")
    return Path(env) if env else config.data_dir() / "personal_setup.json"


def load(path: str | Path | None = None) -> dict:
    p = Path(path) if path else setup_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{p.name}: expected a JSON object")
    return data


def _num(v):
    return None if v is None or v == "" else float(v)


def _flag(v):
    return None if v is None else int(bool(v))


def apply(conn, setup: dict, *, overwrite_settings: bool = False) -> dict:
    """Apply a setup dict to the database. Returns counts and any warnings."""
    res = {"categories_added": 0, "categories_updated": 0, "rules_added": 0,
           "rules_existing": 0, "settings_set": 0, "settings_kept": [], "warnings": []}
    with conn:
        for c in setup.get("categories") or []:
            name = (c.get("name") or "").strip()
            if not name:
                res["warnings"].append("category without a name skipped")
                continue
            if c.get("kind") is not None and c["kind"] not in CATEGORY_KINDS:
                res["warnings"].append(f"category {name}: unknown kind {c['kind']!r}")
                continue
            vals = {k: c[k] for k in CATEGORY_FIELDS if k in c}
            for k in ("one_off", "archived"):
                if k in vals:
                    vals[k] = int(bool(vals[k]))
            row = conn.execute("SELECT * FROM categories WHERE name=?", (name,)).fetchone()
            if row is None:
                vals.setdefault("kind", "variable")
                cols = ["name", *vals]
                conn.execute(f"INSERT INTO categories({','.join(cols)}) "
                             f"VALUES ({','.join('?' * len(cols))})", (name, *vals.values()))
                res["categories_added"] += 1
            else:
                changed = {k: v for k, v in vals.items() if row[k] != v}
                if changed:
                    conn.execute(f"UPDATE categories SET {','.join(k + '=?' for k in changed)} "
                                 "WHERE id=?", (*changed.values(), row["id"]))
                    res["categories_updated"] += 1

        ts = db.now_iso()
        for r in setup.get("rules") or []:
            pattern = r.get("pattern")
            if not pattern:
                res["warnings"].append("rule without a pattern skipped")
                continue
            cid = None
            if r.get("category"):
                cid = db.category_id(conn, r["category"])
                if cid is None:
                    res["warnings"].append(f"rule {pattern!r}: no category {r['category']!r}")
                    continue
            key = (pattern, r.get("match_type") or "contains", r.get("account_kind"),
                   r.get("direction"), _num(r.get("amount_min")), _num(r.get("amount_max")))
            if conn.execute(
                    "SELECT 1 FROM rules WHERE pattern=? AND match_type=? AND account_kind IS ? "
                    "AND direction IS ? AND amount_min IS ? AND amount_max IS ?", key).fetchone():
                res["rules_existing"] += 1
                continue
            conn.execute(
                "INSERT INTO rules(pattern,match_type,account_kind,direction,amount_min,amount_max,"
                "category_id,set_transfer,set_excluded,priority,hits,created_at,enabled,system) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?)",
                (*key, cid, _flag(r.get("set_transfer")), _flag(r.get("set_excluded")),
                 int(r.get("priority", db.SYSTEM_RULE_PRIORITY)), ts,
                 int(bool(r.get("enabled", True))), int(bool(r.get("system", True)))))
            res["rules_added"] += 1

        for k, v in (setup.get("settings") or {}).items():
            cur = db.get_setting(conn, k)
            v = None if v is None else str(v)
            if cur == v:
                continue
            if cur not in (None, "", config.DEFAULT_SETTINGS.get(k)) and not overwrite_settings:
                res["settings_kept"].append(k)
                continue
            db.set_setting(conn, k, v)
            res["settings_set"] += 1
    return res


def apply_file(conn, path: str | Path | None = None, **kw) -> dict:
    return {"file": str(Path(path) if path else setup_path()), **apply(conn, load(path), **kw)}


def apply_if_present(conn) -> dict | None:
    """Called once on a brand-new database: apply data/personal_setup.json if it exists."""
    p = setup_path()
    return apply_file(conn, p) if p.is_file() else None


def _default_rule_keys() -> set[tuple]:
    keys = set()
    for pattern, mt, ak, _cat, _st in db.SEED_RULES:
        keys.add((pattern, mt, ak, None, None, None))
    for pattern, mt, ak, _cat in db.SEED_RULES_V2:
        keys.add((pattern, mt, ak, None, None, None))
    return keys


def _default_categories() -> dict[str, dict]:
    """name -> seeded field values, as a fresh database has them."""
    out = {}
    for name, he, kind, color in db.SEED_CATEGORIES + db.SEED_CATEGORIES_V2:
        out[name] = {"name_he": he, "kind": kind, "color": color, "monthly_budget": None,
                     "one_off": 0, "archived": 0}
    for name, he, kind, color, one_off in db.SEED_CATEGORIES_V3:
        out[name] = {"name_he": he, "kind": kind, "color": color, "monthly_budget": None,
                     "one_off": one_off, "archived": 0}
    old = out.pop("Rent & tuition")  # renamed in migration 6
    out["Home rent"] = {**old, "name_he": "שכירות", "kind": "fixed"}
    out["Education"]["kind"] = "fixed"
    return out


def export(conn) -> dict:
    """The database's setup that a fresh database would not have: non-default categories (or
    changed fields of default ones), every rule that is not a generic default, and settings that
    differ from their defaults."""
    defaults = _default_categories()
    cats = []
    for r in conn.execute("SELECT * FROM categories ORDER BY id"):
        cur = {k: r[k] for k in CATEGORY_FIELDS}
        base = defaults.get(r["name"])
        if base is None:
            cats.append({"name": r["name"], **{k: v for k, v in cur.items()
                                                if v not in (None, 0) or k == "kind"}})
        else:
            diff = {k: v for k, v in cur.items() if (v or None) != (base[k] or None)}
            if diff:
                cats.append({"name": r["name"], **diff})
    default_rules = _default_rule_keys()
    rules = []
    for r in conn.execute("SELECT r.*, c.name AS category FROM rules r "
                          "LEFT JOIN categories c ON c.id=r.category_id ORDER BY r.id"):
        key = tuple(r[k] for k in RULE_KEY)
        if key in default_rules:
            continue
        d = {k: r[k] for k in RULE_KEY if r[k] is not None}
        for k in ("category", "set_transfer", "set_excluded"):
            if r[k] is not None:
                d[k] = r[k]
        d["priority"] = r["priority"]
        if not r["enabled"]:
            d["enabled"] = False
        if not r["system"]:
            d["system"] = False
        rules.append(d)
    settings = {}
    for k, v in conn.execute("SELECT key, value FROM settings ORDER BY key"):
        if k in _STATE_SETTINGS or k.startswith("last_"):
            continue
        if v != config.DEFAULT_SETTINGS.get(k):
            settings[k] = v
    return {"categories": cats, "rules": rules, "settings": settings}
