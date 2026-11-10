// Rules & Categories: editable rules with live match preview, categories (budget/kind/colour), accounts rename.
import { h, data, clear, num, ils, fmtDate, debounce, toast, toastError, empty, asList, badge, tip } from './util.js';
import { api, categories, accounts, invalidateCategories, invalidateAccounts, changed } from './api.js';

const KINDS = ['income', 'fixed', 'variable', 'tracking', 'usage', 'savings', 'transfer'];
const kindsFor = (k) => (k && !KINDS.includes(k) ? [...KINDS, k] : KINDS);
const MATCH = ['contains', 'equals', 'regex'];

export async function render(root, { params }) {
  let tab = params.tab || 'rules';
  const seg = h('div.seg', [['rules', 'Rules'], ['categories', 'Categories'], ['accounts', 'Accounts']].map(([k, l]) =>
    h('button', { class: k === tab ? 'on' : '', onclick: () => { tab = k; [...seg.children].forEach((b, i) => b.classList.toggle('on', ['rules', 'categories', 'accounts'][i] === k)); show(); } }, l)));
  root.append(h('div.page-head', h('h1', 'Rules & Categories'), seg));
  const box = h('div');
  root.append(box);
  async function show() {
    clear(box).append(h('div.loading', h('span.spin'), 'Loading…'));
    try {
      if (tab === 'rules') await rulesTab(box);
      else if (tab === 'categories') await categoriesTab(box);
      else await accountsTab(box);
    } catch (e) { clear(box).append(h('div.error-box', e.message)); }
  }
  await show();
}

// ---------------- rules ----------------
function catSelect(cats, value, attrs = {}) {
  return h('select.inline', attrs, h('option', { value: '' }, '— no category —'),
    cats.filter((c) => !c.archived || c.id === value).map((c) => h('option', { value: c.id, selected: c.id === value }, c.name)));
}
function previewCount(rule, out) {
  if (!rule.pattern) { out.textContent = '—'; return; }
  out.textContent = '…';
  api.post('/rules/preview', {
    pattern: rule.pattern, match_type: rule.match_type, account_kind: rule.account_kind || null,
    amount_min: rule.amount_min ?? null, amount_max: rule.amount_max ?? null,
  }).then((r) => {
    out.textContent = num(r?.count ?? 0);
    out._sample = asList(r?.sample, 'sample');
  }).catch(() => { out.textContent = '?'; });
}
function sampleTip(out) {
  tip(out, () => {
    const s = out._sample || [];
    if (!s.length) return 'No matching transactions';
    return h('div', h('b', 'Sample matches'), h('ul', s.slice(0, 6).map((t) => h('li', fmtDate(t.date), ' · ', data(t.description), ' · ', ils(t.amount)))));
  });
}

