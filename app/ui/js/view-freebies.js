// Freebies: perks, vouchers, gift cards — sorted by expiry with days-left chips and usage.
import { h, clear, ils, num, fmtDate, today, toast, toastError, openModal, field, readForm, empty, progress, badge, asList } from './util.js';
import { api, accounts, changed } from './api.js';
import { openTxnDrawer } from './txns.js';

const SOURCES = { work: 'Work', reserve: 'Reserve duty', gift: 'Gift', other: 'Other' };
const SRC_CLS = { work: 'accent', reserve: 'good', gift: 'warn', other: '' };
/** Manual part of used_amount (server adds linked gift-card spending on top). */
const manualUsed = (f) => f.manual_used_amount ?? Math.max(0, (f.used_amount || 0) - (f.linked_used_amount ?? f.linked_used ?? 0));
const fmtVal = (v, unit) => (unit === 'ILS' || !unit ? ils(v) : `${num(v)} ${unit}`);

export async function render(root) {
  let showOld = false;
  const list = h('div');
  const oldBtn = h('button.btn.sm', { onclick: () => { showOld = !showOld; oldBtn.classList.toggle('on', showOld); draw(); } }, 'Show used & expired');
  root.append(h('div.page-head', h('h1', 'Freebies'), h('span.sub', 'Perks, vouchers and gift cards before they expire'), h('span.spacer'), oldBtn,
    h('button.btn.primary', { onclick: () => freebieModal(null, reload) }, '+ Add freebie')));
  root.append(list);
  let items = [];
  const accts = await accounts().catch(() => []);

  async function reload() {
    try { items = asList(await api.get('/freebies')); draw(); } catch (e) { clear(list).append(h('div.error-box', e.message)); }
  }
  function draw() {
    clear(list);
    const live = items.filter((f) => (f.status || 'active') === 'active');
    const old = items.filter((f) => !live.includes(f));
    if (!items.length) {
      list.append(h('div.card', empty('No freebies tracked', 'Add work perks, reserve-duty vouchers and gift cards so you use them before they expire.',
        h('button.btn.primary', { onclick: () => freebieModal(null, reload) }, 'Add the first one'))));
      return;
    }
    const totalLeft = live.filter((f) => !f.unit || f.unit === 'ILS').reduce((s, f) => s + (f.remaining ?? (f.value - (f.used_amount || 0))), 0);
    list.append(h('div.row.small.muted', { style: { marginBottom: '10px' } }, `${live.length} active · `, h('b.num', ils(totalLeft)), ' unused value'));
    if (!live.length) list.append(h('div.card', empty('Nothing active', 'Everything is used or expired.')));
    list.append(h('div.stack', live.map((f) => card(f))));
    if (showOld && old.length) list.append(h('h3', { style: { margin: '24px 0 10px' } }, 'Used & expired'), h('div.stack', old.map((f) => card(f, true))));
  }
  function card(f, dim = false) {
    const used = f.used_amount || 0;
    const remaining = f.remaining ?? Math.max(0, (f.value || 0) - used);
    const p = f.value ? (used / f.value) * 100 : 0;
    const dl = f.days_left;
    const chip = dl == null ? h('span.chip.days-chip', 'No expiry')
      : dl < 0 ? h('span.chip.days-chip.soon', 'Expired')
        : h('span.chip.days-chip', { class: dl < 14 ? 'soon' : dl < 30 ? 'mid' : '' }, dl === 0 ? 'Expires today' : `${dl} day${dl === 1 ? '' : 's'} left`);
    const linked = f.account_id ? accts.find((a) => a.id === f.account_id) : null;
    return h('div.card.freebie', { class: dim ? 'dimmed' : '' },
      h('div.stack',
        h('div.row.wrap', h('span.title', { dir: 'auto' }, f.name), badge(SOURCES[f.source] || f.source || 'Other', SRC_CLS[f.source] || ''),
          f.status && f.status !== 'active' ? badge(f.status, f.status === 'used' ? 'good' : 'bad') : null),
        h('div.vals',
          h('span', 'Value ', h('b', fmtVal(f.value, f.unit))),
          h('span', 'Used ', h('b', fmtVal(used, f.unit))),
          h('span', 'Remaining ', h('b', { class: remaining > 0 ? '' : 'faint' }, fmtVal(remaining, f.unit))),
          f.expiry_date ? h('span', 'Expires ', h('b', fmtDate(f.expiry_date, true))) : null),
        progress(p, p >= 100 ? 'good' : ''),
        f.where_to_use || f.notes ? h('div.small.muted', { dir: 'auto' }, [f.where_to_use, f.notes].filter(Boolean).join(' · ')) : null,
        linked ? h('div.small', '↳ Tracks spending on ', h('button.link', { onclick: () => openTxnDrawer(linked.name, { account: linked.key, include_transfers: 1 }, { subtitle: 'Gift card spending' }) }, linked.name), ' automatically') : null),
      h('div', { style: { display: 'flex', flexDirection: 'column', gap: '8px', alignItems: 'flex-end' } },
        chip,
        h('div.row',
          f.status === 'active' || !f.status ? h('button.btn.sm', { onclick: () => partialUse(f) }, 'Use some…') : null,
          f.status === 'active' || !f.status ? h('button.btn.sm', { onclick: () => markUsed(f) }, 'Mark used') : null,
          h('button.btn.sm.ghost', { onclick: () => freebieModal(f, reload, accts) }, 'Edit'))));
  }
  async function patch(f, body, msg) {
    const i = items.indexOf(f);
    const optimistic = { ...f, ...body };
    if (body.used_amount != null) optimistic.remaining = Math.max(0, f.value - body.used_amount);
    items[i] = optimistic; draw();
    try {
      const r = await api.patch('/freebies/' + f.id, body);
      if (r && r.id) items[i] = r;
      draw(); toast(msg, { type: 'success' }); changed('freebies');
    } catch (e) { items[i] = f; draw(); toastError(e); }
  }
  function markUsed(f) { patch(f, f.account_id ? { status: 'used' } : { status: 'used', used_amount: f.value }, `“${f.name}” marked used`); }
  function partialUse(f) {
    const input = h('input', { type: 'number', min: 0, step: 'any', required: true, placeholder: f.unit === 'ILS' || !f.unit ? '₪' : f.unit });
    const remaining = f.remaining ?? (f.value - (f.used_amount || 0));
    openModal('Record use', h('div.stack', h('div', { dir: 'auto' }, h('b', f.name)), field(`How much did you use? (${f.unit || 'ILS'})`, input, `Remaining ${fmtVal(remaining, f.unit)}`)), {
      actions: [{ label: 'Cancel' }, {
        label: 'Save', primary: true, fn: (close) => {
          const v = +input.value; if (!v) return;
          close();
          // used_amount is the manual part; linked gift-card spending is added by the server.
          const manualBefore = manualUsed(f);
          const used = manualBefore + v;
          patch(f, { used_amount: used, ...(used >= f.value ? { status: 'used' } : {}) }, `Recorded ${fmtVal(v, f.unit)}`);
        },
      }],
    });
  }
  await reload();
}

