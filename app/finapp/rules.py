"""Categorisation engine.

Precedence: manual > rule (highest priority, then most specific / longest pattern) > specific
RiseUp category > provider category mapped to our taxonomy > generic RiseUp category ("אחר",
"כללי", "RiseUp") > "Uncategorized".
"""

from __future__ import annotations

import re
import sqlite3
from functools import lru_cache

from .db import (
    TXN_SELECT,
    category_id,
    normalize_merchant,
    now_iso,
    txn_json,
    uncategorized_id,
)

USER_RULE_PRIORITY = 200

# RiseUp category (Hebrew) -> our category name. Every RiseUp category in the contract.
RISEUP_MAP: dict[str, str] = {
    "אוכל בחוץ": "Dining out",
    "אחר": "Other",
    "ארנונה": "Arnona",
    "ביטוח": "Insurance",
    "בריאות": "Health",
    "דיגיטל": "Digital",
    "הלוואה": "Loan",
    "העברות": "Transfers to people",
    "הפקדת מזומן": "Cash deposit",
    "השקעה וחיסכון": "Savings & investments",
    "חינוך": "Education",
    "כללי": "General",
    "מזומן": "Cash withdrawal",
    "מילואים": "Reserve duty income",
    "מים": "Water",
    "משיכת פקדון": "Savings & investments",
    "משכורת": "Salary",
    "משכנתא": "Mortgage",
    "סופר": "Groceries",
    "ספורט": "Sports",
    "עמלות": "Fees",
    "קניות": "Shopping",
    "רכב ודלק": "Car & fuel",
    "שיק": "Checks",
    "שכירות ושכר לימוד": "Home rent",  # a tuition rule (personal setup) wins first
    "תחבורה ציבורית": "Public transport",
    "תיירות": "Travel",
    "תקשורת": "Internet & mobile",
    "תרומה": "Donations",
    "תשלומים": "Payments",
    "RiseUp": "Subscriptions",
    "riseup": "Subscriptions",
}

# RiseUp categories that say nothing about the spend: a specific provider category beats them.
GENERIC_RISEUP: frozenset[str] = frozenset({"אחר", "כללי", "RiseUp", "riseup"})

# Exact provider categories (Max קטגוריה) -> ours. None = generic, left to lower precedence.
PROVIDER_EXACT: dict[str, str | None] = {
    "אחר": None,
    "שונות": None,
    "כללי": None,
    "העברת כספים": "Transfers to people",
    "מזון וצריכה": "Groceries",
    "מסעדות, קפה וברים": "Dining out",
    "אופנה": "Clothing",
    "פנאי, בידור וספורט": "Entertainment",
    "רפואה ובתי מרקחת": "Health",
    "עיצוב הבית": "Home",
    "טיסות ותיירות": "Travel",
    "חשמל ומחשבים": "Electronics",
    "תחבורה ורכבים": "Car & fuel",
    "עירייה וממשלה": "Government & municipal",
    "ספרים ודפוס": "Books & print",
    "קוסמטיקה וטיפוח": "Personal care",
    "שירותי תקשורת": "Internet & mobile",
    "דלק, חשמל וגז": "Car & fuel",
    "ביטוח": "Insurance",
    "מוצרי m_a_x": "Fees",
}

