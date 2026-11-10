"""Expense-review insights: detectors over the last 12 months + manual (offline) insights."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, timedelta

from . import db
from .analytics import Ledger, add_months, current_month, median, today, unusual_inflows

SEVERITY_ORDER = {"high": 0, "med": 1, "low": 2}

CARD_FEE_PATTERNS = ["דמי כרטיס", "דמי מנוי", "דמי חבר", "עמלה", "עמלת", "עמלות"]
BANK_FEE_PATTERNS = ["עמלות", "עמלת", "עמלה", "דמי ניהול", "ריבית חובה", "ריבית על משיכת יתר"]
FX_FEE_PATTERNS = ["עמלת המרה", "עמלת מט", "עמלת חו", "עמלת חול", "foreign", "fx fee"]
STREAMING = ["netflix", "spotify", "disney", "apple", "youtube", "hbo", "prime", "נטפליקס",
             "ספוטיפיי", "דיסני", "סלקום tv", "פרטנר tv", "סטינג"]
STREAMING_TOKENS = {"yes", "hot", "יס", "הוט", "max"}  # short names: whole-word match only
NOT_SUBSCRIPTION_CATS = {
    "Mortgage", "Home rent", "Education", "Loan", "Arnona", "Water", "Electricity & gas", "Insurance",
    "Internet & mobile", "Groceries", "Car & fuel", "Public transport", "Fees",
}
DISCRETIONARY_CATS = ["Dining out", "Shopping", "Entertainment", "Travel", "Personal care",
                      "Gifts", "Home", "Cash withdrawal", "Sports"]
REVIEW_CATS = {"Insurance", "Internet & mobile"}


def _h(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _norm_list(pats: list[str]) -> list[str]:
    return [db.normalize_merchant(p) for p in pats]


def _matches(text_norm: str, pats_norm: list[str]) -> bool:
    return any(p and p in text_norm for p in pats_norm)


def _item(type_, key, severity, title, detail, monthly=None, yearly=None, txn_ids=(),
          merchant=None, category_id=None, task_title=None) -> dict:
    if yearly is None and monthly is not None:
        yearly = monthly * 12
    if monthly is None and yearly is not None:
        monthly = yearly / 12
    monthly = round(monthly, 2) if monthly is not None else None
    yearly = round(yearly, 2) if yearly is not None else None
    return {
        "key": key, "type": type_, "severity": severity, "title": title, "detail": detail,
        "est_monthly_saving": monthly, "est_yearly_saving": yearly,
        "txn_ids": list(txn_ids), "merchant": merchant, "category_id": category_id,
        "suggested_task": {
            "title": task_title or title, "notes": detail,
            "priority": severity, "est_monthly_saving": monthly,
            "merchant": merchant, "category_id": category_id,
        },
    }


# ---------------------------------------------------------------- data

def _load(conn: sqlite3.Connection, on: date) -> list[dict]:
    since = (on - timedelta(days=365)).isoformat()
    q = """SELECT t.id, t.date, t.amount, t.merchant, t.description, t.category_id,
                  t.account_id, t.original_currency, t.original_amount, t.cashflow_month,
                  t.installment_total, t.provider_category,
                  c.name cat, c.kind ck, a.kind ak
           FROM transactions t
           LEFT JOIN categories c ON c.id=t.category_id
           LEFT JOIN accounts a ON a.id=t.account_id
           WHERE t.is_transfer=0 AND t.is_excluded=0 AND t.date>=?
             AND COALESCE(c.kind,'') NOT IN ('transfer','savings')
           ORDER BY t.date"""
    return [dict(r) for r in conn.execute(q, (since,))]


def _recurring(rows: list[dict]) -> list[dict]:
    """Recurring charge series by merchant: regular interval + similar amount."""
    by_m: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["amount"] < 0 and (r["installment_total"] or 0) <= 1 and r["merchant"]:
            by_m[r["merchant"]].append(r)
    out = []
    last_date = max((_d(r["date"]) for r in rows), default=None)
    for m, rs in by_m.items():
        if len(rs) < 3:
            continue
        rs.sort(key=lambda r: r["date"])
        # one charge per cashflow month (some merchants bill twice in a month)
        months = {r["cashflow_month"] for r in rs}
        dates = [_d(r["date"]) for r in rs]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        if not gaps:
            continue
        g = statistics.median(gaps)
        amounts = [-r["amount"] for r in rs]
        med = median(amounts)
        if med <= 0:
            continue
        similar = sum(1 for a in amounts if abs(a - med) <= 0.2 * med) / len(amounts)
        regular = sum(1 for x in gaps if 20 <= x <= 40) / len(gaps)
        if 25 <= g <= 35 and regular >= 0.6 and similar >= 0.7 and len(months) >= 3:
            period = "monthly"
            monthly = med
        elif 350 <= g <= 380 and similar >= 0.7:
            period = "yearly"
            monthly = med / 12
        else:
            continue
        active = last_date is None or (last_date - dates[-1]).days <= (45 if period == "monthly"
                                                                       else 400)
        out.append({
            "merchant": m, "period": period, "monthly": monthly, "median": med,
            "rows": rs, "amounts": amounts, "active": active,
            "category_id": rs[-1]["category_id"], "cat": rs[-1]["cat"], "ck": rs[-1]["ck"],
            "label": rs[-1]["description"] or m,
        })
    return out


# ---------------------------------------------------------------- detectors

def detect_duplicate_charge(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["amount"] < 0 and -r["amount"] >= 20 and (r["installment_total"] or 0) <= 1 \
                and r["cat"] != "Public transport" and r["ak"] in ("card", "gift"):
            groups[(r["account_id"], r["merchant"], round(r["amount"], 2))].append(r)
    out = []
    for (acc, m, amt), rs in groups.items():
        rs.sort(key=lambda r: r["date"])
        for a, b in zip(rs, rs[1:]):
            if (_d(b["date"]) - _d(a["date"])).days <= 3:
                ids = sorted([a["id"], b["id"]])
                sev = "high" if -amt >= 200 else "med"
                out.append(_item(
                    "duplicate_charge", f"duplicate_charge:{_h(*ids)}", sev,
                    f"Possible double charge: {a['description']} ₪{-amt:,.2f}",
                    f"Charged twice on the same card within 3 days ({a['date']} and "
                    f"{b['date']}). Check with the merchant or card issuer.",
                    monthly=0.0, yearly=-amt, txn_ids=ids, merchant=m,
                    category_id=a["category_id"],
                    task_title=f"Check double charge at {a['description']} ({a['date']})",
                ))
    return out


def detect_fees(rows: list[dict]) -> list[dict]:
    card_p, bank_p, fx_p = (_norm_list(CARD_FEE_PATTERNS), _norm_list(BANK_FEE_PATTERNS),
                            _norm_list(FX_FEE_PATTERNS))
    buckets: dict[str, list[dict]] = {"card_fee": [], "bank_fee": [], "fx_fee": []}
    for r in rows:
        if r["amount"] >= 0:
            continue
        text = db.normalize_merchant(r["description"])
        if _matches(text, fx_p):
            buckets["fx_fee"].append(r)
        elif r["ak"] in ("card", "gift") and (_matches(text, card_p) or r["cat"] == "Fees"):
            buckets["card_fee"].append(r)
        elif r["ak"] == "bank" and (_matches(text, bank_p) or r["cat"] == "Fees"):
            buckets["bank_fee"].append(r)
    out = []
    labels = {"card_fee": ("Card fees", "Call the card issuer and ask to waive the card fee."),
              "bank_fee": ("Bank fees", "Ask the bank for a fee waiver or a cheaper track "
                                        "(מסלול עמלות), or compare with a digital bank."),
              "fx_fee": ("Foreign-transaction fees", "Use a card without FX fees abroad.")}
    for typ, rs in buckets.items():
        total = sum(-r["amount"] for r in rs)
        if total < 10:
            continue
        name, advice = labels[typ]
        by_desc: dict[str, float] = defaultdict(float)
        for r in rs:
            by_desc[r["description"]] += -r["amount"]
        top = ", ".join(f"{k} ₪{v:,.0f}" for k, v in sorted(by_desc.items(),
                                                            key=lambda kv: -kv[1])[:4])
        sev = "high" if total >= 300 else ("med" if total >= 100 else "low")
        out_type = "card_fee" if typ in ("card_fee", "fx_fee") else "bank_fee"
        out.append(_item(
            out_type, typ, sev, f"{name}: ₪{total:,.0f} in the last 12 months",
            f"{top}. {advice}", yearly=total, txn_ids=[r["id"] for r in rs],
            category_id=rs[0]["category_id"], task_title=f"Negotiate / waive {name.lower()}",
        ))
    return out


def detect_subscriptions(rec: list[dict]) -> list[dict]:
    out = []
    streaming = []
    for s in rec:
        if not s["active"] or s["cat"] in NOT_SUBSCRIPTION_CATS or s["ck"] == "income":
            continue
        toks = set(s["merchant"].split())
        is_stream = (any(k in s["merchant"] for k in STREAMING) or bool(toks & STREAMING_TOKENS)
                     or s["cat"] in ("Digital", "Subscriptions"))
        if is_stream:
            streaming.append(s)
        sev = "low" if s["monthly"] < 100 else "med"
        per = "month" if s["period"] == "monthly" else "year"
        cost = s["median"]
        out.append(_item(
            "subscription", f"subscription:{s['merchant']}", sev,
            f"Subscription: {s['label']} ₪{cost:,.0f}/{per}",
            f"Charged {len(s['rows'])} times in the last 12 months (≈₪{s['monthly'] * 12:,.0f}"
            f"/year). Still using it? Cancel or downgrade if not.",
            # informational: a subscription's cost is not a saving unless it is cut
            monthly=None, txn_ids=[r["id"] for r in s["rows"]],
            merchant=s["merchant"], category_id=s["category_id"],
            task_title=f"Review subscription: {s['label']}",
        ))
    if len(streaming) >= 2:
        names = ", ".join(s["label"] for s in streaming)
        cheapest = min(s["monthly"] for s in streaming)
        out.append(_item(
            "subscription", "subscription:overlap:digital", "med",
            f"{len(streaming)} overlapping digital/streaming subscriptions",
            f"{names}. Keeping one fewer saves at least ₪{cheapest:,.0f}/month.",
            monthly=cheapest, txn_ids=[r["id"] for s in streaming for r in s["rows"]],
            task_title="Cut one overlapping streaming/digital subscription",
        ))
    return out


def detect_price_increase(rec: list[dict]) -> list[dict]:
    out = []
    for s in rec:
        if not s["active"] or len(s["amounts"]) < 3:
            continue
        latest, prev = s["amounts"][-1], s["amounts"][:-1]
        pm = median(prev)
        if pm > 0 and latest > pm * 1.10 and latest - pm >= 3:
            inc = latest - pm
            monthly = inc if s["period"] == "monthly" else inc / 12
            last = s["rows"][-1]
            out.append(_item(
                "price_increase", f"price_increase:{s['merchant']}:{last['date'][:7]}",
                "med" if monthly >= 20 else "low",
                f"Price rose: {s['label']} ₪{pm:,.0f} → ₪{latest:,.0f}",
                f"Latest charge ({last['date']}) is {((latest / pm) - 1) * 100:.0f}% above its "
                f"usual amount. Ask for the old price or look for an alternative.",
                monthly=monthly, txn_ids=[last["id"]], merchant=s["merchant"],
                category_id=s["category_id"], task_title=f"Push back on price rise: {s['label']}",
            ))
    return out


CREEP_SKIP = {"Uncategorized", "Payments", "Transfers to people", "Travel"}  # + one-off categories
CREEP_RECOVERABLE = 0.5  # realistic share of an increase that can be won back


def detect_category_creep(led: Ledger, cur: str) -> list[dict]:
    last3 = [add_months(cur, -i) for i in range(1, 4)]
    prior9 = [add_months(cur, -i) for i in range(4, 13)]
    first = led.first_month()
    if not first:
        return []
    prior9 = [m for m in prior9 if m >= first]
    if len(prior9) < 3:
        return []
    out = []
    for cid, cat in led.cats.items():
        # discretionary only: fixed costs, installment payments and transfers aren't "creep"
        if (cat["kind"] not in ("variable", "tracking") or cat["name"] in CREEP_SKIP
                or cat.get("one_off")):
            continue
        a3 = sum(led.cat_actual(m, cid) for m in last3) / 3
        a9 = sum(led.cat_actual(m, cid) for m in prior9) / len(prior9)
        if a9 > 0 and a3 > a9 * 1.25 and a3 - a9 >= 100:
            diff = a3 - a9
            out.append(_item(
                "category_creep", f"category_creep:{cid}:{last3[0]}",
                "med" if diff >= 300 else "low",
                f"{cat['name']} is creeping up: ₪{a3:,.0f}/month vs ₪{a9:,.0f}",
                f"Last 3 months average is {(a3 / a9 - 1) * 100:.0f}% above the previous "
                f"{len(prior9)} months.",
                monthly=round(diff * CREEP_RECOVERABLE, 2), category_id=cid,
                task_title=f"Bring {cat['name']} back to ₪{a9:,.0f}/month",
            ))
    return out


def detect_top_discretionary(rows: list[dict], conn: sqlite3.Connection) -> list[dict]:
    totals: dict[int, float] = defaultdict(float)
    ids: dict[int, list[str]] = defaultdict(list)
    names: dict[int, str] = {}
    for r in rows:
        if r["cat"] in DISCRETIONARY_CATS and r["ak"] in ("card", "gift", "bank"):
            totals[r["category_id"]] += -r["amount"]
            names[r["category_id"]] = r["cat"]
            if r["amount"] < 0:
                ids[r["category_id"]].append(r["id"])
    months = len({r["cashflow_month"] for r in rows}) or 1
    months = min(12, months)
    out = []
    for cid, tot in sorted(totals.items(), key=lambda kv: -kv[1])[:3]:
        avg = tot / months
        if avg < 200:
            continue
        save = avg * 0.15
        out.append(_item(
            "top_discretionary", f"top_discretionary:{cid}",
            "med" if avg >= 1500 else "low",
            f"{names[cid]}: ₪{avg:,.0f}/month",
            f"One of your biggest discretionary categories (₪{tot:,.0f} over {months} months). "
            f"A 15% cut saves ₪{save * 12:,.0f}/year.",
            monthly=save, txn_ids=ids[cid][-50:], category_id=cid,
            task_title=f"Cut {names[cid]} by 15% (₪{avg * 0.85:,.0f}/month target)",
        ))
    return out


def detect_fx_heavy(rows: list[dict]) -> list[dict]:
    fx = [r for r in rows if r["amount"] < 0 and r["original_currency"]
          and r["original_currency"].upper() not in ("ILS", "NIS", "₪")]
    total = sum(-r["amount"] for r in fx)
    if len(fx) < 10 and total < 3000:
        return []
    est_year = total * 0.025
    return [_item(
        "fx_heavy", "fx_heavy", "med" if est_year >= 150 else "low",
        f"{len(fx)} foreign-currency charges (₪{total:,.0f}) in 12 months",
        "Cards typically add ~2.5–3% on foreign charges. A no-FX-fee card or a multi-currency "
        f"account would save ≈₪{est_year:,.0f}/year.",
        yearly=est_year, txn_ids=[r["id"] for r in fx][-100:],
        task_title="Get a no-FX-fee card / multi-currency account for foreign spending",
    )]


def detect_insurance_review(conn: sqlite3.Connection, rec: list[dict], on: date) -> list[dict]:
    out = []
    cutoff = (on - timedelta(days=365)).isoformat()
    for s in rec:
        is_review = s["cat"] in REVIEW_CATS or any(
            k in (s["rows"][-1]["provider_category"] or "") for k in ("ביטוח", "תקשורת"))
        if not s["active"] or not is_review:
            continue
        first = conn.execute("SELECT MIN(date) FROM transactions WHERE merchant=?",
                             (s["merchant"],)).fetchone()[0]
        if not first or first > cutoff:
            continue
        amts = s["amounts"]
        if max(amts) > min(amts) * 1.10:
            continue  # changed recently -> price_increase handles it
        save = s["monthly"] * 0.15
        out.append(_item(
            "insurance_review", f"insurance_review:{s['merchant']}", "low" if save < 30 else "med",
            f"Compare prices: {s['label']} ₪{s['median']:,.0f}/"
            f"{'month' if s['period'] == 'monthly' else 'year'}",
            f"Unchanged for over 12 months (since {first}). Get competing quotes; switching or "
            "threatening to leave often cuts 10–20%.",
            monthly=save, txn_ids=[r["id"] for r in s["rows"]][-12:], merchant=s["merchant"],
            category_id=s["category_id"], task_title=f"Get competing quotes for {s['label']}",
        ))
    return out


def detect_unusual_inflow(conn: sqlite3.Connection, cur: str) -> list[dict]:
    """Large one-off inflows in the last 12 months (never auto-excluded)."""
    months = [add_months(cur, -i) for i in range(12)]
    out = []
    for r in unusual_inflows(conn, months):
        out.append(_item(
            "unusual_inflow", f"unusual_inflow:{r['id']}", "med",
            f"Large one-off inflow: {r['description']} ₪{r['amount']:,.0f}",
            f"Received {r['date']} ({r['cashflow_month']}). Large one-off inflow — mark as "
            "one-off so it doesn't inflate income?",
            txn_ids=[r["id"]], merchant=r["merchant"], category_id=r["category_id"],
            task_title=f"Mark {r['description']} ({r['date']}) as one-off",
        ))
    return out


def detect_rent_overdue(conn: sqlite3.Connection, on: date) -> list[dict]:
    """Expected rent from the property that has not arrived (arrears > 0)."""
    from .networth import property_view  # late import: networth imports analytics

    p = property_view(conn, on)
    if not p or not (p.get("rent_arrears") or 0) > 0:
        return []
    months = [a["month"] for a in p["rent_arrears_months"]]
    label = ", ".join(date.fromisoformat(m + "-01").strftime("%b %Y") for m in months)
    arrears = p["rent_arrears"]
    return [_item(
        "rent_overdue", f"rent_overdue:{','.join(months)}", "high",
        f"Rent overdue: ₪{arrears:,.0f} ({label})",
        f"Expected rent for {label} from {p.get('name') or 'the property'} has not arrived "
        f"(₪{arrears:,.0f} outstanding). The mortgage keeps running — follow up with the tenants.",
        task_title=f"Collect overdue rent ₪{arrears:,.0f} ({label})",
    )]


def detect_overdraft_risk(conn: sqlite3.Connection, on: date) -> list[dict]:
    from .analytics import overdraft_risk

    r = overdraft_risk(conn, on)
    if not r:
        return []
    return [_item(
        "overdraft_risk", f"overdraft_risk:{r['month']}", "high",
        f"Overdraft risk: projected −₪{-r['projected']:,.0f} at the end of the month",
        r["text"] + ".",
        task_title=f"Move ₪{r['move_amount']:,.0f} to checking before the month ends",
    )]


def manual_items(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM manual_insights"):
        d = dict(r)
        try:
            ids = json.loads(d.get("txn_ids") or "[]")
        except ValueError:
            ids = []
        st = d.get("suggested_task")
        try:
            st_obj = json.loads(st) if st else None
        except ValueError:
            st_obj = None
        if not isinstance(st_obj, dict):
            st_obj = {"title": st or d["title"]}
        item = _item(d.get("type") or "ai_review", d["key"], d.get("severity") or "med",
                     d["title"], d.get("detail"), monthly=d.get("est_monthly_saving"),
                     yearly=d.get("est_yearly_saving"), txn_ids=ids, merchant=d.get("merchant"),
                     category_id=d.get("category_id"), task_title=st_obj.get("title"))
        item["suggested_task"].update({k: v for k, v in st_obj.items() if v is not None})
        out.append(item)
    return out


# ---------------------------------------------------------------- public API

def all_insights(conn: sqlite3.Connection, on: date | None = None) -> list[dict]:
    on = on or today()
    rows = _load(conn, on)
    rec = _recurring(rows)
    led = Ledger(conn)
    cur = current_month(conn, on)
    items: list[dict] = []
    items += detect_duplicate_charge(rows)
    items += detect_fees(rows)
    items += detect_subscriptions(rec)
    items += detect_price_increase(rec)
    items += detect_category_creep(led, cur)
    items += detect_top_discretionary(rows, conn)
    items += detect_fx_heavy(rows)
    items += detect_insurance_review(conn, rec, on)
    items += detect_unusual_inflow(conn, cur)
    items += detect_rent_overdue(conn, on)
    items += detect_overdraft_risk(conn, on)
    items += manual_items(conn)
    tasks = {r["insight_key"]: r["id"] for r in conn.execute(
        "SELECT id, insight_key FROM tasks WHERE insight_key IS NOT NULL")}
    seen = set()
    uniq = []
    for it in items:
        if it["key"] in seen:
            continue
        seen.add(it["key"])
        it["task_id"] = tasks.get(it["key"])
        uniq.append(it)
    uniq.sort(key=lambda i: (SEVERITY_ORDER.get(i["severity"], 3),
                             -(i["est_yearly_saving"] or 0)))
    return uniq


def list_insights(conn: sqlite3.Connection, on: date | None = None,
                  include_dismissed: bool = False) -> dict:
    items = all_insights(conn, on)
    if not include_dismissed:
        dismissed = {r[0] for r in conn.execute("SELECT insight_key FROM insight_dismissals")}
        items = [i for i in items if i["key"] not in dismissed]
    return {"items": items}


def dismiss(conn: sqlite3.Connection, key: str, reason: str | None = None) -> dict:
    conn.execute(
        "INSERT INTO insight_dismissals(insight_key,dismissed_at,reason) VALUES (?,?,?) "
        "ON CONFLICT(insight_key) DO UPDATE SET dismissed_at=excluded.dismissed_at, "
        "reason=excluded.reason",
        (key, db.now_iso(), reason),
    )
    return {"key": key, "dismissed": True}


def insight_to_task(conn: sqlite3.Connection, key: str, on: date | None = None) -> dict:
    existing = conn.execute("SELECT * FROM tasks WHERE insight_key=?", (key,)).fetchone()
    if existing:
        return dict(existing)
    it = next((i for i in all_insights(conn, on) if i["key"] == key), None)
    if it is None:
        raise KeyError(key)
    st = it["suggested_task"]
    pr = st.get("priority") if st.get("priority") in ("high", "med", "low") else "med"
    cur = conn.execute(
        "INSERT INTO tasks(title,notes,status,priority,est_monthly_saving,merchant,category_id,"
        "insight_key,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (st.get("title") or it["title"], st.get("notes") or it["detail"], "open", pr,
         st.get("est_monthly_saving"), it.get("merchant"), it.get("category_id"), key,
         db.now_iso()),
    )
    return dict(conn.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone())
