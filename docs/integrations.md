# Integrations: live sync and file import

Researched 2026-11-10. Facts marked **(unverified)** came from third-party posts or a single source. Export layouts come from public parsers and test fixtures, not from the providers' documentation, so check them against one real file from each provider.

## 1. Live sync: `israeli-bank-scrapers`

| Item | Value |
|---|---|
| Repo / npm | [eshaham/israeli-bank-scrapers](https://github.com/eshaham/israeli-bank-scrapers), `israeli-bank-scrapers` |
| Latest | **v6.13.0** (2026-11-02, Max promotion discounts added to memos) |
| Node | **>= 22.22.2** (README) |
| Engine | Puppeteer + Chromium (several hundred MB). Timezone forced to `Asia/Jerusalem` |
| Maintenance | Active. Commits on 2 Nov, 25 Oct, 29 Sep and 15 Sep. Merges go straight to npm. Most fixes come from one or two contributors, so breakages land in days to weeks |
| History limit | Leumi, Max and Cal each fetch up to **1 year** back |

### Company IDs and credentials

| Provider | `CompanyTypes` | Credential fields |
|---|---|---|
| Bank Leumi | `leumi` | `username`, `password` |
| Max | `max` | `username`, `password` |
| Visa Cal | `visaCal` | `username`, `password` |

### 2FA / OTP

- The library's login types for these three have **no OTP callback**. Only OneZero uses `otpCodeRetriever` or `otpLongTermToken`.
- If the provider demands an SMS code at login, the scraper fails. There is no hook to supply one.
- The bigger problem is breakage and bot detection, not OTP:
  - **Cal**: the login popup markup changed on 2026-10-25 and the fix, [PR #1212](https://github.com/eshaham/israeli-bank-scrapers/pull/1212), was still open on 10 Nov. The dashboard also failed to boot on 7 Aug 2026 ([#1152](https://github.com/eshaham/israeli-bank-scrapers/issues/1152)); it was closed the next day. Installment payments share one `identifier` ([#1219](https://github.com/eshaham/israeli-bank-scrapers/issues/1219)).
  - **Max**: an unknown plan or transaction type throws and fails the **whole scrape** ([#1218](https://github.com/eshaham/israeli-bank-scrapers/issues/1218), [#1211](https://github.com/eshaham/israeli-bank-scrapers/issues/1211)). Both are open.
  - **Leumi**: private accounts fail with homepage navigation timeouts and "Failed to extract account number" ([#1204](https://github.com/eshaham/israeli-bank-scrapers/issues/1204), open). Business accounts have moved to a new UI the scraper doesn't support ([#1106](https://github.com/eshaham/israeli-bank-scrapers/issues/1106)). That issue also reports `401 Session rejected` after repeated automated logins.
  - **Isracard** (no importer here yet) shows the pattern: bot detection blocks the IP ([#1181](https://github.com/eshaham/israeli-bank-scrapers/issues/1181)).
- A third-party RFC ([#1213](https://github.com/eshaham/israeli-bank-scrapers/issues/1213)) claims pure-HTTP Cal and Max clients with persistent device tokens and no Chromium **(unverified)**. Cal also offers an SMS-OTP login in that RFC.

**Verdict:** live sync is realistic for **Max**, plausible but fragile for **Cal**, and the **weakest for Leumi**. Plan on a daily or weekly run that can fail, with file import as the fallback. Don't run it more than once a day, to limit bot-detection risk. Run it from your own Mac and home IP.

### Options (`ScraperOptions`)

`companyId`, `startDate` (required), `combineInstallments` (folds installments into the first one), `showBrowser` (default false), `futureMonthsToScrape`, `additionalTransactionInformation` (slower; adds categories for some providers), `includeRawTransaction`, `timeout`, `defaultTimeout`, `navigationRetryCount`, `storeFailureScreenShotPath`, `outputData.enableTransactionsFilterByDate`, `args`, `executablePath`, `optInFeatures`.

### Output schema

```
{ success, errorType?, errorMessage?,
  accounts: [{ accountNumber, balance?,
    txns: [{ type: 'normal'|'installments', identifier?, date (ISO), processedDate (ISO),
             originalAmount, originalCurrency, chargedAmount, description, memo?,
             installments?: { number, total }, status: 'completed'|'pending' }] }],
  futureDebits?: [{ amount, amountCurrency, chargeDate?, bankAccountNumber? }] }
```

- `chargedAmount` is what hits the account, in ILS. `originalAmount` and `originalCurrency` are the purchase currency.
- `date` is the purchase date. `processedDate` is the billing date.
- Pending rows often have no `identifier`. Don't key on `identifier` alone for Cal: installments collide.
- Suggested dedupe key: `companyId | accountNumber | date | chargedAmount | description | installments.number`.
- `balance` means different things per scraper (Cal: next charge).

### Existing projects and credential storage

- [Moneyman](https://github.com/daniel-hauser/moneyman): credentials sit **in plaintext JSON** (`MONEYMAN_CONFIG`, a JSONC file, a Docker secret or Bitwarden Secrets Manager). Its README warns about this and says to use proper secret management.
- [Caspion](https://github.com/brafdlog/caspion) is an Electron GUI. I did not verify how it stores credentials.
- Other users: Scipio, Firefly iii importer, Actual Budget importer, Clarify, Asher MCP.

### Recommended local setup

Store one Keychain item per provider (the secret is a JSON blob):

```bash
security add-generic-password -U -s finance-scraper -a leumi -w '{"username":"…","password":"…"}'
# repeat with -a max and -a visaCal
```

`scrape.mjs` (in `finance/scraper/`, after `npm i israeli-bank-scrapers`):

```js
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { CompanyTypes, createScraper } from 'israeli-bank-scrapers';

const [company, outPath, daysBack = '45'] = process.argv.slice(2);
const secret = (acct) =>
  JSON.parse(execFileSync('security',
    ['find-generic-password', '-s', 'finance-scraper', '-a', acct, '-w'],
    { encoding: 'utf8' }).trim());

const startDate = new Date(Date.now() - Number(daysBack) * 864e5);
const scraper = createScraper({
  companyId: CompanyTypes[company],           // leumi | max | visaCal
  startDate,
  combineInstallments: false,
  futureMonthsToScrape: 1,
  showBrowser: process.env.SHOW_BROWSER === '1',
  storeFailureScreenShotPath: `/tmp/scrape-fail-${company}.png`,
});
const res = await scraper.scrape(secret(company));
writeFileSync(outPath, JSON.stringify(res, null, 1));
process.exit(res.success ? 0 : 2);            // error type is in the JSON
```

Python side:

```python
import json, subprocess
def scrape(company: str, out: str, days: int = 45) -> dict:
    p = subprocess.run(["node", "scraper/scrape.mjs", company, out, str(days)],
                       capture_output=True, text=True, timeout=300)
    data = json.load(open(out))
    if not data["success"]:
        raise RuntimeError(f'{company}: {data.get("errorType")} {data.get("errorMessage")}')
    return data
```

Notes: allow `security` and `node` access on the first Keychain prompt. Never log the secret. Run one provider at a time. On failure, set `SHOW_BROWSER=1` once to see what the site shows. Keep the scraped JSON for audit, and upsert into SQLite by the dedupe key.

## 2. Manual exports

### Bank Leumi (checking)

- Exports are Excel or CSV ("ייצוא לאקסל"). Often it is an **HTML table saved as `.xls`**, so `openpyxl` fails and `pandas.read_html` or `xlrd` is needed. Detect by sniffing the first bytes for `<html` or `<table`.
- Header (seen in public parsers): `תאריך | תאריך ערך | תיאור | אסמכתא | בחובה | בזכות | היתרה בש"ח`. A title row such as `תנועות בחשבון <number>` sits above it.
- Another variant is `תאריך | סוג תנועה | זכות | חובה | יתרה מצטברת`.
- Dates are `dd/mm/yyyy` or `dd.mm.yyyy`. Amounts are strings such as `1,234.50` or `₪ 1,234.00`. Debit and credit are **separate columns**, so signed amount = credit − debit.
- Card-bill lines ("מקס איט פיננסים", "לאומי דביט") are lump sums. Itemize them from the card files and tag them so they aren't counted twice.
- PDF "דפי חשבון": no machine format found. Don't parse it.

### Max (Excel "פירוט עסקאות")

- Layout copied from a public fixture of a 2026-10 export; older exports differ (one parser uses `header=3`, 11 columns).
- **Up to 3 sheets**: `עסקאות במועד החיוב`, `עסקאות חו"ל ומט"ח`, `עסקאות בחיוב מיידי`.
- 3 title rows (users, cards, month), then the header on the 4th row.
- 16 columns: `תאריך עסקה | שם בית העסק | קטגוריה | 4 ספרות אחרונות של כרטיס האשראי | סוג עסקה | סכום חיוב | מטבע חיוב | סכום עסקה מקורי | מטבע עסקה מקורי | תאריך חיוב | הערות | תיוגים | מועדון הנחות | מפתח דיסקונט | אופן ביצוע ההעסקה | שער המרה ממטבע מקור/התחשבנות לש"ח`.
- Dates are `dd-mm-yyyy`. `סוג עסקה` values seen: `רגילה`, `תשלומים`, `זיכוי`, `חיוב עסקות מיידי`. Refunds have a negative `סכום חיוב`.
- Installments sit in `הערות` as `תשלום 1 מתוך 3`. `סכום חיוב` is the monthly payment and `סכום עסקה מקורי` is the full price.
- Each sheet ends with `סך הכל` and a next row like `688.05₪`. Skip both and use them as a checksum.
- Immediate-charge debit purchases also appear on the bank statement.

### Visa Cal (Excel)

- Header: `תאריך עסקה | שם בית עסק | סכום עסקה | סכום חיוב | סוג עסקה | ענף | הערות`. Note **`בית עסק`**, without the ה that Max uses.
- Other parsers show variants with `סכום בש"ח` and `פרטי חיוב`. Title rows may precede the header.
- I didn't find evidence on separate foreign-currency sheets or on how installments are written. Verify against a real file.

### Parsing strategy: detect the header by keywords

1. Read every sheet with `header=None`, converting all cells to strings. Strip bidi marks (`‎ ‏ ‪-‮`) and normalize `"`, `״` and `'`.
2. Scan the first 25 rows. Score each row by how many cells match known header tokens: date (`תאריך`), merchant (`בית עסק|בית העסק|תיאור|סוג תנועה`), charged amount (`סכום חיוב|בחובה|חובה`), credit (`בזכות|זכות`), original amount (`סכום עסקה`), card (`4 ספרות`), balance (`יתרה`). Header = highest score, at least 3.
3. Skip sheets with no header row (summary tabs).
4. Map columns by token, not position. Classify the file as bank or card by which tokens are present (`בחובה`/`בזכות` means bank).
5. Stop at the first row whose first cell is `סך הכל`, or whose date doesn't parse and whose amounts are empty.
6. Parse dates with `dayfirst` over `dd-mm-yyyy`, `dd/mm/yyyy`, `dd.mm.yyyy` and Excel serials. Parse amounts after stripping `₪`, `$`, commas and parentheses.
7. Reconcile each sheet's sum against its `סך הכל` row, and warn on a mismatch.
8. Hash `source | date | amount | description | card4 | installment` plus an occurrence counter, so two identical coffees on one day both survive.