# Other provider categories (Cal ענף, scraper categories) -> ours, by keyword.
# First match wins, so more specific keywords come first.
PROVIDER_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("מסעד", "קפה", "ברים", "מזון מהיר", "בתי אוכל"), "Dining out"),
    (("טיסות", "תיירות", "מלונ", "נופש"), "Travel"),
    (("מחשב", "אלקטרוניק"), "Electronics"),
    (("דלק",), "Car & fuel"),
    (("חשמל וגז", "חשמל"), "Electricity & gas"),
    (("מזון", "סופר", "מכולת", "צריכה"), "Groceries"),
    (("ספורט",), "Sports"),
    (("פנאי", "בידור", "תרבות", "קולנוע"), "Entertainment"),
    (("ביטוח",), "Insurance"),
    (("רפוא", "בריאות", "מרקחת", "פארם"), "Health"),
    (("קוסמטיק", "טיפוח"), "Personal care"),
    (("תקשורת", "סלולר", "אינטרנט"), "Internet & mobile"),
    (("אופנה", "הלבשה", "ביגוד", "הנעלה"), "Clothing"),
    (("ריהוט", "בית", "עיצוב"), "Home"),
    (("רכב", "תחבורה", "חניה", "מוסך"), "Car & fuel"),
    (("ספרים", "דפוס"), "Books & print"),
    (("חינוך", "לימוד"), "Education"),
    (("חיות",), "Pets"),
    (("תרומ", "עמות"), "Donations"),
    (("ארנונה",), "Arnona"),
    (("עירי", "ממשל", "מיסים"), "Government & municipal"),
    (("מים",), "Water"),
    (("פיננס", "עמל"), "Fees"),
    (("מנוי", "דיגיטל", "סטרימינג"), "Digital"),
    (("מתנ", "צעצוע"), "Gifts"),
]


def is_generic_riseup(name: str | None) -> bool:
    return not name or not name.strip() or name.strip() in GENERIC_RISEUP


def map_riseup_category(conn: sqlite3.Connection, name: str | None) -> int | None:
    if not name:
        return None
    s = name.strip()
    ours = RISEUP_MAP.get(s)
    if ours is None:
        # user-made RiseUp categories extend a built-in one ("משכנתא <property name>")
        ours = next((v for k, v in RISEUP_MAP.items() if s.startswith(k + " ")), None)
    return category_id(conn, ours) if ours else None


def map_provider_category(conn: sqlite3.Connection, name: str | None) -> int | None:
    """Specific provider category -> our id; generic ones ("אחר", "שונות") -> None."""
    if not name or not name.strip():
        return None
    s = name.strip()
    if s in PROVIDER_EXACT:
        ours = PROVIDER_EXACT[s]
        return category_id(conn, ours) if ours else None
    for kws, ours in PROVIDER_KEYWORDS:
        if any(k in s for k in kws):
            return category_id(conn, ours)
    return None


# ---------------------------------------------------------------- matching

@lru_cache(maxsize=512)
def _compile(pattern: str):
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


def rule_matches(rule: dict, txn: dict) -> bool:
    """txn needs merchant, description, amount, account_kind."""
    if not rule.get("enabled", 1):
        return False
    ak = rule.get("account_kind")
    if ak and ak != txn.get("account_kind"):
        return False
    raw_amt = txn.get("amount") or 0.0
    direction = rule.get("direction")
    if direction == "in" and raw_amt <= 0:
        return False
    if direction == "out" and raw_amt >= 0:
        return False
    amt = abs(raw_amt)
    if rule.get("amount_min") is not None and amt < rule["amount_min"]:
        return False
    if rule.get("amount_max") is not None and amt > rule["amount_max"]:
        return False
    merchant = txn.get("merchant") or normalize_merchant(txn.get("description"))
    mt = rule.get("match_type") or "contains"
    pat = rule.get("pattern") or ""
    if mt == "regex":
        rx = _compile(pat)
        if rx is None:
            return False
        return bool(rx.search(merchant) or rx.search(txn.get("description") or ""))
    p = normalize_merchant(pat)
    if not p:
        return False
    if mt == "equals":
        return merchant == p
    return p in merchant


def _specificity(rule: dict) -> int:
    s = len(rule.get("pattern") or "")
    if rule.get("match_type") == "equals":
        s += 20
    for k in ("account_kind", "amount_min", "amount_max", "direction"):
        if rule.get(k) is not None:
            s += 5
    return s


def load_rules(conn: sqlite3.Connection) -> list[dict]:
    rs = [dict(r) for r in conn.execute("SELECT * FROM rules WHERE enabled=1")]
    rs.sort(key=lambda r: (-(r["priority"] or 0), -_specificity(r), r["id"]))
    return rs


