// Dev-only fetch interceptor (enable with ?mock=1). Stateful, in-memory, invented Hebrew sample data
// shaped exactly like the JSON API in docs/ARCHITECTURE.md. Nothing here is real data.

let seed = 20261110;
function rnd() { // mulberry32
  seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
const between = (a, b) => a + rnd() * (b - a);
const pick = (arr) => arr[Math.floor(rnd() * arr.length)];
const r2 = (v) => Math.round(v * 100) / 100;
const pad = (n) => String(n).padStart(2, '0');
const ym = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addM = (m, n) => { const [y, mm] = m.split('-').map(Number); return ym(new Date(y, mm - 1 + n, 1)); };
const median = (a) => { if (!a.length) return 0; const s = [...a].sort((x, y) => x - y); const k = s.length >> 1; return s.length % 2 ? s[k] : (s[k - 1] + s[k]) / 2; };
const quant = (a, q) => { if (!a.length) return 0; const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(q * s.length))]; };

const NOW = new Date();
const TODAY = iso(NOW);
const CUR = ym(NOW);

// ---------------- reference data ----------------
const categories = [
  [1, 'Salary', 'משכורת', 'income', null, '#1f9d63'],
  [2, 'Reserve duty', 'מילואים', 'income', null, '#3aa776'],
  [3, 'Refunds', 'החזרים', 'income', null, '#6cbf98'],
  [4, 'Housing', 'דיור', 'fixed', 5200, '#5b5bd6'],
  [5, 'Arnona', 'ארנונה', 'fixed', 340, '#7272dc'],
  [6, 'Utilities', 'חשמל, מים, גז', 'fixed', 450, '#8a8ae3'],
  [7, 'Internet & mobile', 'אינטרנט וסלולר', 'fixed', 190, '#a0a0e9'],
  [8, 'Insurance', 'ביטוחים', 'fixed', 380, '#4b6fb5'],
  [9, 'Subscriptions', 'מנויים', 'fixed', 160, '#6a8bd0'],
  [10, 'Groceries', 'סופר ומכולת', 'variable', 3000, '#e0803a'],
  [11, 'Dining out', 'מסעדות ובתי קפה', 'variable', 1100, '#e5a03c'],
  [12, 'Transport', 'תחבורה', 'variable', 650, '#c9a227'],
  [13, 'Shopping', 'קניות', 'variable', 1300, '#d16a8b'],
  [14, 'Entertainment', 'פנאי ובילויים', 'tracking', null, '#b26cc7'],
  [15, 'Health', 'בריאות', 'tracking', null, '#39a3a3'],
  [16, 'Travel', 'חופשות וטיולים', 'tracking', null, '#3b8fd9'],
  [17, 'Gifts & donations', 'מתנות ותרומות', 'tracking', null, '#c46666'],
  [18, 'Fees', 'עמלות', 'fixed', null, '#8c8c96'],
  [19, 'Savings & investments', 'השקעה וחיסכון', 'savings', null, '#2c9c8c'],
  [20, 'Card payment', 'חיוב כרטיס', 'transfer', null, '#9a9aa5'],
  [21, 'Internal transfer', 'העברה פנימית', 'transfer', null, '#9a9aa5'],
  [22, 'Uncategorized', 'לא מסווג', 'tracking', null, '#b0b0b8'],
].map(([id, name, name_he, kind, monthly_budget, color]) => ({ id, name, name_he, kind, monthly_budget, color, archived: 0 }));
const UNCAT = 22;

const accounts = [
  { id: 1, key: 'leumi:demo-checking', provider: 'leumi', kind: 'bank', name: 'Leumi checking' },
  { id: 2, key: 'max:1111', provider: 'max', kind: 'card', name: 'Max Visa ·1111' },
  { id: 3, key: 'max:2222', provider: 'max', kind: 'card', name: 'Leumi Visa ·2222' },
  { id: 4, key: 'cal:9999', provider: 'cal', kind: 'gift', name: 'Work gift card ·9999' },
];
const acct = (key) => accounts.find((a) => a.key === key);

// ---------------- transaction generation ----------------
const txns = [];
let nid = 0;
function add(month, day, amount, description, category_id, account_key, extra = {}) {
  const [y, mm] = month.split('-').map(Number);
  const last = new Date(y, mm, 0).getDate();
  const d = new Date(y, mm - 1, Math.min(day, last));
  if (iso(d) > TODAY) return null;
  const a = acct(account_key);
  const bill = a.kind === 'card' ? iso(new Date(y, mm, 2)) : iso(d);
  const t = {
    id: 'm' + (++nid).toString(36).padStart(5, '0'),
    date: iso(d), billing_date: bill, cashflow_month: month,
    amount: r2(amount), description, merchant: description.replace(/\s+\d+$/, '').trim(),
    category_id, category_source: category_id ? 'rule' : 'default',
    account_key, is_transfer: 0, is_excluded: 0,
    installment_no: null, installment_total: null, original_amount: null, original_currency: null, notes: null,
    ...extra,
  };
  if (a.kind === 'gift') t.is_excluded = 1;
  txns.push(t);
  return t;
}

const GROC = ['שופרסל דיל', 'רמי לוי שיווק השקמה', 'יוחננוף', 'מכולת השכונה', 'ויקטורי'];
const DINE = ['ארומה תל אביב', 'קפה גרג', 'וולט', 'תן ביס', 'מסעדת הים', 'פיצה האט'];
const TRANS = ['פז', 'סונול', 'רב-קו', 'גט טקסי', 'חניון אחוזות החוף'];
const SHOP = ['איקאה ראשון לציון', 'זארה', 'H&M', 'KSP מחשבים', 'מקס סטוק'];
const FUN = ['סינמה סיטי', 'הבימה', 'לונה פארק'];
const HEALTH = ['סופר-פארם', 'קופת חולים השכונה', 'אופטיקה הלפרין'];

const months = [];
for (let i = 23; i >= 0; i--) months.push(addM(CUR, -i));

