// Transactions: fast filterable table, inline edits, bulk recategorize, and keyboard Review mode.
import { h, data, clear, ils, ils2, monthLabel, addMonths, fmtDate, debounce, toast, toastError, empty, spinner, asList, badge } from './util.js';
import { api, categories, accounts, catById, changed, isUncat, isUncatCategory } from './api.js';
import { TxnTable, pickCategory, txnBadges } from './txns.js';
import { setParams, go } from './main.js';

const PAGE = 200;

export async function render(root, { params }) {
  if (params.review) return renderReview(root);
  const [cats, accts, ov] = await Promise.all([categories(), accounts().catch(() => []), api.get('/overview').catch(() => null)]);
  const st = {
    q: params.q || '', month: params.month || '', category_id: params.category_id || '', account: params.account || '',
    uncategorized: !!params.uncategorized, include_transfers: !!params.include_transfers,
    import_id: params.import_id || '',
  };
  const months = asList(ov?.months, 'months').map((m) => m.month).reverse();
  const uncCount = ov?.uncategorized_count || 0;

  const search = h('input', { type: 'search', placeholder: 'Search description or note  ( / )', value: st.q, 'aria-label': 'Search', dir: 'auto' });
  const monthSel = h('select', { 'aria-label': 'Month' }, h('option', { value: '' }, 'All months'),
    months.map((m) => h('option', { value: m, selected: m === st.month }, monthLabel(m, true))));
  const catSel = h('select', { 'aria-label': 'Category' }, h('option', { value: '' }, 'All categories'),
    cats.filter((c) => !c.archived).map((c) => h('option', { value: c.id, selected: String(c.id) === String(st.category_id) }, c.name)));
  const acctSel = h('select', { 'aria-label': 'Account' }, h('option', { value: '' }, 'All accounts'),
    accts.map((a) => h('option', { value: a.key, selected: a.key === st.account }, a.name || a.key)));
  const uncBtn = h('button.btn', { class: st.uncategorized ? 'on' : '', 'aria-pressed': st.uncategorized }, 'Uncategorized', uncCount ? badge(String(uncCount), 'warn') : null);
  const trBtn = h('button.btn', { class: st.include_transfers ? 'on' : '', 'aria-pressed': st.include_transfers }, 'Show transfers');

  root.append(h('div.page-head', h('h1', 'Transactions'), h('span.spacer'),
    uncCount ? h('button.btn.primary', { onclick: () => go('transactions', null, { review: 1 }) }, `Review ${uncCount} uncategorized`) : null));
  // "Review N new transactions" after an upload lands here: rows of that one import
  const importChip = st.import_id ? h('span.chip.filter-chip', 'New from the last upload',
    h('button.link', { 'aria-label': 'Clear filter', onclick: () => { st.import_id = ''; importChip.remove(); reload(); } }, '✕')) : null;
  root.append(h('div.toolbar', search, monthSel, catSel, acctSel, uncBtn, trBtn, importChip));

  const bulk = h('div.bulk-bar', { hidden: true });
  const table = new TxnTable({ onChanged: (kind) => { if (kind === 'bulk') load(); } });
  const summary = h('span');
  const more = h('button.btn.sm', { hidden: true, onclick: () => load(true) }, 'Load more');
  const foot = h('div.table-foot', summary, h('span.spacer'), more);
  const listBox = h('div', table.el);
  table.el.append(foot);
  root.append(bulk, listBox);

  let offset = 0, total = 0, seq = 0;
  async function load(append = false) {
    const my = ++seq;
    if (!append) { offset = 0; }
    setParams({ q: st.q, month: st.month, category_id: st.category_id, account: st.account, uncategorized: st.uncategorized ? 1 : '', include_transfers: st.include_transfers ? 1 : '', import_id: st.import_id });
    summary.textContent = 'Loading…';
    try {
      const r = await api.get('/transactions', { ...st, limit: PAGE, offset });
      if (my !== seq) return;
      const items = asList(r);
      total = r?.total ?? items.length;
      if (append) table.append(items); else table.set(items);
      offset += items.length;
      const shown = table.rows.size;
      if (!shown) {
        summary.textContent = '';
        table.tbody.append(h('tr', h('td', { colSpan: table.colCount },
          empty(st.uncategorized ? 'Everything is categorized' : 'No transactions match',
            st.uncategorized ? 'Nice. New imports with unknown merchants will show up here.' : 'Try clearing a filter, or import more data.'))));
      } else {
        const sum = [...table.rows.values()].reduce((s, { t }) => s + (t.is_transfer || t.is_excluded ? 0 : t.amount), 0);
        summary.textContent = `Showing ${shown} of ${total} · net of shown ${ils(sum)}`;
      }
      more.hidden = shown >= total;
    } catch (e) {
      if (my === seq) { summary.textContent = ''; toastError(e); }
    }
  }

  table.onSelect = () => {
    const n = table.selected.size;
    bulk.hidden = !n;
    if (!n) return;
    clear(bulk).append(h('b', `${n} selected`),
      h('button.btn.sm', { onclick: (e) => pickCategory(e.currentTarget, { onPick: (c) => bulkUpdate({ category_id: c.id }, `Moved to ${c.name}`) }) }, 'Recategorize…'),
      h('button.btn.sm', { onclick: () => bulkUpdate({ is_excluded: true }, 'Marked as one-off') }, 'Mark one-off'),
      h('button.btn.sm', { onclick: () => bulkUpdate({ is_excluded: false }, 'Included in budget') }, 'Include'),
      h('span.spacer'),
      h('button.btn.sm.ghost', { onclick: () => table.selectAll(false) }, 'Clear'));
  };
  async function bulkUpdate(body, msg) {
    const ids = [...table.selected];
    // optimistic
    for (const id of ids) {
      const r = table.rows.get(id);
      if (!r) continue;
      const t = { ...r.t };
      if (body.category_id !== undefined) { t.category_id = body.category_id; t.category_name = catById(body.category_id)?.name; t.category_source = 'manual'; }
      if (body.is_excluded !== undefined) t.is_excluded = body.is_excluded ? 1 : 0;
      table.update(t);
    }
    try {
      const r = await api.post('/transactions/bulk', { ids, ...body });
      // same "make it stick" step as a single edit: one rule per merchant, past + future
      const merchants = new Set(ids.map((id) => table.rows.get(id)?.t.merchant).filter(Boolean));
      const action = body.category_id !== undefined && merchants.size ? {
        label: merchants.size === 1 ? 'Always for this merchant' : `Always for these ${merchants.size} merchants`,
        fn: async () => {
          try {
            const rr = await api.post('/transactions/bulk', { ids, category_id: body.category_id, remember: true, apply_to_history: true });
            toast(`Rule${merchants.size === 1 ? '' : 's'} saved · ${rr?.updated_count ?? ids.length} transactions now ${catById(body.category_id)?.name || ''}`, { type: 'success' });
            changed('rules'); load();
          } catch (e) { toastError(e); }
        },
      } : undefined;
      toast(`${msg} · ${r?.updated_count ?? ids.length} transactions`, { type: 'success', action, ms: action ? 8000 : 3500 });
      table.selectAll(false);
      changed('txn');
    } catch (e) { toastError(e); load(); }
  }

  const reload = () => load();
  search.addEventListener('input', debounce(() => { st.q = search.value.trim(); reload(); }, 220));
  monthSel.addEventListener('change', () => { st.month = monthSel.value; reload(); });
  catSel.addEventListener('change', () => { st.category_id = catSel.value; reload(); });
  acctSel.addEventListener('change', () => { st.account = acctSel.value; reload(); });
  uncBtn.addEventListener('click', () => { st.uncategorized = !st.uncategorized; uncBtn.classList.toggle('on', st.uncategorized); reload(); });
  trBtn.addEventListener('click', () => { st.include_transfers = !st.include_transfers; trBtn.classList.toggle('on', st.include_transfers); reload(); });

  const onKey = (e) => {
    if (document.querySelector('.modal-wrap,.combo')) return;
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName);
    if (e.key === '/' && !typing) { e.preventDefault(); search.focus(); search.select(); }
    if (!typing && (e.key === '[' || e.key === ']') && st.month) {
      const next = addMonths(st.month, e.key === '[' ? -1 : 1);
      if (months.includes(next)) { st.month = next; monthSel.value = next; reload(); }
    }
  };
  document.addEventListener('keydown', onKey);
  await load();
  return () => document.removeEventListener('keydown', onKey);
}

