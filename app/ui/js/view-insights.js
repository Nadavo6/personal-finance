// Review (insights): findings grouped by severity; urgent money alerts first, then by yearly saving.
import { h, mixed, clear, ils, toast, toastError, empty, badge, asList } from './util.js';
import { api, changed } from './api.js';
import { openTxnDrawer } from './txns.js';

const SEV = [
  { key: 'high', label: 'Act now', cls: 'bad' },
  { key: 'med', label: 'Worth a look', cls: 'warn' },
  { key: 'low', label: 'Ideas', cls: '' },
];
export const TYPE_LABEL = {
  duplicate_charge: 'Double charge', card_fee: 'Card fee', bank_fee: 'Bank fee', subscription: 'Subscription',
  price_increase: 'Price increase', category_creep: 'Spending creep', top_discretionary: 'Discretionary',
  fx_heavy: 'Foreign currency', insurance_review: 'Insurance', ai_review: 'Review note',
  rent_overdue: 'Rent', overdraft_risk: 'Cash flow', unusual_inflow: 'Large inflow',
};
// Alerts that save nothing but need action: shown with a "why now" chip instead of ₪0/year
const ALERT_LABEL = { rent_overdue: 'Money owed', overdraft_risk: 'Cash risk', unusual_inflow: 'Check income', duplicate_charge: 'Possible refund' };
/** Plain label for an insight type (never the raw snake_case tag). */
export const typeLabel = (t) => TYPE_LABEL[t] || (t ? t.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : 'Finding');
export const yearly = (i) => i.est_yearly_saving ?? (i.est_monthly_saving || 0) * 12;
const SEV_RANK = { high: 0, med: 1, low: 2 };
/** Urgency first (severity; within it, money alerts before savings ideas), then money impact. */
export function rankInsights(items) {
  return items.slice().sort((a, b) => (SEV_RANK[a.severity || 'low'] - SEV_RANK[b.severity || 'low'])
    || ((yearly(a) ? 1 : 0) - (yearly(b) ? 1 : 0)) || (yearly(b) - yearly(a)));
}
export function impact(i) {
  const y = yearly(i);
  return y ? h('div', h('div.yearly', ils(y)), h('div.small.muted.right', 'per year'))
    : h('div.right', badge(ALERT_LABEL[i.type] || 'Act on it', i.severity === 'high' ? 'bad' : 'warn'), h('div.small.muted', { style: { marginTop: '4px' } }, 'why now'));
}

export async function render(root) {
  const header = h('span.sub');
  root.append(h('div.page-head', h('h1', 'Review'), header));
  const box = h('div');
  root.append(box);
  let items = [];
  try { items = asList(await api.get('/insights')); } catch (e) { box.append(h('div.error-box', e.message)); return; }

  function draw() {
    clear(box);
    const total = items.reduce((s, i) => s + yearly(i), 0);
    clear(header).append('Potential savings ', h('b.num.good', `${ils(total)}/year`), ` · ${items.length} finding${items.length === 1 ? '' : 's'}`);
    if (!items.length) {
      box.append(h('div.card', empty('Nothing to review', 'No fees, double charges, price rises or creeping categories found in the last 12 months. Check back after the next import.')));
      return;
    }
    for (const sev of SEV) {
      const group = rankInsights(items.filter((i) => (i.severity || 'low') === sev.key));
      if (!group.length) continue;
      const sum = group.reduce((s, i) => s + yearly(i), 0);
      box.append(h('div.sev-head', h('h3', sev.label), badge(String(group.length), sev.cls), h('span.spacer'), h('span.small.muted', `${ils(sum)}/yr`)),
        h('div.stack', group.map(card)));
    }
  }
  function card(i) {
    const actions = h('div.actions',
      i.txn_ids?.length ? h('button.btn.sm', { onclick: () => openTxnDrawer(i.title, { ids: i.txn_ids }, { subtitle: i.detail }) }, `Show ${i.txn_ids.length} transaction${i.txn_ids.length === 1 ? '' : 's'}`) : null,
      h('button.btn.sm', { onclick: (e) => makeTask(i, e.currentTarget) }, 'Make it a task'),
      h('span.spacer'),
      h('button.btn.sm.ghost', { onclick: () => dismissPrompt(i, actions) }, 'Dismiss'));
    return h('div.card.insight',
      h('div',
        h('div.row.wrap', badge(typeLabel(i.type)), h('span.title', mixed(i.title))),
        h('div.muted', { style: { marginTop: '4px' } }, mixed(i.detail))),
      impact(i),
      actions);
  }
  async function makeTask(i, btn) {
    btn.disabled = true;
    try {
      await api.post('/insights/' + encodeURIComponent(i.key) + '/task', {});
      // collapse the card: the finding now lives in Tasks
      const card = btn.closest('.insight');
      card.replaceWith(h('div.card.insight-moved', h('span.good', '✓'), ' Added to Tasks: ', h('span', mixed(i.title)), h('span.spacer'), h('a', { href: '#/tasks' }, 'Open Tasks →')));
      changed('tasks');
    } catch (e) { btn.disabled = false; toastError(e); }
  }
  function dismissPrompt(i, actions) {
    const reason = h('input', { type: 'text', placeholder: 'Reason (optional), e.g. “intentional”', dir: 'auto', style: { flex: 1, minWidth: '200px' } });
    const go = async () => {
      const idx = items.indexOf(i);
      items.splice(idx, 1); draw();
      try {
        await api.post('/insights/' + encodeURIComponent(i.key) + '/dismiss', { reason: reason.value.trim() || null });
        toast('Dismissed. It won’t come back.');
        changed('insights');
      } catch (e) { items.splice(idx, 0, i); draw(); toastError(e); }
    };
    reason.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); if (e.key === 'Escape') { e.stopPropagation(); draw(); } });
    clear(actions).append(reason, h('button.btn.sm.primary', { onclick: go }, 'Dismiss'), h('button.btn.sm.ghost', { onclick: draw }, 'Cancel'));
    reason.focus();
  }
  draw();
}
