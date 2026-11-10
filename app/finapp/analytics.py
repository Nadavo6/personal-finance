"""Month summaries, overspend, trends, anomalies, forecast, affordability, goals, freebies."""

from __future__ import annotations

import calendar
import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, timedelta

from . import db
from .ingest import cashflow_month

HISTORY_MONTHS = 6  # baseline window for medians (complete months)
OVERSPEND_MIN_OVERAGE = 500.0  # a category flags only if it is at least this much over budget
CATEGORY_BUDGET_FLOOR = 200.0  # derived category budgets never go below this
OVERSPEND_MIN_HISTORY = 3  # months of history needed for the total-spend check
UNUSUAL_INFLOW_MIN = 5000.0
REGULAR_INCOME_CATS = ("Salary", "Reserve duty income")
SPEND_KINDS = ("variable", "tracking")
EXPENSE_KINDS = ("fixed", "variable", "tracking")
INCOME_BUCKETS = ("salary", "rental", "reserve_duty", "other")
_BUCKET_BY_CAT = {"Salary": "salary", "Rental income": "rental",
                  "Reserve duty income": "reserve_duty"}


def income_bucket(cat: dict | None) -> str:
    """salary | rental | reserve_duty | other, by income category name."""
    return _BUCKET_BY_CAT.get((cat or {}).get("name"), "other")


# ---------------------------------------------------------------- month helpers

def add_months(m: str, k: int) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    idx = y * 12 + (mo - 1) + k
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def month_diff(a: str, b: str) -> int:
    """b - a in months."""
    return (int(b[:4]) * 12 + int(b[5:7])) - (int(a[:4]) * 12 + int(a[5:7]))


def month_range(a: str, b: str) -> list[str]:
    return [add_months(a, i) for i in range(month_diff(a, b) + 1)] if a <= b else []


def today() -> date:
    return date.today()


def current_month(conn: sqlite3.Connection, on: date | None = None) -> str:
    sd = int(db.get_setting(conn, "cashflow_start_day", "1") or 1)
    return cashflow_month(on or today(), sd)


def median(xs: list[float]) -> float:
    return float(statistics.median(xs)) if xs else 0.0


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 2)


# ---------------------------------------------------------------- aggregation

