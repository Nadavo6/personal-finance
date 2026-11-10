// Month (home): left-to-spend first, tiles, exceptions-only category bars, anomalies, fixed charges, merchants.
import { h, data, mixed, progress, clear, ils, pct, monthLabel, addMonths, thisMonth, fmtDate, empty, spinner, asList, badge, tip } from './util.js';
import { api, categories } from './api.js';
import { monthStrip, budgetBar } from './charts.js';
import { openTxnDrawer } from './txns.js';
import { go } from './main.js';
import { rankInsights, impact, typeLabel } from './view-insights.js';

const counted = (t) => !t.is_transfer && !t.is_excluded;
const INCOME_LABELS = { salary: 'salary', rental: 'rent', reserve_duty: 'reserve duty', other: 'other' };
const incomeSplit = (b) => {
  const parts = Object.entries(b || {}).filter(([, v]) => v);
  return parts.length > 1 ? parts.map(([k, v]) => `${INCOME_LABELS[k] || k} ${ils(v)}`).join(' · ') : null;
};
// "Next apartment — investments to ₪420K" -> "Next apartment"
const goalLabel = (name) => String(name || 'goal').split(/\s+[—–-]\s+/)[0];
const SHORT = { leumi: 'Leumi', max: 'Max', sheet: 'Sheet' };

/** One-line data freshness next to the month title; links to the Import checklist. */
function freshnessChip(f) {
  const srcs = [...asList(f?.sources, 'sources').filter((s) => s.monthly), f?.sheet].filter(Boolean);
  if (!srcs.length) return null;
  const parts = srcs.filter((s) => s.key !== 'sheet').map((s) => `${SHORT[s.key] || s.label} to ${s.last_date ? fmtDate(s.last_date) : '—'}`);
  const late = srcs.filter((s) => s.status !== 'ok');
  const cls = late.some((s) => s.status === 'missing') ? 'bad' : late.length ? 'warn' : 'good';
  return h('a.fresh-line', { href: '#/import', class: cls, title: 'Data freshness · open the monthly checklist' },
    h('span.status-dot', { class: cls === 'good' ? 'ok' : cls === 'bad' ? 'err' : 'warn' }),
    parts.join(' · '),
    late.length ? h('span', { class: cls }, ' · ⚠ ', late.map((s) => s.key === 'sheet' && s.days != null ? `sheet ${s.days} d old`
      : `${SHORT[s.key] || s.label} ${s.status === 'missing' ? 'missing' : 'getting old'}`).join(', ')) : null);
}

