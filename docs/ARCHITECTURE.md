# Personal Finance App — Architecture & Contract

Single-user, local-only. Code lives in the repo; your data lives in its gitignored `data/` folder. Nothing
leaves the machine except, if you opt in, (a) the RiseUp API (one-off history export) and (b)
israeli-bank-scrapers logging into your bank / card sites (live sync).
Hebrew-first data (merchant names in Hebrew), UI in English labels with Hebrew data rendered RTL (`dir="auto"`).

## Layout

```
finance/
  finance                 # launcher: ./finance  -> starts server on 127.0.0.1:8747 and opens browser
  app/
    pyproject.toml        # python >=3.12, run with `uv run --project app ...`
    finapp/
      __init__.py
      __main__.py         # `python -m finapp serve|import <file>|sync|backup`
      config.py           # paths (ROOT = finance/, DATA = finance/data), settings defaults
      personal_setup.py   # optional data/personal_setup.json: household categories/rules/settings
      db.py               # sqlite schema + migrations + helpers (data/finance.db)
      rules.py            # categorisation engine
      analytics.py        # month summaries, overspend detection, forecast, affordability, goals
      ingest.py           # takes list[RawTxn] -> dedupe -> apply rules -> card-transfer marking -> insert
      server.py           # FastAPI app, JSON API under /api, serves app/ui/ at /
      importers/
        base.py           # RawTxn dataclass + detect(path) -> importer
        leumi.py          # PDF (reversed-Hebrew text) + Excel/HTML-as-xls
        max.py            # Max xlsx
        cal.py            # Visa Cal xlsx
        riseup.py         # data/riseup_transactions.csv (history seed)
        generic.py        # any CSV with date/description/amount
      sync.py             # runs sync/scrape.mjs, feeds results into ingest
    tests/
    ui/
      index.html  app.css  js/  mock/   # no CDN at runtime; ?mock=1 serves invented data
  sync/
    package.json  scrape.mjs   # israeli-bank-scrapers; creds from macOS Keychain
  data/
    finance.db  personal_setup.json  inbox/  imported/  backups/  raw/
  docs/
```

## Importer contract (importers/base.py)

```python
@dataclass
class RawTxn:
    provider: str            # 'leumi' | 'max' | 'cal' | 'riseup' | 'generic'
    account_key: str         # stable id of the account, e.g. 'leumi:<leumi-account-no>', 'max:1234', 'cal:5678', riseup 'riseup:<source>:<hash>'
    account_kind: str        # 'bank' | 'card'
    date: date               # transaction date
    billing_date: date|None  # when charged (cards) / value date (bank)
    amount: float            # ILS, signed: expense < 0, income > 0
    description: str         # merchant/description, logical-order Hebrew (NOT reversed), bidi marks stripped
    original_amount: float|None = None
    original_currency: str|None = None   # 'ILS','USD','EUR'...
    installment_no: int|None = None
    installment_total: int|None = None
    provider_category: str|None = None   # category given by the source (Max קטגוריה, Cal ענף, RiseUp category)
    external_id: str|None = None
    balance_after: float|None = None     # bank running balance if known
    raw: dict = field(default_factory=dict)

def detect(path: Path) -> "Importer"    # sniff file type/headers, return importer
class Importer: provider: str; def parse(self, path: Path) -> list[RawTxn]
```

## Database (SQLite, data/finance.db)

- `accounts(id INTEGER PK, key TEXT UNIQUE, provider, kind, name, created_at)`
- `transactions(id TEXT PK, account_id, date TEXT, billing_date TEXT, cashflow_month TEXT 'YYYY-MM', amount REAL, original_amount, original_currency, description TEXT, merchant TEXT (normalized), category_id, category_source TEXT ('manual'|'rule'|'provider'|'riseup'|'default'), is_transfer INT, is_excluded INT, installment_no, installment_total, provider_category, notes, import_id, created_at, updated_at)`
  - `id` = sha1(account_key|date|amount|normalized description|occurrence_index) — stable across re-imports.
