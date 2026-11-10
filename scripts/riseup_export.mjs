#!/usr/bin/env node
// RiseUp read-only export: fetches every endpoint of the external API, saves raw JSON,
// then builds data/riseup_transactions.csv and docs/riseup_data_profile.md.
//
// Usage (token comes from the Keychain, never from a file):
//   RISEUP_PAT=$(security find-generic-password -a "$USER" -s riseup_pat -w) node scripts/riseup_export.mjs
//   node scripts/riseup_export.mjs --build-only [data/raw/riseup/YYYY-MM-DD]   # rebuild CSV + profile, no API calls
//
// Node 18+, no dependencies. Requests are sequential and paced under the API's 60/minute limit,
// with at most one retry (after the server's retryAfterSeconds on a 429). A second 429 aborts the run.
// Re-running on the same day reuses the month files already saved instead of refetching them.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildOutputs } from './riseup_build.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const RAW_ROOT = path.join(ROOT, 'data', 'raw', 'riseup');
const API_BASE = 'https://input.riseup.co.il';
const DELAY_MS = 1100; // API rate limit: 60 requests per minute window
const MIN_MONTHS_BACK = 24;
const EMPTY_STREAK_STOP = 3;
const HARD_MONTHS_CAP = 180; // safety stop: never walk back more than 15 years
const MAX_DAYS_BACK = 90; // balances.js MAX_DAYS_BACK
const SPEND_AREAS = ['eating_out', 'economy', 'electricity', 'insurance', 'mortgage', 'loan', 'communication', 'car', 'public_transport'];

const sleep = ms => new Promise(r => setTimeout(r, ms));
const today = () => new Date().toISOString().slice(0, 10);

function mkdirPrivate(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  fs.chmodSync(dir, 0o700);
}

function writeJson(file, obj) {
  mkdirPrivate(path.dirname(file));
  fs.writeFileSync(file, JSON.stringify(obj, null, 2) + '\n', { mode: 0o600 });
}

function shiftMonth(ym, delta) {
  const [y, m] = ym.split('-').map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return d.toISOString().slice(0, 7);
}

function lastDayOfMonth(ym) {
  const [y, m] = ym.split('-').map(Number);
  return new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10);
}

// ---------- HTTP ----------

let lastRequestAt = 0;
const manifest = { exportedAt: new Date().toISOString(), apiBase: API_BASE, requests: [] };

async function apiGet(pat, apiPath) {
  const wait = lastRequestAt + DELAY_MS - Date.now();
  if (wait > 0) await sleep(wait);
  for (let attempt = 1; attempt <= 2; attempt++) {
    lastRequestAt = Date.now();
    const started = Date.now();
    let status = null;
    let body = null;
    let error = null;
    try {
      const res = await fetch(API_BASE + apiPath, {
        headers: { Authorization: `Bearer ${pat}`, Accept: 'application/json' },
        signal: AbortSignal.timeout(60_000),
      });
      status = res.status;
      const text = await res.text();
      try { body = JSON.parse(text); } catch { body = text.slice(0, 500); }
    } catch (e) {
      error = e.cause?.code ?? e.name ?? 'network error';
    }
    const ms = Date.now() - started;
    const retryable = error !== null || status === 429 || (status >= 500 && status < 600);
    if (status === 429 && attempt === 2) throw new Error(`Rate limited twice on ${apiPath}; stopping. Re-run later — saved months are reused.`);
    const entry = { path: apiPath, status, ms, attempt, ...(error ? { error } : {}) };
    if (status !== null && (status < 200 || status >= 300)) entry.errorBody = body;
    manifest.requests.push(entry);
    process.stdout.write(`  ${status ?? error} ${String(ms).padStart(5)}ms ${apiPath}\n`);
    if (status === 401) throw new Error('RiseUp PAT rejected (401): token expired or revoked. Stopping.');
    if (retryable && attempt === 1) {
      const retryAfter = status === 429 ? Math.min(Number(body?.retryAfterSeconds) || 60, 65) + 1 : 2;
      process.stdout.write(`  retrying once in ${retryAfter}s\n`);
      await sleep(retryAfter * 1000);
      continue;
    }
    return { status, body, error };
  }
}