export async function render(root, { arg }) {
  const [ov, fresh] = await Promise.all([api.get('/overview'), api.get('/freshness').catch(() => null), categories().catch(() => [])]);
  const month = arg || ov?.current_month || thisMonth();
  const months = asList(ov?.months, 'months');
  const first = months[0]?.month, last = ov?.current_month || thisMonth();

  const nav = h('div.month-nav',
    h('button.icon-btn', { title: 'Previous month ([)', disabled: first && month <= first, onclick: () => go('month', addMonths(month, -1)) }, '←'),
    h('span.cur', monthLabel(month, true)),
    h('button.icon-btn', { title: 'Next month (])', disabled: month >= last, onclick: () => go('month', addMonths(month, 1)) }, '→'));
  const reviewN = ov?.uncategorized_count || 0;
  root.append(h('div.page-head', h('h1', 'Month'), nav, month === last ? freshnessChip(fresh) : null, h('span.spacer'),
    month !== last ? h('button.btn.sm', { onclick: () => go('month', last) }, 'This month') : null,
    reviewN ? h('a.btn.sm', { href: '#/transactions?review=1' }, `${reviewN} uncategorized →`) : null));

  if (months.length) root.append(monthStrip(months.slice(-12), month, (m) => go('month', m)));

  const freebies = asList(ov?.freebies_expiring);
  if (freebies.length) {
    const soonest = freebies[0];
    root.append(h('div.banner',
      h('span', '✦'),
      h('span', h('b', `${freebies.length} freebie${freebies.length === 1 ? '' : 's'} expiring soon`), ' · ',
        data(soonest.name), soonest.days_left != null ? ` in ${soonest.days_left} days` : '',
        soonest.remaining != null && soonest.unit === 'ILS' ? ` (${ils(soonest.remaining)} left)` : ''),
      h('span.spacer'), h('a', { href: '#/freebies' }, 'View →')));
  }

  const body = h('div', spinner());
  root.append(body);
  let m;
  try { m = await api.get('/month/' + month); } catch (e) { clear(body).append(h('div.error-box', e.message)); return; }
  clear(body);
  if (!m || (!m.income && !m.expense && !asList(m.categories).length)) {
    body.append(h('div.card', empty('No data for this month yet', 'Import a bank or card file, or run a sync, and this page fills in.',
      h('a.btn.primary', { href: '#/import' }, 'Import & Sync'))));
    return;
  }
  renderMonth(body, m, month, ov);

  const onKey = (e) => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || document.querySelector('.modal-wrap')) return;
    if (e.key === '[' && (!first || month > first)) go('month', addMonths(month, -1));
    if (e.key === ']' && month < last) go('month', addMonths(month, 1));
  };
  document.addEventListener('keydown', onKey);
  return () => document.removeEventListener('keydown', onKey);
}