months.forEach((m, mi) => {
  const card = () => (rnd() < 0.65 ? 'max:1111' : 'max:2222');
  add(m, 10, between(13800, 14400), 'משכורת - חברת הדגמה בע"מ', 1, 'leumi:demo-checking');
  if (mi % 5 === 2) add(m, 15, between(3800, 5200), 'ביטוח לאומי - תגמולי מילואים', 2, 'leumi:demo-checking');
  add(m, 1, -5200, 'שכר דירה - הוראת קבע', 4, 'leumi:demo-checking');
  if (mi % 2 === 0) add(m, 5, -680, 'עיריית תל אביב ארנונה', 5, 'leumi:demo-checking');
  if (mi % 2 === 1) add(m, 12, -between(320, 560), 'חברת החשמל לישראל', 6, 'max:1111');
  if (mi % 2 === 0) add(m, 18, -between(110, 190), 'תאגיד המים העירוני', 6, 'max:1111');
  add(m, 3, -119, 'ספק אינטרנט מהיר', 7, 'max:1111');
  add(m, 3, mi >= 21 ? -69.9 : -59.9, 'סלולר פלוס', 7, 'max:1111');
  add(m, 7, -386, 'ביטוח הבית בע״מ', 8, 'leumi:demo-checking');
  add(m, 14, -54.9, 'נטפליקס', 9, 'max:2222');
  add(m, 14, -19.9, 'ספוטיפיי', 9, 'max:2222');
  if (mi >= 10) add(m, 20, -39.9, 'דיסני פלוס', 9, 'max:2222');
  add(m, 22, -36.8, 'APPLE.COM/BILL', 9, 'max:1111', { original_amount: -9.99, original_currency: 'USD' });
  add(m, 1, -12.9, 'דמי כרטיס', 18, 'max:1111');
  if (mi % 3 === 0) add(m, 28, -14, 'עמלת פעולה בערוץ ישיר', 18, 'leumi:demo-checking');
  add(m, 11, -1500, 'העברה לחסכון', 19, 'leumi:demo-checking');

  const groceryBoost = mi >= 20 ? 1.3 : 1; // category creep in the last months
  for (let k = 0; k < 9; k++) add(m, 1 + Math.floor(rnd() * 28), -between(60, 520) * groceryBoost, pick(GROC), 10, card());
  for (let k = 0; k < 8; k++) add(m, 1 + Math.floor(rnd() * 28), -between(28, 260), pick(DINE), 11, card());
  for (let k = 0; k < 5; k++) add(m, 1 + Math.floor(rnd() * 28), -between(30, 290), pick(TRANS), 12, card());
  for (let k = 0; k < 3; k++) add(m, 1 + Math.floor(rnd() * 28), -between(80, 480), pick(SHOP), 13, card());
  if (rnd() < 0.6) add(m, 1 + Math.floor(rnd() * 28), -between(60, 240), pick(FUN), 14, card());
  if (rnd() < 0.7) add(m, 1 + Math.floor(rnd() * 28), -between(40, 300), pick(HEALTH), 15, card());
  if (rnd() < 0.3) add(m, 1 + Math.floor(rnd() * 28), -between(80, 400), 'ALIEXPRESS', 13, 'max:2222', { original_currency: 'USD', original_amount: -r2(between(20, 100)) });
  if (mi === 8 || mi === 19) add(m, 6, -between(2800, 4200), 'ELAL ISRAEL AIRLINES', 16, 'max:1111', { original_currency: 'USD', original_amount: -r2(between(750, 1100)) });
  if (mi === 15) add(m, 16, -3200, 'מוסך השרון', 12, 'max:1111', { is_excluded: 1, notes: 'One-off: timing belt' });

  // card bill payments on the bank side (transfers)
  if (mi > 0) {
    const prev = months[mi - 1];
    for (const [k, label] of [['max:1111', 'מקס איט פיננסים'], ['max:2222', 'לאומי ויזה']]) {
      const sum = txns.filter((t) => t.cashflow_month === prev && t.account_key === k && t.amount < 0).reduce((s, t) => s + t.amount, 0);
      add(m, 2, sum, label, 20, 'leumi:demo-checking', { is_transfer: 1, category_source: 'rule' });
    }
  }
});
// installments: 12 payments starting 7 months ago
for (let i = 0; i < 12; i++) {
  const m = addM(CUR, -7 + i);
  if (m > CUR) break;
  add(m, 9, -416.58, 'KSP מחשבים - מחשב נייד', 13, 'max:1111', { installment_no: i + 1, installment_total: 12 });
}
// duplicate charge last month
const lastM = addM(CUR, -1);
add(lastM, 17, -86.4, 'וולט', 11, 'max:2222');
add(lastM, 17, -86.4, 'וולט', 11, 'max:2222');
// gift card spending (excluded automatically)
add(addM(CUR, -2), 8, -180, 'שופרסל דיל', 10, 'cal:9999');
add(addM(CUR, -1), 21, -95, 'ארומה תל אביב', 11, 'cal:9999');
add(CUR, 3, -125, 'סטימצקי', 13, 'cal:9999');
// uncategorized, recent
const UNC = [['PAYPAL *STEAMGAMES', -42.5], ['העברה בביט', -150], ['חנות הפרחים של רותי', -89], ['מאפיית לחם ארז', -46], ['GOOGLE *YouTube', -29.9], ['קיוסק הדר', -18], ['העברה בביט', -200], ['מרכז הספורט העירוני', -260]];
UNC.forEach(([d, a], i) => {
  const m = i < 5 ? CUR : lastM;
  add(m, 1 + ((i * 3) % Math.max(1, NOW.getDate())), a, d, null, i % 2 ? 'max:2222' : 'max:1111', { category_source: 'default' });
});
txns.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));