const isOk = r => r.status >= 200 && r.status < 300;

// Same-day re-runs: a month already saved today is read from disk, not refetched.
async function cachedGet(pat, apiPath, file) {
  if (fs.existsSync(file)) {
    manifest.requests.push({ path: apiPath, status: 200, cached: true });
    return { status: 200, body: JSON.parse(fs.readFileSync(file, 'utf8')), error: null };
  }
  return apiGet(pat, apiPath);
}

// ---------- export ----------

async function exportAll(pat) {
  const outDir = path.join(RAW_ROOT, today());
  mkdirPrivate(outDir);
  const summary = { outDir, endpoints: {} };
  const note = (name, r) => {
    summary.endpoints[name] = isOk(r) ? 'ok' : `${r.status ?? r.error}${r.status === 403 ? ' (missing scope)' : ''}`;
  };

  console.log('budget/current');
  const cur = await apiGet(pat, '/api/external/budget/current');
  note('budget/current', cur);
  if (isOk(cur)) writeJson(path.join(outDir, 'budget', '_current.json'), cur.body);
  const prev = await apiGet(pat, '/api/external/budget/previous');
  note('budget/previous', prev);
  if (isOk(prev)) writeJson(path.join(outDir, 'budget', '_previous.json'), prev.body);

  const currentMonth = (isOk(cur) && cur.body?.budgetDate) || today().slice(0, 7);
  summary.currentCashflowMonth = currentMonth;

  // Next month first (postponed card charges / future-dated items), then walk back.
  const months = [];
  const monthStatus = {};
  let emptyStreak = 0;
  for (let i = -1; i < HARD_MONTHS_CAP; i++) {
    const ym = shiftMonth(currentMonth, -i);
    console.log(`month ${ym}`);
    const b = await cachedGet(pat, `/api/external/budget/${ym}`, path.join(outDir, 'budget', `${ym}.json`));
    const t = await cachedGet(pat, `/api/external/transactions?cashflowMonth=${ym}`, path.join(outDir, 'transactions', `${ym}.json`));
    const envCount = isOk(b) ? (b.body?.envelopes?.length ?? 0) + (b.body?.excluded?.length ?? 0) : 0;
    const actualCount = isOk(b) ? (b.body?.envelopes ?? []).reduce((n, e) => n + (e.actuals?.length ?? 0), 0) + (b.body?.excluded?.length ?? 0) : 0;
    const txCount = isOk(t) ? (t.body?.transactions?.length ?? 0) : 0;
    if (isOk(b)) writeJson(path.join(outDir, 'budget', `${ym}.json`), b.body);
    if (isOk(t)) writeJson(path.join(outDir, 'transactions', `${ym}.json`), t.body);
    if (txCount >= 10000) console.warn(`  WARNING: ${ym} returned ${txCount} transactions (API cap 10000) — may be truncated`);
    monthStatus[ym] = { budget: b.status ?? b.error, transactions: t.status ?? t.error, envelopes: envCount, actuals: actualCount, transactions_count: txCount };
    if (t.status === 403) summary.endpoints.transactions = '403 (missing scope)';
    if (b.status === 403) summary.endpoints['budget/{month}'] = '403 (missing scope)';
    const hasData = actualCount > 0 || txCount > 0;
    const answered = r => isOk(r) || r.status === 404 || r.status === 403;
    if (!answered(b) || !answered(t)) throw new Error(`Month ${ym} failed (budget ${b.status ?? b.error}, transactions ${t.status ?? t.error}); stopping so an error is not mistaken for an empty month.`);
    if (hasData) { months.push(ym); emptyStreak = 0; } else if (i >= 0) { emptyStreak++; }
    if (b.status === 403 && t.status === 403) break; // nothing to walk
    if (i + 1 >= MIN_MONTHS_BACK && emptyStreak >= EMPTY_STREAK_STOP) break;
  }
  summary.endpoints.transactions ??= 'ok';
  summary.endpoints['budget/{month}'] ??= 'ok';
  summary.monthsWithData = months.sort();
  summary.monthStatus = monthStatus;

  console.log('balances');
  const bc = await apiGet(pat, '/api/external/balances/current');
  note('balances/current', bc);
  if (isOk(bc)) writeJson(path.join(outDir, 'balances_current.json'), bc.body);
  const bh = await apiGet(pat, `/api/external/balances/history?daysBack=${MAX_DAYS_BACK}`);
  note('balances/history', bh);
  if (isOk(bh)) writeJson(path.join(outDir, `balances_history_${MAX_DAYS_BACK}d.json`), bh.body);

  // Future balances: up to the end of the NEXT cashflow month. A cashflow month may not start on
  // the 1st, so try the last day of next calendar month; if the server rejects it (400 explains
  // the supported range) fall back once to the last day of the current calendar month.
  console.log('future-balances');
  const candidates = [lastDayOfMonth(shiftMonth(currentMonth, 1)), lastDayOfMonth(currentMonth)];
  let fb = null;
  for (const d of candidates) {
    if (d < today()) continue;
    fb = await apiGet(pat, `/api/external/future-balances?futureDateForPrediction=${d}`);
    if (isOk(fb)) { writeJson(path.join(outDir, `future_balances_${d}.json`), fb.body); summary.futureBalancesDate = d; break; }
    if (fb.status !== 400) break;
  }
  if (fb) note('future-balances', fb);

  console.log('benchmarks');
  const bm = await apiGet(pat, `/api/external/benchmarks?${SPEND_AREAS.map(a => `spendAreas=${a}`).join('&')}`);
  note('benchmarks', bm);
  if (isOk(bm)) writeJson(path.join(outDir, 'benchmarks.json'), bm.body);

  console.log('mortgages');
  const mg = await apiGet(pat, '/api/external/mortgages');
  note('mortgages', mg);
  if (isOk(mg)) writeJson(path.join(outDir, 'mortgages.json'), mg.body);

  manifest.summary = summary;
  writeJson(path.join(outDir, '_manifest.json'), manifest);
  return outDir;
}