- `categories(id INTEGER PK, name TEXT UNIQUE, name_he TEXT, kind TEXT ('income'|'fixed'|'variable'|'tracking'|'savings'|'transfer'), monthly_budget REAL NULL, color TEXT, archived INT, one_off INT default 0)` — `one_off=1` marks a one-off event category (e.g. a renovation): its rows get `is_excluded=1` (unless the user set flags by hand), so they stay visible but don't count in monthly income/expense/overspend.
- `rules(id INTEGER PK, pattern TEXT, match_type ('contains'|'equals'|'regex'), account_kind NULL, direction NULL ('in' amount>0 | 'out' amount<0), amount_min NULL, amount_max NULL (bounds on |amount|), category_id NULL, set_transfer INT NULL, set_excluded INT NULL, priority INT, hits INT, created_at)`
- `goals(id, name, target_amount, target_date, saved_amount, monthly_contribution NULL, priority, status ('active'|'done'|'archived'), notes, created_at)`
- `freebies(id, name, source ('work'|'reserve'|'gift'|'other'), value REAL, unit TEXT ('ILS'|'days'|'uses'|...), received_date, expiry_date NULL, used_amount REAL default 0, status ('active'|'used'|'expired'), where_to_use TEXT, notes, created_at)`
- `imports(id, file_name, provider, rows, new_rows, dup_rows, imported_at)`
- `settings(key PK, value)` — `cashflow_start_day` (default 1), `buffer_months` (default 1), `overspend_pct` (default 120)

## Business rules

1. **Card payments are transfers.** Bank rows whose description matches card-issuer payment patterns
   (e.g. `מקס איט פיננ`, `לאומי ויזה`, `כאל`, `ישראכרט`, `ויזה`) get `is_transfer=1` and category "Card payment"
   (kind transfer). Spending is counted from card line items only. Same for internal transfers between own
   accounts and savings deposits (`הפקדה לפיקדון`, `העברה לחסכון`) → kind savings, not expense.
2. **Categorisation precedence:** manual > rule (highest priority, then most specific/longest pattern) > specific
   RiseUp category (history) > provider category mapped to our taxonomy > generic RiseUp category (`אחר`, `כללי`,
   `RiseUp`, blank) > "Uncategorized". Generic provider categories (`אחר`, `שונות`) map to nothing.
   `python -m finapp recategorize` (`./finance recategorize`) re-runs this on every non-manual row (backs up first).
3. **Edits teach.** PATCH a transaction's category with `remember: true` → creates a rule on its merchant
   (`contains` normalized merchant), optionally `apply_to_history: true` re-categorises all matching non-manual
   rows. Rule preview endpoint returns matching count + sample before saving.
4. **Dedupe** on `id`; also cross-source: a RiseUp history row and a native import row for the same account kind,
   same abs amount, date within ±2 days, similar merchant → keep one (prefer native row, carry over manual category).