def decide(conn, txn: dict, rules: list[dict], kinds: dict[int, str],
           one_offs: set[int] | None = None) -> dict:
    """Compute category + flags for one txn (dict from TXN_SELECT). Respects manual edits.

    Rows in a one-off category (categories.one_off=1, e.g. a renovation) are excluded unless the
    user set the flags by hand."""
    if one_offs is None:
        one_offs = one_off_ids(conn)
    cat_rule = next((r for r in rules if r["category_id"] and rule_matches(r, txn)), None)
    tr_rule = next(
        (r for r in rules if r["set_transfer"] is not None and rule_matches(r, txn)), None
    )
    ex_rule = next(
        (r for r in rules if r["set_excluded"] is not None and rule_matches(r, txn)), None
    )
    out: dict = {"rule_ids": {r["id"] for r in (cat_rule, tr_rule, ex_rule) if r}}
    if txn.get("category_source") == "manual":
        out["category_id"], out["category_source"] = txn["category_id"], "manual"
    elif cat_rule:
        out["category_id"], out["category_source"] = cat_rule["category_id"], "rule"
    elif not is_generic_riseup(ru := txn.get("riseup_category")) and (
            cid := map_riseup_category(conn, ru)) is not None:
        out["category_id"], out["category_source"] = cid, "riseup"
    elif (cid := map_provider_category(conn, txn.get("provider_category"))) is not None:
        out["category_id"], out["category_source"] = cid, "provider"
    elif (cid := map_riseup_category(conn, ru)) is not None:
        out["category_id"], out["category_source"] = cid, "riseup"
    else:
        out["category_id"], out["category_source"] = uncategorized_id(conn), "default"
    if txn.get("flags_manual"):
        out["is_transfer"], out["is_excluded"] = txn["is_transfer"], txn["is_excluded"]
    else:
        if tr_rule:
            out["is_transfer"] = int(tr_rule["set_transfer"])
        else:
            out["is_transfer"] = int(kinds.get(out["category_id"]) == "transfer")
        if ex_rule:
            out["is_excluded"] = int(ex_rule["set_excluded"])
        else:
            out["is_excluded"] = int(bool(txn.get("base_excluded"))
                                     or out["category_id"] in one_offs)
    return out


