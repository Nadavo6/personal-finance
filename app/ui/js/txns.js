// Shared transaction table (Transactions page + drawers) and the category combobox.
import { h, data, clear, ils, ils2, money, fmtDate, toast, toastError, badge, openDrawer, spinner, empty, asList, openModal, field } from './util.js';
import { api, categories, categoriesCached, catById, changed, transactionsByIds, isUncat, isUncatCategory, invalidateCategories } from './api.js';

// ---------------- category combobox (type-ahead) ----------------
let openCombo = null;
export function closeCombo() { if (openCombo) { openCombo.remove(); openCombo = null; } }

/** Opens a type-ahead category picker anchored to `anchor`. onPick(category) */
export async function pickCategory(anchor, { current, onPick, includeTransfer = true } = {}) {
  closeCombo();
  const cats = (await categories()).filter((c) => !c.archived && !isUncatCategory(c) && (includeTransfer || c.kind !== 'transfer'));
  const input = h('input', { type: 'text', placeholder: 'Type to filter…', 'aria-label': 'Category' });
  const list = h('div.combo-list', { role: 'listbox' });
  const create = (name) => { closeCombo(); createCategory(name, { onCreated: onPick }); };
  const foot = h('button.combo-foot', { type: 'button', onmousedown: (e) => { e.preventDefault(); create(input.value.trim()); } }, '＋ New category');
  const box = h('div.combo', input, list, foot);
  let items = [], active = 0;
  const render = () => {
    const raw = input.value.trim(), q = raw.toLowerCase();
    items = cats.filter((c) => !q || c.name.toLowerCase().includes(q) || (c.name_he || '').includes(q));
    items.sort((a, b) => {
      const sa = a.name.toLowerCase().startsWith(q) ? 0 : 1, sb = b.name.toLowerCase().startsWith(q) ? 0 : 1;
      return sa - sb;
    });
    // typed a name that does not exist yet: offer to create it (Enter picks it when nothing matches)
    if (raw && !cats.some((c) => c.name.toLowerCase() === q || c.name_he === raw)) items.push({ create: raw });
    active = Math.min(active, Math.max(items.length - 1, 0));
    clear(list).append(...items.map((c, i) => h('div.combo-item', {
      class: [i === active ? 'active' : '', c.create ? 'create' : ''].join(' '), role: 'option', 'aria-selected': c.id === current,
      onmousedown: (e) => { e.preventDefault(); choose(c); },
      onmousemove: () => { if (active !== i) { active = i; render(); } },
    }, c.create ? h('span.ellipsis', '＋ Create category “', data(c.create), '”')
      : [h('span.dot', { style: { background: c.color || 'var(--faint)' } }), c.name, c.id === current ? ' ✓' : '',
        h('bdi.he', { dir: 'rtl' }, c.name_he || '')])));
    list.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
  };
  const choose = (c) => { if (c.create) { create(c.create); return; } closeCombo(); anchor.focus?.(); onPick(c); };
  input.addEventListener('input', () => { active = 0; render(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { active = Math.min(items.length - 1, active + 1); render(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { active = Math.max(0, active - 1); render(); e.preventDefault(); }
    else if (e.key === 'Enter') { if (items[active]) choose(items[active]); e.preventDefault(); }
    else if (e.key === 'Escape' || e.key === 'Tab') { closeCombo(); anchor.focus?.(); e.preventDefault(); e.stopPropagation(); }
  });
  input.addEventListener('blur', () => setTimeout(() => { if (openCombo === box) closeCombo(); }, 120));
  document.body.append(box);
  openCombo = box;
  const r = anchor.getBoundingClientRect();
  const top = r.bottom + 4 + 360 > innerHeight ? Math.max(8, r.top - 364) : r.bottom + 4;
  box.style.top = top + 'px';
  box.style.left = Math.min(r.left, innerWidth - 270) + 'px';
  render();
  input.focus();
}

const NEW_KINDS = [['variable', 'Everyday spending (gets a budget)'], ['fixed', 'Fixed bill (rent, insurance…)'],
  ['income', 'Income'], ['savings', 'Savings / investment'], ['tracking', 'Track only (no budget)']];
const PALETTE = ['#5b5bd6', '#1f9d63', '#c27c0e', '#d93d42', '#0e8fb8', '#a259d9', '#d9598c', '#6b7a8f'];

/** Small "new category" form. Calls onCreated(category) after saving (e.g. to apply it to a row). */
export function createCategory(name = '', { onCreated } = {}) {
  const nameIn = h('input', { name: 'name', required: true, value: name, dir: 'auto', placeholder: 'e.g. Apartment #2 fund' });
  const heIn = h('input', { name: 'name_he', dir: 'rtl', placeholder: 'שם בעברית (optional)' });
  const kindSel = h('select', { name: 'kind' }, NEW_KINDS.map(([k, l]) => h('option', { value: k }, l)));
  const budgetIn = h('input', { name: 'monthly_budget', type: 'number', min: 0, step: 1, placeholder: 'auto' });
  let color = PALETTE[Math.floor(Math.random() * PALETTE.length)];
  const swatches = h('div.swatches', PALETTE.map((c) => h('button.swatch', {
    type: 'button', style: { background: c }, 'aria-label': 'Colour ' + c, class: c === color ? 'on' : '',
    onclick: (e) => { color = c; swatches.querySelectorAll('.swatch').forEach((x) => x.classList.toggle('on', x === e.currentTarget)); },
  })));
  kindSel.addEventListener('change', () => { budgetIn.disabled = ['income', 'savings', 'tracking'].includes(kindSel.value); });
  const body = h('div.form-grid',
    h('div.full', field('Name', nameIn)),
    field('Kind', kindSel),
    field('Monthly budget ₪', budgetIn, 'Empty = learn it from your spending'),
    h('div.full', field('Hebrew name', heIn)),
    h('div.full', field('Colour', swatches)));
  openModal('New category', body, {
    actions: [{ label: 'Cancel' }, {
      label: onCreated ? 'Create and apply' : 'Create', primary: true, fn: async (close) => {
        const n = nameIn.value.trim();
        if (!n) { nameIn.focus(); return; }
        try {
          const c = await api.post('/categories', {
            name: n, name_he: heIn.value.trim() || null, kind: kindSel.value, color,
            monthly_budget: budgetIn.disabled || budgetIn.value === '' ? null : +budgetIn.value,
          });
          close();
          invalidateCategories(); await categories(true); changed('categories');
          toast(`Category “${c.name}” created`, { type: 'success' });
          onCreated?.(c);
        } catch (e) { toastError(e); }
      },
    }],
  });
  setTimeout(() => { nameIn.focus(); nameIn.select(); }, 0);
}

/** Small action menu anchored at (x, y): items [{label, fn}] */
function openMenu(x, y, items) {
  closeCombo();
  let active = 0;
  const box = h('div.combo.menu', { role: 'menu', tabindex: -1 });
  const draw = () => clear(box).append(...items.map((it, i) => h('div.combo-item', {
    class: i === active ? 'active' : '', role: 'menuitem',
    onmousedown: (e) => { e.preventDefault(); closeCombo(); it.fn(); },
    onmousemove: () => { if (active !== i) { active = i; draw(); } },
  }, it.label)));
  box.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { active = Math.min(items.length - 1, active + 1); draw(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { active = Math.max(0, active - 1); draw(); e.preventDefault(); }
    else if (e.key === 'Enter') { e.preventDefault(); closeCombo(); items[active].fn(); }
    else if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); closeCombo(); }
  });
  box.addEventListener('blur', () => setTimeout(() => { if (openCombo === box) closeCombo(); }, 120));
  draw();
  document.body.append(box);
  openCombo = box;
  const r = box.getBoundingClientRect();
  box.style.top = Math.min(y + 4, innerHeight - r.height - 8) + 'px';
  box.style.left = Math.min(x, innerWidth - r.width - 8) + 'px';
  box.focus();
}

// ---------------- badges ----------------
export function txnBadges(t) {
  const out = [];
  if (t.account?.kind === 'gift') out.push(badge('paid by gift card', 'accent'));
  if (t.is_transfer) out.push(badge('transfer'));
  if (t.is_excluded && t.account?.kind !== 'gift') out.push(badge('one-off', 'warn'));
  if (t.installment_total) out.push(badge(`installment ${t.installment_no ?? '?'}/${t.installment_total}`));
  if (t.original_currency && t.original_currency !== 'ILS') out.push(badge(money(t.original_amount, t.original_currency), 'accent'));
  return out;
}

/**
 * TxnTable: renders rows, keeps each row's DOM so edits update in place (no full re-render).
 * opts: {selectable, compact, onChanged(kind), showAccount}
 */
export class TxnTable {
  constructor(opts = {}) {
    this.opts = { selectable: true, showAccount: true, ...opts };
    this.rows = new Map(); // id -> {t, tr}
    this.selected = new Set();
    this.tbody = h('tbody');
    const head = h('tr',
      this.opts.selectable ? h('th', { style: { width: '28px' } }, this.selAll = h('input', { type: 'checkbox', 'aria-label': 'Select all', onchange: (e) => this.selectAll(e.target.checked) })) : null,
      h('th', { style: { width: '74px' } }, 'Date'),
      h('th', 'Description'),
      h('th', { style: { width: '180px' } }, 'Category ', h('span.th-hint', 'click to change')),
      this.opts.showAccount ? h('th', { style: { width: '130px' } }, 'Account') : null,
      h('th.num', { style: { width: '110px' } }, 'Amount'),
      h('th', { style: { width: '92px' } }));
    this.table = h('table', h('thead', head), this.tbody);
    this.el = h('div.table-wrap', this.table);
    this.onSelect = () => {};
  }
  get colCount() { return 5 + (this.opts.selectable ? 1 : 0) + (this.opts.showAccount ? 1 : 0); }
  clear() { clear(this.tbody); this.rows.clear(); this.selected.clear(); this.onSelect(); if (this.selAll) this.selAll.checked = false; }
  set(items) { this.clear(); this.append(items); }
  append(items) {
    const frag = document.createDocumentFragment();
    for (const t of items) {
      const tr = this.renderRow(t);
      this.rows.set(t.id, { t, tr });
      frag.append(tr);
    }
    this.tbody.append(frag);
  }
  selectAll(on) {
    for (const [id, { tr }] of this.rows) {
      on ? this.selected.add(id) : this.selected.delete(id);
      tr.classList.toggle('selected', on);
      const cb = tr.querySelector('input[type=checkbox]');
      if (cb) cb.checked = on;
    }
    this.onSelect();
  }
  update(t) {
    const r = this.rows.get(t.id);
    if (!r) return;
    const tr = this.renderRow(t);
    if (this.selected.has(t.id)) { tr.classList.add('selected'); tr.querySelector('input[type=checkbox]').checked = true; }
    r.tr.replaceWith(tr);
    r.t = t; r.tr = tr;
  }
  renderRow(t) {
    const c = catById(t.category_id);
    const uncategorized = isUncat(t);
    const catBtn = h('button.cat-pill', {
      class: uncategorized ? 'uncat' : '', title: 'Change category',
      onclick: (e) => pickCategory(e.currentTarget, { current: t.category_id, onPick: (cat) => this.setCategory(t, cat) }),
    }, h('span.dot', { style: { background: c?.color || 'var(--faint)' } }), h('span.cat-pill-name', c?.name || t.category_name || 'Uncategorized'), h('span.chev', { 'aria-hidden': 'true' }, '▾'));
    const isPos = t.amount > 0;
    const tr = h('tr.txn-row', {
      class: t.is_excluded || t.is_transfer ? 'muted-row' : '', dataset: { id: t.id },
      // a click anywhere on the row (not on a control) opens the edit menu
      onclick: (e) => { if (!e.target.closest('button,input,a,select,label')) this.rowMenu(e, t); },
    },
      this.opts.selectable ? h('td', h('input', {
        type: 'checkbox', 'aria-label': 'Select', checked: this.selected.has(t.id),
        onchange: (e) => { e.target.checked ? this.selected.add(t.id) : this.selected.delete(t.id); tr.classList.toggle('selected', e.target.checked); this.onSelect(); },
      })) : null,
      h('td.num.muted', { style: { textAlign: 'left' } }, fmtDate(t.date)),
      h('td', h('div.txn-desc', data(t.description), ...txnBadges(t)),
        t.notes ? h('div.note-text', data(t.notes)) : null),
      h('td', catBtn),
      this.opts.showAccount ? h('td.small.muted.acct-cell', { title: t.account?.name || t.account?.key || '' }, t.account?.name || t.account?.key || '') : null,
      h('td.num', { class: isPos ? 'amount-pos' : '' }, ils2(t.amount)),
      h('td.txn-actions',
        h('button.icon-btn', { class: t.is_excluded ? 'on' : '', title: t.is_excluded ? 'Counted as one-off (excluded). Click to include' : 'Mark as one-off (exclude from budget)', 'aria-label': t.is_excluded ? 'Include in budget' : 'Mark as one-off', onclick: () => this.patch(t, { is_excluded: !t.is_excluded }) }, '⊘'),
        h('button.icon-btn', { class: t.is_transfer ? 'on' : '', title: t.is_transfer ? 'Transfer. Click to unmark' : 'Mark as transfer', 'aria-label': t.is_transfer ? 'Not a transfer' : 'Mark as transfer', onclick: () => this.patch(t, { is_transfer: !t.is_transfer }) }, '⇄'),
        h('button.icon-btn', { class: t.notes ? 'on' : '', title: 'Note', 'aria-label': t.notes ? 'Edit note' : 'Add note', onclick: () => this.editNote(t) }, '✎')));
    return tr;
  }
  rowMenu(e, t) {
    const pill = () => this.rows.get(t.id)?.tr.querySelector('.cat-pill');
    const cat = catById(t.category_id);
    const merchant = t.merchant || t.description;
    openMenu(e.clientX, e.clientY, [
      { label: 'Change category…', fn: () => pill()?.click() },
      { label: t.is_excluded ? 'Count in budget again' : 'Mark as one-off', fn: () => this.patch(t, { is_excluded: !t.is_excluded }) },
      { label: t.is_transfer ? 'Not a transfer' : 'Mark as transfer', fn: () => this.patch(t, { is_transfer: !t.is_transfer }) },
      { label: t.notes ? 'Edit note' : 'Add note', fn: () => this.editNote(t) },
      cat && !isUncat(t) ? { label: h('span', 'Always use ', h('b', cat.name), ' for ', data(merchant)), fn: () => this.showLearnPrompt(t, cat) } : null,
    ].filter(Boolean));
  }
  async patch(t, body, { quiet = false } = {}) {
    const before = { ...t };
    const optimistic = { ...t, ...Object.fromEntries(Object.entries(body).filter(([k]) => !['remember', 'apply_to_history'].includes(k)).map(([k, v]) => [k, typeof v === 'boolean' ? (v ? 1 : 0) : v])) };
    if (body.category_id !== undefined) optimistic.category_name = catById(body.category_id)?.name ?? null;
    this.update(optimistic);
    try {
      const r = await api.patch('/transactions/' + encodeURIComponent(t.id), body);
      const fresh = r?.txn || optimistic;
      this.update(fresh);
      if (!quiet) {
        if (body.is_excluded !== undefined) toast(body.is_excluded ? 'Marked as one-off' : 'Included in budget again', { action: { label: 'Undo', fn: () => this.patch(fresh, { is_excluded: !body.is_excluded }, { quiet: true }) } });
        if (body.is_transfer !== undefined) toast(body.is_transfer ? 'Marked as transfer' : 'No longer a transfer');
      }
      changed('txn');
      this.opts.onChanged?.('txn', fresh);
      return r;
    } catch (e) {
      this.update(before);
      toastError(e);
      return null;
    }
  }
  async setCategory(t, cat) {
    if (cat.id === t.category_id) return;
    const r = await this.patch(t, { category_id: cat.id }, { quiet: true });
    if (!r) return;
    const fresh = r.txn || { ...t, category_id: cat.id };
    // undo: a rule-given category comes back by re-running the rules (category_id: null)
    const back = t.category_source === 'manual' ? t.category_id : null;
    toast(`Moved to ${cat.name}`, { action: { label: 'Undo', fn: () => { this.tbody.querySelectorAll('tr.prompt-row').forEach((x) => x.remove()); this.patch(this.rows.get(t.id)?.t || fresh, { category_id: back }, { quiet: true }); } } });
    this.showLearnPrompt(fresh, cat);
  }
  showLearnPrompt(t, cat) {
    this.tbody.querySelectorAll('tr.prompt-row').forEach((x) => x.remove());
    const row = this.rows.get(t.id);
    if (!row) return;
    const merchant = t.merchant || t.description;
    const close = () => pr.remove();
    const doRule = async () => {
      close();
      try {
        const r = await api.patch('/transactions/' + encodeURIComponent(t.id), { category_id: cat.id, remember: true, apply_to_history: true });
        const n = Math.max(0, (r?.updated_count ?? 1) - 1);
        toast(`Rule saved: ${merchant} → ${cat.name}${n ? ` · ${n} other transaction${n === 1 ? '' : 's'} updated` : ''}`, { type: 'success' });
        changed('rules');
        if (n) this.opts.onChanged?.('bulk');
      } catch (e) { toastError(e); }
    };
    const text = h('span', 'Checking other ', data(merchant), ' transactions…');
    const yes = h('button.btn.sm.primary', { onclick: doRule }, 'Yes, all + future');
    const pr = h('tr.prompt-row', h('td', { colSpan: this.colCount },
      h('div.row.wrap', text, h('span.spacer'),
        h('button.btn.sm', { onclick: close }, 'Just this one'), yes)));
    row.tr.after(pr);
    // live count of the other rows the rule would change (manual edits are never touched)
    api.post('/rules/preview', { pattern: merchant, match_type: 'contains', category_id: cat.id }).then((p) => {
      const n = p?.would_change ?? 0;
      clear(text).append(...(n
        ? ['Apply ', h('b', cat.name), ` to all ${n} other `, data(merchant), ' transaction' + (n === 1 ? '' : 's'), ' and future ones?']
        : ['Use ', h('b', cat.name), ' for future ', data(merchant), ' transactions too?']));
      yes.textContent = n ? `Yes, all (${n} + future)` : 'Yes, remember';
    }).catch(() => { clear(text).append('Use ', h('b', cat.name), ' for all ', data(merchant), ' transactions, past and future?'); });
  }
  editNote(t) {
    const row = this.rows.get(t.id);
    if (!row) return;
    const cell = row.tr.children[this.opts.selectable ? 2 : 1];
    const input = h('input', { type: 'text', value: t.notes || '', placeholder: 'Add a note…', dir: 'auto', style: { width: '100%', marginTop: '4px' } });
    const save = () => {
      const v = input.value.trim() || null;
      if (v === (t.notes || null)) { this.update(t); return; }
      this.patch(t, { notes: v }, { quiet: true }).then((r) => r && toast('Note saved'));
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      if (e.key === 'Escape') { e.stopPropagation(); input.value = t.notes || ''; input.blur(); }
    });
    input.addEventListener('blur', save, { once: true });
    cell.querySelector('.note-text')?.remove();
    cell.append(input);
    input.focus();
  }
}

