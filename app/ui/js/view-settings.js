// Settings: cashflow start day, buffer, overspend threshold, forecast income, sync providers, export.
import { h, toast, toastError, field } from './util.js';
import { api, changed } from './api.js';

const PROVIDERS = [['leumi', 'Leumi (bank)'], ['max', 'Max (all your Max-issued cards)'], ['visaCal', 'Visa Cal']];

export async function render(root) {
  root.append(h('div.page-head', h('h1', 'Settings')));
  const s = (await api.get('/settings')) || {};
  const save = async (patch, msg = 'Saved') => {
    try { await api.patch('/settings', patch); toast(msg, { type: 'success' }); changed('settings'); } catch (e) { toastError(e); }
  };
  const numInput = (key, def, attrs) => {
    const el = h('input', { type: 'number', value: s[key] ?? def, style: { width: '120px' }, ...attrs });
    el.addEventListener('change', () => {
      const v = +el.value;
      if (Number.isNaN(v) || (attrs.min != null && v < attrs.min) || (attrs.max != null && v > attrs.max)) { el.value = s[key] ?? def; toast('Out of range', { type: 'error' }); return; }
      s[key] = v; save({ [key]: v });
    });
    return el;
  };
  const salaryInput = () => {
    const el = h('input', { type: 'number', min: 0, step: 100, placeholder: 'median', value: s.salary_net_baseline ?? '', style: { width: '140px' } });
    el.addEventListener('change', () => {
      const raw = el.value.trim();
      if (raw !== '' && (Number.isNaN(+raw) || +raw < 0)) { el.value = s.salary_net_baseline ?? ''; toast('Enter a positive amount', { type: 'error' }); return; }
      s.salary_net_baseline = raw === '' ? null : +raw;
      save({ salary_net_baseline: s.salary_net_baseline });
    });
    return el;
  };
  const enabled = new Set(String(s.sync_providers ?? 'leumi,max').split(',').map((x) => x.trim()).filter(Boolean));
  const provBoxes = PROVIDERS.map(([k, l]) => h('label.check', h('input', {
    type: 'checkbox', checked: enabled.has(k),
    onchange: (e) => { e.target.checked ? enabled.add(k) : enabled.delete(k); save({ sync_providers: [...enabled].join(',') }, 'Sync providers saved'); },
  }), l));

  root.append(h('div.card', { style: { maxWidth: '720px' } },
    h('div.card-head', h('h2', 'Budget')),
    h('div.stack',
      field('Cashflow month starts on day', numInput('cashflow_start_day', 1, { min: 1, max: 28, step: 1 }),
        'Set this to your salary day (e.g. 10) so each month runs salary to salary. Changes how transactions group into months.'),
      field('Safety buffer (months of expenses)', numInput('buffer_months', 1, { min: 0, max: 12, step: 0.5 }),
        '“Can I afford it?” answers Yes only if this much is left after the purchase.'),
      field('Overspend threshold (% of category budget)', numInput('overspend_pct', 120, { min: 100, max: 300, step: 5 }),
        'A month is flagged when a variable category passes this share of its budget, or when spending beats income.'))),
  h('div.card.section', { style: { maxWidth: '720px' } },
    h('div.card-head', h('h2', 'Forecast income')),
    h('div.stack',
      field('Net salary baseline (₪/month)', salaryInput(),
        'The forecast and “Can I afford it?” count this salary plus the flat’s expected rent. Leave empty to use the median salary of recent months.'),
      h('label.check', h('input', {
        type: 'checkbox', checked: String(s.count_variable_income_in_forecast) === 'true',
        onchange: (e) => save({ count_variable_income_in_forecast: e.target.checked }),
      }), 'Count reserve duty, bonuses and other irregular income in the forecast'),
      field('Rental tax reserve (% of expected rent)', numInput('rental_tax_pct', 10, { min: 0, max: 50, step: 1 }),
        'Set aside in the property P&L for tax on rental income.'))),
  h('div.card.section', { style: { maxWidth: '720px' } },
    h('div.card-head', h('h2', 'Sync')),
    h('div.stack', provBoxes, h('div.small.muted', 'Credentials live in the macOS Keychain. The app never stores passwords.'),
      h('label.check', h('input', {
        type: 'checkbox', checked: String(s.cal_account_kind ?? 'gift') === 'gift',
        onchange: (e) => save({ cal_account_kind: e.target.checked ? 'gift' : 'card' }, 'Saved: applies to the next Cal import'),
      }), 'My Cal card is a prepaid gift card (its purchases are paid by the gift, not counted as spending)'))),
  h('div.card.section', { style: { maxWidth: '720px' } },
    h('div.card-head', h('h2', 'Data')),
    h('div.row.wrap', h('a.btn', { href: '/api/export.csv', download: 'transactions.csv' }, 'Export all transactions (CSV)'),
      h('span.small.muted', 'Database: data/finance.db · daily backups in data/backups/'))));
}