function renderMonth(body, m, month, ov) {
  const label = monthLabel(month, true);
  const drawer = (title, query, filter) => openTxnDrawer(title, { month, ...query }, { subtitle: label, filter });
  const isCurrent = m.is_current ?? (month === (ov?.current_month || thisMonth()));

  // --- hero: left to spend + tiles
  const lts = m.left_to_spend ?? (m.income - m.expense - (m.goals_reserved || 0));
  let pace = null;
  if (isCurrent) {
    const now = new Date();
    const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
    const daysLeft = m.days_left ?? (daysInMonth - now.getDate() + 1);
    const daily = m.daily_pace ?? (daysLeft > 0 ? lts / daysLeft : 0);
    pace = h('div.muted', `${daysLeft} day${daysLeft === 1 ? '' : 's'} left · `,
      lts < 0 ? h('b.bad', `over by ${ils(-lts)}`) : [h('b.num', ils(daily)), ' a day']);
  }
  // the headline's arithmetic, so the number explains itself
  const incomeIn = isCurrent ? Math.max(m.expected_income || 0, m.income || 0) : m.income || 0;
  const terms = [
    [isCurrent && m.expected_income > m.income ? 'Expected income' : 'Income', incomeIn, ''],
    ['Spent so far', m.expense, '−'],
    ['Saved', m.savings, '−'],
    ['Fixed bills still due', isCurrent ? m.remaining_fixed : 0, '−'],
    ['Goals this month', m.goals_reserved, '−'],
  ].filter(([, v], i) => i === 0 || Math.round(v || 0) !== 0);
  const calc = h('div.calc', { 'aria-label': 'How this is calculated' },
    terms.map(([lbl, v, op]) => [h('span.calc-op', op), h('span.muted', lbl), h('span.num', ils(Math.abs(v || 0)))]),
    h('span.calc-op', '='), h('b', isCurrent ? 'Left to spend' : 'Left over'), h('b.num', { class: lts < 0 ? 'bad' : '' }, ils(lts)));
  const left = h('div.card.left-card',
    h('div.label', isCurrent ? 'Left to spend' : 'Left over'),
    h('div.big-number.num-link', { class: lts < 0 ? 'neg' : '', title: 'Show this month\'s spending', onclick: () => drawer('Spent', {}, (t) => counted(t) && t.amount < 0) }, ils(lts)),
    pace,
    paceBar(m, isCurrent),
    calc,
    ov?.balance?.amount != null ? h('div.small.faint', { style: { marginTop: '6px' } }, `Bank balance ${ils(ov.balance.amount)}`) : null,
    ov?.net_worth?.total != null ? h('a.nw-tile', { href: '#/networth', title: 'Open Net worth' },
      h('span.muted', 'Net worth'), h('b', ils(ov.net_worth.total)),
      h('span.small.faint', `liquid ${ils(ov.net_worth.liquid)}`), h('span.spacer'), h('span.small.muted', '→')) : null);
  const tile = (lbl, value, sub, onclick, cls = '') => h('button.tile', { onclick, class: cls },
    h('div.tile-label', lbl), h('div.tile-value', value), sub ? h('div.tile-sub', sub) : null);
  const tiles = h('div.tiles',
    tile('Income', ils(m.income), isCurrent && m.expected_income > m.income ? `expecting ${ils(m.expected_income)}` : incomeSplit(m.income_breakdown), () => drawer('Income', {}, (t) => counted(t) && t.amount > 0)),
    tile('Spent', ils(m.expense, { abs: true }), m.savings ? `+ ${ils(m.savings, { abs: true })} saved` : null, () => drawer('Spent', {}, (t) => counted(t) && t.amount < 0)),
    tile('Net', h('span', { class: m.net < 0 ? 'bad' : 'good' }, ils(m.net, { sign: true })), m.net < 0 ? 'Spent more than earned' : null, () => drawer('All transactions', {}, counted)),
    goalsTile(m));
  const oo = m.one_offs;
  const oneOffs = oo && oo.count > 0 ? h('div.small.muted', { style: { marginTop: '-4px' } },
    h('button.link', { onclick: () => drawer('One-offs', {}, (t) => !!t.is_excluded && !t.is_transfer && t.account?.kind !== 'gift') },
      `One-offs (${oo.count}):`), ' ',
    oo.income ? h('span.num.good', ils(Math.abs(oo.income), { sign: true })) : null, oo.income && oo.expense ? ' / ' : '',
    oo.expense ? h('span.num', '−' + ils(Math.abs(oo.expense))) : null,
    ' · not counted in the totals above') : null;
  const od = isCurrent ? m.overdraft_risk : null;
  if (od) body.append(h('div.overdraft-banner', { role: 'alert' },
    h('b', 'Overdraft risk'), ' · ', od.text, '. ', h('a', { href: '#/networth' }, 'Net worth →')));
  body.append(h('div.hero', left, h('div', { style: { display: 'flex', flexDirection: 'column', gap: '12px' } }, tiles, oneOffs, anomaliesCard(m, drawer),
    isCurrent ? topActions() : null)));

  // --- categories: exceptions first
  const cats = asList(m.categories).filter((c) => c.kind !== 'income' && c.kind !== 'transfer' && c.kind !== 'savings' && (c.actual || c.budget));
  const isOver = (c) => (c.over != null ? !!c.over : !!(c.budget && Math.abs(c.actual) > c.budget));
  const near = (c) => !isOver(c) && c.kind !== 'fixed' && c.budget && Math.abs(c.actual) / c.budget >= 0.85;
  const hot = cats.filter((c) => isOver(c) || near(c)).sort((a, b) => (b.pct || 0) - (a.pct || 0));
  const calm = cats.filter((c) => !hot.includes(c)).sort((a, b) => Math.abs(b.actual) - Math.abs(a.actual));
  const catRow = (c) => {
    const row = h('div.cat-row', { tabindex: 0, role: 'button', onclick: () => drawer(c.name, { category_id: c.id }), onkeydown: (e) => { if (e.key === 'Enter') row.click(); } },
      h('div.cat-name', h('span.dot', { style: { background: c.color || 'var(--faint)' } }), c.name,
        c.kind === 'fixed' ? badge('fixed') : c.kind === 'tracking' ? badge('tracking') : null),
      budgetBar(c.actual, c.budget, { over: isOver(c) }),
      h('div.cat-amt', h('span', { class: isOver(c) ? 'bad' : '' }, ils(c.actual, { abs: true })),
        c.budget ? h('span.of', ` / ${ils(c.budget)}`) : h('span.of', ' no budget')));
    tip(row, () => h('div', h('b', c.name), c.name_he ? h('div', data(c.name_he)) : null,
      h('div', `${c.count ?? '?'} transactions`), c.budget ? h('div', `${pct(c.pct ?? (Math.abs(c.actual) / c.budget) * 100)} of budget`) : null,
      c.median3 != null ? h('div.muted', `3-month median ${ils(c.median3)}`) : null));
    return row;
  };
  const calmBox = h('div', { hidden: hot.length > 0 }, calm.map(catRow));
  const catCard = h('div.card',
    h('div.card-head', h('h2', 'Categories'), h('span.muted.small', hot.length ? `${hot.length} need attention` : 'All within budget'),
      h('div.legend', h('span', { style: { '--c': 'var(--accent)' } }, 'spent'), h('span', { style: { '--c': 'var(--bad)' } }, 'over budget'))),
    hot.map(catRow),
    hot.length && calm.length ? h('button.link.small.collapse-toggle', {
      onclick: (e) => { calmBox.hidden = !calmBox.hidden; e.target.textContent = calmBox.hidden ? `Show ${calm.length} on-budget categories` : 'Hide on-budget categories'; },
    }, `Show ${calm.length} on-budget categories`) : null,
    calmBox,
    !cats.length ? empty('No spending yet', 'Categories show up as transactions arrive.') : null);

  // --- fixed charges
  const fixed = asList(m.fixed);
  const fixedCard = h('div.card',
    h('div.card-head', h('h2', 'Fixed charges'), fixed.length ? h('span.muted.small', `${fixed.filter((f) => f.actual > 0).length}/${fixed.length} arrived`) : null),
    fixed.length ? h('ul.list', fixed.map((f) => {
      // net spend > 0: the charge came in (a refund alone, e.g. a roommate's share, is not the charge)
      const arrived = f.actual != null && f.actual > 0;
      const diff = arrived && f.expected ? Math.abs(f.actual) - Math.abs(f.expected) : 0;
      return h('li.clickable', { onclick: () => drawer(f.name, { q: f.name }) },
        h('span', { class: arrived ? 'good' : 'faint', title: arrived ? 'Arrived' : 'Not yet' }, arrived ? '✓' : '○'),
        data(f.name),
        diff > 1 ? badge(`+${ils(diff)}`, 'bad') : null,
        h('span.amount.num', arrived ? ils(f.actual, { abs: true }) : h('span.faint', `expected ${ils(f.expected, { abs: true })}`)));
    })) : empty('No fixed charges detected', 'Recurring bills (rent, phone, insurance) show here with expected vs arrived.'));

  // --- top merchants
  const tm = asList(m.top_merchants);
  const tmCard = h('div.card',
    h('div.card-head', h('h2', 'Top merchants')),
    tm.length ? h('ul.list', tm.slice(0, 8).map((x) => h('li.clickable', { onclick: () => drawer(x.merchant, { q: x.merchant }) },
      data(x.label || x.merchant), h('span.faint.small', `×${x.count}`), h('span.amount.num', ils(x.amount, { abs: true }))))) : empty('No merchants yet'));

  body.append(catCard, h('div.grid-2.section', fixedCard, tmCard));
}