class Ledger:
    """Per-month aggregates (excluded and transfer rows dropped).

    income: income-kind categories + positive bank rows that have no category yet
      (Uncategorized). Positive rows in an expense-kind category (fixed/variable/tracking) are
      refunds: they reduce that category's spend on any account and never count as income.
    expense: money out in expense categories, net of refunds. Per category per month the net
      spend floors at 0; inflow beyond that month's outflows counts as other income.
    savings: net money moved into savings categories.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.cats = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM categories")}
        self.months: dict[str, dict] = defaultdict(
            lambda: {"income": 0.0, "expense": 0.0, "savings": 0.0, "fixed": 0.0,
                     "variable": 0.0, "installments": 0.0,
                     "income_by": {k: 0.0 for k in INCOME_BUCKETS},
                     "cats": defaultdict(lambda: {"actual": 0.0, "count": 0})}
        )
        self._uncat = {cid for cid, c in self.cats.items() if c["name"] == "Uncategorized"}
        spend_cells: dict[tuple[str, int | None], bool] = {}  # (month, cid) -> fixed?
        q = """
        SELECT t.cashflow_month m, t.category_id cid, c.kind ck, a.kind ak,
               SUM(CASE WHEN t.amount > 0 THEN t.amount ELSE 0 END) pos,
               SUM(CASE WHEN t.amount < 0 THEN t.amount ELSE 0 END) neg,
               SUM(CASE WHEN t.amount < 0 AND COALESCE(t.installment_total,0) > 1
                        THEN t.amount ELSE 0 END) inst,
               COUNT(*) n
        FROM transactions t
        LEFT JOIN categories c ON c.id=t.category_id
        LEFT JOIN accounts a ON a.id=t.account_id
        WHERE t.is_excluded=0 AND t.is_transfer=0 AND t.cashflow_month IS NOT NULL
        GROUP BY m, cid, ck, ak
        """
        for r in conn.execute(q):
            md = self.months[r["m"]]
            ck, pos, neg = r["ck"] or "tracking", r["pos"] or 0.0, r["neg"] or 0.0
            cat = md["cats"][r["cid"]]
            cat["count"] += r["n"]
            if ck == "transfer":
                continue
            if ck == "savings":
                md["savings"] -= pos + neg
                cat["actual"] -= pos + neg
                continue
            if ck == "income":
                md["income"] += pos + neg
                md["income_by"][income_bucket(self.cats.get(r["cid"]))] += pos + neg
                cat["actual"] += pos + neg
                continue
            spent = -neg
            if r["ak"] == "bank" and self._unclassified(r["cid"]):
                # money in with no category yet: income until the user says otherwise
                md["income"] += pos
                md["income_by"]["other"] += pos
            else:
                # refunds / reimbursements (e.g. a roommate's share of the rent) reduce the
                # category's spend and never count as income
                spent -= pos
            cat["actual"] += spent
            md["installments"] += -(r["inst"] or 0.0)
            spend_cells[(r["m"], r["cid"])] = ck == "fixed"
        # Per category per month, net spend floors at 0: refunds / inbound transfers beyond
        # the month's outflows in that category count as other income, never negative spend.
        for (m, cid), is_fixed in spend_cells.items():
            md = self.months[m]
            cat = md["cats"][cid]
            spent = cat["actual"]
            if spent < 0:
                md["income"] += -spent
                md["income_by"]["other"] += -spent
                cat["actual"] = spent = 0.0
            md["expense"] += spent
            md["fixed" if is_fixed else "variable"] += spent
        self.data_months = sorted(self.months)

    def _unclassified(self, cid) -> bool:
        return cid is None or cid in self._uncat or cid not in self.cats

    def first_month(self) -> str | None:
        return self.data_months[0] if self.data_months else None

    def get(self, m: str) -> dict:
        if m in self.months:
            return self.months[m]
        return {"income": 0.0, "expense": 0.0, "savings": 0.0, "fixed": 0.0, "variable": 0.0,
                "installments": 0.0, "income_by": {k: 0.0 for k in INCOME_BUCKETS}, "cats": {}}

    def prior_months(self, m: str, n: int) -> list[str]:
        """Up to n months before m, not earlier than the first data month."""
        first = self.first_month()
        if not first:
            return []
        out = [add_months(m, -i) for i in range(1, n + 1)]
        return [x for x in out if x >= first]

    def cat_actual(self, m: str, cid: int) -> float:
        c = self.get(m)["cats"].get(cid)
        return c["actual"] if c else 0.0

    def median3(self, m: str, cid: int) -> float:
        ms = self.prior_months(m, 3)
        return median([self.cat_actual(x, cid) for x in ms]) if ms else 0.0

    def budget(self, m: str, cid: int) -> float:
        b = self.cats.get(cid, {}).get("monthly_budget")
        return float(b) if b is not None else self.median3(m, cid)

    def history_months(self, m: str, n: int = HISTORY_MONTHS) -> list[str]:
        """Up to n months before m that have data."""
        return [x for x in self.prior_months(m, n) if x in self.months]

    def spend_budget(self, m: str, cid: int) -> float:
        """Budget for a variable/tracking category: explicit, else the median of the trailing
        6 months that had spend in it, floored at CATEGORY_BUDGET_FLOOR."""
        b = self.cats.get(cid, {}).get("monthly_budget")
        if b is not None:
            return float(b)
        spent = [a for x in self.prior_months(m, HISTORY_MONTHS)
                 if (a := self.cat_actual(x, cid)) > 0]
        return max(CATEGORY_BUDGET_FLOOR, median(spent))

    def fixed_baseline(self, cur: str, n: int = HISTORY_MONTHS) -> tuple[float, dict]:
        """Expected monthly fixed spend, summed per fixed category so a charge that started
        recently (e.g. a new mortgage next to the rent) counts in full.

        Per category: 0 if nothing was charged in the last 2 complete months (stopped or
        sporadic), else max(median over n months incl. zeros, mean of the last 3 months)."""
        ms = self.prior_months(cur, n)
        total, by_cat = 0.0, {}
        for cid, cat in self.cats.items():
            if cat["kind"] != "fixed":
                continue
            vals = [self.cat_actual(x, cid) for x in ms]
            if not vals or not any(v > 0 for v in vals[:2]):
                continue
            exp = max(median(vals), sum(vals[:3]) / len(vals[:3]))
            if exp > 0:
                by_cat[cat["name"]] = round(exp, 2)
                total += exp
        return total, by_cat

    def baseline(self, cur: str, n: int = HISTORY_MONTHS) -> dict:
        """Medians over the last n complete months that have data."""
        ms = [x for x in self.prior_months(cur, n) if x in self.months]
        inc = [self.get(x)["income"] for x in ms]
        fix = [self.get(x)["fixed"] for x in ms]
        var = [self.get(x)["variable"] for x in ms]
        inst = [self.get(x)["installments"] for x in ms]
        var_ex = [self.get(x)["variable"] - self.get(x)["installments"] for x in ms]
        fixed, fixed_by_cat = self.fixed_baseline(cur, n)
        return {
            "months": ms, "income": median(inc), "fixed": fixed, "variable": median(var),
            "fixed_median_total": median(fix), "fixed_by_category": fixed_by_cat,
            "variable_p25": percentile(var, 25), "variable_p75": percentile(var, 75),
            "installments": median(inst), "variable_ex_installments": median(var_ex),
        }


# ---------------------------------------------------------------- overspend

def category_over(actual: float, budget: float, pct: float) -> bool:
    """A category is over when it is ≥ ₪500 over budget AND above pct% of it (0/0 never)."""
    if actual <= 0:
        return False
    return actual - budget >= OVERSPEND_MIN_OVERAGE and actual > budget * pct / 100.0


def category_flag(led: Ledger, m: str, cid: int, actual: float, pct: float) -> bool:
    """category_over against the spend budget. A derived (non-explicit) budget needs
    OVERSPEND_MIN_HISTORY months of history, so the first months of data never flag."""
    cat = led.cats.get(cid)
    if not cat or cat["kind"] not in SPEND_KINDS:
        return False
    if cat.get("monthly_budget") is None and len(led.history_months(m)) < OVERSPEND_MIN_HISTORY:
        return False
    return category_over(actual, led.spend_budget(m, cid), pct)


def overspend(led: Ledger, m: str, pct: float) -> tuple[bool, list[str]]:
    """Overspend month (red): (a) expenses > income, OR (b) variable+tracking spend > pct% of
    its trailing 6-month median. Single-category overages are `attention`, not overspend.
    Returns at most 3 reasons, biggest overage first."""
    md = led.get(m)
    found: list[tuple[float, str]] = []
    if md["expense"] > md["income"] and md["expense"] > 0:
        d = md["expense"] - md["income"]
        found.append((d, f"Spent ₪{d:,.0f} more than income"))
    hist = [led.get(x)["variable"] for x in led.history_months(m)]
    if len(hist) >= OVERSPEND_MIN_HISTORY:
        med = median(hist)
        if med > 0 and md["variable"] > med * pct / 100.0:
            found.append((md["variable"] - med,
                          f"Day-to-day spending ₪{md['variable']:,.0f} vs usual ₪{med:,.0f} "
                          f"({md['variable'] / med * 100:.0f}%)"))
    found.sort(key=lambda x: -x[0])
    return bool(found), [t for _, t in found[:3]]


def attention(led: Ledger, m: str, pct: float) -> list[str]:
    """(c) variable/tracking categories ≥ ₪500 over budget AND > pct% of it (amber in the UI).
    At most 3, biggest overage first."""
    md = led.get(m)
    found: list[tuple[float, str]] = []
    for cid, c in md["cats"].items():
        if category_flag(led, m, cid, c["actual"], pct):
            cat, b = led.cats[cid], led.spend_budget(m, cid)
            share = f" ({c['actual'] / b * 100:.0f}%)" if b > 0 else ""
            found.append((c["actual"] - b,
                          f"{cat['name']}: ₪{c['actual']:,.0f} vs budget ₪{b:,.0f}{share}"))
    found.sort(key=lambda x: -x[0])
    return [t for _, t in found[:3]]


def _settings_num(conn, key: str, default: float) -> float:
    try:
        return float(db.get_setting(conn, key, str(default)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- balance

def balance(conn: sqlite3.Connection, upto: str | None = None) -> dict:
    """Σ latest known balance per bank account (on or before `upto` 'YYYY-MM-DD' if given)."""
    total, as_of, found = 0.0, None, False
    for a in conn.execute("SELECT id FROM accounts WHERE kind='bank'"):
        last = conn.execute(
            "SELECT MAX(date) FROM transactions WHERE account_id=? AND balance_after IS NOT NULL"
            + (" AND date<=?" if upto else ""),
            (a["id"], upto) if upto else (a["id"],),
        ).fetchone()[0]
        if not last:
            continue
        rs = conn.execute(
            "SELECT amount, balance_after FROM transactions WHERE account_id=? AND date=? "
            "AND balance_after IS NOT NULL ORDER BY rowid",
            (a["id"], last),
        ).fetchall()
        befores = {round(r["balance_after"] - r["amount"], 2) for r in rs}
        final = next((r for r in rs if round(r["balance_after"], 2) not in befores), rs[0])
        total += final["balance_after"]
        found = True
        as_of = max(as_of or last, last)
    return {"amount": _r(total) if found else None, "as_of": as_of}


def pending_card_charges(conn: sqlite3.Connection, as_of: str | None, cur: str) -> float:
    """Card spending counted up to the current month but billed after the balance date."""
    if not as_of:
        return 0.0
    r = conn.execute(
        """SELECT COALESCE(SUM(-t.amount),0) FROM transactions t
           JOIN accounts a ON a.id=t.account_id
           LEFT JOIN categories c ON c.id=t.category_id
           WHERE a.kind='card' AND t.is_excluded=0 AND t.is_transfer=0
             AND COALESCE(c.kind,'') NOT IN ('transfer','savings')
             AND t.billing_date > ? AND t.cashflow_month <= ?""",
        (as_of, cur),
    ).fetchone()[0]
    return float(r or 0.0)


# ---------------------------------------------------------------- goals

def _parse_d(s) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _month_bounds(m: str) -> tuple[str, str]:
    y, mo = int(m[:4]), int(m[5:7])
    return f"{m}-01", f"{m}-{calendar.monthrange(y, mo)[1]:02d}"


def linked_asset(conn: sqlite3.Connection, asset_id) -> dict | None:
    """{id, name, value, as_of} of a goal's linked asset (latest value), or None."""
    if not asset_id:
        return None
    from . import networth  # late import: networth imports this module

    a = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    if a is None:
        return None
    v = networth.latest_asset_value(conn, dict(a)) or {}
    return {"id": a["id"], "name": a["name"], "key": a["key"], "value": _r(v.get("value")),
            "as_of": v.get("as_of")}