function latestExportDir() {
  const dirs = fs.readdirSync(RAW_ROOT).filter(d => /^\d{4}-\d{2}-\d{2}$/.test(d)).sort();
  if (!dirs.length) throw new Error(`No exports under ${RAW_ROOT}`);
  return path.join(RAW_ROOT, dirs.at(-1));
}

async function main() {
  const args = process.argv.slice(2);
  let outDir;
  if (args[0] === '--build-only') {
    outDir = args[1] ? path.resolve(args[1]) : latestExportDir();
  } else {
    const pat = process.env.RISEUP_PAT;
    if (!pat) throw new Error('RISEUP_PAT is not set. Run: RISEUP_PAT=$(security find-generic-password -a "$USER" -s riseup_pat -w) node scripts/riseup_export.mjs');
    if (!pat.startsWith('riseup_pat_')) throw new Error('RISEUP_PAT does not have the riseup_pat_ prefix.');
    for (const d of [path.join(ROOT, 'data'), path.join(ROOT, 'data', 'raw'), RAW_ROOT]) mkdirPrivate(d);
    outDir = await exportAll(pat);
  }
  console.log(`raw export: ${outDir}`);
  if (args.includes('--fetch-only')) return;
  const res = buildOutputs(ROOT, outDir);
  console.log(`csv: ${res.csvPath} (${res.rows} rows)`);
  console.log(`profile: ${res.profilePath}`);
}

main().catch(e => { console.error(`ERROR: ${e.message}`); process.exit(1); });
