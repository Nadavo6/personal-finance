// Net worth: total + liquid, split by kind, history, assets (dated values), liabilities, property, planned items, equity comp.
import { h, clear, data, ils, pct, fmtDate, monthLabel, shortMonth, thisMonth, today, toast, toastError, openModal, field, readForm, empty, badge, tip, asList } from './util.js';
import { api, changed } from './api.js';
import { lineChart } from './charts.js';

export const KINDS = {
  cash: ['Cash', '#3aa776'],
  investment: ['Investments', '#5b5bd6'],
  pension: ['Pension', '#8a8ae3'],
  study_fund: ['Study fund', '#39a3a3'],
  property_equity: ['Property equity', '#c9a227'],
  property: ['Property', '#e0803a'],
  equity_comp: ['Equity comp', '#d16a8b'],
  other: ['Other', '#9a9aa5'],
  liabilities: ['Liabilities', '#d93d42'],
};
const kindLabel = (k) => (KINDS[k] || [k])[0];
const kindColor = (k) => (KINDS[k] || [null, '#9a9aa5'])[1];

export async function render(root) {
  const head = h('div.page-head', h('h1', 'Net worth'), h('span.sub', ''), h('span.spacer'),
    h('button.btn', { onclick: () => importSheet(reload) }, 'Import sheet snapshot'),
    h('button.btn.primary', { onclick: () => assetModal(null, reload) }, '+ Asset'));
  const body = h('div');
  root.append(head, body);

  async function reload() {
    let nw;
    try { nw = await api.get('/networth'); } catch (e) { clear(body).append(h('div.error-box', e.message)); return; }
    const imp = nw.last_sheet_import;
    head.querySelector('.sub').textContent = [nw.as_of ? `as of ${fmtDate(nw.as_of, true)}` : null,
      imp?.sheet_last_updated ? `sheet updated ${fmtDate(imp.sheet_last_updated, true)}` : null].filter(Boolean).join(' · ');
    draw(body, nw, reload);
  }
  await reload();
}

function draw(body, nw, reload) {
  clear(body);
  const assets = asList(nw.assets, 'assets');
  if (!assets.length) {
    body.append(h('div.card', empty('No assets yet',
      'Import the sheet snapshot (data/sheet/snapshot_<date>.json) or add an asset by hand. Bank balances fill the checking account automatically.',
      h('div.row', h('button.btn.primary', { onclick: () => importSheet(reload) }, 'Import sheet snapshot'),
        h('button.btn', { onclick: () => assetModal(null, reload) }, '+ Asset')))));
    drawPlanned(body, asList(nw.planned, 'planned'), reload);
    return;
  }

  // --- hero: total, liquid, split by kind
  const kinds = asList(nw.by_kind, 'by_kind').filter((k) => k.total);
  const pos = kinds.filter((k) => k.total > 0);
  const posTotal = pos.reduce((s, k) => s + k.total, 0) || 1;
  const stale = assets.filter((a) => a.stale && a.include_in_net_worth);
  const left = h('div.card.left-card',
    h('div.label', 'Net worth'),
    h('div.big-number', { class: nw.total < 0 ? 'neg' : '' }, ils(nw.total)),
    h('div.muted', 'Liquid ', h('b.num', ils(nw.liquid)), h('span.faint', ' · cash + liquid investments')),
    nw.liabilities_total ? h('div.muted.small', `after ${ils(nw.liabilities_total)} of liabilities`) : null,
    stale.length ? h('div.small.warn', { style: { marginTop: '10px' } }, `⚠ ${stale.length} value${stale.length === 1 ? ' is' : 's are'} older than ${nw.stale_after_days || 45} days (marked “stale” below). `,
      h('a', { href: '#/import' }, 'Refresh the sheet snapshot →')) : null);
  const split = h('div.card',
    h('div.card-head', h('h2', 'By kind')),
    h('div.nw-stack', pos.map((k) => tip(h('span', { style: { width: (k.total / posTotal) * 100 + '%', background: kindColor(k.kind) } }),
      `${kindLabel(k.kind)}: ${ils(k.total)} (${pct((k.total / posTotal) * 100)})`))),
    h('ul.list.nw-kinds', kinds.map((k) => h('li',
      h('span.dot', { style: { background: kindColor(k.kind) } }), kindLabel(k.kind),
      h('span.spacer'), k.total > 0 ? h('span.faint.small', pct((k.total / posTotal) * 100)) : null,
      h('span.amount.num', { class: k.total < 0 ? 'bad' : '' }, ils(k.total))))));
  body.append(h('div.hero', left, split));

  // --- history
  const hist = asList(nw.history, 'history');
  const histCard = h('div.card', h('div.card-head', h('h2', 'History'), h('span.muted.small', 'Month-end, from dated values (carried forward)')));
  if (hist.length >= 2) {
    histCard.append(lineChart(hist.map((p) => ({ label: monthLabel(p.month, true), short: shortMonth(p.month), y: p.total, y2: p.liquid })),
      { height: 200, zero: false, area: false, second: { label: 'liquid' } }));
  } else {
    histCard.append(h('div.muted', { style: { padding: '6px 0' } }, 'History builds up as you add dated values (one per sheet refresh or update).'));
  }
  body.append(histCard);

  // --- assets
  body.append(h('h3.section', { style: { margin: '24px 0 10px' } }, 'Assets'),
    h('div.cards', assets.map((a) => assetCard(a, reload))));

  // --- liabilities + property
  const liabs = asList(nw.liabilities, 'liabilities');
  const grid = h('div.grid-2.section');
  grid.append(liabilitiesCard(liabs, reload));
  if (nw.property) grid.append(propertyCard(nw.property));
  body.append(grid);

  drawPlanned(body, asList(nw.planned, 'planned'), reload);
  const eq = asList(nw.equity_comp, 'equity_comp');
  if (eq.length) body.append(equityCard(eq));
}

