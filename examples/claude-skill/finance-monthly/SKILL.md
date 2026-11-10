---
name: finance-monthly
description: Monthly refresh of the local finance app — import new bank/card files from the inbox (or Downloads), run live sync if credentials exist, optionally refresh the net-worth sheet snapshot, then review the month's expenses and post findings + tasks into the app. Use when the user says "finance monthly", "/finance-monthly", "monthly finance review" or "refresh my finance app".
---

# Monthly finance refresh

Copy this folder to `~/.claude/skills/finance-monthly/` and replace `<FINANCE_DIR>` with your clone's path.
Optional: replace `<SHEET_ID>` / `<SHEET_TABS>` if you keep net worth in a Google Sheet.

Everything is personal data. Work only inside `<FINANCE_DIR>`; never copy data elsewhere (no scratchpad, no /tmp)
and never send it anywhere except the optional Google Sheets read below. Real-data screenshots go to `data/screens/`.

App: `cd <FINANCE_DIR> && ./finance` (serves on 127.0.0.1:8747, falls back to the next free port).
CLI: `./finance import <file> | import-inbox | import-sheet [path] | sync | recategorize | backup | apply-setup`.

## Steps

1. **Backup**: `./finance backup`.
2. **Move new files**: look in `~/Downloads` for new bank and card exports (Leumi PDF / Excel, Max `.xlsx`, Cal `.xlsx`,
   any CSV). Show the list, then `mv` them into `data/inbox/` with a dated name (`<provider>_YYYY-MM-DD.<ext>`),
   `chmod 600`.
3. **Live sync** (optional): if Keychain items `finapp-<provider>` exist
   (`security find-generic-password -s finapp-max -a username >/dev/null`), run `./finance sync`; report per-provider
   ok / error. On failure, fall back to files.
4. **Import**: `./finance import-inbox`; report rows / new / dup per file.
5. **Net-worth sheet** (optional): read `<SHEET_ID>` (tabs `<SHEET_TABS>`) with the Google Sheets connector and write
   `data/sheet/snapshot_<today>.json` in the format of `examples/sheet_snapshot.example.json` (assets with `as_of` = the
   sheet's last-updated date, property, rental_income, fixed_monthly, future_income / future_expenses, equity_comp,
   tasks, freebies). Then `./finance import-sheet`. If the sheet is older than 45 days, say which balances look stale.
6. **Review the month** (previous cashflow month). First read `insight_dismissals` (key, reason) and open / done `tasks`:
   never re-raise a dismissed topic and don't duplicate an existing task. Then query `data/finance.db` (sqlite3,
   read-only) or the API. Look for: new recurring charges, price increases on fixed charges, fees (דמי כרטיס, עמלה,
   ריבית), duplicate charges (same merchant + amount ≤ 3 days), categories far above their 6-month median,
   uncategorized rows, large one-off inflows / outflows that need tagging (one-off event / savings), expiring freebies,
   goals off track. Post each finding with `POST /api/insights/manual` (`key` stable like `ai:<topic>-<YYYY-MM>`,
   `severity`, `title`, `detail`, `est_monthly_saving`, `est_yearly_saving`, `merchant`, `suggested_task`). Save the
   batch to `data/review/ai_review_<date>.json`.
7. **Report** in ≤ 8 lines, bottom line first: month income / spend / net vs usual, red or amber flags with the
   reason, top 3 actions (₪/yr), and anything that needs a decision (uncategorized count, one-offs to confirm, stale
   balances).

Never commit data: `data/` is gitignored.