def goal_put_away(conn: sqlite3.Connection, g: dict, m: str) -> dict:
    """Money put toward a goal in month m: outflows into savings-kind categories, or the linked
    asset's rise over the month when a value dated in m exists — whichever is larger, so a
    deposit that also shows up in the asset's value is not counted twice."""
    out_ = float(conn.execute(
        """SELECT COALESCE(SUM(-t.amount),0) FROM transactions t
           JOIN categories c ON c.id=t.category_id
           WHERE c.kind='savings' AND t.amount<0 AND t.is_excluded=0 AND t.is_transfer=0
             AND t.cashflow_month=?""", (m,)).fetchone()[0] or 0.0)
    change = None
    if g.get("asset_id"):
        from . import networth

        a = conn.execute("SELECT * FROM assets WHERE id=?", (g["asset_id"],)).fetchone()
        if a is not None:
            start, end = _month_bounds(m)
            prev_end = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
            now_v = networth.latest_asset_value(conn, dict(a), end)
            before = networth.latest_asset_value(conn, dict(a), prev_end)
            if now_v and before and now_v["as_of"] and now_v["as_of"] >= start:
                change = now_v["value"] - before["value"]
    put = max(out_, change) if change is not None and change > 0 else out_
    return {"month": m, "amount": _r(put), "savings_outflows": _r(out_),
            "asset_change": _r(change)}


GOAL_HORIZON_MONTHS = 600  # projection stops after 50 years without reaching the target
SCHEDULE_MONTHS = 12        # contribution_by_month: the current month + 11


def _goal_steps_raw(g: dict) -> list[dict]:
    """goals.step_ups (JSON text or an already parsed list) as a list of dicts."""
    raw = g.get("step_ups")
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict) and x.get("from_month")]