// ---------------------------------------------------------------- assets

function staleChip(days, after = 45) {
  if (days == null) return null;
  return days > after ? badge(`stale · ${days} d`, 'warn') : null;
}

function assetCard(a, reload) {
  const bd = Array.isArray(a.breakdown) ? a.breakdown.filter((x) => x && x.value != null) : [];
  const bdTotal = bd.reduce((s, x) => s + (+x.value || 0), 0) || 1;
  const upd = h('div', { hidden: true });
  const card = h('div.card.nw-asset', { class: a.include_in_net_worth ? '' : 'dimmed' },
    h('div.row', h('span.dot', { style: { background: kindColor(a.kind) } }), h('b', { dir: 'auto' }, a.name), h('span.spacer'),
      staleChip(a.stale_days)),
    h('div.row.wrap', { style: { margin: '4px 0 8px', gap: '6px' } },
      badge(kindLabel(a.kind)), a.liquid ? badge('liquid', 'accent') : null,
      a.track_from_bank ? badge('live from bank', 'good') : null,
      a.include_in_net_worth ? null : badge('not counted')),
    h('div.nw-value.num', a.value == null ? '—' : ils(a.value)),
    h('div.small.muted', a.as_of ? `as of ${fmtDate(a.as_of, true)}` : 'no value yet',
      a.value_source ? ` · ${a.value_source === 'bank' ? 'bank balance' : a.value_source}` : ''),
    bd.length ? h('div.nw-breakdown', bd.map((x) => h('div.nw-bd-row',
      h('span', { dir: 'auto' }, x.name), h('span.spacer'), h('span.num', ils(x.value)),
      h('div.nw-bd-bar', h('span', { style: { width: ((+x.value || 0) / bdTotal) * 100 + '%', background: kindColor(a.kind) } }))))) : null,
    bd.length && a.value && Math.abs(bdTotal - a.value) > 1 ? h('div.small.faint', `Breakdown sums to ${ils(bdTotal)}`) : null,
    a.notes ? h('div.small.muted', { dir: 'auto', style: { marginTop: '8px' } }, a.notes) : null,
    upd,
    h('div.row', { style: { marginTop: '10px' } },
      a.track_from_bank ? h('span.small.faint', 'Updates with each bank import') : h('button.btn.sm', { onclick: () => { upd.hidden = !upd.hidden; upd.querySelector('input')?.focus(); } }, 'Update value'),
      h('span.spacer'), h('button.btn.sm.ghost', { onclick: () => assetModal(a, reload) }, 'Edit')));
  upd.append(valueForm({ value: a.value, label: 'New value (₪)' }, async (v) => {
    await api.post(`/assets/${a.id}/value`, { as_of: v.as_of, value: v.value });
    toast(`${a.name}: ${ils(v.value)}`, { type: 'success' }); changed('networth'); reload();
  }));
  return card;
}