// ---------------- other entities ----------------
let rules = [
  { id: 1, pattern: 'שופרסל', match_type: 'contains', account_kind: null, amount_min: null, amount_max: null, category_id: 10, set_transfer: null, set_excluded: null, priority: 10, hits: 0, enabled: 1, system: 0, created_at: '2026-06-01' },
  { id: 2, pattern: 'מקס איט פיננ', match_type: 'contains', account_kind: 'bank', amount_min: null, amount_max: null, category_id: 20, set_transfer: 1, set_excluded: null, priority: 100, hits: 0, enabled: 1, system: 1, created_at: '2026-06-01' },
  { id: 3, pattern: 'לאומי ויזה', match_type: 'contains', account_kind: 'bank', amount_min: null, amount_max: null, category_id: 20, set_transfer: 1, set_excluded: null, priority: 100, hits: 0, enabled: 1, system: 1, created_at: '2026-06-01' },
  { id: 4, pattern: 'וולט|תן ביס', match_type: 'regex', account_kind: null, amount_min: null, amount_max: null, category_id: 11, set_transfer: null, set_excluded: null, priority: 20, hits: 0, enabled: 1, system: 0, created_at: '2026-07-14' },
  { id: 5, pattern: 'העברה לחסכון', match_type: 'equals', account_kind: 'bank', amount_min: null, amount_max: null, category_id: 19, set_transfer: null, set_excluded: null, priority: 50, hits: 0, enabled: 0, system: 0, created_at: '2026-06-01' },
];
let goals = [
  { id: 1, name: 'Trip to Japan', target_amount: 16000, target_date: `${NOW.getFullYear() + 1}-09-30`, saved_amount: 6200, monthly_contribution: null, priority: 1, status: 'active', notes: 'Flights + 2 weeks', created_at: '2026-03-01' },
  { id: 2, name: 'Emergency fund top-up', target_amount: 30000, target_date: `${NOW.getFullYear() + 1}-06-30`, saved_amount: 21500, monthly_contribution: null, priority: 2, status: 'active', notes: null, created_at: '2025-11-01' },
  { id: 3, name: 'New bike', target_amount: 4500, target_date: addM(CUR, 2) + '-15', saved_amount: 1200, monthly_contribution: null, priority: 3, status: 'active', notes: null, created_at: '2026-08-01' },
];
const plusDays = (n) => iso(new Date(NOW.getFullYear(), NOW.getMonth(), NOW.getDate() + n));
let freebies = [
  { id: 1, name: 'Work holiday gift card (Cal)', source: 'gift', value: 1000, unit: 'ILS', received_date: iso(new Date(NOW.getFullYear(), NOW.getMonth() - 2, 1)), expiry_date: plusDays(55), used_amount: 0, status: 'active', where_to_use: 'Most supermarkets and cafés', notes: null, account_id: 4, created_at: '2026-09-01' },
  { id: 2, name: 'Reserve duty vacation voucher', source: 'reserve', value: 1500, unit: 'ILS', received_date: '2026-05-10', expiry_date: plusDays(9), used_amount: 400, status: 'active', where_to_use: 'Participating hotels', notes: 'Book before it expires', account_id: null, created_at: '2026-05-10' },
  { id: 3, name: 'Gym membership via work', source: 'work', value: 12, unit: 'months', received_date: '2026-01-01', expiry_date: plusDays(25), used_amount: 10, status: 'active', where_to_use: 'Holmes Place', notes: null, account_id: null, created_at: '2026-01-01' },
  { id: 4, name: 'Cinema tickets', source: 'work', value: 4, unit: 'uses', received_date: '2026-07-01', expiry_date: plusDays(120), used_amount: 1, status: 'active', where_to_use: 'Cinema City', notes: null, account_id: null, created_at: '2026-07-01' },
  { id: 5, name: 'Bookstore gift voucher', source: 'gift', value: 200, unit: 'ILS', received_date: '2026-02-01', expiry_date: null, used_amount: 0, status: 'active', where_to_use: 'Steimatzky', notes: 'Birthday gift', account_id: null, created_at: '2026-02-01' },
  { id: 6, name: 'Spa day (reserve duty)', source: 'reserve', value: 1, unit: 'uses', received_date: '2025-12-01', expiry_date: plusDays(-20), used_amount: 0, status: 'expired', where_to_use: null, notes: null, account_id: null, created_at: '2025-12-01' },
];
let tasks = [
  { id: 1, title: 'Call the mobile provider to lower the plan', notes: 'Price went up 17%', due_date: plusDays(5), status: 'open', priority: 'high', est_monthly_saving: 20, actual_monthly_saving: null, merchant: 'סלולר פלוס', category_id: 7, insight_key: null, created_at: '2026-10-20', done_at: null },
  { id: 2, title: 'Pick one streaming service to cancel', notes: null, due_date: null, status: 'open', priority: 'med', est_monthly_saving: 40, actual_monthly_saving: null, merchant: null, category_id: 9, insight_key: null, created_at: '2026-10-22', done_at: null },
  { id: 3, title: 'Compare car insurance quotes', notes: 'Renewal in March', due_date: plusDays(40), status: 'open', priority: 'low', est_monthly_saving: 60, actual_monthly_saving: null, merchant: null, category_id: 8, insight_key: null, created_at: '2026-10-01', done_at: null },
  { id: 4, title: 'Ask bank to waive the direct-channel fee', notes: null, due_date: null, status: 'done', priority: 'med', est_monthly_saving: 5, actual_monthly_saving: 5, merchant: null, category_id: 18, insight_key: null, created_at: '2026-08-01', done_at: '2026-08-12' },
];
// ---------------- net worth (invented) ----------------
const ago = (m) => addM(CUR, -m);
let assets = [
  { id: 1, key: 'checking', name: 'Leumi checking (עובר ושב)', kind: 'cash', liquid: 1, include_in_net_worth: 1, track_from_bank: 1, source: 'bank', notes: 'Live balance comes from the bank import' },
  { id: 2, key: 'broker', name: 'Brokerage account (תיק השקעות)', kind: 'investment', liquid: 1, include_in_net_worth: 1, track_from_bank: 0, source: 'sheet', notes: null },
  { id: 3, key: 'pension', name: 'Pension fund (קרן פנסיה)', kind: 'pension', liquid: 0, include_in_net_worth: 1, track_from_bank: 0, source: 'sheet', notes: null },
  { id: 4, key: 'study', name: 'Study fund (קרן השתלמות)', kind: 'study_fund', liquid: 0, include_in_net_worth: 1, track_from_bank: 0, source: 'sheet', notes: null },
  { id: 5, key: 'flat_equity', name: 'Flat — my equity', kind: 'property_equity', liquid: 0, include_in_net_worth: 1, track_from_bank: 0, source: 'sheet', notes: 'My share of the down payment and costs' },
];
let assetValues = [
  [2, ago(4) + '-10', 142000, [{ name: 'Index funds (מחקות מדד)', value: 98000 }, { name: 'Money market (כספית)', value: 30000 }, { name: 'Single stocks', value: 14000 }]],
  [2, ago(1) + '-12', 151500, [{ name: 'Index funds (מחקות מדד)', value: 105000 }, { name: 'Money market (כספית)', value: 31000 }, { name: 'Single stocks', value: 15500 }]],
  [3, ago(4) + '-10', 96000], [4, ago(4) + '-10', 41000], [5, ago(4) + '-10', 210000],
].map(([asset_id, as_of, value, breakdown]) => ({ asset_id, as_of, value, breakdown: breakdown || null }));
let liabilities = [
  { id: 1, key: 'mortgage', name: 'Mortgage — demo bank', kind: 'mortgage', principal: 900000, rate_pct: null, start_month: ago(4), monthly_payment: 4800, include_in_net_worth: 0, notes: null, balances: [{ as_of: ago(4) + '-10', balance: 900000 }] },
];
let planned = [
  { id: 1, kind: 'income', when_month: addM(CUR, 1), amount: 5800, what: 'Course refund (החזר קורס)', notes: null, done: 0 },
  { id: 2, kind: 'expense', when_month: addM(CUR, 2), amount: 2400, what: 'Annual rental tax', notes: '10% track', done: 0 },
  { id: 3, kind: 'expense', when_month: null, amount: 12000, what: 'New sofa', notes: 'Some time next year', done: 0 },
];
const equityComp = [
  { id: 1, key: 'options:' + addM(CUR, 3), vests: addM(CUR, 3), qty: 1500, type: 'options', notes: 'Promotion grant', value: null },
  { id: 2, key: 'shares:' + addM(CUR, 20), vests: addM(CUR, 20), qty: 800, type: 'shares', notes: 'Joining grant', value: null },
];
const propertyData = {
  name: 'Demo St 1, Tel Aviv', price: 1500000, bank_financed_pct: 60,
  purchase_costs: [{ item: 'Purchase tax (מס רכישה)', amount: 8000, note: 'split half-half' }, { item: 'Lawyer', amount: 6000, note: '' }, { item: 'Renovation', amount: 45000, note: 'kitchen + paint' }],
  total_costs: 410000, contributions: { me: 210000, family: 200000 },
  rental_income: [{ month: ago(3), amount: 6000, note: 'first tenant from mid-month' }, { month: ago(2), amount: 7500 }, { month: ago(1), amount: 7500 }, { month: CUR, amount: 7500 }, { month: addM(CUR, 1), amount: 7500 }],
  rental_tax_note: '10% tax on rental income',
};
const BANK_BAL = 23458.12;
const daysAgo = (d) => Math.round((NOW - new Date(d + 'T00:00:00')) / 86400000);
function assetJson(a) {
  let v = assetValues.filter((x) => x.asset_id === a.id).sort((x, y) => (x.as_of < y.as_of ? 1 : -1))[0];
  if (a.track_from_bank) v = { value: BANK_BAL, as_of: TODAY, breakdown: null, source: 'bank' };
  const stale = v ? daysAgo(v.as_of) : null;
  return { ...a, liquid: !!a.liquid, include_in_net_worth: !!a.include_in_net_worth, track_from_bank: !!a.track_from_bank,
    value: v?.value ?? null, as_of: v?.as_of ?? null, breakdown: v?.breakdown ?? null, value_source: v ? (v.source || 'sheet') : null,
    stale_days: stale, stale: stale != null && stale > 45 };
}
function liabJson(l) {
  const b = [...l.balances].sort((x, y) => (x.as_of < y.as_of ? 1 : -1))[0];
  const { balances, ...rest } = l;
  return { ...rest, include_in_net_worth: !!l.include_in_net_worth, balance: b?.balance ?? null, as_of: b?.as_of ?? null, stale_days: b ? daysAgo(b.as_of) : null };
}
function plannedJson(p) { return { ...p, done: !!p.done, signed_amount: p.kind === 'income' ? p.amount : -p.amount, overdue: !p.done && !!p.when_month && p.when_month < CUR }; }
function networthJson() {
  const as = assets.map(assetJson), ls = liabilities.map(liabJson);
  const inc = as.filter((a) => a.include_in_net_worth && a.value != null);
  const liab = ls.filter((l) => l.include_in_net_worth).reduce((s, l) => s + (l.balance || 0), 0);
  const byKind = {};
  inc.forEach((a) => { byKind[a.kind] = (byKind[a.kind] || 0) + a.value; });
  const kinds = Object.entries(byKind).map(([kind, total]) => ({ kind, total: r2(total) })).sort((a, b) => b.total - a.total);
  if (liab) kinds.push({ kind: 'liabilities', total: -liab });
  const total = r2(inc.reduce((s, a) => s + a.value, 0) - liab), liquid = r2(inc.filter((a) => a.liquid).reduce((s, a) => s + a.value, 0));
  const history = [4, 3, 2, 1, 0].map((k, i) => ({ month: ago(k), total: r2(total - (4 - i) * 3100 + (i === 2 ? 2500 : 0)), liquid: r2(liquid - (4 - i) * 2600) }));
  const mort = liabJson(liabilities[0]);
  const received = { [ago(3)]: 6000, [ago(2)]: 7500 };
  const rental = propertyData.rental_income.map((r) => ({ month: r.month, expected: r.amount, received: received[r.month] ?? null, note: r.note || null, future: r.month > CUR }));
  return {
    total, liquid, liabilities_total: liab, as_of: TODAY, by_kind: kinds, assets: as,
    liabilities: ls, history, stale_after_days: 45,
    property: { id: 1, key: 'flat', ...propertyData, contributions_total: 410000, mortgage: mort, equity_estimate: propertyData.price - mort.balance,
      rental_income: rental, rent_expected_monthly: 7500, rent_received_12m: 13500, rent_yield_pct: 6.0 },
    planned: planned.map(plannedJson), equity_comp: equityComp.map((e) => ({ ...e, months_to_vest: e.vests === addM(CUR, 3) ? 3 : 20, vested: false })),
    last_sheet_import: { at: plusDays(-6) + 'T10:00:00', file: 'snapshot_demo.json', sheet_last_updated: ago(1) + '-12' },
  };
}
const dismissed = new Set();
let settings = { cashflow_start_day: '1', buffer_months: '1', overspend_pct: '120', sync_providers: 'leumi,max', schema_version: '3' };
let lastImport = { file_name: 'max_transactions_demo.xlsx', provider: 'max', rows: 412, new_rows: 23, dup_rows: 389, imported_at: plusDays(-3) + ' 21:14:00' };
let syncState = { running: false, providers: [{ name: 'leumi', ok: true, error: null, new_rows: 4, at: plusDays(-1) + ' 08:00:00' }, { name: 'max', ok: false, error: 'Login failed: one-time code required', new_rows: 0, at: plusDays(-1) + ' 08:01:00' }] };
let nextId = 100;