/** Goals reserved, with this month's progress for the top goal(s) as bars. */
function goalsTile(m) {
  const due = (g) => g.contribution ?? g.contribution_now ?? g.monthly_need ?? 0;
  const gp = asList(m.goal_progress).filter((g) => due(g));
  return h('button.tile', { onclick: () => go('goals') },
    h('div.tile-label', 'Goals this month'), h('div.tile-value', ils(m.goals_reserved || 0)),
    gp.length ? gp.slice(0, 2).map((g) => {
      const put = g.put_away || 0, need = due(g), met = put >= need;
      return h('div.tile-goal',
        h('div.small.muted.ellipsis', { dir: 'auto', title: g.name }, goalLabel(g.name)),
        progress((put / need) * 100, met ? 'good' : ''),
        h('div.small.num', h('span', { class: met ? 'good' : '' }, ils(put)), h('span.faint', ` of ${ils(need)} put away`)));
    }) : h('div.tile-sub', 'Scheduled contributions of active goals'));
}

/** Top 3 things to act on this month (Review findings ranked by urgency, then money). */
function topActions() {
  const box = h('div.card.top-actions', { hidden: true });
  api.get('/insights').then((r) => {
    const top = rankInsights(asList(r)).slice(0, 3);
    if (!top.length) return;
    box.hidden = false;
    box.append(h('div.card-head', h('h2', 'Top 3 this month'), h('a.small', { href: '#/insights' }, 'All findings →')),
      h('ul.list', top.map((i) => h('li.clickable', { onclick: () => go('insights') },
        badge(typeLabel(i.type)), h('span', { style: { minWidth: 0, flex: 1 } }, mixed(i.title)),
        impact(i)))));
  }).catch(() => {});
  return box;
}