/** Inline "dated value" form: value + as-of date + save. */
function valueForm({ value, label, name = 'value' }, onSave) {
  const vIn = h('input', { type: 'number', name, step: 1, value: value != null ? Math.round(value) : '', required: true, style: { width: '140px' } });
  const dIn = h('input', { type: 'date', name: 'as_of', value: today(), max: today() });
  const form = h('form.nw-update', {
    onsubmit: async (e) => {
      e.preventDefault();
      if (vIn.value === '') { vIn.focus(); return; }
      try { await onSave({ value: +vIn.value, as_of: dIn.value }); } catch (err) { toastError(err); }
    },
  }, field(label, vIn), field('As of', dIn), h('button.btn.sm.primary', { type: 'submit' }, 'Save'));
  return form;
}

function assetModal(a, reload) {
  const v = { name: '', kind: 'investment', liquid: null, include_in_net_worth: true, notes: '', ...(a || {}) };
  const liquid = v.liquid ?? ['cash', 'investment'].includes(v.kind);
  const form = h('div.form-grid',
    h('div.full', field('Name', h('input', { name: 'name', required: true, value: v.name, dir: 'auto', placeholder: 'e.g. Brokerage account' }))),
    field('Kind', h('select', { name: 'kind' }, Object.keys(KINDS).filter((k) => k !== 'liabilities').map((k) => h('option', { value: k, selected: v.kind === k }, kindLabel(k))))),
    a ? h('div') : field('Value (₪)', h('input', { name: 'value', type: 'number', step: 1 }), 'Optional: first dated value'),
    h('label.check', h('input', { type: 'checkbox', name: 'liquid', checked: liquid }), 'Liquid (counts in the safety net)'),
    h('label.check', h('input', { type: 'checkbox', name: 'include_in_net_worth', checked: !!v.include_in_net_worth }), 'Count in net worth'),
    a ? null : field('As of', h('input', { name: 'as_of', type: 'date', value: today(), max: today() })),
    h('div.full', field('Notes', h('textarea', { name: 'notes', rows: 2, dir: 'auto' }, v.notes || ''))));
  const actions = [];
  if (a) actions.push({ label: 'Delete', danger: true, fn: async (close) => {
    if (!confirm(`Delete “${a.name}” and its value history?`)) return;
    try { await api.del('/assets/' + a.id); close(); toast('Asset deleted'); changed('networth'); reload(); } catch (e) { toastError(e); }
  } });
  actions.push({ label: 'Cancel' }, {
    label: a ? 'Save' : 'Add asset', primary: true, fn: async (close) => {
      const b = readForm(form);
      try {
        if (a) await api.patch('/assets/' + a.id, b); else await api.post('/assets', b);
        close(); toast(a ? 'Saved' : 'Asset added', { type: 'success' }); changed('networth'); reload();
      } catch (e) { toastError(e); }
    },
  });
  openModal(a ? 'Edit asset' : 'New asset', form, { actions });
}

async function importSheet(reload) {
  try {
    const r = await api.post('/networth/import-sheet', {});
    const added = (r.assets_added || 0) + (r.planned_added || 0) + (r.tasks_added || 0) + (r.freebies_added || 0);
    toast(`${r.file}: ${r.values_written} value${r.values_written === 1 ? '' : 's'} updated, ${added} new item${added === 1 ? '' : 's'}`, { type: 'success', ms: 5000 });
    changed('networth'); reload?.();
    return r;
  } catch (e) { toastError(e); return null; }
}
export { importSheet };

// ---------------------------------------------------------------- liabilities