async function rulesTab(box) {
  const [rulesR, cats] = await Promise.all([api.get('/rules'), categories(true)]);
  let rules = asList(rulesR).sort((a, b) => (b.priority || 0) - (a.priority || 0));
  let hasEnabled = false;
  clear(box);

  // new-rule row
  const nr = { pattern: '', match_type: 'contains', account_kind: null, category_id: null };
  const nCount = h('span.num', '—');
  sampleTip(nCount);
  const nPat = h('input.pattern-in', { type: 'text', placeholder: 'Merchant text, e.g. שופרסל', dir: 'auto', style: { flex: 1, minWidth: '200px' } });
  const nMatch = h('select', MATCH.map((m) => h('option', { value: m }, m)));
  const nCat = catSelect(cats, null);
  nCat.classList.remove('inline');
  const nTransfer = h('input', { type: 'checkbox' });
  const nExcl = h('input', { type: 'checkbox' });
  const refreshNew = debounce(() => { nr.pattern = nPat.value.trim(); nr.match_type = nMatch.value; previewCount(nr, nCount); }, 250);
  nPat.addEventListener('input', refreshNew);
  nMatch.addEventListener('change', refreshNew);
  const addForm = h('form.card', {
    onsubmit: async (e) => {
      e.preventDefault();
      if (!nPat.value.trim()) { nPat.focus(); return; }
      if (nMatch.value === 'regex') { try { new RegExp(nPat.value); } catch { toast('Invalid regular expression', { type: 'error' }); return; } }
      const body = { pattern: nPat.value.trim(), match_type: nMatch.value, category_id: nCat.value ? +nCat.value : null,
        set_transfer: nTransfer.checked ? 1 : null, set_excluded: nExcl.checked ? 1 : null, priority: 10 };
      try {
        const r = await api.post('/rules', body);
        toast('Rule added. New imports use it.', { type: 'success' });
        changed('rules');
        rules.unshift(r?.id ? r : body);
        nPat.value = ''; nCount.textContent = '—';
        drawTable();
      } catch (err) { toastError(err); }
    },
  },
  h('div.card-head', h('h2', 'New rule'), h('span.muted.small', 'Matches are counted live against all transactions')),
  h('div.row.wrap', nPat, nMatch, nCat,
    h('label.check.small', nTransfer, 'transfer'), h('label.check.small', nExcl, 'one-off'),
    h('span.small.muted', 'matches ', nCount), h('button.btn.primary', { type: 'submit' }, 'Add rule')));
  const tableBox = h('div.section');
  box.append(addForm, tableBox);

  function drawTable() {
    hasEnabled = rules.some((x) => x.enabled != null);
    clear(tableBox);
    if (!rules.length) { tableBox.append(h('div.card', empty('No rules yet', 'Rules are created when you choose “Always” after recategorizing a transaction, or add one above.'))); return; }
    const tbody = h('tbody', rules.map(ruleRow));
    tableBox.append(h('div.table-wrap', h('table',
      h('thead', h('tr', hasEnabled ? h('th', { style: { width: '36px' }, title: 'Enabled' }, 'On') : null, h('th', 'Pattern'), h('th', { style: { width: '110px' } }, 'Match'), h('th', { style: { width: '100px' } }, 'Account'),
        h('th', { style: { width: '190px' } }, 'Category'), h('th', { style: { width: '120px' } }, 'Flags'),
        h('th.num', { style: { width: '80px' } }, 'Priority'), h('th.num', { style: { width: '80px' } }, 'Matches'), h('th', { style: { width: '40px' } }))),
      tbody)));
  }
  function ruleRow(r) {
    const count = h('span.num', r.hits != null ? num(r.hits) : '—');
    sampleTip(count);
    // Matching/outcome changes re-apply to history (backend default; non-manual rows only).
    // Priority / enabled-only edits must not: send apply_to_history:false.
    const META = new Set(['priority', 'enabled']);
    const save = async (patch, opts = {}) => {
      const before = { ...r };
      if (Object.keys(patch).every((k) => before[k] === patch[k])) return;
      Object.assign(r, patch);
      const metaOnly = Object.keys(patch).every((k) => META.has(k));
      try {
        const res = await api.patch('/rules/' + r.id, metaOnly ? { ...patch, apply_to_history: false } : patch);
        const n = res?.updated_count ?? res?.applied ?? null;
        if (!opts.quiet) toast(metaOnly ? 'Rule saved' : `Rule saved${n != null ? ` · ${n} past transaction${n === 1 ? '' : 's'} re-categorized` : ' · re-applied to past transactions'}`);
        changed('rules');
      } catch (e) { toastError(e); }
      if (opts.preview) previewCount(r, count);
    };
    const pat = h('input.inline.pattern-in', { value: r.pattern || '', dir: 'auto', 'aria-label': 'Pattern' });
    const livePat = debounce(() => { const v = pat.value.trim(); if (v) previewCount({ ...r, pattern: v }, count); }, 250);
    pat.addEventListener('input', livePat);
    pat.addEventListener('change', () => {
      const v = pat.value.trim();
      if (!v) { pat.value = r.pattern; return; }
      if (r.match_type === 'regex') { try { new RegExp(v); } catch { toast('Invalid regular expression', { type: 'error' }); return; } }
      save({ pattern: v }, { preview: true });
    });
    const match = h('select.inline', { 'aria-label': 'Match type', onchange: (e) => save({ match_type: e.target.value }, { preview: true }) },
      MATCH.map((m) => h('option', { value: m, selected: r.match_type === m }, m)));
    const kind = h('select.inline', { 'aria-label': 'Account kind', onchange: (e) => save({ account_kind: e.target.value || null }, { preview: true }) },
      [['', 'any'], ['bank', 'bank'], ['card', 'card'], ['gift', 'gift']].map(([v, l]) => h('option', { value: v, selected: (r.account_kind || '') === v }, l)));
    const cat = catSelect(cats, r.category_id, { 'aria-label': 'Category', onchange: (e) => save({ category_id: e.target.value ? +e.target.value : null }) });
    const flags = h('div.row.small',
      h('label.check', { title: 'Mark matches as transfer' }, h('input', { type: 'checkbox', checked: !!r.set_transfer, onchange: (e) => save({ set_transfer: e.target.checked ? 1 : null }) }), '⇄'),
      h('label.check', { title: 'Mark matches as one-off (excluded)' }, h('input', { type: 'checkbox', checked: !!r.set_excluded, onchange: (e) => save({ set_excluded: e.target.checked ? 1 : null }) }), '⊘'));
    const prio = h('input.inline.num', { type: 'number', value: r.priority ?? 0, style: { textAlign: 'right' }, 'aria-label': 'Priority', onchange: (e) => save({ priority: +e.target.value }) });
    const enabled = r.enabled == null ? null : h('input', {
      type: 'checkbox', checked: !!r.enabled, 'aria-label': 'Enabled', title: 'Enabled',
      onchange: (e) => { tr.classList.toggle('muted-row', !e.target.checked); save({ enabled: e.target.checked ? 1 : 0 }); },
    });
    const tr = h('tr', { class: r.enabled === 0 || r.enabled === false ? 'muted-row' : '' },
      hasEnabled ? h('td', enabled) : null,
      h('td', h('div.row', pat, r.system ? h('span.badge', { title: 'Built-in rule' }, 'system') : null)), h('td', match), h('td', kind), h('td', cat), h('td', flags), h('td', prio), h('td.num', count),
      h('td', r.system ? null : h('button.icon-btn', {
        title: 'Delete rule', onclick: async () => {
          if (!confirm(`Delete rule “${r.pattern}”? Existing categories stay as they are.`)) return;
          try { await api.del('/rules/' + r.id); rules = rules.filter((x) => x !== r); tr.remove(); toast('Rule deleted'); changed('rules'); } catch (e) { toastError(e); }
        },
      }, '✕')));
    if (r.hits == null) previewCount(r, count);
    return tr;
  }
  drawTable();
}