// ---------------- derived ----------------
const cat = (id) => categories.find((c) => c.id === id);
function txnJson(t) {
  const c = cat(t.category_id);
  const a = acct(t.account_key);
  const out = {
    id: t.id, date: t.date, billing_date: t.billing_date, cashflow_month: t.cashflow_month, amount: t.amount,
    description: t.description, merchant: t.merchant, category_id: t.category_id || UNCAT, category_name: c ? c.name : 'Uncategorized',
    category_kind: (c || cat(UNCAT)).kind, provider_category: null,
    category_source: t.category_source, account: { key: a.key, provider: a.provider, kind: a.kind, name: a.name },
    is_transfer: t.is_transfer, is_excluded: t.is_excluded, installment_no: t.installment_no, installment_total: t.installment_total,
    original_amount: t.original_amount, original_currency: t.original_currency, notes: t.notes,
  };
  if (!t.category_id) { // extension: suggestion for review mode
    const sim = txns.find((o) => o !== t && o.category_id && o.merchant.split(' ')[0] === t.merchant.split(' ')[0]);
    out.suggested_category_id = sim ? sim.category_id : (/ביט|העברה/.test(t.description) ? 17 : null);
  }
  return out;
}
const counted = (t) => !t.is_transfer && !t.is_excluded && (cat(t.category_id)?.kind !== 'savings');
const isSavings = (t) => cat(t.category_id)?.kind === 'savings';
const EXP_KINDS = new Set(['fixed', 'variable', 'tracking']);