function liabilitiesCard(liabs, reload) {
  const card = h('div.card', h('div.card-head', h('h2', 'Liabilities')));
  if (!liabs.length) { card.append(h('div.muted', 'No loans or mortgage recorded.')); return card; }
  liabs.forEach((l) => {
    const upd = h('div', { hidden: true });
    upd.append(valueForm({ value: l.balance, label: 'Balance (₪)', name: 'balance' }, async (v) => {
      await api.post(`/liabilities/${l.id}/value`, { as_of: v.as_of, balance: v.value });
      toast(`${l.name}: ${ils(v.value)}`, { type: 'success' }); changed('networth'); reload();
    }));
    card.append(h('div.nw-liab',
      h('div.row', h('b', { dir: 'auto' }, l.name), h('span.spacer'), staleChip(l.stale_days),
        h('span.num', { style: { fontWeight: 600 } }, ils(l.balance))),
      h('div.small.muted', [
        l.as_of ? `balance as of ${fmtDate(l.as_of, true)}` : 'no balance yet',
        l.principal != null ? ` · principal ${ils(l.principal)}` : '',
        l.monthly_payment ? ` · ${ils(l.monthly_payment)}/month` : '',
        l.rate_pct != null ? ` · ${l.rate_pct}%` : ''].join('')),
      l.include_in_net_worth ? null : h('div.small.faint', 'Not subtracted from net worth: the flat counts at your equity, which already nets the mortgage.'),
      upd,
      h('div.row', { style: { marginTop: '6px' } },
        h('button.btn.sm', { onclick: () => { upd.hidden = !upd.hidden; } }, 'Update balance'),
        h('button.btn.sm.ghost', {
          onclick: async () => {
            try { await api.patch('/liabilities/' + l.id, { include_in_net_worth: !l.include_in_net_worth }); changed('networth'); reload(); } catch (e) { toastError(e); }
          },
        }, l.include_in_net_worth ? 'Don’t count in net worth' : 'Count in net worth'))));
  });
  return card;
}

// ---------------------------------------------------------------- property