5. **Installments:** each installment is its own monthly row (as the card statement shows it).
6. **Cashflow month** = month of `billing_date` for cards and `date` for bank, shifted by `cashflow_start_day`.
   (Matches RiseUp: card purchases count in the month they're billed.)
7. **Overspend month (red):** (a) expenses (excluding transfers/savings/excluded) > income, OR (b) total
   variable+tracking spend > overspend_pct% of its trailing 6-month median (needs ≥3 months of history).
   **Attention (amber, `attention:[reasons]`):** (c) a variable/tracking category is ≥ ₪500 over its budget AND
   > overspend_pct% of it — listed, but never turns the month red on its own. Category budget =
   explicit `monthly_budget`, else median of the trailing 6 months that had spend in it, floored at ₪200
   (derived budgets need ≥3 months of history). 0 budget + 0 spend never flags. `reasons` and `attention`:
   max 3 each, biggest overage first. Month-view category `over` uses (c); its `budget` is that budget for variable/tracking.
8. **Goals = virtual fixed envelopes:** monthly need = (target − saved) / months left; deducted from left-to-spend.
8b. **One-off events:** `./finance tag-event --category <name> --from YYYY-MM-DD --to YYYY-MM-DD [--dry-run]` sets
   `<name>` (manual) on positive non-transfer bank rows in the range that are not savings or regular income
   (Salary / Reserve duty income / Rental income). Seeded: Rental income (income). One-off categories and
   household rules (a transfer-to-savings amount band, rent received by transfer, a broker's name) come from
   the personal setup file (see "Personal setup" below).
9. **Affordability(amount, month):** projected free cash at month = current balance + Σ over months until then of
   (median income − median fixed − goal contributions − median variable). "Median fixed" is summed per fixed category: a category
   with no charge in the last 2 complete months counts 0, else max(6-month median incl. zeros, last-3-month mean),
   so a recently started charge (a mortgage next to the rent) counts in full. Verdict `yes` if remaining ≥ buffer
   (buffer_months × median monthly fixed+variable), `tight` if ≥ 0, else `no`. Return p25/p75 scenarios and
   per-month projection.

## JSON API (all under /api, JSON in/out, amounts ILS, months 'YYYY-MM')

| Method | Path | Notes |
|---|---|---|
| GET | `/api/overview` | `{current_month, months:[{month,income,expense,net,savings,overspend:bool,reasons:[],attention:[]}], balance:{amount,as_of}, freebies_expiring:[...], uncategorized_count, open_tasks_count, insights_count, last_import, last_sync}` (last 24 months) |
| GET | `/api/month/{m}` | `{month, income, expense, net, overspend, reasons, attention, left_to_spend, goals_reserved, one_offs:{income,expense,count} (is_excluded non-transfer rows of the month), categories:[{id,name,name_he,kind,color,actual,budget,median3,pct,over:bool,count}], top_merchants:[{merchant,amount,count}], anomalies:[{type,text,txn_ids}], fixed:[{name,expected,actual}] }` |
| GET | `/api/transactions` | query: `month, category_id, account, q, uncategorized, include_transfers, excluded, limit, offset` → `{items:[txn], total}`; `ids=a,b,c` returns exactly those rows (ids of merged rows resolve via `txn_aliases`; other filters ignored). Uncategorized items carry `suggested_category_id` (most frequent manual/rule category of the same merchant, else mapped provider category, else null; null on categorized items) |
| PATCH | `/api/transactions/{id}` | body: `{category_id?, is_excluded?, is_transfer?, notes?, remember?:bool, apply_to_history?:bool}` → `{txn, rule?, updated_count}` |
| POST | `/api/transactions/bulk` | `{ids:[], category_id?, is_excluded?, remember?}` |
| GET/POST/PATCH/DELETE | `/api/categories[/{id}]` | |
| GET/POST/PATCH/DELETE | `/api/rules[/{id}]` | |
| POST | `/api/rules/preview` | `{pattern, match_type, ...}` → `{count, sample:[txn]}` |
| GET/POST/PATCH/DELETE | `/api/goals[/{id}]` | GET adds `monthly_need, months_left, progress_pct, on_track` |
| GET/POST/PATCH/DELETE | `/api/freebies[/{id}]` | GET adds `days_left`, `used_amount`, `linked_used_amount` (alias `linked_used`), `remaining`, sorted by expiry |
| GET | `/api/trends?months=12` | `{months:[...], categories:[{id,name,series:[...]}]}` |
| GET | `/api/forecast?months=6` | projected income/expense/balance per month |
| GET | `/api/affordability?amount=&month=` | `{verdict, projected_free, buffer, p25, p75, path:[{month,free}]}` |
| POST | `/api/import` | multipart `file` → `{provider, rows, new_rows, dup_rows}` |
| POST | `/api/import/inbox` | imports every file in data/inbox, moves them to data/imported/ |
| POST | `/api/sync` | runs scrapers in background; GET `/api/sync/status` → `{running, providers:[{name,ok,error,new_rows,at}]}` |
| GET/PATCH | `/api/settings` | |
| GET | `/api/export.csv` | all transactions |

Transaction JSON: `{id, date, billing_date, cashflow_month, amount, description, merchant, category_id, category_name, category_source, account:{key,provider,kind,name}, is_transfer, is_excluded, installment_no, installment_total, original_amount, original_currency, notes}`

## Added: Financial tasks + Expense review (insights)

Table `tasks(id INTEGER PK, title TEXT, notes TEXT, due_date NULL, status ('open'|'done'|'dropped'), priority ('high'|'med'|'low'), est_monthly_saving REAL NULL, actual_monthly_saving REAL NULL, merchant TEXT NULL, category_id NULL, insight_key TEXT NULL, created_at, done_at NULL)`
Table `insight_dismissals(insight_key TEXT PK, dismissed_at, reason)`

`/api/tasks[/{id}]` GET/POST/PATCH/DELETE — GET supports `?status=open`; returns `total_est_monthly_saving` for open and `total_actual_monthly_saving` for done.

`GET /api/insights` → `{items:[{key, type, severity ('high'|'med'|'low'), title, detail, est_monthly_saving, est_yearly_saving, txn_ids:[], merchant, category_id, suggested_task}]}` — stable `key` per finding; dismissed keys omitted (`POST /api/insights/{key}/dismiss {reason}`); `POST /api/insights/{key}/task` creates a task from it.
Detectors (all on last 12 months, excluding transfers):
- `duplicate_charge`: same merchant + same amount within 3 days on the same card (possible double charge).
- `refund_missing`: n/a v1 — skip.
- `card_fee` / `bank_fee`: card fees (דמי כרטיס, דמי מנוי, עמלה), bank fees (עמלות, דמי ניהול, ריבית חובה), foreign-transaction fees → yearly total; suggest calling to waive/negotiate.
- `subscription`: recurring monthly/yearly charges (≥3 occurrences, regular interval, similar amount) → list with monthly cost; flag ones with no use signal or that overlap (e.g. multiple streaming/digital).
- `price_increase`: recurring/fixed charge whose latest amount is >10% above its previous median.
- `category_creep`: category whose last-3-month average is >25% above its prior 9-month average.
- `top_discretionary`: biggest variable categories vs a reasonable target (e.g. eating out, shopping) with what a 15% cut saves per year.
- `fx_heavy`: many foreign-currency card charges → suggest a no-FX-fee option.
- `insurance_review`: insurance/communication fixed charges unchanged >12 months → suggest price comparison.
- `unusual_inflow`: positive non-transfer, non-excluded income rows (bank or income category) ≥ ₪5,000 that are not
  Salary / Reserve duty income and whose merchant has no positive rows in ≥3 months within ±11 months →
  "Large one-off inflow — mark as one-off so it doesn't inflate income?" with `txn_ids`. Never auto-excluded.
  Also emitted as a month-view anomaly (`type: unusual_inflow`).
- `ai_review`: rows inserted into a `manual_insights` table (same shape) by an offline review (Claude) — served alongside detectors.

## Added: Account kinds, gift cards, input files

- Typical setup: one bank checking account and one or more credit cards. Max issues several bank-branded cards
  (RiseUp source `leumicard`); one Max login / export covers all of them (the export lists each card's last4).
  Bank lines `מקס איט פיננ` and `לאומי ויזה` are card bill payments → transfers.
- **Gift cards.** A Cal card can be a prepaid gift card (a common employer present). Cal imports use
  `account_kind='gift'`. Ingest sets `is_excluded=1` on gift-account transactions (paid by the gift, not your money);
  the UI shows them with a "paid by gift card" badge.
- `accounts.kind` ∈ bank | card | gift. `freebies.account_id` NULL → links a freebie to a gift account; freebie GET returns `used_amount` = manual used + Σ|expenses| on the linked account since received_date, and `remaining` = value − used_amount.
- Settings default `sync_providers='leumi,max'`. Live sync = leumi + max by default (visaCal when listed).
- RiseUp history accounts get display names (unless renamed by the user): bank → "Leumi checking (RiseUp history)";
  card → "Max •<last4> (RiseUp history)" with the Max card most of its rows merged into (`txn_aliases.account_id`
  records the merged-away row's account; older aliases are backfilled from the RiseUp CSV by `recategorize`);
  no merges → "Card <hash4> (RiseUp history)". Refreshed after imports that merge and by `recategorize`.
- **Uncategorized** (overview `uncategorized_count` and `/api/transactions?uncategorized=1`, one predicate):
  category Uncategorized or NULL, not a transfer, not excluded.
- `GET /api/accounts` → `[{id,key,provider,kind,name,txn_count,first_date,last_date}]`; `PATCH /api/accounts/{id} {name}`.
- Input files (drop them in data/inbox/ or on the Import screen; they never leave data/):
  - **Leumi PDF statement** (דף חשבון). Text extracts come in reversed visual order, newest first:
    `₪12,000.00 ₪1,234.56 הזיו ימואל 02.11.2026` = balance, amount, description (reversed "לאומי ויזה"), date.
    The sign comes from running-balance deltas (or word x-positions). Leumi Excel / HTML-as-xls exports also work.
  - **Max xlsx** (פירוט עסקאות): sheets `עסקאות במועד החיוב` and `עסקאות חו"ל ומט"ח`; rows 1–3 titles, header row 4
    (16 cols: תאריך עסקה, שם בית העסק, קטגוריה, 4 ספרות אחרונות של כרטיס האשראי, סוג עסקה, סכום חיוב, מטבע חיוב,
    סכום עסקה מקורי, מטבע עסקה מקורי, תאריך חיוב, הערות, תיוגים, מועדון הנחות, מפתח דיסקונט, אופן ביצוע ההעסקה,
    שער המרה ממטבע מקור/התחשבנות לש"ח). Dates `dd-mm-yyyy`. סכום חיוב positive = charge, negative = refund →
    amount = −סכום חיוב. The foreign sheet may be charged in `$` → convert with the rate column; if missing flag
    `raw['needs_fx']`.
  - **RiseUp CSV** (`data/riseup_transactions.csv`, built by `scripts/riseup_export.mjs`, UTF-8 BOM): `amount_ils`
    already signed; `source` leumiBank (bank) / leumicard (card, one `account_hash` per card); `is_excluded` 'true' =
    user one-off; keep its `cashflow_month`. Built-in categories: אוכל בחוץ, אחר, ארנונה, ביטוח, בריאות, דיגיטל,
    הלוואה, העברות, הפקדת מזומן, השקעה וחיסכון, חינוך, כללי, מזומן, מילואים, מים, משיכת פקדון, משכורת, משכנתא, סופר,
    ספורט, עמלות, קניות, רכב ודלק, שיק, שכירות ושכר לימוד, תחבורה ציבורית, תיירות, תקשורת, תרומה, תשלומים, RiseUp —
    all map to our taxonomy (מילואים = reserve-duty income). A user-made RiseUp category that extends a built-in one
    ("משכנתא <property>") maps like the built-in.
- Overlap: RiseUp history (cards + bank) overlaps the native card and bank files. Cross-source dedupe (rule 4) collapses
  these; prefer native rows, carry over the RiseUp category when the native row has no manual/rule category.

## Added: Net worth, property & sheet sync

- Tables (migration 5): `assets(id, key UNIQUE, name, kind ∈ cash|investment|pension|study_fund|property_equity|property|equity_comp|other, include_in_net_worth, liquid (NULL → cash/investment liquid), notes, source ∈ sheet|manual|bank, track_from_bank, created_at)`, `asset_values(asset_id, as_of, value, breakdown_json, source, PK(asset_id,as_of))`, `liabilities(id, key UNIQUE, name, kind ∈ mortgage|loan|other, principal, rate_pct, start_month, monthly_payment, notes, include_in_net_worth)` + `liability_values(liability_id, as_of, balance)`, `planned_items(id, kind ∈ income|expense, when_month NULL, amount (positive), what, notes, done, source)`, `properties(id, key, name, data_json)` (price, purchase_costs, contributions, expected rental_income), `equity_comp(id, key '<type>:<vests>', vests, qty, type, notes, value)`.
- `./finance import-sheet [path]` (default: latest `data/sheet/snapshot_*.json`; format: `examples/sheet_snapshot.example.json`) / `POST /api/networth/import-sheet {file?}` — idempotent upsert; tasks dedupe by title, freebies by name (case-insensitive); a value the user entered for the same date wins over the sheet. `detect()` rejects `.json` with a pointer to `import-sheet`.
- `track_from_bank` assets take the live bank balance (`analytics.balance`, also per month-end for history). The mortgage is imported with `include_in_net_worth=0`: the flat counts at the user's equity (property_equity asset).
- Net worth = Σ included asset values − Σ included liability balances; liquid = included assets with `liquid`. History = month-end carry-forward, from the first non-bank value. Stale = value older than 45 days.
- `GET /api/networth` → `{total, liquid, liabilities_total, as_of, by_kind:[{kind,total}], assets:[...stale_days, stale, breakdown, value_source], liabilities:[...balance, as_of], history:[{month,total,liquid}], property:{price, purchase_costs, contributions, mortgage, equity_estimate (price − mortgage balance), rental_income:[{month,expected,received,future}], rent_expected_monthly, rent_yield_pct (gross, of price), rent_received_12m}, planned:[...signed_amount, overdue], equity_comp:[...months_to_vest, vested], last_sheet_import}`. Rent received = positive non-transfer rows in category "Rental income" by cashflow month.
- CRUD: `POST/PATCH/DELETE /api/assets[/{id}]`, `POST /api/assets/{id}/value {as_of, value, breakdown?}`, same for `/api/liabilities` (`/value {as_of, balance}`), `GET/POST/PATCH/DELETE /api/planned[/{id}]`.
- Forecast months add `planned` (open dated items, in their month; current month's go into the start) and `safety_net` (= balance + liquid assets beyond the bank balance); top-level `safety_net_now`. Affordability adds `safety_net`, `safety_net_after`, `covered_by_safety_net`, `planned_in_range`, and `safety_net` per path point. Overview adds `net_worth: {total, liquid, as_of}` (null with no assets).

## Added: Household adjustments (rent split, refunds, income baseline, property P&L, goal links, fixed ends)

- Migration 6: "Rent & tuition" renamed **"Home rent" / שכירות** (fixed); tuition lives in **Education** (fixed). RiseUp `שכירות ושכר לימוד` → Home rent (a tuition rule, if you add one, wins first). Rent rules by amount band — e.g. `שיק` bank out within your rent's band → Home rent, `הוראת קבע` bank in within a roommate's share → Home rent — are personal setup.
- **Refunds:** a positive row in an expense-kind category (fixed/variable/tracking) reduces that category's spend on any account (bank or card) and never counts as income (month, overview, trends, forecast, affordability, `unusual_inflow`). Positive bank rows that are Uncategorized / uncategorised still count as income.
- Settings: `salary_net_baseline` (default null → median Salary of the last 6 complete months), `count_variable_income_in_forecast` (default `false`), `rental_tax_pct` (default 10).
- Forecast/affordability income per month = salary baseline + the property's expected rent for that month (plan months; after the last plan month the last amount carries on). Reserve duty, bonus (salary above a set baseline) and other income are `extra_income` lines (6-month averages), added only when the setting is true. Responses add `income_baseline:{salary, salary_source, rental_expected, total, extra_income:[{name,monthly_avg}], extra_included}`; forecast months add `salary, rental, extra_income, fixed_ended`. Month view adds `income_breakdown:{salary,rental,reserve_duty,other}`, `goal_progress:[{goal_id,name,put_away,monthly_need,detail,linked_asset}]`; current-month `expected_income` = max(income, baseline for the month).
- Property (`/api/networth.property`) adds `pnl:[{month,expected,received,mortgage,insurance,tax_reserve,net,balance}]` from the first expected-rent month to the current month (mortgage = Mortgage category; insurance = rows matching flat-insurance patterns, else `insurance_monthly` parsed from the sheet's fixed_monthly at import-sheet; tax = rental_tax_pct of expected), `rent_arrears` = max(0, Σ expected for months ≤ current − Σ received to date) (cumulative: lump payments carry forward; upcoming months are not due), `balance` per month = running Σ received − Σ expected, `rent_arrears_months:[{month,due}]` (months still open once receipts cover the oldest first), `rent_credit` (paid ahead), `insurance_monthly`, `insurance_source`, `tax_pct`. Insight `rent_overdue` (high) while arrears > 0.
- `goals.asset_id` (nullable): when set, `saved_amount` = the asset's latest value (`saved_source:'asset'`, `manual_saved_amount`, `linked_asset:{id,name,key,value,as_of}`), and GET adds `put_away_this_month:{amount, savings_outflows, asset_change}` = max(savings-kind outflows, the asset's rise over the month when a value dated in the month exists) — max, so a deposit that also lifts the asset is not counted twice.
- Table `fixed_ends(id, category_id NULL, merchant NULL, last_month 'YYYY-MM', notes, created_at)`; `GET/POST/PATCH/DELETE /api/fixed-ends[/{id}]` → `{…, what, monthly_amount, ended}`. The forecast and affordability drop `monthly_amount` (whole category fixed baseline, or the merchant's share of it) for months after `last_month`; forecast/affordability return `fixed_ends`.
- Forecast/affordability fixed outflows add, per month while rent is expected: the rental-tax reserve (`rental_tax_pct` × expected rent) and the flat's insurance (`insurance_monthly`) unless an Insurance charge is already in the fixed baseline. Forecast months add `rental_tax`, `flat_insurance`; `income_baseline` adds `rental_tax_reserve`, `rental_tax_pct`, `flat_insurance`, `flat_insurance_in_fixed`.
- **Overdraft risk:** when the forecast's `end_of_current_month` < 0, insight `overdraft_risk` (high) and month-view (current month) `overdraft_risk:{month, projected, rent_arrears, move_amount, source, text}`; `move_amount` = shortfall rounded up to ₪500, capped at the largest money-market line (`money market`/`כספית`) in a liquid asset's breakdown. The Month screen shows a red banner.

## Added: UX round 1 (freshness, category floor, import range)

- **Category spend floors at 0 (Ledger):** per category per month, net spend = outflows − refunds/inflows, floored at 0;
  inflow beyond that month's outflows counts as **other income** (`income_by.other`), never as negative expense. Applies to
  month, overview, trends, forecast and affordability (all read the Ledger). Timing note: a reimbursement that arrives
  before the charge (e.g. a roommate's rent share before the rent check) shows as other income until the charge lands.
- `GET /api/freshness` → `{today, status ('ok'|'stale'|'missing'), needs_update:[key], sources:[{key ('leumi'|'max'|…),
  label, monthly, last_date, days, status, missing_months:['YYYY-MM'], last_import:{file_name,imported_at,rows,new_rows},
  accounts:[{id,key,name,kind,txn_count,first_date,last_date,days,status}]}], sheet:{key:'sheet', last_date
  (sheet_last_updated), days, status, last_import:{file_name,imported_at}}}`. Judged on the newest row's date: ok ≤ 10 d,
  stale ≤ 35 d, missing beyond (sheet: ok ≤ 35 d, else stale). RiseUp history and row-less accounts are left out; an
  account whose provider has a newer row elsewhere (a Max card while a sibling card is current) is `idle`, not stale;
  non-monthly sources (Cal gift) are `optional`. Leumi and Max always appear (`missing` when never imported).
- `POST /api/import` / `ingest()` results add `first_date`, `last_date` (date range of the file's rows).
- `GET /api/transactions?import_id=N` → rows inserted by that import ("Review N new transactions").
- Month-view anomaly texts start with the amount and end with the merchant (`₪308 · over 3× the usual Car & fuel charge (₪97) — <merchant>`).

## Added: Goal contribution schedule (step-ups)

- Migration 7: `goals.step_ups` TEXT (JSON, nullable) = `[{from_month 'YYYY-MM', add_amount, reason, fixed_end_id NULL}]`.
  POST/PATCH validate it (month format, fixed end exists, amount or link required) and store it sorted; a step-up linked to a
  fixed end without `add_amount` saves that charge's current `monthly_amount` as a snapshot. When computed, a linked step-up
  adds the fixed end's live `monthly_amount` (`amount_source:'fixed_end'`), falling back to the snapshot once that is 0
  (`'fixed_end_saved'`). `[]`/null clears.
- **Scheduled contribution** for month m = `monthly_contribution` (else `monthly_need`) + every step-up with `from_month ≤ m`,
  counted from the current month on and stopping once saved + contributions reach the target (the last month is capped).
  Replaces rule 8's monthly need: `goals_reserved(month)` (month view uses the viewed month, past months the current one), the
  overview, the forecast (`goals` per month) and affordability (per path month) all use it.
- Goal GET adds `step_ups` (resolved: `add_amount`, `saved_add_amount`, `amount_source`, `fixed_end:{id,what,last_month,monthly_amount}`),
  `contribution_now`, `contribution_by_month:[{month,amount}]` (current + 11), `projected_completion_month` (null if never),
  `projected_vs_target` ('on time' | 'N months late' | 'N months early' | 'not reached'), `target_month`, `has_plan`.
  With a plan (monthly_contribution or step-ups) `on_track` = completion month ≤ target month; without one, the old
  straight-line check. Month `goal_progress` items add `contribution_now` and `contribution` (for the viewed month).

## Added: Personal setup (data/personal_setup.json)

- Seeded defaults are generic only (card-bill payments, internal transfers, savings deposits, salary `משכורת`,
  reserve duty `מילואים`, bank fees). Anything that describes one household — rent / roommate amount bands, the
  mortgage lender's or broker's description, a tuition payee, a one-off event category, `salary_net_baseline` —
  lives in `data/personal_setup.json` (gitignored). Format: `examples/personal_setup.example.json`
  (`categories[]`, `rules[]` with `category` by name, `settings{}`).
- `./finance apply-setup [file]` (backs up first) is idempotent: categories upsert by name (listed fields only),
  a rule is added only when no rule with the same pattern / match_type / account_kind / direction / amount band
  exists, settings are filled only while unset (or still at their default) unless `--overwrite-settings`.
- A brand-new database (no `transactions` table before migrating) applies the file automatically when it exists.
  `FINAPP_SETUP` overrides the path (tests use it).
- `./finance export-setup [--out f]` writes the database's non-default categories (or changed fields of default
  ones), every non-default rule (including rules learned from edits) and non-default settings, so a fresh rebuild
  reproduces the setup.
