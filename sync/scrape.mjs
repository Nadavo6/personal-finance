#!/usr/bin/env node
// Live sync via israeli-bank-scrapers. Credentials come from the macOS Keychain:
//   security find-generic-password -s finapp-<provider> -a <field> -w
// Usage: node scrape.mjs --providers leumi,max --start YYYY-MM-DD --out result.json [--show-browser]
// Output (JSON array, one entry per provider, run sequentially):
//   [{provider, ok, error, errorType, accounts:[{accountNumber, balance, txns}]}]
// Credentials are never logged or written anywhere.

import { execFileSync } from 'node:child_process';
import { writeFileSync, renameSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const COMPANY = { leumi: 'leumi', max: 'max', visaCal: 'visaCal', cal: 'visaCal' };
const FIELDS = { leumi: ['username', 'password'], max: ['username', 'password'], visaCal: ['username', 'password'] };

function parseArgs(argv) {
  const args = { providers: ['leumi', 'max'], start: null, out: null, showBrowser: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => {
      if (i + 1 >= argv.length) throw new Error(`missing value for ${a}`);
      return argv[++i];
    };
    if (a === '--providers') args.providers = next().split(',').map((s) => s.trim()).filter(Boolean);
    else if (a === '--start') args.start = next();
    else if (a === '--out') args.out = next();
    else if (a === '--show-browser') args.showBrowser = true;
    else throw new Error(`unknown argument ${a}`);
  }
  if (!args.start) {
    args.start = new Date(Date.now() - 45 * 864e5).toISOString().slice(0, 10);
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(args.start)) throw new Error('--start must be YYYY-MM-DD');
  return args;
}

function keychainSecret(provider, field) {
  try {
    return execFileSync('security', ['find-generic-password', '-s', `finapp-${provider}`, '-a', field, '-w'], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).replace(/\n$/, '');
  } catch {
    throw new Error(`Keychain item finapp-${provider} / ${field} not found (run sync/setup_credentials.sh)`);
  }
}

function credentials(provider) {
  const creds = {};
  for (const f of FIELDS[provider]) creds[f] = keychainSecret(provider, f);
  return creds;
}

async function scrapeOne(lib, provider, startDate, showBrowser) {
  const key = provider === 'cal' ? 'visaCal' : provider;
  const companyId = lib.CompanyTypes[COMPANY[provider]];
  if (!companyId) return { provider: key, ok: false, error: `unsupported provider ${provider}`, accounts: [] };
  let creds;
  try {
    creds = credentials(key);
  } catch (e) {
    return { provider: key, ok: false, errorType: 'CREDENTIALS', error: e.message, accounts: [] };
  }
  const scraper = lib.createScraper({
    companyId,
    startDate,
    combineInstallments: false,
    showBrowser,
    futureMonthsToScrape: 1,
    storeFailureScreenShotPath: join(tmpdir(), `finapp-scrape-fail-${key}.png`),
  });
  try {
    const res = await scraper.scrape(creds);
    return {
      provider: key,
      ok: Boolean(res.success),
      errorType: res.errorType ?? null,
      error: res.success ? null : `${res.errorType ?? 'ERROR'}: ${res.errorMessage ?? ''}`.trim(),
      accounts: (res.accounts ?? []).map((a) => ({
        accountNumber: a.accountNumber,
        balance: a.balance ?? null,
        txns: a.txns ?? [],
      })),
    };
  } catch (e) {
    return { provider: key, ok: false, errorType: 'EXCEPTION', error: String(e?.message ?? e), accounts: [] };
  } finally {
    creds = null;
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  let lib;
  try {
    lib = await import('israeli-bank-scrapers');
  } catch {
    const err = 'israeli-bank-scrapers is not installed: run `npm install` in sync/';
    const results = args.providers.map((p) => ({ provider: p, ok: false, errorType: 'NOT_INSTALLED', error: err, accounts: [] }));
    emit(args.out, results);
    process.exit(3);
  }
  const startDate = new Date(`${args.start}T00:00:00`);
  const results = [];
  for (const p of args.providers) {
    const r = await scrapeOne(lib, p, startDate, args.showBrowser);
    const n = r.accounts.reduce((s, a) => s + a.txns.length, 0);
    process.stderr.write(`${r.provider}: ${r.ok ? 'ok' : 'FAILED'} accounts=${r.accounts.length} txns=${n}${r.ok ? '' : ` (${r.error})`}\n`);
    results.push(r);
  }
  emit(args.out, results);
  process.exit(results.every((r) => r.ok) ? 0 : 2);
}

function emit(out, results) {
  const json = JSON.stringify(results, null, 1);
  if (!out) {
    process.stdout.write(json + '\n');
    return;
  }
  writeFileSync(`${out}.tmp`, json);
  renameSync(`${out}.tmp`, out);
}

main().catch((e) => {
  process.stderr.write(`scrape failed: ${e?.message ?? e}\n`);
  process.exit(1);
});