// ---------------- categories ----------------
async function categoriesTab(box) {
  let cats = await categories(true);
  clear(box);
  const tbody = h('tbody');
  const nName = h('input', { placeholder: 'Name', required: true });
  const nHe = h('input', { placeholder: 'שם בעברית', dir: 'rtl' });
  const nKind = h('select', KINDS.map((k) => h('option', { value: k, selected: k === 'variable' }, k)));
  const nBudget = h('input', { type: 'number', min: 0, step: 1, placeholder: 'Budget ₪/mo', style: { width: '130px' } });
  const nColor = h('input', { type: 'color', value: '#5b5bd6' });
  box.append(h('form.card', {
    onsubmit: async (e) => {
      e.preventDefault();
      try {
        const body = { name: nName.value.trim(), name_he: nHe.value.trim() || null, kind: nKind.value, monthly_budget: nBudget.value ? +nBudget.value : null, color: nColor.value };
        if (!body.name) return;
        const r = await api.post('/categories', body);
        invalidateCategories(); changed('categories');
        cats = [...cats, r?.id ? r : body];
        nName.value = ''; nHe.value = ''; nBudget.value = '';
        draw(); toast('Category added', { type: 'success' });
      } catch (err) { toastError(err); }
    },
  }, h('div.card-head', h('h2', 'New category'), h('span.muted.small', 'Budget empty = trailing 3-month median')),
  h('div.row.wrap', nName, nHe, nKind, nBudget, nColor, h('button.btn.primary', { type: 'submit' }, 'Add'))));
  const showArchived = h('input', { type: 'checkbox', onchange: () => draw() });
  box.append(h('div.row.section', { style: { marginBottom: '8px' } }, h('span.spacer'), h('label.check.small', showArchived, 'Show archived')));
  box.append(h('div.table-wrap', h('table',
    h('thead', h('tr', h('th', { style: { width: '44px' } }, 'Color'), h('th', 'Name'), h('th', 'Hebrew name'), h('th', { style: { width: '130px' } }, 'Kind'),
      h('th.num', { style: { width: '140px' } }, 'Budget ₪/mo'), h('th', { style: { width: '100px' } }))), tbody)));

  function draw() {
    clear(tbody);
    const order = (c) => (KINDS.includes(c.kind) ? KINDS.indexOf(c.kind) : KINDS.length);
    cats.filter((c) => showArchived.checked || !c.archived).sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name)).forEach((c) => tbody.append(row(c)));
  }
  function row(c) {
    const save = async (patch) => {
      const prev = { ...c };
      Object.assign(c, patch);
      try { await api.patch('/categories/' + c.id, patch); invalidateCategories(); changed('categories'); toast('Category saved'); }
      catch (e) { Object.assign(c, prev); toastError(e); draw(); }
    };
    return h('tr', { class: c.archived ? 'muted-row' : '' },
      h('td', h('input', { type: 'color', value: c.color || '#9a9aa5', 'aria-label': 'Color', onchange: (e) => save({ color: e.target.value }) })),
      h('td', h('input.inline', { value: c.name, 'aria-label': 'Name', onchange: (e) => e.target.value.trim() && save({ name: e.target.value.trim() }) })),
      h('td', h('input.inline', { value: c.name_he || '', dir: 'rtl', 'aria-label': 'Hebrew name', onchange: (e) => save({ name_he: e.target.value.trim() || null }) })),
      h('td', h('select.inline', { 'aria-label': 'Kind', onchange: (e) => save({ kind: e.target.value }) }, kindsFor(c.kind).map((k) => h('option', { value: k, selected: c.kind === k }, k)))),
      h('td', h('input.inline', {
        type: 'number', min: 0, step: 1, value: c.monthly_budget ?? '', placeholder: 'auto', style: { textAlign: 'right' }, 'aria-label': 'Monthly budget',
        disabled: c.kind === 'income' || c.kind === 'transfer',
        onchange: (e) => save({ monthly_budget: e.target.value === '' ? null : +e.target.value }),
      })),
      h('td.right', h('button.btn.sm.ghost', { onclick: () => save({ archived: c.archived ? 0 : 1 }).then(draw) }, c.archived ? 'Restore' : 'Archive')));
  }
  draw();
}

