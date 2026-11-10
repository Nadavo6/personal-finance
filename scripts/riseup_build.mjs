// Builds data/riseup_transactions.csv and docs/riseup_data_profile.md from one raw RiseUp export
// directory (data/raw/riseup/YYYY-MM-DD). No network access. Called by riseup_export.mjs.

import fs from 'node:fs';
import path from 'node:path';

// Stored category names that the RiseUp app shows differently (see tools/budget.js).
const SHOWN_NAME = { 'כלכלה': 'סופר', '__saving-hidden-category__': 'השקעה וחיסכון' };
const shown = name => (name == null ? '' : SHOWN_NAME[name] ?? name);
const SAVINGS = 'השקעה וחיסכון';

export const CSV_COLUMNS = [
  'id', 'date', 'billing_date', 'cashflow_month', 'amount_ils', 'is_income', 'original_amount', 'original_currency',
  'business_name', 'riseup_category', 'category_label', 'expense_type', 'category_type', 'envelope_type', 'actual_type',
  'is_excluded', 'is_postponed', 'source', 'source_type', 'account_hash', 'account_nickname', 'is_installment',
  'installment_number', 'installments_total', 'sequence_id', 'commitment_id', 'payment_app_addressee',
  'customer_comment', 'origin', 'raw_json',
];

const readJson = f => JSON.parse(fs.readFileSync(f, 'utf8'));
const monthFiles = dir => (fs.existsSync(dir) ? fs.readdirSync(dir).filter(f => /^\d{4}-\d{2}\.json$/.test(f)).sort() : []);
const r2 = n => Math.round(n * 100) / 100;
const fmt = n => Math.round(n).toLocaleString('en-US');
const day = s => (s ? String(s).slice(0, 10) : '');