function propertyCard(p) {
  const arrears = p.rent_arrears > 0
    ? h('span.chip.arrears-chip', { title: 'Late: ' + asList(p.rent_arrears_months).map((a) => `${shortMonth(a.month)} ${ils(a.due)}`).join(', ') }, `Arrears ${ils(p.rent_arrears)}`)
    : null;
  const card = h('div.card', h('div.card-head', h('h2', 'Property'), data(p.name, 'muted small'), h('span.spacer'), arrears));
  const m = p.mortgage || {};
  card.append(h('dl.kv',
    h('dt', 'Price incl. renovation'), h('dd', ils(p.price)),
    p.bank_financed_pct != null ? [h('dt', 'Bank financed'), h('dd', pct(p.bank_financed_pct))] : null,
    m.principal != null ? [h('dt', 'Mortgage'), h('dd', `${ils(m.balance ?? m.principal)}`, m.monthly_payment ? h('span.muted', ` · ${ils(m.monthly_payment)}/month`) : null)] : null,
    p.equity_estimate != null ? [h('dt', 'Equity (price − mortgage)'), h('dd', h('b', ils(p.equity_estimate)))] : null,
    p.rent_expected_monthly ? [h('dt', 'Expected rent'), h('dd', `${ils(p.rent_expected_monthly)}/month`)] : null,
    p.rent_yield_pct != null ? [h('dt', 'Gross yield'), h('dd', `${p.rent_yield_pct.toFixed(1)}%`, h('span.muted', ' of price, before tax'))] : null,
    p.rent_received_12m ? [h('dt', 'Rent received (12 mo)'), h('dd', ils(p.rent_received_12m))] : null));

  const costs = asList(p.purchase_costs);
  if (costs.length) {
    card.append(h('h3', { style: { margin: '16px 0 6px' } }, 'Purchase costs'),
      h('table.nw-table', h('tbody',
        costs.map((c) => h('tr', h('td', { dir: 'auto' }, c.item), h('td.small.muted', { dir: 'auto' }, c.note || ''), h('td.num', ils(c.amount)))),
        p.total_costs != null ? h('tr.nw-total', h('td', 'Total purchase + renovation'), h('td'), h('td.num', h('b', ils(p.total_costs)))) : null)));
  }
  const contrib = Object.entries(p.contributions || {});
  if (contrib.length) {
    const tot = p.contributions_total || 1;
    card.append(h('h3', { style: { margin: '16px 0 6px' } }, 'Contributions'),
      h('ul.list', contrib.map(([who, v]) => h('li', h('span', { dir: 'auto', style: { textTransform: 'capitalize' } }, who), h('span.spacer'),
        h('span.faint.small', pct((v / tot) * 100)), h('span.amount.num', ils(v))))));
  }
  const rent = asList(p.rental_income);
  if (rent.length) {
    // running balance (Σ received − Σ expected) from the P&L; upcoming months are not due yet
    const bal = Object.fromEntries(asList(p.pnl).map((r) => [r.month, r.balance]));
    card.append(h('h3', { style: { margin: '16px 0 6px' } }, 'Rent: expected vs received'),
      h('table.nw-table', h('thead', h('tr', h('th', 'Month'), h('th.num', 'Expected'), h('th.num', 'Received'),
        h('th.num', { title: 'Cumulative: rent received so far − rent due so far. Lump payments carry forward.' }, 'Balance'))),
        h('tbody', rent.map((r) => {
          const b = bal[r.month];
          return h('tr', { class: r.future ? 'muted-row' : '' },
            h('td', monthLabel(r.month, true), r.note ? h('div.small.faint', { dir: 'auto' }, r.note) : null),
            h('td.num', r.expected != null ? ils(r.expected) : '—'),
            h('td.num', r.received ? ils(r.received) : h('span.faint', r.future ? '' : '—')),
            h('td.num.small', r.future || b == null ? h('span.faint', 'upcoming')
              : Math.abs(b) < 1 ? h('span.good', '✓ even') : h('span', { class: b < 0 ? 'bad' : 'good' }, b < 0 ? `owed ${ils(-b)}` : `ahead ${ils(b)}`)));
        }))),
      p.rent_arrears > 0 ? h('div.small.muted', { style: { marginTop: '6px' } },
        h('span.bad', `${ils(p.rent_arrears)} rent outstanding`), ' through ', shortMonth(thisMonth()),
        '. Rent counts from transactions in the “Rental income” category; ',
        h('a', { href: '#/transactions?q=' + encodeURIComponent('העברה') }, 're-categorise the inflow'), ' if it landed elsewhere.') : null);
  }
  const pnl = asList(p.pnl);
  if (pnl.length) {
    const sum = (k) => pnl.reduce((a, r) => a + (r[k] || 0), 0);
    const cell = (v, cls = '') => h('td.num', { class: cls }, v ? ils(v) : h('span.faint', '—'));
    card.append(h('h3', { style: { margin: '16px 0 6px' } }, 'Rental P&L'),
      h('table.nw-table.pnl-table',
        h('thead', h('tr', h('th', 'Month'), h('th.num', 'Expected'), h('th.num', 'Received'), h('th.num', 'Mortgage'),
          h('th.num', 'Insurance'), h('th.num', { title: `Reserve of ${p.tax_pct ?? 10}% of the expected rent for rental-income tax` }, `Tax ${p.tax_pct ?? 10}%`), h('th.num', 'Net'))),
        h('tbody', pnl.map((r) => h('tr',
          h('td', shortMonth(r.month)), cell(r.expected), cell(r.received, r.received < r.expected ? 'bad' : ''),
          cell(r.mortgage), cell(r.insurance), cell(r.tax_reserve),
          h('td.num', h('b', { class: r.net < 0 ? 'bad' : 'good' }, ils(r.net, { sign: true }))))),
        h('tr.nw-total', h('td', 'Total'), cell(sum('expected')), cell(sum('received')), cell(sum('mortgage')),
          cell(sum('insurance')), cell(sum('tax_reserve')), h('td.num', h('b', { class: sum('net') < 0 ? 'bad' : 'good' }, ils(sum('net'), { sign: true })))))),
      h('div.small.muted', { style: { marginTop: '6px' } },
        'Net = received − mortgage − insurance − tax reserve. ',
        p.insurance_source === 'sheet' ? 'Insurance from the sheet’s fixed costs. ' : p.insurance_source ? '' : 'No flat insurance identified. ',
        p.rent_arrears > 0 ? h('span.bad', `Arrears ${ils(p.rent_arrears)} cumulative (unpaid: ${asList(p.rent_arrears_months).map((a) => `${shortMonth(a.month)} ${ils(a.due)}`).join(', ')}). `) : null,
        p.rent_credit > 0 ? `${ils(p.rent_credit)} paid ahead of what is due.` : null));
  }
  if (p.rental_tax_note) card.append(h('div.small.muted', { dir: 'auto', style: { marginTop: '10px' } }, p.rental_tax_note));
  return card;
}

// ---------------------------------------------------------------- planned items

