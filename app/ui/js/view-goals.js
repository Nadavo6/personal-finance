// Goals (virtual envelopes) + "Can I afford it?"
import { h, clear, ils, pct, monthLabel, addMonths, thisMonth, fmtDate, monthEnd, toast, toastError, openModal, field, readForm, empty, progress, badge, asList, shortMonth } from './util.js';
import { api, changed } from './api.js';
import { lineChart } from './charts.js';

export async function render(root, { params }) {
  root.append(h('div.page-head', h('h1', 'Goals'), h('span.sub', 'Each goal reserves its scheduled monthly contribution from left-to-spend'), h('span.spacer'),
    h('button.btn.primary', { onclick: () => goalModal(null, reload) }, '+ New goal')));
  const list = h('div');
  const afford = h('div.card.section');
  root.append(list, afford);

  async function reload() {
    try {
      const [goals, ends] = await Promise.all([api.get('/goals'), api.get('/fixed-ends').catch(() => [])]);
      drawGoals(list, asList(goals), reload, asList(ends));
    } catch (e) { clear(list).append(h('div.error-box', e.message)); }
  }
  await reload();
  drawAfford(afford, params, reload);
}

function drawGoals(box, goals, reload, ends = []) {
  clear(box);
  const active = goals.filter((g) => (g.status || 'active') === 'active');
  const rest = goals.filter((g) => !active.includes(g));
  if (!goals.length) {
    box.append(h('div.card', empty('No goals yet', 'A goal is a target amount by a date, like a trip or a new laptop. Its monthly need is set aside from your left-to-spend.',
      h('button.btn.primary', { onclick: () => goalModal(null, reload) }, 'Create a goal'))));
    return;
  }
  const totalNeed = active.reduce((s, g) => s + (g.contribution_now ?? g.monthly_need ?? 0), 0);
  box.append(h('div.row.small.muted', { style: { marginBottom: '10px' } }, `${active.length} active · reserving `, h('b.num', ils(totalNeed)), ' a month'));
  box.append(h('div.cards', active.map((g) => goalCard(g, reload, ends))));
  if (rest.length) {
    box.append(h('h3', { style: { margin: '24px 0 10px' } }, 'Done & archived'), h('div.cards', rest.map((g) => goalCard(g, reload, ends))));
  }
}