function csvCell(v) {
  if (v === null || v === undefined) return '';
  const s = typeof v === 'string' ? v : String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// ---------- load + join ----------

function loadExport(dir) {
  const files = fs.readdirSync(dir);
  const opt = f => (f && fs.existsSync(path.join(dir, f)) ? readJson(path.join(dir, f)) : null);
  return {
    budgets: monthFiles(path.join(dir, 'budget')).map(f => readJson(path.join(dir, 'budget', f))),
    txByMonth: monthFiles(path.join(dir, 'transactions')).map(f => ({ month: f.slice(0, 7), ...readJson(path.join(dir, 'transactions', f)) })),
    manifest: opt('_manifest.json'),
    balancesCurrent: opt('balances_current.json'),
    balancesHistory: opt(files.find(f => f.startsWith('balances_history_'))),
    futureBalances: opt(files.find(f => f.startsWith('future_balances_'))),
    benchmarks: opt('benchmarks.json'),
    mortgages: opt('mortgages.json'),
  };
}

function buildRows(ex) {
  // Budget actuals keyed by transactionId, with the envelope they sit in.
  const budgetActual = new Map();
  for (const b of ex.budgets) {
    for (const e of b.envelopes ?? []) {
      const tc = e.details?.trackingCategory;
      for (const a of e.actuals ?? []) {
        budgetActual.set(a.transactionId, { a, envelopeType: e.type, trackingName: tc?.name ?? null, month: b.budgetDate });
      }
    }
    for (const a of b.excluded ?? []) budgetActual.set(a.transactionId, { a, envelopeType: 'excluded', trackingName: null, month: b.budgetDate });
  }

  const toRow = (t, ba) => {
    const a = ba?.a;
    const isIncome = t ? t.isIncome : a.isIncome;
    const amount = t ? t.amount : (isIncome ? a.incomeAmount : a.billingAmount);
    const category = ba?.trackingName != null ? shown(ba.trackingName) : shown(t?.categoryLabel ?? a?.expense ?? '');
    const isInstallment = Boolean(t?.isInstallment ?? a?.isInstallment);
    return {
      id: t?.transactionId ?? a.transactionId,
      date: day(t?.transactionDate ?? a.transactionDate),
      billing_date: day(t?.billingDate ?? a?.billingDate),
      cashflow_month: t?.cashflowDate ?? ba.month,
      amount_ils: r2(isIncome ? amount : -amount),
      is_income: isIncome,
      original_amount: a?.originalAmount ?? '',
      original_currency: '', // the API does not return a currency code
      business_name: String(t?.businessName ?? a.businessName ?? '').trim(), // bank pads names; raw_json keeps the original
      riseup_category: category,
      category_label: t?.categoryLabel ?? '',
      expense_type: t?.expenseType ?? a?.expense ?? '',
      category_type: t?.categoryType ?? '',
      envelope_type: ba?.envelopeType ?? 'unassigned',
      actual_type: t?.actualType ?? a?.placement ?? '',
      is_excluded: t ? Boolean(t.isExcluded) : ba.envelopeType === 'excluded',
      is_postponed: Boolean(t?.isPostponed ?? a?.isPostponed),
      source: t?.source ?? a.source,
      source_type: t?.sourceType ?? a.sourceType,
      account_hash: t?.accountNumberHash ?? a?.accountNumberHash ?? '',
      account_nickname: t?.accountNickname ?? a?.accountNickname ?? '',
      is_installment: isInstallment,
      installment_number: isInstallment ? (t?.installmentNumber ?? a?.paymentNumber ?? '') : '',
      installments_total: isInstallment ? (t?.totalNumberOfInstallments ?? t?.totalNumberOfPayments ?? a?.totalNumberOfPayments ?? '') : '',
      sequence_id: a?.sequenceId ?? '',
      commitment_id: t?.commitmentId ?? '',
      payment_app_addressee: t?.paymentAppAddressee ?? '',
      customer_comment: t?.transactionCustomerComment ?? '',
      origin: t && a ? 'both' : t ? 'transactions_api' : 'budget_only',
      raw_json: JSON.stringify({ transaction: t ?? null, budgetActual: a ?? null }),
    };
  };

  const rows = new Map();
  for (const m of ex.txByMonth) {
    for (const t of m.transactions ?? []) rows.set(t.transactionId, toRow(t, budgetActual.get(t.transactionId)));
  }
  for (const [id, ba] of budgetActual) if (!rows.has(id)) rows.set(id, toRow(null, ba));

  return [...rows.values()].sort((x, y) => (x.date < y.date ? -1 : x.date > y.date ? 1 : x.id < y.id ? -1 : 1));
}

function writeCsv(file, rows) {
  const lines = [CSV_COLUMNS.join(',')];
  for (const r of rows) lines.push(CSV_COLUMNS.map(c => csvCell(r[c])).join(','));
  fs.writeFileSync(file, '﻿' + lines.join('\r\n') + '\r\n', { mode: 0o600 });
}

// ---------- profile ----------

function table(headers, rows) {
  const esc = v => String(v ?? '').replace(/\|/g, '\\|');
  return [`| ${headers.join(' | ')} |`, `| ${headers.map(() => '---').join(' | ')} |`, ...rows.map(r => `| ${r.map(esc).join(' | ')} |`)].join('\n');
}

function countBy(arr, key) {
  const m = new Map();
  for (const x of arr) { const k = key(x); m.set(k, (m.get(k) ?? 0) + 1); }
  return m;
}

function fieldPresence(objs) {
  const m = new Map();
  for (const o of objs) for (const k of Object.keys(o ?? {})) m.set(k, (m.get(k) ?? 0) + 1);
  return [...m].map(([k, n]) => `\`${k}\` (${n}/${objs.length})`).join(', ');
}

function buildProfile(ex, rows, exportDir, root) {
  const out = [];
  const p = s => out.push(s);
  const current = ex.manifest?.summary?.currentCashflowMonth ?? rows.at(-1)?.cashflow_month;
  const months = [...new Set(rows.map(r => r.cashflow_month))].sort();
  const complete = months.filter(m => m < current);
  const last12 = complete.slice(-12);
  const counted = rows.filter(r => !r.is_excluded);

  p('# RiseUp data profile');
  p('');
  p(`Generated ${new Date().toISOString().slice(0, 10)} from \`${path.relative(root, exportDir)}\` by \`scripts/riseup_export.mjs\`. All amounts in ILS, rounded. Personal data — keep local.`);
  p('');
  p('## Coverage');
  p('');
  p(`- Cashflow months with transactions: **${months[0]} → ${months.at(-1)}** (${months.length} months; current cashflow month ${current} is partial).`);
  p(`- Budget responses saved for ${ex.budgets.length} months (${ex.budgets[0]?.budgetDate} → ${ex.budgets.at(-1)?.budgetDate}), incl. empty months before the first transaction and next month, which holds only planned envelopes.`);
  p(`- CSV rows: **${rows.length}** (de-duplicated by \`transactionId\`); ${rows.filter(r => r.is_excluded).length} excluded from cashflow, ${counted.length} counted.`);
  const origin = countBy(rows, r => r.origin);
  p(`- Row origin: ${[...origin].map(([k, n]) => `${k} ${n}`).join(', ')}. \`budget_only\` rows are budget \`excluded\` items the transactions endpoint omits (bank-side, e.g. credit-card bill payments that would double-count the card purchases). \`transactions_api\` rows have no budget envelope (\`envelope_type=unassigned\`).`);
  p(`- Averages below use the last 12 complete cashflow months (${last12[0]} → ${last12.at(-1)}) and exclude \`is_excluded\` rows.`);
  p('');

  p('### Rows, income and expenses per cashflow month');
  p('');
  const perMonth = months.map(m => {
    const rs = rows.filter(r => r.cashflow_month === m);
    const c = rs.filter(r => !r.is_excluded);
    const inc = c.filter(r => r.is_income).reduce((s, r) => s + r.amount_ils, 0);
    const savings = c.filter(r => !r.is_income && r.riseup_category === SAVINGS).reduce((s, r) => s - r.amount_ils, 0);
    const exp = c.filter(r => !r.is_income && r.riseup_category !== SAVINGS).reduce((s, r) => s - r.amount_ils, 0);
    return [m, rs.length, rs.length - c.length, fmt(inc), fmt(exp), fmt(savings), fmt(inc - exp - savings)];
  });
  p(table(['month', 'rows', 'excluded', 'income', 'expenses (excl. savings)', 'savings/investment', 'net'], perMonth));
  p('');
  p('Income = counted rows with `is_income=true`; expenses = counted non-income rows outside the savings category, net of refunds; savings = non-income rows in "השקעה וחיסכון". Cash basis on RiseUp\'s cashflow month.');
  p('');

  p('## Sources and accounts');
  p('');
  const acc = new Map();
  for (const r of rows) {
    const k = `${r.source}|${r.source_type}|${r.account_hash}`;
    const a = acc.get(k) ?? { source: r.source, type: r.source_type, hash: r.account_hash, nick: '', n: 0, first: r.date, last: r.date };
    a.n++; if (r.date < a.first) a.first = r.date; if (r.date > a.last) a.last = r.date; if (r.account_nickname) a.nick = r.account_nickname;
    acc.set(k, a);
  }
  p(table(['source', 'source_type', 'account_hash', 'nickname', 'rows', 'first', 'last'], [...acc.values()].sort((a, b) => b.n - a.n).map(a => [a.source, a.type, a.hash || '(null)', a.nick, a.n, a.first, a.last])));
  p('');
  for (const b of ex.balancesCurrent?.balances ?? []) {
    p(`- Current balance (${b.source}, ${b.accountNumberHash}): ${fmt(b.balance)}, updated ${b.lastUpdated}; overdraft limit ${b.overdraftLimit ?? 'unknown'}, available ${b.availableOverdraft ?? 'unknown'}.`);
  }
  for (const a of ex.balancesHistory?.accounts ?? []) {
    p(`- 90-day balance history (${a.source}, ${a.accountNumberHash}): ${a.balances.length} daily points, ${a.balances.at(-1)?.date} → ${a.balances[0]?.date}; min ${fmt(a.minBalance)}, max ${fmt(a.maxBalance)}, avg ${fmt(a.avgBalance)}.`);
  }
  for (const a of ex.futureBalances?.accounts ?? []) {
    p(`- Future balance (${a.bankName}, ${a.accountNumberHash}) at ${a.futureDateForPrediction}: ${fmt(a.futurePredictedBalance)} predicted from ${fmt(a.currentBalance)} (${a.currentBalanceUpdatedAt}); known billings ${fmt(a.amountToBeBilledUntilFuturePredictionDate)} (${a.transactionsBilledUntilFuturePredictionDate.length} items), predicted fixed ${fmt(a.amountFixedPredictedToBeBilledUntilFuturePredictionDate)} (${a.fixedTransactionsPredictedToBeBilledUntilFuturePredictionDate.length} items), ${a.creditCardsInfo.length} card(s).`);
  }
  p('');

  p('## Categories');
  p('');
  p('`riseup_category` = the tracking-category name as the app shows it ("כלכלה"→"סופר", "__saving-hidden-category__"→"השקעה וחיסכון") when the transaction sits in a tracking-category envelope; otherwise the transaction\'s `categoryLabel` (or the budget actual\'s `expense` for budget-only rows). Spend = non-income rows of counted transactions, net of refunds.');
  p('');
  const cats = new Map();
  for (const r of counted) {
    if (r.is_income) continue;
    const c = cats.get(r.riseup_category) ?? { total: 0, last12: 0, n: 0, months: new Set(), env: new Map() };
    c.total -= r.amount_ils; c.n++; c.months.add(r.cashflow_month);
    if (last12.includes(r.cashflow_month)) c.last12 -= r.amount_ils;
    c.env.set(r.envelope_type, (c.env.get(r.envelope_type) ?? 0) + 1);
    cats.set(r.riseup_category, c);
  }
  p(table(['category', 'rows', 'months seen', 'total spend', 'avg / month (last 12)', 'envelope types'],
    [...cats].sort((a, b) => b[1].total - a[1].total).map(([k, c]) => [k || '(blank)', c.n, c.months.size, fmt(c.total), fmt(c.last12 / last12.length), [...c.env].map(([e, n]) => `${e} ${n}`).join(', ')])));
  p('');
  const incCats = new Map();
  for (const r of counted.filter(r => r.is_income)) {
    const c = incCats.get(r.riseup_category) ?? { n: 0, total: 0 };
    c.n++; c.total += r.amount_ils; incCats.set(r.riseup_category, c);
  }
  p(`Income by category: ${[...incCats].sort((a, b) => b[1].total - a[1].total).map(([k, c]) => `${k || '(blank)'} ${c.n} rows / ${fmt(c.total)}`).join('; ')}.`);
  p('');

  p(`### Category targets (${current} budget)`);
  p('');
  const curBudget = ex.budgets.find(b => b.budgetDate === current);
  const targets = new Map();
  for (const e of curBudget?.envelopes ?? []) {
    if (e.type !== 'trackingCategory') continue;
    const tc = e.details.trackingCategory;
    const t = targets.get(tc.name) ?? { target: 0, spent: 0, custom: e.isCustomPrediction, freq: tc.frequency, type: tc.trackingCategoryType, weeks: 0 };
    t.target += e.originalAmount ?? 0; t.weeks++;
    for (const a of e.actuals ?? []) t.spent += (a.billingAmount ?? 0) - (a.incomeAmount ?? 0);
    targets.set(tc.name, t);
  }
  p(table(['category (shown)', 'stored name', 'type', 'frequency', 'target', 'set by', 'spent so far'],
    [...targets].map(([k, t]) => [shown(k), k, t.type, t.freq + (t.freq === 'weekly' ? ` (${t.weeks} envelopes)` : ''), fmt(t.target), t.custom ? 'customer' : 'RiseUp estimate', fmt(t.spent)])));
  p('');
  const goals = (curBudget?.envelopes ?? []).filter(e => e.type === 'riseupGoal');
  p(`RiseUp goal envelopes in ${current}: ${goals.length}${goals.length ? ` (originalAmount ${goals.map(g => fmt(g.originalAmount ?? 0)).join(', ')}; ${goals.reduce((n, g) => n + (g.actuals?.length ?? 0), 0)} actuals)` : ''}.`);
  p('');

  p('## Top 30 merchants by spend');
  p('');
  const merch = new Map();
  for (const r of counted) {
    if (r.is_income) continue;
    const m = merch.get(r.business_name) ?? { total: 0, n: 0, cats: new Map(), months: new Set() };
    m.total -= r.amount_ils; m.n++; m.months.add(r.cashflow_month);
    m.cats.set(r.riseup_category, (m.cats.get(r.riseup_category) ?? 0) + 1);
    merch.set(r.business_name, m);
  }
  p(table(['#', 'business_name', 'rows', 'months', 'total spend', 'main category'],
    [...merch].sort((a, b) => b[1].total - a[1].total).slice(0, 30).map(([k, m], i) => [i + 1, k, m.n, m.months.size, fmt(m.total), [...m.cats].sort((a, b) => b[1] - a[1])[0][0] || '(blank)'])));
  p('');

  p('## Fixed (recurring) incomes and expenses');
  p('');
  p('From budget `fixed` envelopes, grouped by the actuals\' `sequenceId` (envelope ids change every month; `sequenceId` is stable). "Planned" = the envelope\'s `originalAmount` in the latest month it appears (negative = income).');
  p('');
  const seq = new Map();
  for (const b of ex.budgets) {
    for (const e of b.envelopes ?? []) {
      if (e.type !== 'fixed') continue;
      for (const a of e.actuals ?? []) {
        const s = seq.get(a.sequenceId) ?? { names: new Map(), income: a.isIncome, months: new Set(), sum: 0, last: '', planned: null, interval: a.monthsInterval, comment: '' };
        s.names.set(a.businessName, (s.names.get(a.businessName) ?? 0) + 1);
        s.months.add(b.budgetDate); s.sum += (a.isIncome ? a.incomeAmount : a.billingAmount) ?? 0;
        if (b.budgetDate >= s.last) { s.last = b.budgetDate; s.planned = e.originalAmount; s.comment = e.sequenceCustomerComment ?? s.comment; }
        seq.set(a.sequenceId, s);
      }
    }
  }
  const seqRows = [...seq].map(([id, s]) => ({ kind: s.income ? 'income' : 'expense', avg: s.sum / s.months.size, cells: [s.income ? 'income' : 'expense', [...s.names].sort((a, b) => b[1] - a[1])[0][0], s.months.size, fmt(s.sum / s.months.size), fmt(s.planned ?? 0), s.last, s.interval ?? '', s.comment || '', id] }));
  seqRows.sort((a, b) => (a.kind === b.kind ? b.avg - a.avg : a.kind === 'income' ? -1 : 1));
  p(table(['kind', 'business_name (most common)', 'months seen', 'avg / month seen', 'planned (latest)', 'last month', 'monthsInterval', 'customer note', 'sequenceId'], seqRows.map(r => r.cells)));
  p('');
  const fixedNoActuals = (curBudget?.envelopes ?? []).filter(e => e.type === 'fixed' && !(e.actuals ?? []).length);
  p(`Fixed envelopes in ${current} still waiting for their transaction: ${fixedNoActuals.length} (planned total ${fmt(fixedNoActuals.reduce((s, e) => s + (e.originalAmount ?? 0), 0))}; the API gives these no name).`);
  p('');

  p('## Benchmarks and mortgage');
  p('');
  for (const b of ex.benchmarks?.benchmarks ?? []) {
    p(`- ${b.spendArea} (${b.category}): customer ${fmt(b.customerSpend)}/month (6-month typical), percentile ${b.customerSpendPercentile}, similar customers mean ${fmt(b.similarCustomersMean)}.`);
  }
  const cb = ex.benchmarks?.benchmarks?.[0]?.comparedBy;
  if (cb) p(`- Compared by: kids ${cb.numberOfKids ?? 'n/a'}, marital ${cb.maritalStatus ?? 'n/a'}, city ${cb.city ?? 'n/a'}, income range ${cb.monthlyIncomeRange ? `${cb.monthlyIncomeRange.from}–${cb.monthlyIncomeRange.to ?? '+'}` : 'n/a'}.`);
  const mg = ex.mortgages;
  if (mg) p(`- Mortgage: hasMortgageData=${mg.hasMortgageData}; payments in cashflow ${mg.paymentsCashflowMonth}: ${fmt(mg.mortgagePaymentsInCashflow ?? 0)} (hasMortgagePaymentsInCashflow=${mg.hasMortgagePaymentsInCashflow}).${mg.hasMortgageData ? ` Remaining ${fmt(mg.totalRemainingAmount)}, monthly ${fmt(mg.monthlyPayment)}, ${mg.parts?.length ?? 0} parts.` : ' No mortgage details (parts, rates, remaining) on file.'}`);
  p('');

  p('## Endpoint status');
  p('');
  const eps = ex.manifest?.summary?.endpoints ?? {};
  p(table(['endpoint', 'status'], Object.entries(eps)));
  p('');
  const failed = Object.entries(eps).filter(([, v]) => v !== 'ok');
  p(failed.length ? `Failed: ${failed.map(([k, v]) => `${k} (${v})`).join(', ')}.` : 'No endpoint returned 403 or failed.');
  p('The API is rate-limited to 60 requests per minute (429 with `retryAfterSeconds`); the exporter paces at ~55/min.');
  p('');

  p('## Fields available for an app');
  p('');
  const allTx = ex.txByMonth.flatMap(m => m.transactions ?? []);
  const allEnv = ex.budgets.flatMap(b => b.envelopes ?? []);
  const allAct = allEnv.flatMap(e => e.actuals ?? []);
  const allExc = ex.budgets.flatMap(b => b.excluded ?? []);
  p(`- **transactions** (\`/api/external/transactions?cashflowMonth=\`, one month per call, ≤10000 rows): ${fieldPresence(allTx)}.`);
  p(`- **budget** top level: ${fieldPresence(ex.budgets)}.`);
  p(`- **budget envelopes** (types: ${[...countBy(allEnv, e => e.type)].map(([k, n]) => `${k} ${n}`).join(', ')}): ${fieldPresence(allEnv)}. \`details.trackingCategory\` gives name / trackingCategoryType / frequency; weekly envelopes add startDate, endDate, weeklyIndex. \`originalAmount\` on trackingCategory = category target, \`isCustomPrediction\` = who set it.`);
  p(`- **budget actuals**: ${fieldPresence(allAct)}. Only here: \`originalAmount\` (pre-conversion / full installment amount, no currency code), \`sequenceId\`, \`monthsInterval\`, \`placement\`.`);
  p(`- **budget excluded**: ${fieldPresence(allExc)}.`);
  p(`- **balances/current**: ${fieldPresence(ex.balancesCurrent?.balances ?? [])}.`);
  p(`- **balances/history** (max 90 days): ${fieldPresence(ex.balancesHistory?.accounts ?? [])}; balances[] = {date, balance}.`);
  p(`- **future-balances** (up to end of next cashflow month): ${fieldPresence(ex.futureBalances?.accounts ?? [])}.`);
  p(`- **benchmarks** (${(ex.benchmarks?.benchmarks ?? []).length} of 9 spend areas returned): ${fieldPresence(ex.benchmarks?.benchmarks ?? [])}.`);
  p(`- **mortgages**: ${fieldPresence(mg ? [mg] : [])}.`);
  p('');
  p('Not in the API: a currency code for `originalAmount`, merchant category codes, names for fixed envelopes (only via their actuals\' businessName), envelope ids that stay stable across months, balance history beyond 90 days.');
  p('');
  p('## CSV columns');
  p('');
  p(`\`${CSV_COLUMNS.join(',')}\``);
  p('');
  p('`amount_ils` is signed (expense negative, income positive; a refund is a positive row with `is_income=false`). `raw_json` holds the transactions-endpoint object and the budget actual, compact.');
  return out.join('\n') + '\n';
}

export function buildOutputs(root, exportDir) {
  const ex = loadExport(exportDir);
  const rows = buildRows(ex);
  const csvPath = path.join(root, 'data', 'riseup_transactions.csv');
  writeCsv(csvPath, rows);
  const docsDir = path.join(root, 'docs');
  fs.mkdirSync(docsDir, { recursive: true });
  const profilePath = path.join(docsDir, 'riseup_data_profile.md');
  fs.writeFileSync(profilePath, buildProfile(ex, rows, exportDir, root), { mode: 0o600 });
  return { csvPath, profilePath, rows: rows.length };
}
