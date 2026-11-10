# Personal-finance app: feature research (RiseUp replacement)

**Evidence caveat:** web search returned mostly promotional Hebrew reviews. RiseUp details marked (†) come from hands-on use of the app, not verified online. Verified from sources: price ~NIS 45/mo (reports of ~430/yr); WhatsApp-origin, human coaching; forward cash-flow/overdraft projection; alerts when fixed expenses rise vs another month; complaints: price, full bank-connection dependence, partial bank coverage/disconnects, no cash tracking, weak for variable-income/self-employed, "no basic edit options" (App Store review).

## (a) RiseUp feature inventory
- **Cashflow month (†):** month runs salary-to-salary, not calendar. One headline number: "what's left to spend this month".
- **Envelope types:** *Fixed* (rent, bills, subscriptions; expected amount, flagged when it rises), *Tracking categories* (watched, no hard cap), *Variable* (budgeted discretionary: food, fun) with weekly budget slices.
- **Savings "השקעה וחיסכון":** transfers to savings/investments are treated as spending-that-isn't-lost, shown separately.
- **One-off exclusions:** mark a transaction as not belonging to the month's budget.
- **Forecast:** projected end-of-month balance and overdraft risk from known fixed items + expected variable pace.
- **Alerts/insights:** overspend alerts, fixed-expense increase alerts, monthly summary (WhatsApp/push).
- **Plans/goals "תוכניות":** target + date → monthly amount.
- **Onboarding:** connect bank + cards, auto-categorise, human-assisted setup.
- **Loved:** automation, a single "left to spend" number, forward-looking, human support. **Hated:** price, limited editing, bank-sync gaps, no cash, poor fit for irregular income.

## (b) Competitor highlights
| App | Why good | Goals / sinking funds | Anomaly / forecast | Rules | Monthly-review UX |
|---|---|---|---|---|---|
| YNAB | Zero-based, "give every shekel a job" | Targets per category with due dates; auto "needed this month" | Age of money; weak forecast | Payee auto-cat | Overspent categories turn red; move money to cover |
| Monarch | Dashboard, couples | Goals separate from budget (reviewers call clunky); rollover budgets | Cash-flow forecast, recurring calendar | Rules engine | Weekly recap, review queue |
| Copilot | Best-in-class UX, ML categorisation | Category rollover | Recurring detection, "unusual" flags | Learns from edits | Swipe/inbox review of new txns |
| Actual Budget | Free, local-first, envelope | Goal templates (`#template`) per category | Schedules → upcoming | Powerful rules (regex, payee) | Fast keyboard entry, rules apply on import |
| Firefly III | Self-hosted, flexible | Piggy banks (target+date) | Bills, recurring | Rules + triggers | Heavy, form-driven |
| Lunch Money | Multi-currency, API | Recurring items | Recurring expected/missing | Rules | Clean table + bulk edit |
| Tiller | Sheets-based, own your data | DIY | DIY | AutoCat rules | Spreadsheet freedom |
| Caspion / Moneyman (IL, OSS) | Scrape Leumi/Max/Cal via israeli-bank-scrapers (MIT) | none | none | Caspion pattern→category | Export to YNAB/Actual/Sheets/Firefly |

**Takeaway:** israeli-bank-scrapers is the realistic data source for Leumi + Max + Cal (or CSV/Excel export import as fallback). Finanda: nothing found.