/** Spent vs (expected) income, with a "today" marker showing how far into the month we are. */
function paceBar(m, isCurrent) {
  const income = Math.max(m.expected_income || 0, m.income || 0);
  const spent = Math.abs(m.expense || 0);
  if (!income) return null;
  const ratio = spent / income;
  let elapsed = null;
  if (isCurrent) {
    const now = new Date();
    const days = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
    elapsed = m.days_left != null ? (days - m.days_left + 1) / days : now.getDate() / days;
  }
  const ahead = elapsed != null && ratio > elapsed + 0.1;
  const cls = ratio > 1 ? 'bad' : ahead ? 'warn' : '';
  const bar = h('div', { style: { position: 'relative' } }, progress(ratio * 100, cls),
    elapsed != null ? h('div.today-mark', { style: { left: `calc(${Math.min(100, elapsed * 100)}% - 1px)` }, title: 'Today' }) : null);
  return h('div.pace-bar',
    h('div.row.small', h('span.muted', 'Spent'), h('b.num', ils(spent)), h('span.muted', `of ${ils(income)} ${isCurrent && m.expected_income > m.income ? 'expected ' : ''}income`),
      h('span.spacer'), h('span.num', { class: cls }, `${Math.round(ratio * 100)}%`)),
    bar,
    elapsed != null ? h('div.small.faint', ahead ? `Ahead of pace: ${Math.round(elapsed * 100)}% of the month has passed` : `${Math.round(elapsed * 100)}% of the month has passed`) : null);
}

function anomaliesCard(m, drawer) {
  const an = asList(m.anomalies);
  return h('div.card', { style: { flex: 1 } },
    h('div.card-head', h('h2', 'Exceptions'), an.length ? badge(String(an.length), 'warn') : null),
    an.length
      ? h('ul.list', an.slice(0, 5).map((a) => {
        const show = (autoPick = false) => a.txn_ids?.length ? openTxnDrawer('Exception', { ids: a.txn_ids }, { subtitle: mixed(a.text), autoPick }) : null;
        const fixable = a.txn_ids?.length === 1 && a.type !== 'unusual_inflow';
        return h('li.clickable', { onclick: () => show() },
          h('span.warn', '!'), mixed(a.text),
          a.txn_ids?.length ? h('span.amount.row.small',
            fixable ? h('button.link', { onclick: (e) => { e.stopPropagation(); show(true); } }, 'Fix category') : null,
            h('span.faint', 'Show →')) : null);
      }))
      : h('div.muted', { style: { padding: '6px 0' } }, 'Nothing unusual this month.'));
}