/** Drawer listing transactions. query: params for /api/transactions, or {ids:[...]}; filter: optional client-side predicate */
export async function openTxnDrawer(title, query, { subtitle, filter, onClose, autoPick = false } = {}) {
  const body = h('div', spinner());
  openDrawer(title, body, { subtitle, onClose });
  await categories().catch(() => {});
  try {
    let items;
    if (query.ids) items = await transactionsByIds(query.ids, query.include_transfers === 0 ? {} : {});
    else items = asList(await api.get('/transactions', { limit: 2000, ...query }));
    if (filter) items = items.filter(filter);
    clear(body);
    if (!items.length) { body.append(empty('No transactions', 'Nothing matches this figure yet.')); return; }
    const total = items.reduce((s, t) => s + (t.is_transfer || t.is_excluded ? 0 : t.amount), 0);
    const table = new TxnTable({ selectable: false, showAccount: true });
    table.set(items);
    body.append(h('div.row', { style: { marginBottom: '10px' } },
      h('span.muted', `${items.length} transaction${items.length === 1 ? '' : 's'}`), h('span.spacer'),
      h('span', 'Total ', h('b.num', ils(total)))), table.el);
    // "Fix category": open the picker on the (first) row straight away
    if (autoPick) setTimeout(() => table.tbody.querySelector('.cat-pill')?.click(), 220);
  } catch (e) {
    clear(body).append(h('div.error-box', e.message));
  }
}

export { categoriesCached };