function catSpend(m, cid) {
  return -txns.filter((t) => t.cashflow_month === m && (t.category_id || UNCAT) === cid && counted(t) && t.amount < 0).reduce((s, t) => s + t.amount, 0);
}
function monthTotals(m) {
  const rows = txns.filter((t) => t.cashflow_month === m);
  const income = rows.filter((t) => counted(t) && t.amount > 0).reduce((s, t) => s + t.amount, 0);
  const expense = -rows.filter((t) => counted(t) && t.amount < 0).reduce((s, t) => s + t.amount, 0);
  const savings = -rows.filter((t) => isSavings(t) && !t.is_transfer).reduce((s, t) => s + t.amount, 0);
  return { income: r2(income), expense: r2(expense), net: r2(income - expense), savings: r2(savings) };
}
function budgetFor(c, m) {
  const median3 = median([1, 2, 3].map((k) => catSpend(addM(m, -k), c.id)));
  return { budget: c.monthly_budget ?? (median3 || null), median3: r2(median3) };
}
function overspend(m) {
  const tot = monthTotals(m);
  const reasons = [];
  if (tot.expense > tot.income) reasons.push(`Spent ₪${Math.round(tot.expense - tot.income).toLocaleString('en-US')} more than income`);
  return reasons;
}
function attention(m) {
  const reasons = [];
  const lim = +settings.overspend_pct / 100;
  for (const c of categories.filter((x) => x.kind === 'variable' && x.monthly_budget)) {
    if (c.id === UNCAT) continue;
    const { budget } = budgetFor(c, m);
    const a = catSpend(m, c.id);
    if (budget && a > budget * lim && a > 300) reasons.push(`${c.name} at ${Math.round((a / budget) * 100)}% of budget`);
  }
  return reasons;
}
function goalJson(g) {
  const [ty, tm] = g.target_date.split('-').map(Number);
  const months_left = Math.max(1, (ty - NOW.getFullYear()) * 12 + (tm - 1 - NOW.getMonth()));
  const need = Math.max(0, g.target_amount - g.saved_amount);
  const monthly_need = g.status === 'active' ? Math.ceil(need / months_left) : 0;
  const created = new Date(g.created_at);
  const total = Math.max(1, (ty - created.getFullYear()) * 12 + (tm - 1 - created.getMonth()));
  const elapsed = Math.max(0, total - months_left);
  const expected = (g.target_amount * elapsed) / total;
  return { ...g, monthly_need, months_left, progress_pct: r2((g.saved_amount / g.target_amount) * 100), on_track: g.saved_amount >= expected * 0.9 };
}
function freebieJson(f) {
  const days_left = f.expiry_date ? Math.round((new Date(f.expiry_date) - new Date(TODAY)) / 86400000) : null;
  let used = f.used_amount || 0, linked_used = 0;
  if (f.account_id) {
    const a = accounts.find((x) => x.id === f.account_id);
    linked_used = -txns.filter((t) => t.account_key === a?.key && t.amount < 0 && t.date >= f.received_date)        .reduce((s, t) => s + t.amount, 0);
    used += linked_used;
  }
  const status = f.status === 'active' && days_left != null && days_left < 0 ? 'expired' : f.status;
  return { ...f, status, used_amount: r2(used), linked_used: r2(linked_used), remaining: r2(Math.max(0, f.value - used)), days_left };
}
const freebiesSorted = () => freebies.map(freebieJson).sort((a, b) => (a.expiry_date || '9999') < (b.expiry_date || '9999') ? -1 : 1);