// "Broker Account (חשבון ברוקר)" -> "Broker Account": the Latin part of an asset name
function shortName(name) {
  const m = String(name || '').match(/^([^(]+?)\s*\(/);
  return (m ? m[1] : String(name || '')).split(/\s+/).slice(0, 2).join(' ');
}

// "2027-01" -> "Jan 2027"
const mon = (m) => monthLabel(m, true).replace(/^(\w{3})\w*/, '$1');

// "Car loan ends — redirect to the trip fund" -> "car loan ends"; else "<fixed end> ends"
function stepLabel(s) {
  const r = String(s.reason || '').split(/\s+[—–-]\s+/)[0].trim();
  if (r) return r.charAt(0).toLowerCase() + r.slice(1);
  return s.fixed_end ? `${String(s.fixed_end.what || '').toLowerCase()} ends` : '';
}

/** "₪1,500/mo now → ₪2,700/mo from Jan 2027 (car loan ends)" */
function scheduleLine(g) {
  const steps = asList(g.step_ups);
  if (!steps.length) return null;
  const base = g.monthly_contribution ?? g.monthly_need ?? 0;
  const now = thisMonth();
  const parts = [h('b.num', `${ils(g.contribution_now ?? base)}/mo`), h('span.muted', ' now')];
  let run = base + steps.filter((s) => s.from_month <= now).reduce((t, s) => t + (s.add_amount || 0), 0);
  for (const s of steps.filter((x) => x.from_month > now)) {
    run += s.add_amount || 0;
    const why = stepLabel(s);
    parts.push(h('span.muted', ' → '), h('b.num', `${ils(run)}/mo`), h('span.muted', ` from ${mon(s.from_month)}${why ? ` (${why})` : ''}`));
  }
  return h('div.small.goal-schedule', { style: { margin: '6px 0' } }, parts);
}

function projectionLine(g) {
  if ((g.status || 'active') !== 'active' || !g.target_month) return null;
  const vs = g.projected_vs_target || '';
  const cls = vs === 'on time' || vs.endsWith('early') ? 'good' : 'bad';
  return h('div.row.small', { style: { margin: '4px 0', gap: '8px', alignItems: 'center' } },
    h('span', h('span.muted', 'Projected done '), h('b', g.projected_completion_month ? mon(g.projected_completion_month) : 'never'),
      h('span.muted', ` · target ${mon(g.target_month)}`)), vs ? badge(vs, cls) : null);
}

function endLabel(e) { return `${e.what}${e.last_month ? ` (ends ${monthLabel(e.last_month)})` : ''}`; }

/** Step-ups: one line each, editable in place (month + amount, or linked to a fixed end). */
function stepUpsBox(g, reload, ends) {
  const box = h('div.goal-stepups', { style: { margin: '6px 0' } });
  const steps = asList(g.step_ups).map((s) => ({ ...s }));
  const save = async (next) => {
    try {
      await api.patch('/goals/' + g.id, { step_ups: next.map(({ from_month, add_amount, reason, fixed_end_id, amount_source }) => ({
        from_month, reason, fixed_end_id: fixed_end_id || null,
        add_amount: fixed_end_id && amount_source === 'fixed_end' ? null : add_amount,
      })) });
      toast('Step-ups saved', { type: 'success' }); changed('goals'); reload();
    } catch (e) { toastError(e); }
  };
  const view = (s, i) => h('div.row.small', { style: { gap: '6px', alignItems: 'center' } },
    h('span', h('span.muted', `Step-up from ${mon(s.from_month)}: `), h('b.num', `+${ils(s.add_amount)}/mo`),
      s.fixed_end ? h('span.muted', { dir: 'auto' }, ` · linked to ${endLabel(s.fixed_end)}`) : null),
    h('span.spacer'), h('button.btn.sm.ghost', { onclick: () => box.replaceChildren(...steps.map((x, j) => (j === i ? edit(x, i) : view(x, j))), addBtn) }, 'Edit'));
  const edit = (s, i) => {
    const month = h('input', { type: 'month', value: s.from_month || addMonths(thisMonth(), 1), style: { width: '140px' } });
    const link = h('select', {}, h('option', { value: '' }, 'Amount'),
      ends.map((e) => h('option', { value: e.id, selected: +s.fixed_end_id === e.id }, `Linked to ${endLabel(e)}`)));
    const amt = h('input', { type: 'number', min: 0, step: 1, value: s.add_amount ?? '', placeholder: '₪/mo', style: { width: '100px' } });
    const sync = () => { amt.hidden = !!link.value; };
    link.onchange = sync; sync();
    const reason = h('input', { type: 'text', value: s.reason || '', placeholder: 'Reason', dir: 'auto', style: { width: '160px' } });
    return h('div.row.small.wrap', { style: { gap: '6px', alignItems: 'center' } }, month, link, amt, reason,
      h('button.btn.sm.primary', { onclick: () => {
        const fid = link.value ? +link.value : null;
        if (!month.value || (!fid && amt.value === '')) { toastError(new Error('Pick a month and an amount or a fixed end')); return; }
        const next = steps.slice();
        next[i] = { from_month: month.value, reason: reason.value || null, fixed_end_id: fid, add_amount: fid ? null : +amt.value, amount_source: fid ? 'fixed_end' : 'manual' };
        save(next);
      } }, 'Save'),
      i < steps.length ? h('button.btn.sm.ghost', { onclick: () => save(steps.filter((_, j) => j !== i)) }, 'Remove') : null,
      h('button.btn.sm.ghost', { onclick: () => draw() }, 'Cancel'));
  };
  const addBtn = h('button.btn.sm.ghost', { onclick: () => box.replaceChildren(...steps.map(view), edit({}, steps.length)) }, '+ Step-up');
  const draw = () => box.replaceChildren(...steps.map(view), addBtn);
  draw();
  return box;
}

function goalCard(g, reload, ends = []) {
  const p = g.progress_pct ?? (g.target_amount ? (g.saved_amount / g.target_amount) * 100 : 0);
  const done = g.status === 'done' || p >= 100;
  const isActive = (g.status || 'active') === 'active';
  // "on track" only once this month's contribution is in; before that, say what is still to put away
  const due = g.has_plan ? (g.contribution_now ?? 0) : (g.monthly_need || 0);
  const toGo = due - (g.put_away_this_month?.amount || 0);
  const status = g.status === 'archived' ? badge('archived') : done ? badge('done', 'good') : g.on_track === false ? badge(g.has_plan ? 'late' : 'behind', 'bad')
    : g.put_away_this_month && toGo > 0 ? badge(`${ils(toGo)} to put away this month`, 'warn') : badge('on track', 'good');
  return h('div.card.goal-card',
    h('div.row', h('b', { dir: 'auto' }, g.name), h('span.spacer'), status),
    h('div.row', { style: { margin: '8px 0 6px', alignItems: 'baseline' } },
      h('span.goal-target.num', ils(g.saved_amount || 0)), h('span.muted', ` of ${ils(g.target_amount)}`), h('span.spacer'), h('span.muted.small', pct(p))),
    progress(p, done ? 'good' : g.on_track === false ? 'bad' : ''),
    g.linked_asset ? h('div.small.muted.goal-link', { dir: 'auto' }, `linked to ${shortName(g.linked_asset.name)}`,
      g.linked_asset.as_of ? ` · as of ${fmtDate(g.linked_asset.as_of)}` : '') : null,
    isActive ? scheduleLine(g) : null,
    projectionLine(g),
    g.put_away_this_month ? h('div.small', { style: { margin: '4px 0' } }, 'This month you put away ',
      h('b', { class: (g.put_away_this_month.amount || 0) >= due ? 'good' : '' }, ils(g.put_away_this_month.amount || 0)),
      due ? h('span.muted', ` of ${ils(due)} ${g.has_plan ? 'planned' : 'needed'}`) : null) : null,
    h('div.meta',
      h('span.muted', 'Target date'), h('b', g.target_date ? fmtDate(g.target_date, true) : '—'),
      h('span.muted', 'Monthly need'), h('b', g.status === 'active' || !g.status ? ils(g.monthly_need) : '—'),
      h('span.muted', 'Months left'), h('b', g.months_left ?? '—'),
      g.has_plan && isActive ? h('span.muted', 'Planned now / mo') : null, g.has_plan && isActive ? h('b', ils(g.contribution_now)) : null),
    isActive ? stepUpsBox(g, reload, ends) : null,
    g.notes ? h('div.small.muted', { dir: 'auto', style: { marginBottom: '8px' } }, g.notes) : null,
    h('div.row',
      (g.status === 'active' || !g.status) && !g.linked_asset ? h('button.btn.sm', { onclick: () => addSaved(g, reload) }, '+ Add savings') : null,
      g.linked_asset ? h('a.btn.sm', { href: '#/networth' }, 'Update asset value') : null,
      h('span.spacer'),
      h('button.btn.sm.ghost', { onclick: () => goalModal(g, reload) }, 'Edit')));
}

function addSaved(g, reload) {
  const input = h('input', { type: 'number', name: 'amount', min: 0, step: 1, required: true, value: g.monthly_need ? Math.round(g.monthly_need) : '' });
  openModal(`Add to “${g.name}”`, h('div.stack', field('Amount saved (₪)', input, `Currently ${ils(g.saved_amount)} of ${ils(g.target_amount)}`)), {
    actions: [{ label: 'Cancel' }, {
      label: 'Add', primary: true, fn: async (close) => {
        const v = +input.value;
        if (!v) return;
        const saved = (g.saved_amount || 0) + v;
        try {
          await api.patch('/goals/' + g.id, { saved_amount: saved, ...(saved >= g.target_amount ? { status: 'done' } : {}) });
          close(); toast(saved >= g.target_amount ? `Goal reached: ${g.name} 🎉` : `Added ${ils(v)}`, { type: 'success' });
          changed('goals'); reload();
        } catch (e) { toastError(e); }
      },
    }],
  });
}

export function goalModal(g, reload, preset = {}) {
  const v = { name: '', target_amount: '', target_date: '', saved_amount: 0, monthly_contribution: '', priority: 2, status: 'active', notes: '', ...preset, ...(g || {}) };
  const form = h('div.form-grid',
    h('div.full', field('Name', h('input', { name: 'name', required: true, value: v.name, dir: 'auto', placeholder: 'e.g. Trip to Japan' }))),
    field('Target (₪)', h('input', { name: 'target_amount', type: 'number', min: 1, step: 1, required: true, value: v.target_amount })),
    field('Target date', h('input', { name: 'target_date', type: 'date', required: true, value: v.target_date || '' })),
    field('Saved so far (₪)', h('input', { name: 'saved_amount', type: 'number', min: 0, step: 1, value: v.saved_amount ?? 0 })),
    field('Planned monthly (₪)', h('input', { name: 'monthly_contribution', type: 'number', min: 0, step: 1, value: v.monthly_contribution ?? '' }), 'Optional. Otherwise computed.'),
    field('Priority', h('select', { name: 'priority' }, [1, 2, 3].map((p) => h('option', { value: p, selected: +v.priority === p }, ['High', 'Medium', 'Low'][p - 1])))),
    field('Status', h('select', { name: 'status' }, ['active', 'done', 'archived'].map((s) => h('option', { value: s, selected: v.status === s }, s)))),
    h('div.full', field('Notes', h('textarea', { name: 'notes', rows: 2, dir: 'auto' }, v.notes || ''))));
  const actions = [];
  if (g) actions.push({ label: 'Delete', danger: true, fn: async (close) => {
    if (!confirm(`Delete goal “${g.name}”?`)) return;
    try { await api.del('/goals/' + g.id); close(); toast('Goal deleted'); changed('goals'); reload?.(); } catch (e) { toastError(e); }
  } });
  actions.push({ label: 'Cancel' }, {
    label: g ? 'Save' : 'Create goal', primary: true, fn: async (close) => {
      const body = readForm(form);
      body.priority = +body.priority;
      try {
        if (g) await api.patch('/goals/' + g.id, body); else await api.post('/goals', body);
        close(); toast(g ? 'Goal saved' : 'Goal created', { type: 'success' }); changed('goals'); reload?.();
      } catch (e) { toastError(e); }
    },
  });
  openModal(g ? 'Edit goal' : 'New goal', form, { actions });
}

function drawAfford(card, params, reload) {
  const amount = h('input', { type: 'number', min: 1, step: 1, placeholder: '₪ amount', required: true, value: params.amount || '', style: { width: '160px' } });
  const monthIn = h('input', { type: 'month', value: params.month || addMonths(thisMonth(), 3), min: thisMonth() });
  const what = h('input', { type: 'text', placeholder: 'What is it? (optional)', dir: 'auto', style: { width: '220px' } });
  const result = h('div');
  const form = h('form.afford-form', { onsubmit: (e) => { e.preventDefault(); run(); } },
    field('Amount', amount), field('In month', monthIn), field('For', what), h('button.btn.primary', { type: 'submit' }, 'Check'));
  card.append(h('div.card-head', h('h2', 'Can I afford it?'), h('span.muted.small', 'Projects free cash from your balance, median income and spending, and goal contributions')), form, result);

  async function run() {
    const a = +amount.value;
    if (!a) { amount.focus(); return; }
    clear(result).append(h('div.loading', h('span.spin'), 'Projecting…'));
    try {
      const r = await api.get('/affordability', { amount: a, month: monthIn.value });
      clear(result);
      const verdict = (r.verdict || '').toLowerCase();
      const label = { yes: 'Yes', tight: 'Tight', no: 'No' }[verdict] || r.verdict;
      const remaining = (r.projected_free ?? 0) - a;
      const explain = verdict === 'yes' ? `You'd still have ${ils(remaining)} free, above your ${ils(r.buffer)} buffer.`
        : verdict === 'tight' ? `You'd have ${ils(remaining)} left, below your ${ils(r.buffer)} safety buffer.`
          : `You'd be short ${ils(-remaining, { abs: true })} in ${monthLabel(monthIn.value, true)}.`;
      const path = asList(r.path, 'path');
      const pts = path.map((p) => ({ label: monthLabel(p.month, true), short: shortMonth(p.month), y: p.free, lo: p.p25 ?? p.low, hi: p.p75 ?? p.high, y2: p.safety_net }));
      const hasNet = r.safety_net != null && r.safety_net !== r.projected_free;
      const monthsTo = Math.max(1, path.length || 1);
      const perMonth = Math.ceil(a / monthsTo);
      result.append(h('div.grid-2', { style: { marginTop: '16px', alignItems: 'start' } },
        h('div.stack',
          h('div.verdict', { class: verdict }, label),
          h('div', explain),
          h('dl.kv',
            h('dt', 'Projected free cash'), h('dd', ils(r.projected_free)),
            h('dt', 'After purchase'), h('dd', { class: remaining < 0 ? 'bad' : '' }, ils(remaining)),
            h('dt', 'Safety buffer'), h('dd', ils(r.buffer)),
            r.p25 != null ? h('dt', 'Worse case (p25)') : null, r.p25 != null ? h('dd', ils(r.p25 - a)) : null,
            r.p75 != null ? h('dt', 'Better case (p75)') : null, r.p75 != null ? h('dd', ils(r.p75 - a)) : null,
            r.planned_in_range ? h('dt', 'Planned items included') : null, r.planned_in_range ? h('dd', ils(r.planned_in_range, { sign: true })) : null,
            hasNet ? h('dt', 'Safety net after purchase') : null, hasNet ? h('dd', { class: r.safety_net_after < 0 ? 'bad' : '' }, ils(r.safety_net_after)) : null),
          hasNet && r.covered_by_safety_net ? h('div.small.muted', 'Your liquid investments would cover it with the buffer intact, but you would be dipping into savings.') : null,
          h('div.row', h('button.btn', {
            onclick: () => goalModal(null, reload, { name: what.value || `Purchase ${ils(a)}`, target_amount: a, target_date: monthEnd(monthIn.value), saved_amount: 0 }),
          }, 'Make it a goal'), h('span.muted.small', `≈ ${ils(perMonth)}/month until ${monthLabel(monthIn.value)}`))),
        pts.length ? h('div', h('div.small.muted', { style: { marginBottom: '4px' } }, 'Projected free cash (shaded: p25–p75)'),
          lineChart(pts, { height: 220, width: 560, ref: { value: a, label: `cost ${ils(a)}` }, second: hasNet ? { label: 'safety net' } : null }),
          hasNet ? h('div.small.faint', 'Dashed green: free cash + liquid investments (safety net)') : null) : null));
    } catch (e) { clear(result).append(h('div.error-box', e.message)); }
  }
  if (params.amount) run();
}