// ---------------- Review mode ----------------
async function renderReview(root) {
  root.append(h('div.page-head', h('h1', 'Review'), h('span.muted', 'Uncategorized transactions, one at a time'), h('span.spacer'),
    h('button.btn.sm', { onclick: () => go('transactions') }, '← Back to table')));
  const box = h('div', spinner());
  root.append(box);
  const [cats, queueR, sampleR] = await Promise.all([
    categories(),
    api.get('/transactions', { uncategorized: 1, limit: 500 }),
    api.get('/transactions', { limit: 1000 }).catch(() => ({ items: [] })),
  ]);
  let queue = asList(queueR).filter((t) => isUncat(t) && !t.is_transfer);
  const sample = asList(sampleR).filter((t) => !isUncat(t));
  // merchant → category from history (fallback suggestion when the API gives none)
  const byMerchant = new Map();
  for (const t of sample) if (!byMerchant.has(t.merchant)) byMerchant.set(t.merchant, t.category_id);
  const counts = new Map();
  for (const t of sample) counts.set(t.category_id, (counts.get(t.category_id) || 0) + 1);
  const active = cats.filter((c) => !c.archived && c.kind !== 'transfer' && !isUncatCategory(c));
  const topFor = (amount) => {
    const want = amount > 0 ? (c) => c.kind === 'income' : (c) => c.kind !== 'income';
    const ranked = active.filter(want).sort((a, b) => (counts.get(b.id) || 0) - (counts.get(a.id) || 0));
    return ranked.slice(0, 9);
  };
  let remember = true, idx = 0, done = 0;

  function suggestion(t) {
    const id = t.suggested_category_id ?? byMerchant.get(t.merchant);
    const c = id ? catById(id) : null;
    return c && !isUncatCategory(c) ? c : null;
  }
  async function assign(t, cat) {
    const body = { category_id: cat.id };
    if (remember) { body.remember = true; body.apply_to_history = true; }
    queue.splice(idx, 1);
    done++;
    draw();
    try {
      const r = await api.patch('/transactions/' + encodeURIComponent(t.id), body);
      const extra = Math.max(0, (r?.updated_count ?? 1) - 1);
      if (remember) {
        const before = queue.length;
        queue = queue.filter((x) => x.merchant !== t.merchant);
        if (before !== queue.length) { idx = Math.min(idx, Math.max(queue.length - 1, 0)); draw(); }
      }
      toast(`${cat.name}${extra ? ` · rule applied to ${extra} more` : ''}`, {
        type: 'success', ms: 2500,
        action: { label: 'Undo', fn: async () => { await api.patch('/transactions/' + encodeURIComponent(t.id), { category_id: null }).catch(toastError); queue.splice(idx, 0, t); done--; draw(); changed('txn'); } },
      });
      changed('txn');
    } catch (e) {
      toastError(e);
      queue.splice(idx, 0, t); done--; draw();
    }
  }
  function skip() { if (queue.length) { idx = (idx + 1) % queue.length; draw(); } }

  function draw() {
    clear(box);
    if (!queue.length) {
      box.append(h('div.card.review-card', empty('All caught up', done ? `You categorized ${done} transaction${done === 1 ? '' : 's'}. Rules you saved apply to future imports.` : 'No uncategorized transactions.',
        h('a.btn.primary', { href: '#/month' }, 'Back to Month'))));
      return;
    }
    if (idx >= queue.length) idx = 0;
    const t = queue[idx];
    const sug = suggestion(t);
    const top = topFor(t.amount);
    const keys = h('div.review-keys', top.map((c, i) => h('button.btn', {
      class: sug && sug.id === c.id ? 'suggest' : '', onclick: () => assign(t, c),
    }, h('kbd', String(i + 1)), h('span.dot', { style: { background: c.color || 'var(--faint)' } }), c.name)));
    const other = h('button.btn', { onclick: (e) => pickCategory(e.currentTarget, { onPick: (c) => assign(t, c), includeTransfer: true }) }, h('kbd', 'C'), 'Other category…');
    box.append(h('div.card.review-card',
      h('div.row', h('span.muted.small', `${idx + 1} of ${queue.length} left`), h('span.spacer'), done ? h('span.small.good', `${done} done`) : null),
      h('div.review-txn', data(t.description)),
      h('div.row.wrap', h('span.muted', fmtDate(t.date, true)), h('span.muted', '·'), h('span.muted', t.account?.name || t.account?.key || ''), ...txnBadges(t)),
      h('div.review-amount', { class: t.amount > 0 ? 'good' : '' }, ils2(t.amount)),
      sug ? h('div.row', { style: { marginTop: '10px' } }, h('span.muted', 'Suggested:'), h('b', sug.name),
        h('button.btn.sm.primary', { onclick: () => assign(t, sug) }, 'Accept', h('kbd', '↵'))) : h('div.muted.small', { style: { marginTop: '10px' } }, 'No suggestion. Pick a category.'),
      keys,
      h('div.row.wrap', { style: { marginTop: '12px' } }, other,
        h('button.btn', { onclick: skip }, h('kbd', 'S'), 'Skip'),
        h('span.spacer'),
        h('label.check.small', h('input', { type: 'checkbox', checked: remember, onchange: (e) => { remember = e.target.checked; } }),
          'Remember for ', data(t.merchant || t.description), ' (all past + future)', h('kbd', 'R')))));
    box._current = { t, sug, top, other };
  }

  const onKey = (e) => {
    if (document.querySelector('.modal-wrap,.combo')) return;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) && e.target.type !== 'checkbox') return;
    const cur = box._current;
    if (!cur || !queue.length) return;
    if (/^[1-9]$/.test(e.key) && cur.top[+e.key - 1]) { e.preventDefault(); assign(cur.t, cur.top[+e.key - 1]); }
    else if (e.key === 'Enter' && cur.sug) { e.preventDefault(); assign(cur.t, cur.sug); }
    else if (e.key.toLowerCase() === 's') { e.preventDefault(); skip(); }
    else if (e.key.toLowerCase() === 'r') { e.preventDefault(); remember = !remember; draw(); }
    else if (e.key.toLowerCase() === 'c') { e.preventDefault(); cur.other.click(); }
  };
  document.addEventListener('keydown', onKey);
  draw();
  return () => document.removeEventListener('keydown', onKey);
}