function monthSummary(m) {
  const tot = monthTotals(m);
  const cats = categories.filter((c) => EXP_KINDS.has(c.kind)).map((c) => {
    const actual = r2(catSpend(m, c.id));
    const { budget, median3 } = budgetFor(c, m);
    const count = txns.filter((t) => t.cashflow_month === m && (t.category_id || UNCAT) === c.id && counted(t) && t.amount < 0).length;
    const pctv = budget ? r2((actual / budget) * 100) : null;
    return { id: c.id, name: c.name, name_he: c.name_he, kind: c.kind, color: c.color, actual, budget: budget ? r2(budget) : null, median3, pct: pctv, over: !!(budget && actual > budget * (c.kind === 'fixed' ? 1.05 : 1)), count };
  }).filter((c) => c.actual > 0 || c.budget);
  const goalsReserved = goals.filter((g) => g.status === 'active').map(goalJson).reduce((s, g) => s + g.monthly_need, 0);
  const medIncome = median([1, 2, 3].map((k) => monthTotals(addM(m, -k)).income));
  const expIncome = m === CUR ? Math.max(tot.income, medIncome) : tot.income;
  const fixedRemaining = m === CUR ? cats.filter((c) => c.kind === 'fixed').reduce((s, c) => s + Math.max(0, (c.budget || 0) - c.actual), 0) : 0;
  const rows = txns.filter((t) => t.cashflow_month === m && counted(t) && t.amount < 0);
  const byM = new Map();
  rows.forEach((t) => { const x = byM.get(t.merchant) || { merchant: t.merchant, amount: 0, count: 0 }; x.amount += -t.amount; x.count++; byM.set(t.merchant, x); });
  const top_merchants = [...byM.values()].sort((a, b) => b.amount - a.amount).slice(0, 8).map((x) => ({ ...x, amount: r2(x.amount) }));
  const anomalies = [];
  for (const t of rows) {
    const c = cat(t.category_id);
    if (!c || c.kind !== 'variable') continue;
    const hist = txns.filter((o) => o.category_id === c.id && o.amount < 0 && o.cashflow_month < m).map((o) => -o.amount);
    const med = median(hist);
    if (med && -t.amount > 3 * med && -t.amount > 400) anomalies.push({ type: 'large_txn', text: `${t.merchant}: ₪${Math.round(-t.amount).toLocaleString('en-US')} is ${Math.round(-t.amount / med)}× your usual ${c.name.toLowerCase()} purchase`, txn_ids: [t.id] });
  }
  const seen = new Map();
  for (const t of rows) {
    const k = `${t.merchant}|${t.amount}|${t.date}|${t.account_key}`;
    if (seen.has(k)) anomalies.push({ type: 'duplicate_charge', text: `Possible double charge: ${t.merchant} ₪${Math.abs(t.amount)} twice on ${t.date}`, txn_ids: [seen.get(k), t.id] });
    else seen.set(k, t.id);
  }
  cats.filter((c) => c.over && c.kind !== 'fixed').forEach((c) => anomalies.push({ type: 'over_budget', text: `${c.name} is ${Math.round(c.pct)}% of budget`, txn_ids: rows.filter((t) => t.category_id === c.id).map((t) => t.id) }));
  const prev = addM(m, -1);
  const fixedMerchants = new Map();
  txns.filter((t) => t.cashflow_month === prev && cat(t.category_id)?.kind === 'fixed' && t.amount < 0).forEach((t) => fixedMerchants.set(t.merchant, -t.amount));
  const fixed = [...fixedMerchants.entries()].map(([name, expected]) => {
    const got = txns.filter((t) => t.cashflow_month === m && t.merchant === name && t.amount < 0);
    return { name, expected: r2(expected), actual: got.length ? r2(-got.reduce((s, t) => s + t.amount, 0)) : null };
  });
  const oo = txns.filter((t) => t.cashflow_month === m && t.is_excluded && !t.is_transfer && acct(t.account_key).kind !== 'gift');
  const one_offs = { income: r2(oo.filter((t) => t.amount > 0).reduce((s, t) => s + t.amount, 0)), expense: r2(-oo.filter((t) => t.amount < 0).reduce((s, t) => s + t.amount, 0)), count: oo.length };
  return {
    month: m, ...tot, one_offs, left_to_spend: r2(expIncome - tot.expense - goalsReserved - tot.savings - fixedRemaining),
    goals_reserved: goalsReserved, categories: cats.sort((a, b) => b.actual - a.actual), top_merchants, anomalies: anomalies.slice(0, 6), fixed,
  };
}
function insights() {
  const items = [
    { key: 'subscription:streaming', type: 'subscription', severity: 'med', title: 'Three streaming subscriptions overlap', detail: 'נטפליקס, דיסני פלוס and APPLE.COM/BILL charge every month (₪131/mo together). Keep one or two.', est_monthly_saving: 40, txn_ids: txns.filter((t) => /נטפליקס|דיסני|APPLE/.test(t.description)).slice(0, 9).map((t) => t.id), merchant: null, category_id: 9, suggested_task: 'Pick one streaming service to cancel' },
    { key: 'price_increase:mobile', type: 'price_increase', severity: 'high', title: 'סלולר פלוס went up 17%', detail: 'From ₪59.90 to ₪69.90 per month three months ago, with no plan change.', est_monthly_saving: 10, txn_ids: txns.filter((t) => t.merchant === 'סלולר פלוס').slice(0, 6).map((t) => t.id), merchant: 'סלולר פלוס', category_id: 7, suggested_task: 'Call the mobile provider and ask to return to the old price' },
    { key: 'duplicate_charge:wolt', type: 'duplicate_charge', severity: 'high', title: 'Possible double charge at וולט', detail: 'Two identical ₪86.40 charges on the same day on card ·2222.', est_monthly_saving: 7.2, txn_ids: txns.filter((t) => t.merchant === 'וולט' && t.amount === -86.4).map((t) => t.id), merchant: 'וולט', category_id: 11, suggested_task: 'Ask Max to reverse the duplicate וולט charge' },
    { key: 'card_fee:max1111', type: 'card_fee', severity: 'med', title: 'Card fees: ₪155 a year', detail: 'דמי כרטיס ₪12.90 every month on Max ·1111. Most banks waive it if you ask.', est_monthly_saving: 12.9, txn_ids: txns.filter((t) => t.merchant === 'דמי כרטיס').slice(0, 12).map((t) => t.id), merchant: 'דמי כרטיס', category_id: 18, suggested_task: 'Ask Max to waive the monthly card fee' },
    { key: 'category_creep:groceries', type: 'category_creep', severity: 'med', title: 'Groceries up 31% in the last 3 months', detail: 'Average ₪3,050/mo vs ₪2,330/mo in the prior 9 months.', est_monthly_saving: 300, txn_ids: [], merchant: null, category_id: 10, suggested_task: 'Plan weekly shop, one big basket at רמי לוי' },
    { key: 'top_discretionary:dining', type: 'top_discretionary', severity: 'low', title: 'Dining out: a 15% cut saves ₪1,650/yr', detail: 'Your second-largest variable category, ₪915/mo on average.', est_monthly_saving: 137, txn_ids: [], merchant: null, category_id: 11, suggested_task: 'Cap delivery orders at 4 a month' },
    { key: 'fx_heavy:2222', type: 'fx_heavy', severity: 'low', title: 'Foreign-currency charges cost ~₪120/yr in fees', detail: '31 USD charges in 12 months. A card without FX fees would avoid the ~2.5% markup.', est_monthly_saving: 10, txn_ids: txns.filter((t) => t.original_currency === 'USD').slice(0, 20).map((t) => t.id), merchant: null, category_id: null, suggested_task: 'Look into a no-FX-fee card for online purchases' },
    { key: 'insurance_review:home', type: 'insurance_review', severity: 'low', title: 'Insurance unchanged for 24 months', detail: 'ביטוח הבית בע״מ ₪386/mo. A comparison often finds 10–20% lower.', est_monthly_saving: 50, txn_ids: txns.filter((t) => t.merchant === 'ביטוח הבית בע״מ').slice(0, 6).map((t) => t.id), merchant: 'ביטוח הבית בע״מ', category_id: 8, suggested_task: 'Get two competing insurance quotes' },
  ];
  return items.filter((i) => !dismissed.has(i.key)).map((i) => ({ ...i, est_yearly_saving: r2(i.est_monthly_saving * 12) }));
}

// ---------------- router ----------------
function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}
const notFound = () => json({ detail: 'Not found (mock)' }, 404);
function crud(list, setList, id, method, body, view = (x) => x) {
  if (method === 'GET' && id == null) return json(list().map(view));
  if (method === 'POST' && id == null) { const o = { id: ++nextId, created_at: TODAY, ...body }; setList([...list(), o]); return json(view(o)); }
  const o = list().find((x) => x.id === id);
  if (!o) return notFound();
  if (method === 'GET') return json(view(o));
  if (method === 'PATCH') { Object.assign(o, body); return json(view(o)); }
  if (method === 'DELETE') { setList(list().filter((x) => x !== o)); return json({ ok: true }); }
  return notFound();
}
function ruleMatches(r, t) {
  const d = t.description || '';
  if (r.account_kind && acct(t.account_key).kind !== r.account_kind) return false;
  if (r.amount_min != null && Math.abs(t.amount) < r.amount_min) return false;
  if (r.amount_max != null && Math.abs(t.amount) > r.amount_max) return false;
  if (!r.pattern) return false;
  if (r.match_type === 'equals') return t.merchant === r.pattern || d === r.pattern;
  if (r.match_type === 'regex') { try { return new RegExp(r.pattern, 'i').test(d); } catch { return false; } }
  return d.toLowerCase().includes(r.pattern.toLowerCase());
}

