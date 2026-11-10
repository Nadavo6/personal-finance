// Tasks: money to-dos with estimated monthly savings.
import { h, clear, ils, fmtDate, today, toast, toastError, openModal, field, readForm, empty, asList, data } from './util.js';
import { api, categories, catById, changed } from './api.js';

const PRIOS = ['high', 'med', 'low'];
const PRIO_LABEL = { high: 'High', med: 'Medium', low: 'Low' };

export async function render(root, { params }) {
  let filter = params.status || 'open';
  let items = [], totals = {};
  await categories().catch(() => {});
  const header = h('div.row.small.muted');
  const seg = h('div.seg', ['open', 'done', 'all'].map((s) => h('button', { class: s === filter ? 'on' : '', onclick: () => { filter = s; [...seg.children].forEach((b) => b.classList.toggle('on', b.textContent.toLowerCase() === s)); draw(); } }, s[0].toUpperCase() + s.slice(1))));
  root.append(h('div.page-head', h('h1', 'Tasks'), header, h('span.spacer'), seg));

  const title = h('input', { name: 'title', placeholder: 'Add a task, e.g. “Call the mobile provider about the new price”', dir: 'auto', 'aria-label': 'Task title' });
  const est = h('input', { name: 'est', type: 'number', min: 0, step: 1, placeholder: '₪/mo saving', style: { width: '130px' }, 'aria-label': 'Estimated monthly saving' });
  const due = h('input', { name: 'due', type: 'date', 'aria-label': 'Due date' });
  const prio = h('select', { 'aria-label': 'Priority' }, PRIOS.map((p) => h('option', { value: p, selected: p === 'med' }, PRIO_LABEL[p])));
  const quick = h('form.quick-add', { onsubmit: (e) => { e.preventDefault(); add(); } }, title, est, due, prio, h('button.btn.primary', { type: 'submit' }, 'Add'));
  const list = h('div.card', { style: { padding: '6px 14px' } });
  root.append(quick, list);

  async function reload() {
    try {
      const r = await api.get('/tasks');
      items = asList(r);
      totals = Array.isArray(r) ? {} : r || {};
      draw();
    } catch (e) { clear(list).append(h('div.error-box', e.message)); }
  }
  function draw() {
    const open = items.filter((t) => (t.status || 'open') === 'open');
    const done = items.filter((t) => t.status === 'done');
    const pot = totals.total_est_monthly_saving ?? open.reduce((s, t) => s + (t.est_monthly_saving || 0), 0);
    const saved = totals.total_actual_monthly_saving ?? done.reduce((s, t) => s + (t.actual_monthly_saving || 0), 0);
    // per year, like the Review page (tasks store a monthly estimate)
    clear(header).append('Potential: ', h('b.num', `${ils(pot * 12)}/yr`), ' · Saved so far: ', h('b.num.good', `${ils(saved * 12)}/yr`));
    const shown = (filter === 'all' ? items : items.filter((t) => (t.status || 'open') === filter))
      .slice().sort((a, b) => PRIOS.indexOf(a.priority) - PRIOS.indexOf(b.priority) || (a.due_date || '9') .localeCompare(b.due_date || '9'));
    clear(list);
    if (!shown.length) {
      list.append(empty(filter === 'done' ? 'Nothing done yet' : 'No open tasks',
        filter === 'done' ? 'Completed tasks and what they actually save show here.' : 'Add one above, or turn a finding on the Review page into a task.',
        filter !== 'done' ? h('a.btn', { href: '#/insights' }, 'Go to Review') : null));
      return;
    }
    shown.forEach((t) => list.append(row(t)));
  }
  function row(t) {
    const isDone = t.status === 'done';
    const overdue = !isDone && t.due_date && t.due_date < today();
    const c = catById(t.category_id);
    return h('div.task', { class: isDone ? 'done' : '' },
      h('input', { type: 'checkbox', checked: isDone, 'aria-label': 'Done', onchange: (e) => (e.target.checked ? complete(t, e.target) : reopen(t)) }),
      h('div',
        h('div.row.wrap', h('span.prio', { class: t.priority || 'med', title: PRIO_LABEL[t.priority] || '' }), h('span.title', { dir: 'auto' }, t.title)),
        h('div.row.wrap.small.muted', { style: { marginTop: '2px' } },
          t.due_date ? h('span', { class: overdue ? 'bad' : '' }, `Due ${fmtDate(t.due_date, true)}`) : null,
          c ? h('span', c.name) : null,
          t.merchant ? data(t.merchant) : null,
          t.insight_key ? h('span', 'from Review') : null),
        t.notes ? h('div.small.muted', { dir: 'auto', style: { marginTop: '2px' } }, t.notes) : null),
      h('div', { style: { textAlign: 'right' } },
        isDone ? h('div.good.num', { title: t.actual_monthly_saving != null ? `${ils(t.actual_monthly_saving)}/mo` : null }, t.actual_monthly_saving != null ? `${ils(t.actual_monthly_saving * 12)}/yr saved` : 'done')
          : t.est_monthly_saving ? h('div.num', { title: `${ils(t.est_monthly_saving)}/mo` }, `~${ils(t.est_monthly_saving * 12)}/yr`) : null,
        h('button.btn.sm.ghost', { onclick: () => taskModal(t) }, 'Edit')));
  }
  async function add() {
    const v = title.value.trim();
    if (!v) { title.focus(); return; }
    const body = { title: v, est_monthly_saving: est.value ? +est.value : null, due_date: due.value || null, priority: prio.value, status: 'open' };
    const temp = { id: 'tmp' + Date.now(), ...body };
    items.unshift(temp); draw();
    title.value = ''; est.value = ''; due.value = '';
    try {
      const r = await api.post('/tasks', body);
      items[items.indexOf(temp)] = r?.id ? r : { ...temp };
      draw(); changed('tasks');
      if (!r?.id) reload();
    } catch (e) { items = items.filter((x) => x !== temp); draw(); toastError(e); }
    title.focus();
  }
  function complete(t, cb) {
    const input = h('input', { type: 'number', min: 0, step: 1, value: t.est_monthly_saving ?? '', placeholder: '₪ per month' });
    let saved = false;
    openModal('Nice, task done', h('div.stack', h('div', { dir: 'auto' }, h('b', t.title)),
      field('How much does it actually save per month? (₪)', input, t.est_monthly_saving ? `Estimated ${ils(t.est_monthly_saving)}/mo` : 'Leave empty if it saves nothing directly')), {
      actions: [{ label: 'Cancel', fn: (close) => { close(); cb.checked = false; } }, {
        label: 'Mark done', primary: true, fn: async (close) => {
          saved = true; close();
          await patch(t, { status: 'done', actual_monthly_saving: input.value === '' ? null : +input.value }, 'Task done');
        },
      }],
    });
    // closing via Esc/backdrop without saving restores the checkbox
    const wrap = document.querySelector('#modal-host .modal-wrap:last-child');
    new MutationObserver((_, obs) => { if (!wrap.isConnected) { obs.disconnect(); if (!saved) cb.checked = false; } }).observe(document.getElementById('modal-host'), { childList: true });
  }
  function reopen(t) { patch(t, { status: 'open', actual_monthly_saving: null }, 'Task reopened'); }
  async function patch(t, body, msg) {
    const i = items.indexOf(t);
    items[i] = { ...t, ...body }; draw();
    try {
      const r = await api.patch('/tasks/' + t.id, body);
      if (r?.id) items[i] = r;
      await reload();
      toast(msg, { type: 'success' }); changed('tasks');
    } catch (e) { items[i] = t; draw(); toastError(e); }
  }
  function taskModal(t) {
    const form = h('div.form-grid',
      h('div.full', field('Title', h('input', { name: 'title', required: true, value: t.title, dir: 'auto' }))),
      field('Due date', h('input', { name: 'due_date', type: 'date', value: t.due_date || '' })),
      field('Priority', h('select', { name: 'priority' }, PRIOS.map((p) => h('option', { value: p, selected: t.priority === p }, PRIO_LABEL[p])))),
      field('Est. saving ₪/mo', h('input', { name: 'est_monthly_saving', type: 'number', min: 0, step: 1, value: t.est_monthly_saving ?? '' })),
      field('Actual saving ₪/mo', h('input', { name: 'actual_monthly_saving', type: 'number', min: 0, step: 1, value: t.actual_monthly_saving ?? '' })),
      field('Status', h('select', { name: 'status' }, ['open', 'done', 'dropped'].map((s) => h('option', { value: s, selected: (t.status || 'open') === s }, s)))),
      h('div'),
      h('div.full', field('Notes', h('textarea', { name: 'notes', rows: 3, dir: 'auto' }, t.notes || ''))));
    openModal('Edit task', form, {
      actions: [
        { label: 'Delete', danger: true, fn: async (close) => {
          try { await api.del('/tasks/' + t.id); close(); items = items.filter((x) => x !== t); draw(); toast('Task deleted'); changed('tasks'); } catch (e) { toastError(e); }
        } },
        { label: 'Cancel' },
        { label: 'Save', primary: true, fn: async (close) => { close(); await patch(t, readForm(form), 'Saved'); } },
      ],
    });
  }
  await reload();
  setTimeout(() => title.focus(), 0);
}