// ---------------- accounts ----------------
async function accountsTab(box) {
  const accts = await accounts(true);
  clear(box);
  if (!accts.length) { box.append(h('div.card', empty('No accounts yet', 'Accounts appear after the first import or sync.', h('a.btn.primary', { href: '#/import' }, 'Import & Sync')))); return; }
  const KIND = { bank: ['Bank', 'accent'], card: ['Card', ''], gift: ['Gift card', 'warn'] };
  // RiseUp history copies and empty accounts are noise here: tucked behind a toggle
  const history = accts.filter((a) => a.provider === 'riseup' || !a.txn_count);
  const showHist = h('input', { type: 'checkbox', onchange: () => { tbody.querySelectorAll('tr.hist-row').forEach((tr) => { tr.hidden = !showHist.checked; }); } });
  let tbody;
  box.append(h('div.table-wrap', h('table',
    h('thead', h('tr', h('th', 'Name'), h('th', 'Key'), h('th', 'Kind'), h('th.num', 'Transactions'), h('th', 'Range'))),
    tbody = h('tbody', accts.map((a) => {
      const [kl, kc] = KIND[a.kind] || [a.kind, ''];
      const hist = history.includes(a);
      return h('tr', { class: hist ? 'hist-row muted-row' : '', hidden: hist },
        h('td', h('input.inline', {
          value: a.name || '', placeholder: a.key, dir: 'auto', 'aria-label': 'Account name',
          onchange: async (e) => {
            const v = e.target.value.trim();
            try { await api.patch('/accounts/' + a.id, { name: v || null }); a.name = v; invalidateAccounts(); toast('Account renamed'); } catch (err) { toastError(err); }
          },
        })),
        h('td.small.muted', a.key), h('td', badge(kl, kc), a.provider ? h('span.small.muted', ' ' + a.provider) : null),
        h('td.num', num(a.txn_count)),
        h('td.small.muted', a.first_date ? `${fmtDate(a.first_date, true)} – ${fmtDate(a.last_date, true)}` : '—'));
    })))),
  history.length ? h('label.check.small', { style: { marginTop: '10px' } }, showHist, `Show ${history.length} history / empty account${history.length === 1 ? '' : 's'} (RiseUp import)`) : null,
  h('p.small.muted', 'Gift-card accounts (kind “gift”) are excluded from your budget. Their spending counts toward a linked freebie. How fresh each account is: ', h('a', { href: '#/import' }, 'Import & Sync →')));
}