async function handle(method, url, body) {
  const u = new URL(url, location.origin);
  const p = u.pathname.replace(/^\/api/, '');
  const q = Object.fromEntries(u.searchParams);
  const seg = p.split('/').filter(Boolean);
  const id = seg[1] != null && /^\d+$/.test(seg[1]) ? +seg[1] : seg[1];

  switch (seg[0]) {
    case 'overview': {
      const ms = months.map((m) => { const reasons = overspend(m); return { month: m, ...monthTotals(m), overspend: reasons.length > 0, reasons, attention: attention(m) }; });
      return json({
        current_month: CUR, months: ms, balance: { amount: 23458.12, as_of: TODAY },
        net_worth: (() => { const n = networthJson(); return { total: n.total, liquid: n.liquid, as_of: n.as_of }; })(),
        freebies_expiring: freebiesSorted().filter((f) => f.status === 'active' && f.days_left != null && f.days_left <= 30),
        uncategorized_count: txns.filter((t) => !t.category_id && !t.is_transfer).length,
        last_import: lastImport, last_sync: syncState.providers.reduce((a, b) => (a.at > b.at ? a : b)),
      });
    }
    case 'month': return json(monthSummary(seg[1] || CUR));
    case 'networth': {
      if (seg[1] === 'import-sheet') return json({ file: 'snapshot_demo.json', sheet_last_updated: ago(1) + '-12', assets_added: 0, assets_updated: 5, values_written: 0, liabilities_added: 0, planned_added: 0, planned_updated: 0, tasks_added: 0, tasks_skipped: 3, freebies_added: 0, freebies_skipped: 2, equity_comp_added: 0, property: 'unchanged' });
      return json(networthJson());
    }
    case 'assets': {
      if (seg[2] === 'value' && method === 'POST') {
        assetValues = assetValues.filter((v) => !(v.asset_id === id && v.as_of === (body.as_of || TODAY)));
        assetValues.push({ asset_id: id, as_of: body.as_of || TODAY, value: +body.value, breakdown: body.breakdown || null, source: 'manual' });
        const a = assets.find((x) => x.id === id);
        return a ? json(assetJson(a)) : notFound();
      }
      if (method === 'POST') {
        const a = { id: ++nextId, key: 'a' + nextId, name: body.name, kind: body.kind || 'other', liquid: body.liquid ? 1 : 0, include_in_net_worth: body.include_in_net_worth === false ? 0 : 1, track_from_bank: 0, source: 'manual', notes: body.notes || null };
        assets.push(a);
        if (body.value != null) assetValues.push({ asset_id: a.id, as_of: body.as_of || TODAY, value: +body.value, breakdown: null, source: 'manual' });
        return json(assetJson(a));
      }
      return crud(() => assets, (l) => { assets = l; }, id, method, body, assetJson);
    }
    case 'liabilities': {
      const l = liabilities.find((x) => x.id === id);
      if (seg[2] === 'value' && method === 'POST') { if (!l) return notFound(); l.balances.push({ as_of: body.as_of || TODAY, balance: +(body.balance ?? body.value) }); return json(liabJson(l)); }
      if (method === 'POST') { const n = { id: ++nextId, key: 'l' + nextId, balances: [], include_in_net_worth: 1, ...body }; if (body.principal != null) n.balances.push({ as_of: TODAY, balance: +body.principal }); liabilities.push(n); return json(liabJson(n)); }
      return crud(() => liabilities, (x) => { liabilities = x; }, id, method, body, liabJson);
    }
    case 'planned': {
      if (body && body.amount != null) body.amount = Math.abs(+body.amount);
      if (method === 'POST') body = { kind: 'expense', done: 0, notes: null, when_month: null, ...body };
      return crud(() => planned, (l) => { planned = l; }, id, method, body, plannedJson);
    }
    case 'transactions': {
      if (method === 'GET') {
        let rows = txns;
        if (q.ids) { const s = new Set(q.ids.split(',')); rows = rows.filter((t) => s.has(t.id)); }
        if (q.month) rows = rows.filter((t) => t.cashflow_month === q.month);
        if (q.category_id) rows = rows.filter((t) => (t.category_id || UNCAT) === +q.category_id);
        if (q.account) rows = rows.filter((t) => t.account_key === q.account);
        if (q.uncategorized && q.uncategorized !== '0') rows = rows.filter((t) => !t.category_id);
        if (!q.include_transfers || q.include_transfers === '0') rows = rows.filter((t) => !t.is_transfer);
        if (q.q) { const s = q.q.toLowerCase(); rows = rows.filter((t) => t.description.toLowerCase().includes(s) || (t.notes || '').toLowerCase().includes(s)); }
        const off = +q.offset || 0, lim = +q.limit || 100;
        return json({ items: rows.slice(off, off + lim).map(txnJson), total: rows.length });
      }
      if (method === 'POST' && seg[1] === 'bulk') {
        const set = new Set(body.ids || []);
        let n = 0;
        txns.forEach((t) => {
          if (!set.has(t.id)) return;
          if (body.category_id !== undefined) { t.category_id = body.category_id; t.category_source = 'manual'; }
          if (body.is_excluded !== undefined) t.is_excluded = body.is_excluded ? 1 : 0;
          if (body.is_transfer !== undefined) t.is_transfer = body.is_transfer ? 1 : 0;
          n++;
        });
        return json({ updated_count: n });
      }
      if (method === 'PATCH') {
        const t = txns.find((x) => x.id === seg[1]);
        if (!t) return notFound();
        let rule = null, updated = 1;
        if (body.category_id !== undefined) { t.category_id = body.category_id === UNCAT ? null : body.category_id; t.category_source = body.category_id == null ? 'default' : 'manual'; }
        for (const k of ['is_excluded', 'is_transfer']) if (body[k] !== undefined) t[k] = body[k] ? 1 : 0;
        if (body.notes !== undefined) t.notes = body.notes;
        if (body.remember && body.category_id) {
          rule = { id: ++nextId, pattern: t.merchant, match_type: 'contains', account_kind: null, amount_min: null, amount_max: null, category_id: body.category_id, set_transfer: null, set_excluded: null, priority: 30, hits: 0, created_at: TODAY };
          rules.push(rule);
          if (body.apply_to_history) {
            txns.forEach((o) => { if (o !== t && o.category_source !== 'manual' && ruleMatches(rule, o)) { o.category_id = body.category_id; o.category_source = 'rule'; updated++; } });
          }
        }
        return json({ txn: txnJson(t), rule, updated_count: updated });
      }
      return notFound();
    }
    case 'categories': return crud(() => categories, (l) => { categories.length = 0; categories.push(...l); }, id, method, body);
    case 'rules': {
      if (seg[1] === 'preview') {
        const m = txns.filter((t) => ruleMatches(body, t));
        return json({ count: m.length, sample: m.slice(0, 5).map(txnJson) });
      }
      return crud(() => rules, (l) => { rules = l; }, id, method, body, (r) => ({ ...r, hits: txns.filter((t) => ruleMatches(r, t)).length }));
    }
    case 'goals': return crud(() => goals, (l) => { goals = l; }, id, method, body, goalJson);
    case 'freebies': {
      if (method === 'GET' && id == null) return json(freebiesSorted());
      return crud(() => freebies, (l) => { freebies = l; }, id, method, body, freebieJson);
    }
    case 'accounts': {
      const view = (a) => { const r = txns.filter((t) => t.account_key === a.key); return { ...a, txn_count: r.length, first_date: r.at(-1)?.date ?? null, last_date: r[0]?.date ?? null }; };
      return crud(() => accounts, () => {}, id, method, body, view);
    }
    case 'tasks': {
      if (method === 'GET' && id == null) {
        const list = q.status ? tasks.filter((t) => t.status === q.status) : tasks;
        return json({
          items: list,
          total_est_monthly_saving: tasks.filter((t) => t.status === 'open').reduce((s, t) => s + (t.est_monthly_saving || 0), 0),
          total_actual_monthly_saving: tasks.filter((t) => t.status === 'done').reduce((s, t) => s + (t.actual_monthly_saving || 0), 0),
        });
      }
      if (method === 'PATCH' && body.status === 'done') body.done_at = TODAY;
      if (method === 'POST') body = { status: 'open', priority: 'med', ...body };
      return crud(() => tasks, (l) => { tasks = l; }, id, method, body);
    }
    case 'insights': {
      if (method === 'GET') return json({ items: insights() });
      const key = decodeURIComponent(seg[1]);
      if (seg[2] === 'dismiss') { dismissed.add(key); return json({ ok: true }); }
      if (seg[2] === 'task') {
        const i = insights().find((x) => x.key === key);
        if (!i) return notFound();
        const t = { id: ++nextId, title: i.suggested_task || i.title, notes: i.detail, due_date: null, status: 'open', priority: i.severity, est_monthly_saving: i.est_monthly_saving, actual_monthly_saving: null, merchant: i.merchant, category_id: i.category_id, insight_key: key, created_at: TODAY, done_at: null };
        tasks.push(t);
        return json(t);
      }
      return notFound();
    }
    case 'trends': {
      const n = +q.months || 12;
      const ms = months.slice(-n);
      return json({ months: ms, categories: categories.filter((c) => EXP_KINDS.has(c.kind) && c.id !== UNCAT).map((c) => ({ id: c.id, name: c.name, name_he: c.name_he, kind: c.kind, color: c.color, series: ms.map((m) => r2(catSpend(m, c.id))) })) });
    }
    case 'forecast': {
      const n = +q.months || 6;
      const hist = months.slice(-7, -1).map(monthTotals);
      const inc = median(hist.map((x) => x.income)), exp = median(hist.map((x) => x.expense)), sav = median(hist.map((x) => x.savings));
      let bal = 23458.12;
      const out = [];
      const extra = networthJson().liquid - BANK_BAL;
      for (let i = 1; i <= n; i++) {
        const m = addM(CUR, i);
        const pl = planned.filter((p) => !p.done && p.when_month === m).reduce((s, p) => s + (p.kind === 'income' ? p.amount : -p.amount), 0);
        bal += inc - exp - sav + pl;
        out.push({ month: m, income: r2(inc), expense: r2(exp), planned: r2(pl), net: r2(inc - exp - sav + pl), balance: r2(bal), safety_net: r2(bal + extra) });
      }
      return json({ months: out, end_of_current_month: 23458.12, safety_net_now: r2(23458.12 + extra) });
    }
    case 'affordability': {
      const amount = +q.amount || 0, target = q.month || addM(CUR, 3);
      const hist = months.slice(-13, -1).map(monthTotals);
      const inc = median(hist.map((x) => x.income));
      const exps = hist.map((x) => x.expense);
      const goalsM = goals.filter((g) => g.status === 'active').map(goalJson).reduce((s, g) => s + g.monthly_need, 0);
      const sav = median(hist.map((x) => x.savings));
      const buffer = r2(+settings.buffer_months * median(exps));
      let free = 23458.12 - 6000, f25 = free, f75 = free;
      const path = [];
      for (let m = CUR; m <= target; m = addM(m, 1)) {
        free += inc - median(exps) - goalsM - sav;
        f25 += inc - quant(exps, 0.75) - goalsM - sav;
        f75 += inc - quant(exps, 0.25) - goalsM - sav;
        path.push({ month: m, free: r2(free), p25: r2(f25), p75: r2(f75) });
      }
      const remaining = free - amount;
      const verdict = remaining >= buffer ? 'yes' : remaining >= 0 ? 'tight' : 'no';
      const extra = networthJson().liquid - BANK_BAL;
      path.forEach((pt) => { pt.safety_net = r2(pt.free + extra); });
      return json({ verdict, projected_free: r2(free), buffer, p25: r2(f25), p75: r2(f75), path,
        safety_net: r2(free + extra), safety_net_after: r2(free + extra - amount), covered_by_safety_net: verdict !== 'yes' && free + extra - amount >= buffer, planned_in_range: 0 });
    }
    case 'import': {
      if (seg[1] === 'inbox') {
        lastImport = { ...lastImport, imported_at: TODAY + ' 12:00:00' };
        return json({ files: [{ file_name: 'leumi_statement_demo.pdf', provider: 'leumi', rows: 210, new_rows: 6, dup_rows: 204 }, { file_name: 'max_demo.xlsx', provider: 'max', rows: 412, new_rows: 11, dup_rows: 401 }] });
      }
      const f = body instanceof FormData ? body.get('file') : null;
      const name = f?.name || 'upload';
      const provider = /leumi/i.test(name) ? 'leumi' : /cal/i.test(name) ? 'cal' : /max/i.test(name) ? 'max' : 'generic';
      lastImport = { file_name: name, provider, rows: 120, new_rows: 9, dup_rows: 111, imported_at: TODAY + ' 12:00:00' };
      return json({ provider, rows: 120, new_rows: 9, dup_rows: 111 });
    }
    case 'sync': {
      if (seg[1] === 'status') return json(syncState);
      if (syncState.running) return json({ detail: 'Sync already running' }, 409);
      syncState = { running: true, providers: [] };
      setTimeout(() => { syncState.providers.push({ name: 'leumi', ok: true, error: null, new_rows: 3, at: new Date().toISOString() }); }, 1500);
      setTimeout(() => { syncState.providers.push({ name: 'max', ok: true, error: null, new_rows: 12, at: new Date().toISOString() }); syncState.running = false; }, 3500);
      return json({ started: true });
    }
    case 'settings': {
      if (method === 'PATCH') Object.assign(settings, Object.fromEntries(Object.entries(body).map(([k, v]) => [k, String(v)])));
      return json(settings);
    }
    case 'export.csv': {
      const head = 'id,date,amount,description,category\n';
      return new Response(head + txns.slice(0, 50).map((t) => [t.id, t.date, t.amount, `"${t.description}"`, cat(t.category_id)?.name || ''].join(',')).join('\n'), { headers: { 'Content-Type': 'text/csv' } });
    }
    default: return notFound();
  }
}

export function installMock() {
  const real = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url;
    const path = new URL(url, location.origin).pathname;
    if (!path.startsWith('/api')) return real(input, init);
    const method = (init.method || 'GET').toUpperCase();
    let body = init.body;
    if (typeof body === 'string') { try { body = JSON.parse(body); } catch { /* keep */ } }
    await new Promise((r) => setTimeout(r, 60 + Math.random() * 90));
    try { return await handle(method, url, body || {}); } catch (e) { console.error('[mock]', e); return json({ detail: String(e) }, 500); }
  };
  // Export link works in mock mode too.
  document.addEventListener('click', async (e) => {
    const a = e.target.closest?.('a[href^="/api/export.csv"]');
    if (!a) return;
    e.preventDefault();
    const res = await window.fetch('/api/export.csv');
    const blob = new Blob([await res.text()], { type: 'text/csv' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob); link.download = 'transactions-mock.csv'; link.click();
  });
  console.info('[mock] API interceptor installed — invented sample data');
}