function freebieModal(f, reload, accts = []) {
  const v = { name: '', source: 'work', value: '', unit: 'ILS', received_date: today(), expiry_date: '', used_amount: 0, status: 'active', where_to_use: '', notes: '', account_id: null, ...(f || {}) };
  const gift = accts.filter((a) => a.kind === 'gift');
  const form = h('div.form-grid',
    h('div.full', field('Name', h('input', { name: 'name', required: true, value: v.name, dir: 'auto', placeholder: 'e.g. Holiday gift card' }))),
    field('Source', h('select', { name: 'source' }, Object.entries(SOURCES).map(([k, l]) => h('option', { value: k, selected: v.source === k }, l)))),
    field('Status', h('select', { name: 'status' }, ['active', 'used', 'expired'].map((s) => h('option', { value: s, selected: v.status === s }, s)))),
    field('Value', h('input', { name: 'value', type: 'number', min: 0, step: 'any', required: true, value: v.value })),
    field('Unit', h('select', { name: 'unit' }, ['ILS', 'days', 'uses', 'months', 'items'].map((u) => h('option', { value: u, selected: v.unit === u }, u)))),
    field('Received', h('input', { name: 'received_date', type: 'date', value: v.received_date || '' })),
    field('Expires', h('input', { name: 'expiry_date', type: 'date', value: v.expiry_date || '' }), 'Leave empty if it never expires'),
    field('Used so far', h('input', { name: 'used_amount', type: 'number', min: 0, step: 'any', value: f ? manualUsed(f) : 0 }), f?.account_id ? 'Manual part only; linked card spending is added automatically' : null),
    field('Linked gift-card account', h('select', { name: 'account_id' }, h('option', { value: '' }, gift.length ? 'None' : 'No gift accounts imported'),
      gift.map((a) => h('option', { value: a.id, selected: v.account_id === a.id }, a.name || a.key))), 'Spending on it counts as used'),
    h('div.full', field('Where to use', h('input', { name: 'where_to_use', value: v.where_to_use || '', dir: 'auto' }))),
    h('div.full', field('Notes', h('textarea', { name: 'notes', rows: 2, dir: 'auto' }, v.notes || ''))));
  const actions = [];
  if (f) actions.push({ label: 'Delete', danger: true, fn: async (close) => {
    if (!confirm(`Delete “${f.name}”?`)) return;
    try { await api.del('/freebies/' + f.id); close(); toast('Deleted'); changed('freebies'); reload(); } catch (e) { toastError(e); }
  } });
  actions.push({ label: 'Cancel' }, {
    label: f ? 'Save' : 'Add', primary: true, fn: async (close) => {
      const body = readForm(form);
      body.account_id = body.account_id ? +body.account_id : null;
      try {
        if (f) await api.patch('/freebies/' + f.id, body); else await api.post('/freebies', body);
        close(); toast(f ? 'Saved' : 'Freebie added', { type: 'success' }); changed('freebies'); reload();
      } catch (e) { toastError(e); }
    },
  });
  openModal(f ? 'Edit freebie' : 'Add freebie', form, { actions, width: '560px' });
}