def _kinds(conn) -> dict[int, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT id, kind FROM categories")}


def one_off_ids(conn) -> set[int]:
    return {r[0] for r in conn.execute("SELECT id FROM categories WHERE one_off=1")}


def apply_rules(
    conn: sqlite3.Connection,
    only_non_manual: bool = True,
    txn_ids: list[str] | None = None,
) -> int:
    """Re-categorise transactions. Returns the number of rows changed.

    Full runs (txn_ids None) recount every rule's hits; partial runs add to them.
    """
    rules = load_rules(conn)
    kinds = _kinds(conn)
    one_offs = one_off_ids(conn)
    q = TXN_SELECT
    params: list = []
    if txn_ids is not None:
        if not txn_ids:
            return 0
        q += f" WHERE t.id IN ({','.join('?' * len(txn_ids))})"
        params = list(txn_ids)
    hits: dict[int, int] = {}
    changed = 0
    ts = now_iso()
    for r in conn.execute(q, params).fetchall():
        txn = dict(r)
        if not only_non_manual and txn.get("category_source") == "manual":
            txn["category_source"] = None
        d = decide(conn, txn, rules, kinds, one_offs)
        for rid in d["rule_ids"]:
            hits[rid] = hits.get(rid, 0) + 1
        new = (d["category_id"], d["category_source"], d["is_transfer"], d["is_excluded"])
        old = (txn["category_id"], txn["category_source"], txn["is_transfer"], txn["is_excluded"])
        if new != old:
            conn.execute(
                "UPDATE transactions SET category_id=?, category_source=?, is_transfer=?, "
                "is_excluded=?, updated_at=? WHERE id=?",
                (*new, ts, txn["id"]),
            )
            changed += 1
    if txn_ids is None:
        conn.execute("UPDATE rules SET hits=0")
    for rid, n in hits.items():
        conn.execute("UPDATE rules SET hits=hits+? WHERE id=?", (n, rid))
    return changed


# ---------------------------------------------------------------- CRUD helpers

RULE_FIELDS = (
    "pattern", "match_type", "account_kind", "amount_min", "amount_max", "category_id",
    "set_transfer", "set_excluded", "priority", "enabled", "direction",
)


def create_rule(conn: sqlite3.Connection, spec: dict) -> dict:
    data = {k: spec.get(k) for k in RULE_FIELDS}
    data["match_type"] = data["match_type"] or "contains"
    if data["priority"] is None:
        data["priority"] = USER_RULE_PRIORITY
    if data["enabled"] is None:
        data["enabled"] = 1
    if not data["pattern"]:
        raise ValueError("pattern is required")
    if data["match_type"] == "regex" and _compile(data["pattern"]) is None:
        raise ValueError("invalid regex")
    if data["direction"] not in (None, "in", "out"):
        raise ValueError("direction must be 'in', 'out' or null")
    cols = ",".join(data)
    cur = conn.execute(
        f"INSERT INTO rules({cols},hits,created_at,system) VALUES "
        f"({','.join('?' * len(data))},0,?,0)",
        (*data.values(), now_iso()),
    )
    return dict(conn.execute("SELECT * FROM rules WHERE id=?", (cur.lastrowid,)).fetchone())


def create_rule_from_txn(
    conn: sqlite3.Connection,
    txn_id: str,
    category_id_: int | None = None,
    set_transfer: int | None = None,
    set_excluded: int | None = None,
    apply_to_history: bool = False,
) -> tuple[dict, int]:
    """Rule `contains <normalized merchant>` from a transaction. Returns (rule, updated_count)."""
    t = conn.execute(TXN_SELECT + " WHERE t.id=?", (txn_id,)).fetchone()
    if t is None:
        raise KeyError(txn_id)
    pattern = t["merchant"] or normalize_merchant(t["description"])
    existing = conn.execute(
        "SELECT * FROM rules WHERE pattern=? AND match_type='contains' AND system=0 "
        "AND account_kind IS NULL AND amount_min IS NULL AND amount_max IS NULL "
        "AND direction IS NULL",
        (pattern,),
    ).fetchone()
    if existing:
        sets, vals = [], []
        for k, v in (("category_id", category_id_), ("set_transfer", set_transfer),
                     ("set_excluded", set_excluded)):
            if v is not None:
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            conn.execute(f"UPDATE rules SET {','.join(sets)} WHERE id=?", (*vals, existing["id"]))
        rule = dict(conn.execute("SELECT * FROM rules WHERE id=?", (existing["id"],)).fetchone())
    else:
        rule = create_rule(conn, {
            "pattern": pattern, "match_type": "contains", "category_id": category_id_,
            "set_transfer": set_transfer, "set_excluded": set_excluded,
        })
    if apply_to_history:
        ids = [r["id"] for r in matching_rows(conn, rule) if r["category_source"] != "manual"]
        updated = apply_rules(conn, only_non_manual=True, txn_ids=ids)
    else:
        updated = 0
    return rule, updated


def matching_rows(conn: sqlite3.Connection, spec: dict) -> list[dict]:
    rule = {k: spec.get(k) for k in RULE_FIELDS}
    rule["match_type"] = rule["match_type"] or "contains"
    rule["enabled"] = 1
    out = []
    for r in conn.execute(TXN_SELECT + " ORDER BY t.date DESC"):
        d = dict(r)
        if rule_matches(rule, d):
            out.append(d)
    return out


def preview(conn: sqlite3.Connection, spec: dict, sample: int = 20) -> dict:
    if (spec.get("match_type") == "regex") and _compile(spec.get("pattern") or "") is None:
        raise ValueError("invalid regex")
    rows = matching_rows(conn, spec)
    would_change = sum(
        1 for r in rows
        if r["category_source"] != "manual"
        and spec.get("category_id") is not None
        and r["category_id"] != spec.get("category_id")
    )
    return {
        "count": len(rows),
        "would_change": would_change,
        "manual_count": sum(1 for r in rows if r["category_source"] == "manual"),
        "sample": [txn_json(r) for r in rows[:sample]],
    }