def _num(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def goal_step_ups(g: dict, ends: list[dict] | None = None) -> list[dict]:
    """A goal's step-ups, sorted by from_month, with the amount each adds per month from then on.
    Linked to a fixed end (`fixed_end_id`): that charge's current monthly_amount, so the step-up
    tracks the real charge; once the charge has stopped (amount 0) the stored add_amount (the
    snapshot taken when it was saved) applies."""
    by_id = {e["id"]: e for e in ends or []}
    out = []
    for x in _goal_steps_raw(g):
        stored = _num(x.get("add_amount"))
        fid = x.get("fixed_end_id") or None
        fe = by_id.get(fid) if fid else None
        amt, src = (stored or 0.0), "manual"
        if fe and (fe.get("monthly_amount") or 0) > 0:
            amt, src = float(fe["monthly_amount"]), "fixed_end"
        elif fid and stored is not None:
            src = "fixed_end_saved"
        out.append({"from_month": str(x["from_month"])[:7], "add_amount": _r(amt),
                    "saved_add_amount": _r(stored), "reason": x.get("reason") or None,
                    "fixed_end_id": fid, "amount_source": src,
                    "fixed_end": ({"id": fe["id"], "what": fe.get("what"),
                                   "last_month": fe.get("last_month"),
                                   "monthly_amount": fe.get("monthly_amount")} if fe else None)})
    out.sort(key=lambda s_: s_["from_month"])
    return out


def _has_planned_contribution(g: dict) -> bool:
    return g.get("monthly_contribution") is not None or bool(_goal_steps_raw(g))


def scheduled_contribution(base: float, steps: list[dict], m: str) -> float:
    """Monthly contribution scheduled for month m: base + every step-up already started."""
    return base + sum(s["add_amount"] or 0.0 for s in steps if s["from_month"] <= m)


def goal_projection(remaining: float, base: float, steps: list[dict], cur: str,
                    months: int = SCHEDULE_MONTHS) -> tuple[list[dict], str | None]:
    """([{month, amount}] for `months` months from cur, completion month or None).
    Contributions count from the current month on and stop once the target is reached
    (the last one is only what is still missing)."""
    rem = max(0.0, remaining)
    completion = cur if rem <= 0.005 else None
    by_month = []
    for i in range(max(months, GOAL_HORIZON_MONTHS)):
        m = add_months(cur, i)
        amt = min(max(0.0, scheduled_contribution(base, steps, m)), rem)
        rem -= amt
        if i < months:
            by_month.append({"month": m, "amount": _r(amt)})
        if completion is None and rem <= 0.005:
            completion = m
        if i >= months - 1 and (completion is not None or rem <= 0.005):
            break
    return by_month, completion


def _vs_target(completion: str | None, target_m: str | None) -> str | None:
    if not target_m:
        return None
    if completion is None:
        return "not reached"
    d = month_diff(target_m, completion)
    if d == 0:
        return "on time"
    n = abs(d)
    return f"{n} month{'s' if n != 1 else ''} {'late' if d > 0 else 'early'}"


def goal_ends(conn: sqlite3.Connection, on: date | None = None,
              led: "Ledger | None" = None) -> list[dict]:
    """fixed_ends as the forecast sees them now (for step-ups linked to one)."""
    return fixed_ends(conn, led or Ledger(conn), current_month(conn, on))


def goal_fields(g: dict, on: date | None = None, conn: sqlite3.Connection | None = None,
                ends: list[dict] | None = None) -> dict:
    """Goal + monthly_need, months_left, progress_pct, on_track, contribution schedule. With
    `conn` and a linked asset (goals.asset_id), saved_amount is that asset's latest value
    (`saved_source: 'asset'`).

    Scheduled contribution per month = monthly_contribution (else monthly_need) + step-ups
    that have started. projected_completion_month = when saved + scheduled contributions reach
    the target. A goal with a plan (monthly_contribution or step-ups) is on track when that month
    is ≤ the target month; without a plan, when saved ≥ the straight-line expectation by now."""
    on = on or today()
    target = float(g.get("target_amount") or 0)
    saved = float(g.get("saved_amount") or 0)
    link = linked_asset(conn, g.get("asset_id")) if conn is not None else None
    if link and link["value"] is not None:
        saved = float(link["value"])
    remaining = max(0.0, target - saved)
    td = _parse_d(g.get("target_date"))
    now_m = current_month(conn, on) if conn is not None else f"{on.year:04d}-{on.month:02d}"
    target_m = f"{td.year:04d}-{td.month:02d}" if td else None
    months_left = None
    monthly_need = g.get("monthly_contribution")
    if td:
        months_left = max(1, month_diff(now_m, target_m))
        monthly_need = remaining / months_left
    elif monthly_need is None:
        monthly_need = 0.0
    progress = (saved / target * 100.0) if target > 0 else 0.0
    expected = None
    created = _parse_d(g.get("created_at"))
    if td and created and td > created:
        total = (td - created).days
        elapsed = min(max((on - created).days, 0), total)
        expected = target * elapsed / total
    if conn is not None and ends is None and any(
            x.get("fixed_end_id") for x in _goal_steps_raw(g)):
        ends = goal_ends(conn, on)
    steps = goal_step_ups(g, ends)
    contrib = _num(g.get("monthly_contribution"))
    base = contrib if contrib is not None else float(monthly_need or 0.0)
    active = (g.get("status") or "active") == "active"
    by_month, completion = goal_projection(remaining if active else 0.0, base, steps, now_m)
    if not active and remaining > 0:
        completion = None
    planned = _has_planned_contribution(g)
    if g.get("status") == "done" or saved >= target > 0:
        on_track = True
    elif planned and target_m:
        on_track = completion is not None and completion <= target_m
    elif contrib is not None and monthly_need is not None and contrib >= monthly_need - 0.5:
        on_track = True
    elif expected is not None:
        on_track = saved >= expected - 1
    else:
        on_track = True
    out = dict(g)
    out.update(
        monthly_need=_r(monthly_need), months_left=months_left, progress_pct=round(progress, 1),
        on_track=on_track, expected_by_now=_r(expected), remaining=_r(remaining),
        step_ups=steps, contribution_now=by_month[0]["amount"] if by_month else 0.0,
        contribution_by_month=by_month, projected_completion_month=completion,
        projected_vs_target=_vs_target(completion, target_m) if active else None,
        target_month=target_m, has_plan=planned,
    )
    if conn is not None:
        out["linked_asset"] = link
        out["saved_source"] = "asset" if link and link["value"] is not None else "manual"
        if link and link["value"] is not None:
            out["saved_amount"] = _r(saved)
            out["manual_saved_amount"] = _r(g.get("saved_amount"))
        if active:
            out["put_away_this_month"] = goal_put_away(conn, g, now_m)
    return out


def goal_plan(conn: sqlite3.Connection, on: date | None = None, months: int = SCHEDULE_MONTHS,
              ends: list[dict] | None = None, led: "Ledger | None" = None) -> dict[str, float]:
    """Total scheduled contribution of all active goals by month, for `months` months from the
    current month (each goal stops once its target is projected to be reached)."""
    cur = current_month(conn, on)
    goals = [dict(r) for r in conn.execute("SELECT * FROM goals WHERE status='active'")]
    if ends is None and any(x.get("fixed_end_id") for g in goals for x in _goal_steps_raw(g)):
        ends = goal_ends(conn, on, led)
    tot: dict[str, float] = {add_months(cur, i): 0.0 for i in range(max(1, months))}
    for g in goals:
        gf = _goal_need_only(conn, g, on)
        steps = goal_step_ups(g, ends)
        contrib = _num(g.get("monthly_contribution"))
        base = contrib if contrib is not None else float(gf["monthly_need"] or 0.0)
        by_month, _ = goal_projection(float(gf["remaining"] or 0.0), base, steps, cur,
                                      max(1, months))
        for x in by_month:
            tot[x["month"]] += x["amount"] or 0.0
    return tot


def goals_reserved(conn: sqlite3.Connection, on: date | None = None, month: str | None = None,
                   ends: list[dict] | None = None, led: "Ledger | None" = None) -> float:
    """Goal contributions scheduled for `month` (default: the current month; a past month counts
    as the current one)."""
    cur = current_month(conn, on)
    m = month if month and month > cur else cur
    return goal_plan(conn, on, month_diff(cur, m) + 1, ends, led).get(m, 0.0)


def goal_contributions(conn: sqlite3.Connection, on: date | None = None) -> float:
    """Scheduled goal contributions of the current month."""
    return goals_reserved(conn, on)


def _goal_need_only(conn, g: dict, on: date | None) -> dict:
    """goal_fields with the linked asset's value as saved, without the put-away lookup."""
    link = linked_asset(conn, g.get("asset_id"))
    if link and link["value"] is not None:
        g = {**g, "saved_amount": link["value"]}
    return goal_fields(g, on)


# ---------------------------------------------------------------- freebies

def freebie_fields(conn: sqlite3.Connection, f: dict, on: date | None = None,
                   persist: bool = True) -> dict:
    on = on or today()
    out = dict(f)
    exp = _parse_d(f.get("expiry_date"))
    out["days_left"] = (exp - on).days if exp else None
    used = float(f.get("used_amount") or 0)
    linked = 0.0
    if f.get("account_id"):
        q = "SELECT COALESCE(SUM(-amount),0) FROM transactions WHERE account_id=? AND amount<0"
        params: list = [f["account_id"]]
        if f.get("received_date"):
            q += " AND date>=?"
            params.append(str(f["received_date"])[:10])
        linked = float(conn.execute(q, params).fetchone()[0] or 0)
    out["manual_used_amount"] = _r(used)
    out["linked_used_amount"] = _r(linked)
    out["linked_used"] = out["linked_used_amount"]  # alias read by the UI
    out["used_amount"] = _r(used + linked)
    value = f.get("value")
    out["remaining"] = _r(float(value) - used - linked) if value is not None else None
    status = f.get("status") or "active"
    if status == "active":
        if exp and exp < on:
            status = "expired"
        elif f.get("account_id") and value is not None and out["remaining"] <= 0:
            status = "used"
        if status != f.get("status") and persist and f.get("id"):
            conn.execute("UPDATE freebies SET status=? WHERE id=?", (status, f["id"]))
            conn.commit()
    out["status"] = status
    return out


def list_freebies(conn: sqlite3.Connection, on: date | None = None) -> list[dict]:
    fs = [freebie_fields(conn, dict(r), on) for r in conn.execute("SELECT * FROM freebies")]
    fs.sort(key=lambda x: (x["expiry_date"] is None, x["expiry_date"] or "", x["id"]))
    return fs


# ---------------------------------------------------------------- overview

def overview(conn: sqlite3.Connection, on: date | None = None) -> dict:
    led = Ledger(conn)
    cur = current_month(conn, on)
    pct = _settings_num(conn, "overspend_pct", 120)
    months = []
    first = led.first_month()
    if first:
        start = max(first, add_months(cur, -23))
        for m in month_range(start, cur):
            md = led.get(m)
            over, reasons = overspend(led, m, pct)
            months.append({
                "month": m, "income": _r(md["income"]), "expense": _r(md["expense"]),
                "savings": _r(md["savings"]),
                "net": _r(md["income"] - md["expense"] - md["savings"]),
                "overspend": over, "reasons": reasons, "attention": attention(led, m, pct),
            })
    warn = _settings_num(conn, "freebie_warn_days", 30)
    expiring = [f for f in list_freebies(conn, on)
                if f["status"] == "active" and f["days_left"] is not None
                and 0 <= f["days_left"] <= warn]
    unc = conn.execute(
        "SELECT COUNT(*) FROM transactions t LEFT JOIN categories c ON c.id=t.category_id "
        "WHERE " + db.UNCATEGORIZED_WHERE
    ).fetchone()[0]
    from .insights import list_insights  # late import: insights imports this module

    open_tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='open'").fetchone()[0]
    try:
        n_insights = len(list_insights(conn, on)["items"])
    except Exception:  # a detector bug must not take the overview down
        n_insights = None
    li = conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 1").fetchone()
    ls = db.get_setting(conn, "last_sync")
    try:
        last_sync = json.loads(ls) if ls else None
    except ValueError:
        last_sync = ls
    return {
        "current_month": cur,
        "months": months,
        "balance": balance(conn),
        "freebies_expiring": expiring,
        "uncategorized_count": unc,
        "open_tasks_count": open_tasks,
        "insights_count": n_insights,
        "last_import": dict(li) if li else None,
        "last_sync": last_sync,
        "goals_reserved": _r(goals_reserved(conn, on)),
        "net_worth": _net_worth_brief(conn, on),
    }


def _net_worth_brief(conn, on) -> dict | None:
    from . import networth  # late import: networth imports this module

    try:
        return networth.brief(conn, on)
    except Exception:  # never take the overview down
        return None


def _networth_inputs(conn, on, cur: str) -> tuple[dict[str, float], float]:
    """(open planned items' net amount by month ≥ cur, liquid assets beyond the bank balance)."""
    from . import networth

    return networth.planned_by_month(conn, cur), networth.liquid_extra(conn, on)


# ---------------------------------------------------------------- month

def month_summary(conn: sqlite3.Connection, m: str, on: date | None = None) -> dict:
    on = on or today()
    led = Ledger(conn)
    cur = current_month(conn, on)
    pct = _settings_num(conn, "overspend_pct", 120)
    md = led.get(m)
    cats_out = []
    fixed_out = []
    remaining_fixed = 0.0
    for cid, cat in sorted(led.cats.items(), key=lambda kv: kv[1]["name"]):
        if cat["kind"] == "transfer":
            continue
        c = md["cats"].get(cid, {"actual": 0.0, "count": 0})
        actual = c["actual"]
        spend_cat = cat["kind"] in SPEND_KINDS
        b = led.spend_budget(m, cid) if spend_cat else led.budget(m, cid)
        # spend budgets are floored, so an idle spend category only shows with an explicit one
        if (not actual and not c["count"] and cat.get("monthly_budget") is None
                and (spend_cat or not b)):
            continue
        m3 = led.median3(m, cid)
        p = (actual / b * 100.0) if b > 0 else None
        over = category_flag(led, m, cid, actual, pct)
        cats_out.append({
            "id": cid, "name": cat["name"], "name_he": cat["name_he"], "kind": cat["kind"],
            "color": cat["color"], "actual": _r(actual), "budget": _r(b), "median3": _r(m3),
            "pct": round(p, 1) if p is not None else None, "over": over, "count": c["count"],
        })
        if cat["kind"] == "fixed" and (b or actual):
            fixed_out.append({"id": cid, "name": cat["name"], "name_he": cat["name_he"],
                              "expected": _r(b), "actual": _r(actual)})
            remaining_fixed += max(0.0, b - actual)
    cats_out.sort(key=lambda x: -(x["actual"] or 0))
    top = [
        {"merchant": r["merchant"], "label": r["label"], "amount": _r(r["amt"]), "count": r["n"]}
        for r in conn.execute(
            """SELECT t.merchant, MAX(t.description) label, SUM(-t.amount) amt, COUNT(*) n
               FROM transactions t
               LEFT JOIN categories c ON c.id=t.category_id
               WHERE t.cashflow_month=? AND t.is_excluded=0 AND t.is_transfer=0
                 AND COALESCE(c.kind,'') NOT IN ('transfer','savings','income')
                 AND t.amount<0
               GROUP BY t.merchant ORDER BY amt DESC LIMIT 10""",
            (m,),
        )
    ]
    reserved = goals_reserved(conn, on, m, led=led)
    income = md["income"]
    is_current = m == cur
    expected_income = income
    if is_current:
        # salary baseline + expected rent (irregular income only when the setting says so)
        expected_income = max(income, income_for_month(income_baseline(conn, led, m), m)[0])
    else:
        remaining_fixed = 0.0
    left = expected_income - md["expense"] - md["savings"] - reserved - remaining_fixed
    over, reasons = overspend(led, m, pct)
    out = {
        "month": m, "is_current": is_current, "income": _r(income),
        "expected_income": _r(expected_income), "expense": _r(md["expense"]),
        "savings": _r(md["savings"]), "net": _r(income - md["expense"] - md["savings"]),
        "left_to_spend": _r(left), "goals_reserved": _r(reserved),
        "remaining_fixed": _r(remaining_fixed), "overspend": over, "reasons": reasons,
        "attention": attention(led, m, pct),
        "one_offs": one_offs(conn, m),
        "income_breakdown": {k: _r(v) for k, v in md["income_by"].items()},
        "goal_progress": goal_progress(conn, m, on, led),
        "categories": cats_out, "top_merchants": top, "anomalies": anomalies(conn, m, led),
        "fixed": fixed_out,
    }
    if is_current:
        try:
            out["overdraft_risk"] = overdraft_risk(conn, on)
        except Exception:  # never take the month view down
            out["overdraft_risk"] = None
        sd = int(db.get_setting(conn, "cashflow_start_day", "1") or 1)
        end = _month_end_date(m, sd)
        days_left = max(0, (end - on).days + 1)
        out["days_left"] = days_left
        out["daily_pace"] = _r(left / days_left) if days_left else None
    return out


def goal_progress(conn: sqlite3.Connection, m: str, on: date | None = None,
                  led: "Ledger | None" = None) -> list[dict]:
    """Active goals: what was put away toward them in month m vs this month's need and the
    contribution scheduled for m (`contribution`; a past month uses the current one)."""
    out = []
    goals = [dict(r) for r in conn.execute(
        "SELECT * FROM goals WHERE status='active' ORDER BY priority DESC, id")]
    ends = (goal_ends(conn, on, led)
            if any(x.get("fixed_end_id") for g in goals for x in _goal_steps_raw(g)) else None)
    cur = current_month(conn, on)
    for g in goals:
        gf = goal_fields(g, on, conn, ends)
        put = gf.get("put_away_this_month") if m == cur else goal_put_away(conn, g, m)
        sched = {x["month"]: x["amount"] for x in gf["contribution_by_month"]}
        out.append({"goal_id": g["id"], "name": g["name"], "put_away": put["amount"],
                    "monthly_need": gf["monthly_need"], "contribution_now": gf["contribution_now"],
                    "contribution": sched.get(max(m, cur), gf["contribution_now"]),
                    "detail": put, "linked_asset": gf["linked_asset"]})
    return out


def one_offs(conn: sqlite3.Connection, m: str) -> dict:
    """Excluded (one-off) non-transfer rows of month m, shown apart from income/expense."""
    r = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN amount>0 THEN amount END),0) inc,
                  COALESCE(SUM(CASE WHEN amount<0 THEN -amount END),0) exp, COUNT(*) n
           FROM transactions WHERE cashflow_month=? AND is_excluded=1 AND is_transfer=0""",
        (m,)).fetchone()
    return {"income": _r(r["inc"]), "expense": _r(r["exp"]), "count": r["n"]}


def _month_end_date(m: str, start_day: int) -> date:
    """Last calendar day of cashflow month m."""
    if start_day <= 1:
        y, mo = int(m[:4]), int(m[5:7])
        return date(y, mo, calendar.monthrange(y, mo)[1])
    # period named m starts on start_day of m (<=15) or of m-1 (>15); ends the day before next
    start_m = m if start_day <= 15 else add_months(m, -1)
    nxt = add_months(start_m, 1)
    return date(int(nxt[:4]), int(nxt[5:7]), start_day) - timedelta(days=1)


# ---------------------------------------------------------------- anomalies

def unusual_inflows(conn: sqlite3.Connection, months: list[str]) -> list[dict]:
    """Large one-off inflows in `months`: positive, non-transfer, non-excluded rows that count
    as income (bank rows or income categories), ≥ UNUSUAL_INFLOW_MIN, not Salary / Reserve duty
    income, and from a merchant without positive rows in ≥ 3 months within ±11 months."""
    if not months:
        return []
    q = f"""SELECT t.id, t.amount, t.merchant, t.description, t.date, t.cashflow_month,
                   t.category_id, c.name cat
            FROM transactions t
            LEFT JOIN categories c ON c.id=t.category_id
            LEFT JOIN accounts a ON a.id=t.account_id
            WHERE t.is_excluded=0 AND t.is_transfer=0 AND t.amount >= ?
              AND COALESCE(c.kind,'') NOT IN ('transfer','savings')
              AND (c.kind='income' OR (a.kind='bank' AND (c.id IS NULL
                                                         OR c.name='Uncategorized')))
              AND COALESCE(c.name,'') NOT IN ({','.join('?' * len(REGULAR_INCOME_CATS))})
              AND t.cashflow_month IN ({','.join('?' * len(months))})
            ORDER BY t.date"""
    cands = [dict(r) for r in conn.execute(q, (UNUSUAL_INFLOW_MIN, *REGULAR_INCOME_CATS,
                                               *months))]
    if not cands:
        return []
    seen: dict[str, set[str]] = defaultdict(set)
    for r in conn.execute(
            "SELECT merchant, cashflow_month FROM transactions WHERE amount>0 AND is_transfer=0 "
            "AND merchant IN (%s)" % ",".join("?" * len({c["merchant"] for c in cands})),
            sorted({c["merchant"] for c in cands})):
        seen[r["merchant"]].add(r["cashflow_month"])
    out = []
    for c in cands:
        lo, hi = add_months(c["cashflow_month"], -11), add_months(c["cashflow_month"], 11)
        recent = {x for x in seen.get(c["merchant"], ()) if x and lo <= x <= hi}
        if len(recent) < 3:
            out.append(c)
    return out


def anomalies(conn: sqlite3.Connection, m: str, led: Ledger | None = None) -> list[dict]:
    led = led or Ledger(conn)
    out = []
    base = """SELECT t.id, t.amount, t.merchant, t.description, t.category_id, t.date,
                     t.cashflow_month, c.kind ck
              FROM transactions t LEFT JOIN categories c ON c.id=t.category_id
              WHERE t.is_excluded=0 AND t.is_transfer=0 AND t.amount<0
                AND COALESCE(c.kind,'') NOT IN ('transfer','savings','income')"""
    rows = [dict(r) for r in conn.execute(base + " AND t.cashflow_month=?", (m,))]
    hist_from = add_months(m, -6)
    hist: dict[int, list[float]] = defaultdict(list)
    for r in conn.execute(base + " AND t.cashflow_month>=? AND t.cashflow_month<?",
                          (hist_from, m)):
        hist[r["category_id"]].append(-r["amount"])
    for r in rows:
        h = hist.get(r["category_id"], [])
        amt = -r["amount"]
        if len(h) >= 5 and amt >= 200 and amt > 3 * median(h):
            cname = led.cats.get(r["category_id"], {}).get("name", "?")
            out.append({"type": "large_txn",
                        "text": f"₪{amt:,.0f} · over 3× the usual {cname} charge "
                                f"(₪{median(h):,.0f}) — {r['description']}",
                        "txn_ids": [r["id"]]})
    first = led.first_month()
    if first and month_diff(first, m) >= 3:
        seen = {r[0] for r in conn.execute(
            "SELECT DISTINCT merchant FROM transactions WHERE cashflow_month<?", (m,))}
        for r in rows:
            if r["merchant"] not in seen and -r["amount"] >= 500:
                out.append({"type": "new_merchant",
                            "text": f"₪{-r['amount']:,.0f} · new merchant — {r['description']}",
                            "txn_ids": [r["id"]]})
    for r in unusual_inflows(conn, [m]):
        out.append({"type": "unusual_inflow",
                    "text": f"₪{r['amount']:,.0f} · large one-off inflow — {r['description']}. "
                            "Mark as one-off so it doesn't inflate income?",
                    "txn_ids": [r["id"]]})
    md = led.get(m)
    for cid, c in md["cats"].items():
        cat = led.cats.get(cid)
        if not cat or cat["kind"] != "fixed":
            continue
        m3 = led.median3(m, cid)
        if m3 > 0 and c["actual"] > m3 * 1.1 and c["actual"] - m3 > 20:
            ids = [r["id"] for r in rows if r["category_id"] == cid]
            out.append({"type": "fixed_increase",
                        "text": f"{cat['name']} rose to ₪{c['actual']:,.0f} "
                                f"(usually ₪{m3:,.0f})",
                        "txn_ids": ids})
    return out


# ---------------------------------------------------------------- trends

def trends(conn: sqlite3.Connection, months: int = 12, on: date | None = None) -> dict:
    led = Ledger(conn)
    cur = current_month(conn, on)
    ms = month_range(add_months(cur, -(months - 1)), cur)
    cats = []
    for cid, cat in sorted(led.cats.items(), key=lambda kv: kv[1]["name"]):
        if cat["kind"] == "transfer":
            continue
        series = [_r(led.cat_actual(m, cid)) for m in ms]
        if any(series):
            cats.append({"id": cid, "name": cat["name"], "name_he": cat["name_he"],
                         "kind": cat["kind"], "color": cat["color"], "series": series})
    cats.sort(key=lambda c: -sum(c["series"]))
    return {
        "months": ms,
        "income": [_r(led.get(m)["income"]) for m in ms],
        "expense": [_r(led.get(m)["expense"]) for m in ms],
        "savings": [_r(led.get(m)["savings"]) for m in ms],
        "categories": cats,
    }


# ---------------------------------------------------------------- income baseline

def _setting_bool(conn, key: str) -> bool:
    return str(db.get_setting(conn, key, "false") or "").strip().lower() in ("1", "true", "yes", "on")


def _setting_float(conn, key: str) -> float | None:
    v = db.get_setting(conn, key)
    try:
        return float(v) if v not in (None, "", "null", "None") else None
    except (TypeError, ValueError):
        return None


def expected_rent(conn: sqlite3.Connection) -> tuple[dict[str, float], str | None, float | None]:
    """(expected rent by month, first month, steady monthly rent) of the first property.

    Months listed in the property's rental_income plan use their amount; months after the last
    listed one carry its amount forward (the flat stays let); months before the first get 0."""
    r = conn.execute("SELECT data_json FROM properties ORDER BY id LIMIT 1").fetchone()
    if r is None or not r["data_json"]:
        return {}, None, None
    try:
        d = json.loads(r["data_json"]) or {}
    except ValueError:
        return {}, None, None
    plan = {}
    for e in d.get("rental_income") or []:
        m, amt = str(e.get("month") or "")[:7], e.get("amount")
        if len(m) == 7 and isinstance(amt, (int, float)):
            plan[m] = float(amt)
    if not plan:
        return {}, None, None
    first = min(plan)
    return plan, first, plan[max(plan)]


def rent_for_month(plan: tuple[dict[str, float], str | None, float | None], m: str) -> float:
    by_month, first, steady = plan
    if not first or m < first:
        return 0.0
    if m in by_month:
        return by_month[m]
    return steady if m > max(by_month) else 0.0


def income_baseline(conn: sqlite3.Connection, led: "Ledger", cur: str) -> dict:
    """Forecast income: net salary baseline (setting, else median Salary of the last complete
    months) + the property's expected rent for the month. Reserve duty, bonuses (salary above the
    baseline) and other income are irregular: shown as `extra` lines (monthly averages) and only
    added when `count_variable_income_in_forecast` is true."""
    ms = led.history_months(cur)
    sal = [led.get(x)["income_by"]["salary"] for x in ms]
    setting = _setting_float(conn, "salary_net_baseline")
    salary = setting if setting is not None else median(sal)
    avg = (lambda xs: sum(xs) / len(xs) if xs else 0.0)
    extra = {
        "reserve_duty": avg([led.get(x)["income_by"]["reserve_duty"] for x in ms]),
        "bonus": avg([max(0.0, v - salary) for v in sal]) if setting is not None else 0.0,
        "other": avg([led.get(x)["income_by"]["other"] for x in ms]),
    }
    include = _setting_bool(conn, "count_variable_income_in_forecast")
    plan = expected_rent(conn)
    return {"salary": salary, "salary_source": "setting" if setting is not None else "median",
            "extra": extra, "extra_total": sum(extra.values()), "include_extra": include,
            "rent_plan": plan, "months": ms}


def _property_insurance(conn) -> float | None:
    r = conn.execute("SELECT data_json FROM properties ORDER BY id LIMIT 1").fetchone()
    try:
        v = (json.loads(r["data_json"]) or {}).get("insurance_monthly") if r and r["data_json"] else None
    except ValueError:
        return None
    return float(v) if isinstance(v, (int, float)) else None


def rent_costs(ib: dict, m: str) -> tuple[float, float]:
    """(rental-tax reserve, flat insurance) the forecast counts as fixed outflows in month m,
    while rent is expected that month."""
    if rent_for_month(ib["rent_plan"], m) <= 0:
        return 0.0, 0.0
    return rent_for_month(ib["rent_plan"], m) * ib["tax_pct"] / 100.0, ib["flat_insurance"]


def income_for_month(ib: dict, m: str) -> tuple[float, float]:
    """(baseline income for month m, of which expected rent)."""
    rent = rent_for_month(ib["rent_plan"], m)
    return ib["salary"] + rent + (ib["extra_total"] if ib["include_extra"] else 0.0), rent


def _income_out(ib: dict, cur: str) -> dict:
    inc, rent = income_for_month(ib, cur)
    tax, ins = rent_costs(ib, cur) if "tax_pct" in ib else (0.0, 0.0)
    return {"salary": _r(ib["salary"]), "salary_source": ib["salary_source"],
            "rental_expected": _r(rent), "total": _r(inc),
            "rental_tax_reserve": _r(tax), "rental_tax_pct": ib.get("tax_pct"),
            "flat_insurance": _r(ins), "flat_insurance_in_fixed": ib.get("flat_insurance_in_fixed"),
            "extra_income": [{"name": k, "monthly_avg": _r(v)} for k, v in ib["extra"].items()],
            "extra_included": ib["include_extra"]}


# ---------------------------------------------------------------- fixed charges with an end

def fixed_ends(conn: sqlite3.Connection, led: "Ledger", cur: str) -> list[dict]:
    """Fixed charges with a known last month, with the monthly amount the forecast drops after
    it: a whole category's fixed baseline, or (merchant set) that merchant's share of it —
    max(6-month median incl. zeros, last-3-month mean), 0 if not charged in the last 2 months."""
    _, by_cat = led.fixed_baseline(cur)
    ms = led.prior_months(cur, HISTORY_MONTHS)
    out = []
    for r in conn.execute("""SELECT f.*, c.name cat FROM fixed_ends f
                             LEFT JOIN categories c ON c.id=f.category_id ORDER BY f.id"""):
        d = dict(r)
        cat_amt = by_cat.get(d["cat"], 0.0) if d["cat"] else None
        if d.get("merchant"):
            pat = db.normalize_merchant(d["merchant"])
            q = """SELECT t.cashflow_month m, SUM(-t.amount) s FROM transactions t
                   WHERE t.is_excluded=0 AND t.is_transfer=0 AND t.merchant LIKE ?"""
            params: list = [f"%{pat}%"]
            if d["category_id"]:
                q += " AND t.category_id=?"
                params.append(d["category_id"])
            got = {x["m"]: x["s"] for x in conn.execute(q + " GROUP BY 1", params)}
            vals = [max(0.0, got.get(x, 0.0) or 0.0) for x in ms]
            amt = 0.0
            if vals and any(v > 0 for v in vals[:2]):
                amt = max(median(vals), sum(vals[:3]) / len(vals[:3]))
            if cat_amt is not None:
                amt = min(amt, cat_amt)
            what = d["merchant"]
        else:
            amt, what = cat_amt or 0.0, d["cat"] or "?"
        out.append({"id": d["id"], "category_id": d["category_id"], "category": d["cat"],
                    "merchant": d.get("merchant"), "what": what, "last_month": d["last_month"],
                    "notes": d.get("notes"), "monthly_amount": _r(amt),
                    "ended": d["last_month"] < cur})
    return out


def fixed_drop(ends: list[dict], m: str) -> float:
    """Fixed spend that no longer applies in month m."""
    return sum(e["monthly_amount"] or 0.0 for e in ends if m > e["last_month"])


# ---------------------------------------------------------------- forecast

def known_installments(conn: sqlite3.Connection, cur: str) -> dict[str, float]:
    """Remaining installments of running installment plans, by future cashflow month."""
    out: dict[str, float] = defaultdict(float)
    q = """SELECT t.account_id, t.merchant, t.installment_total, t.amount,
                  MAX(t.installment_no) no, MAX(t.cashflow_month) m
           FROM transactions t
           WHERE COALESCE(t.installment_total,0) > 1 AND t.installment_no IS NOT NULL
             AND t.is_excluded=0 AND t.is_transfer=0 AND t.amount < 0
           GROUP BY t.account_id, t.merchant, t.installment_total, ROUND(t.amount, 0)"""
    for r in conn.execute(q):
        left = int(r["installment_total"]) - int(r["no"])
        if left <= 0 or not r["m"] or month_diff(r["m"], cur) > 1:
            continue  # finished, or a stale plan we no longer see
        for k in range(1, left + 1):
            fm = add_months(r["m"], k)
            if fm > cur:
                out[fm] += -r["amount"]
    return dict(out)


def _current_remainder(conn, led: Ledger, cur: str, base: dict, variable: float,
                       goals: float, ends: list[dict] | None = None,
                       extra_fixed: float = 0.0) -> tuple[float, dict]:
    bal = balance(conn)
    amount = bal["amount"] or 0.0
    md = led.get(cur)
    pending = pending_card_charges(conn, bal["as_of"], cur)
    inc_rem = max(0.0, base["income"] - md["income"])
    fixed = base["fixed"] - fixed_drop(ends or [], cur) + extra_fixed
    exp_rem = max(0.0, fixed + variable - md["expense"])
    free = amount - pending + inc_rem - exp_rem - goals
    return free, {"balance": bal["amount"], "balance_as_of": bal["as_of"],
                  "pending_card_charges": _r(pending), "income_remaining": _r(inc_rem),
                  "expense_remaining": _r(exp_rem)}


def _with_income_baseline(conn, led: Ledger, cur: str) -> tuple[dict, dict, list[dict]]:
    """led.baseline(cur) with `income` replaced by the salary + expected-rent baseline for the
    current month (`income_median` keeps the plain median), + income plan + fixed ends."""
    base = led.baseline(cur)
    ib = income_baseline(conn, led, cur)
    base["income_median"] = base["income"]
    base["income"] = income_for_month(ib, cur)[0]
    ib["tax_pct"] = _settings_num(conn, "rental_tax_pct", 10)
    # the flat's insurance counts only if no Insurance charge is in the fixed baseline already
    ins = _property_insurance(conn)
    ib["flat_insurance_in_fixed"] = bool(base["fixed_by_category"].get("Insurance"))
    ib["flat_insurance"] = 0.0 if ib["flat_insurance_in_fixed"] or ins is None else ins
    return base, ib, fixed_ends(conn, led, cur)


def forecast(conn: sqlite3.Connection, months: int = 6, on: date | None = None) -> dict:
    led = Ledger(conn)
    cur = current_month(conn, on)
    base, ib, ends = _with_income_baseline(conn, led, cur)
    plan = goal_plan(conn, on, months + 1, ends, led)
    goals = plan[cur]
    inst = known_installments(conn, cur)
    free, start = _current_remainder(conn, led, cur, base, base["variable"], goals, ends,
                                     sum(rent_costs(ib, cur)))
    planned, extra = _networth_inputs(conn, on, cur)
    free += planned.get(cur, 0.0)
    start["planned_current_month"] = _r(planned.get(cur, 0.0))
    start["liquid_assets_beyond_bank"] = _r(extra)
    bal = free
    out = []
    for k in range(1, months + 1):
        m = add_months(cur, k)
        known = inst.get(m, 0.0)
        inst_m = max(known, base["installments"])
        tax, ins = rent_costs(ib, m)
        fixed = base["fixed"] - fixed_drop(ends, m) + tax + ins
        expense = fixed + base["variable_ex_installments"] + inst_m
        pl = planned.get(m, 0.0)
        income, rent = income_for_month(ib, m)
        goals_m = plan.get(m, 0.0)
        net = income - expense - goals_m + pl
        bal += net
        out.append({"month": m, "income": _r(income), "salary": _r(ib["salary"]),
                    "rental": _r(rent),
                    "extra_income": _r(ib["extra_total"] if ib["include_extra"] else 0.0),
                    "fixed": _r(fixed), "fixed_ended": _r(fixed_drop(ends, m)),
                    "rental_tax": _r(tax), "flat_insurance": _r(ins),
                    "variable": _r(base["variable_ex_installments"]),
                    "installments": _r(inst_m), "known_installments": _r(known),
                    "goals": _r(goals_m), "planned": _r(pl), "expense": _r(expense), "net": _r(net),
                    "balance": _r(bal), "safety_net": _r(bal + extra)})
    start["goals_current_month"] = _r(goals)
    return {"current_month": cur, "start": start, "end_of_current_month": _r(free),
            "safety_net_now": _r(free + extra),
            "baseline": {k: (_r(v) if isinstance(v, float) else v) for k, v in base.items()},
            "income_baseline": _income_out(ib, cur), "fixed_ends": ends,
            "months": out}


# ---------------------------------------------------------------- overdraft risk

MONEY_MARKET_WORDS = ("money market", "money-market", "כספית")
OVERDRAFT_ROUND = 500.0


def money_market_holdings(conn: sqlite3.Connection) -> dict | None:
    """Largest money-market line in a liquid asset's latest breakdown:
    {asset_id, asset, name, value} or None."""
    from . import networth  # late import: networth imports this module

    best = None
    for a in networth.assets_list(conn):
        if not (a["liquid"] and a["include_in_net_worth"]):
            continue
        for it in a.get("breakdown") or []:
            name = str(it.get("name") or "")
            v = it.get("value")
            if isinstance(v, (int, float)) and any(w in name.lower() for w in MONEY_MARKET_WORDS):
                if best is None or v > best["value"]:
                    best = {"asset_id": a["id"], "asset": a["name"], "name": name, "value": float(v)}
    return best


def overdraft_risk(conn: sqlite3.Connection, on: date | None = None) -> dict | None:
    """When the forecast ends the current month below zero: {month, projected, rent_arrears,
    move_amount, source, text}. move_amount = the shortfall rounded up to ₪500, capped at what the
    money-market fund holds."""
    f = forecast(conn, 1, on)
    end = f["end_of_current_month"]
    if end is None or end >= 0:
        return None
    from . import networth

    p = networth.property_view(conn, on) or {}
    arrears = p.get("rent_arrears") or 0.0
    mm = money_market_holdings(conn)
    need = -(-(-end) // OVERDRAFT_ROUND) * OVERDRAFT_ROUND  # ceil to ₪500
    move = min(need, mm["value"]) if mm else need
    label = date.fromisoformat(f["current_month"] + "-01").strftime("%b %Y")
    text = f"Projected to end {label} at −₪{-end:,.0f}"
    if arrears > 0:
        text += f"; rent arrears ₪{arrears:,.0f}"
    short = mm["asset"].split(" (")[0] if mm else ""  # keep mixed EN/HE text readable
    text += (f"; consider moving ₪{move:,.0f} from the money-market fund ({short})" if mm
             else f"; consider moving ₪{move:,.0f} from liquid savings")
    return {"month": f["current_month"], "projected": end, "rent_arrears": _r(arrears),
            "move_amount": _r(move), "source": mm, "text": text}


# ---------------------------------------------------------------- affordability

def affordability(conn: sqlite3.Connection, amount: float, month: str | None = None,
                  on: date | None = None) -> dict:
    """Rule 9."""
    led = Ledger(conn)
    cur = current_month(conn, on)
    target = month if month and month >= cur else cur
    base, ib, ends = _with_income_baseline(conn, led, cur)
    plan = goal_plan(conn, on, month_diff(cur, target) + 1, ends, led)
    goals = plan[cur]
    buffer_months = _settings_num(conn, "buffer_months", 1)
    buffer = buffer_months * (base["fixed"] + base["variable"])

    planned, extra = _networth_inputs(conn, on, cur)

    def run(variable: float) -> tuple[float, list[dict], dict]:
        free, start = _current_remainder(conn, led, cur, base, variable, goals, ends,
                                         sum(rent_costs(ib, cur)))
        free += planned.get(cur, 0.0)
        path = [{"month": cur, "free": _r(free), "safety_net": _r(free + extra)}]
        for m in month_range(add_months(cur, 1), target):
            fixed = base["fixed"] - fixed_drop(ends, m) + sum(rent_costs(ib, m))
            free += (income_for_month(ib, m)[0] - fixed - plan.get(m, 0.0) - variable
                     + planned.get(m, 0.0))
            path.append({"month": m, "free": _r(free), "safety_net": _r(free + extra)})
        return free, path, start

    projected, path, start = run(base["variable"])
    worst, _, _ = run(base["variable_p75"])
    best, _, _ = run(base["variable_p25"])
    remaining = projected - amount
    verdict = "yes" if remaining >= buffer else ("tight" if remaining >= 0 else "no")
    n_months = max(1, month_diff(cur, target) + 1)
    shortfall = max(0.0, buffer - remaining) if verdict != "yes" else 0.0
    return {
        "verdict": verdict, "amount": _r(amount), "month": target,
        "projected_free": _r(projected), "remaining_after": _r(remaining),
        "buffer": _r(buffer), "p25": _r(worst - amount), "p75": _r(best - amount),
        "shortfall": _r(shortfall), "monthly_saving_needed": _r(shortfall / n_months),
        "path": path,
        # second line: liquid net worth (cash + liquid investments) as a safety net
        "safety_net": _r(projected + extra), "safety_net_after": _r(projected + extra - amount),
        "covered_by_safety_net": verdict != "yes" and projected + extra - amount >= buffer,
        "planned_in_range": _r(sum(v for m, v in planned.items() if cur <= m <= target)),
        "income_baseline": _income_out(ib, cur), "fixed_ends": ends,
        "assumptions": {**start, "median_income": _r(base["income"]),
                        "income_median": _r(base["income_median"]),
                        "liquid_assets_beyond_bank": _r(extra),
                        "median_fixed": _r(base["fixed"]),
                        "median_variable": _r(base["variable"]),
                        "goal_contributions": _r(goals), "baseline_months": base["months"]},
    }