function drawPlanned(body, items, reload) {
  const open = items.filter((p) => !p.done);
  const net = open.filter((p) => p.when_month).reduce((s, p) => s + (p.signed_amount ?? (p.kind === 'income' ? p.amount : -p.amount)), 0);
  const card = h('div.card.section',
    h('div.card-head', h('h2', 'Planned income & expenses'),
      h('span.muted.small', 'Dated items count in the forecast and “Can I afford it?” in their month'),
      h('span.spacer'), h('button.btn.sm', { onclick: () => plannedModal(null, reload) }, '+ Add')));
  if (!items.length) {
    card.append(empty('Nothing planned', 'Add known future income (a refund, a bonus) or expenses (a tax payment, a trip).'));
  } else {
    card.append(h('ul.list', items.map((p) => {
      const amt = p.signed_amount ?? (p.kind === 'income' ? p.amount : -p.amount);
      return h('li.clickable', { class: p.done ? 'nw-done' : '', onclick: (e) => { if (e.target.type !== 'checkbox') plannedModal(p, reload); } },
        h('input', {
          type: 'checkbox', checked: !!p.done, title: p.done ? 'Mark as open' : 'Mark as done',
          onchange: async (e) => {
            try { await api.patch('/planned/' + p.id, { done: e.target.checked }); changed('networth'); reload(); } catch (err) { toastError(err); }
          },
        }),
        h('span.nw-when', p.when_month ? monthLabel(p.when_month) : h('span.faint', 'undated')),
        h('span', data(p.what), p.notes ? h('div.small.faint', { dir: 'auto' }, p.notes) : null),
        p.overdue ? badge('past due', 'warn') : null,
        h('span.amount.num', { class: amt > 0 ? 'good' : '' }, ils(amt, { sign: true })));
    })),
    open.length ? h('div.small.muted', { style: { marginTop: '8px' } }, 'Open dated items, net: ', h('b.num', ils(net, { sign: true }))) : null);
  }
  body.append(card);
}

function plannedModal(p, reload) {
  const v = { kind: 'expense', when_month: '', amount: '', what: '', notes: '', ...(p || {}) };
  const form = h('div.form-grid',
    h('div.full', field('What', h('input', { name: 'what', required: true, value: v.what, dir: 'auto', placeholder: 'e.g. Tax payment' }))),
    field('Type', h('select', { name: 'kind' }, ['expense', 'income'].map((k) => h('option', { value: k, selected: v.kind === k }, k === 'income' ? 'Income' : 'Expense')))),
    field('Amount (₪)', h('input', { name: 'amount', type: 'number', min: 0, step: 1, required: true, value: v.amount })),
    field('Month', h('input', { name: 'when_month', type: 'month', value: v.when_month || '' }), 'Leave empty if undated'),
    h('label.check', h('input', { type: 'checkbox', name: 'done', checked: !!v.done }), 'Done (stops counting in the forecast)'),
    h('div.full', field('Notes', h('textarea', { name: 'notes', rows: 2, dir: 'auto' }, v.notes || ''))));
  const actions = [];
  if (p) actions.push({ label: 'Delete', danger: true, fn: async (close) => {
    if (!confirm(`Delete “${p.what}”?`)) return;
    try { await api.del('/planned/' + p.id); close(); toast('Deleted'); changed('networth'); reload(); } catch (e) { toastError(e); }
  } });
  actions.push({ label: 'Cancel' }, {
    label: p ? 'Save' : 'Add', primary: true, fn: async (close) => {
      const b = readForm(form);
      try {
        if (p) await api.patch('/planned/' + p.id, b); else await api.post('/planned', b);
        close(); toast(p ? 'Saved' : 'Added', { type: 'success' }); changed('networth'); reload();
      } catch (e) { toastError(e); }
    },
  });
  openModal(p ? 'Edit planned item' : 'New planned item', form, { actions });
}

// ---------------------------------------------------------------- equity comp

function equityCard(items) {
  return h('div.card.section',
    h('div.card-head', h('h2', 'Equity compensation'), h('span.muted.small', 'Not counted in net worth')),
    h('ul.list', items.map((e) => h('li',
      h('span.nw-when', e.vests ? monthLabel(e.vests.slice(0, 7)) : '—'),
      badge(e.type || 'grant'),
      h('span', h('b.num', e.qty != null ? Number(e.qty).toLocaleString('en-US') : '—'), ' ', e.type || 'units',
        e.notes ? h('div.small.faint', { dir: 'auto' }, e.notes) : null),
      h('span.spacer'),
      h('span.amount.small', e.vested ? h('span.good', 'vested') : e.months_to_vest != null ? h('span.muted', `in ${e.months_to_vest} month${e.months_to_vest === 1 ? '' : 's'}`) : null)))));
}