## (c) Prioritised feature spec
**MUST (v1)**
1. Import Leumi + Max + Cal via scraper or CSV/XLSX; idempotent dedupe on (account, date, amount, merchant, ref); handle Hebrew text and RTL.
2. Cashflow month: configurable start day (salary day); all views use it.
3. Credit-card handling: card transactions are the spending; the Leumi monthly card-charge line is a *transfer*, excluded to avoid double count. Installments (תשלומים) split by month.
4. Monthly home: income, spent, left-to-spend, per-category bar vs budget, red on overspend.
5. Category budgets typed Fixed / Variable / Tracking; budget defaults to trailing 3-month median.
6. Transaction list with inline recategorise + bulk edit; "Always do this" creates a rule (merchant contains/regex → category), applied to history optionally and to all future imports.
7. Rules page: view, reorder, disable, test-match counts.
8. One-off flag (exclude from month's budget, keep in history).
9. Overspend month detector: month-over-month heat strip; flag months where spend > income or any category > 120% of its budget.
10. Goals/sinking funds (see f) with target, date, monthly contribution, progress.
11. Affordability check (see f).
12. Freebies tracker: item, type (work perk / miluim / gift card / voucher), value, expiry date, status (unused/used/expired), "expiring in 30 days" panel on home.
13. Local-only SQLite, no cloud; backup/export CSV.

**SHOULD**
- Recurring-item detection (subscriptions) + "price rose" and "missing this month" alerts.
- Anomaly flags: new merchant > X, txn > 3× category median.
- Balance forecast to end of month and next 3 months (recurring + median variable).
- Review inbox: uncategorised/low-confidence txns, keyboard-driven.
- Savings category/transfers type; net-worth-lite (savings balance).
- Search, tag (e.g. "miluim", "trip-2027").
- Freebie reminders (email/desktop notification), link a transaction to a freebie ("used").

**LATER**
- Weekly budget slices for variable envelopes; split transactions; merchant-name normalisation via ML; cash tracking; multi-currency for trips (FX at charge date); yearly report; LLM merchant classification fallback; reserve-duty (מילואים) payout income tracking.

## (d) UX principles
1. **One number first:** "left to spend this month" (+ days left, daily pace) above the fold.
2. **Exceptions only:** show red categories and anomalies; collapse everything on-budget.
3. **Review inbox, not a ledger:** uncategorised items first, one-key category assign, auto-advance.
4. **Every edit teaches:** recategorise prompts "apply to future/all past?" with match count preview.
5. **Month strip navigation:** 12 mini-bars (spent vs income); click to jump; overspend months visibly red.
6. **Defaults over setup:** pre-seeded taxonomy and budgets from history; zero config to first useful screen.
7. **Hebrew-first, RTL-correct:** merchant names displayed as-is; numbers LTR; NIS formatting.
8. **Explain numbers:** click any figure to see the transactions that compose it.

## (e) Default taxonomy (Hebrew / English), aligned with RiseUp naming where known
Fixed: דיור (שכר דירה/משכנתא) Housing · ארנונה Property tax · חשמל, מים, גז Utilities · אינטרנט וסלולר Internet & mobile · ביטוחים Insurance · מנויים Subscriptions · החזרי הלוואות Loans · חינוך וילדים Education & kids · בריאות Health.
Variable: סופר ומכולת Groceries · מסעדות ובתי קפה Dining out · תחבורה (דלק, חניה, תחבורה ציבורית) Transport · קניות (ביגוד, בית, אלקטרוניקה) Shopping · פנאי ובילויים Entertainment · טיפוח Personal care · מתנות ותרומות Gifts & donations · חופשות וטיולים Travel.
Other: השקעה וחיסכון Savings & investment · העברות (כולל חיוב כרטיס) Transfers (excluded) · הכנסות: משכורת Salary, מילואים Reserve pay, החזרים Refunds, אחר Other · לא מסווג Uncategorised.

## (f) Goals, sinking funds, affordability
**Model:** `goal(id, name, target_amount, target_date, start_balance, category_id, priority)` + `goal_ledger(goal_id, date, amount, source)`. Monthly need = (target − saved) / months_left (ceil). Saved = sum of ledger entries (manual or auto-moved from "savings" budget line). A goal *is* a virtual envelope: its monthly need appears as a Fixed line in the month's budget, so left-to-spend already accounts for it.
- Trip example: flights 4,000 NIS due Jul 2027 → ~500/mo over 8 months; spending tagged Travel in that month draws down the fund instead of hitting the variable budget.
- Status: on track / behind (saved < expected-by-now) / done; surface "behind" in the monthly overspend view.

**Affordability("can I afford X in month Y"):**
`projected_free(Y) = current_free_cash + Σ_{m=now..Y}(expected_income_m − fixed_m − goal_contributions_m − median_variable_m)`, with free cash = Leumi balance − upcoming card charge − reserved goal balances. Verdict: *Yes* if `projected_free(Y) − X ≥ buffer` (default 1 month of fixed costs); *Tight* if ≥0; *No* otherwise, showing shortfall and the monthly saving needed to turn it into a goal ("Make this a goal"). Show best/worst case using p25/p75 of historical variable spend.
