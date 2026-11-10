// Trends: 24-month income vs expense (overspend red, hover reasons), category small multiples, 6-month forecast.
import { h, mixed, clear, ils, monthLabel, shortMonth, empty, spinner, asList } from './util.js';
import { api, categories, catById } from './api.js';
import { incomeExpenseBars, sparkBars, lineChart, monthTip } from './charts.js';
import { openTxnDrawer } from './txns.js';
import { go } from './main.js';

export async function render(root) {
  root.append(h('div.page-head', h('h1', 'Trends'), h('span.sub', 'Last 24 months')));
  await categories().catch(() => []);
  const [ov, tr, fc] = await Promise.all([
    api.get('/overview'),
    api.get('/trends', { months: 24 }).catch((e) => ({ error: e })),
    api.get('/forecast', { months: 6 }).catch((e) => ({ error: e })),
  ]);
  const months = asList(ov?.months, 'months');
  if (!months.length) {
    root.append(h('div.card', empty('No history yet', 'Trends appear after the first import.', h('a.btn.primary', { href: '#/import' }, 'Import & Sync'))));
    return;
  }

  // --- income vs expense
  const overN = months.filter((m) => m.overspend).length;
  const attnN = months.filter((m) => !m.overspend && asList(m.attention).length).length;
  const avg = (k) => months.reduce((s, m) => s + Math.abs(m[k] || 0), 0) / months.length;
  const rows = months.map((m) => ({
    month: m.month, a: m.income || 0, b: Math.abs(m.expense || 0), red: !!m.overspend, amber: asList(m.attention).length > 0,
    onclick: () => go('month', m.month),
    tip: monthTip(m, h('div.muted.small', { style: { marginTop: '4px' } }, 'Click to open month')),
  }));
  root.append(h('div.card',
    h('div.card-head', h('h2', 'Income vs spending'),
      h('div.legend', h('span', { style: { '--c': 'var(--income)' } }, 'income'), h('span', { style: { '--c': 'var(--expense)' } }, 'spent'), h('span', { style: { '--c': 'var(--bad)' } }, 'overspend month'), h('span', { style: { '--c': 'var(--warn)' } }, 'needs attention'))),
    h('div.row.wrap.small.muted', { style: { marginBottom: '8px', gap: '18px' } },
      h('span', 'Avg income ', h('b.num', ils(avg('income')))), h('span', 'Avg spent ', h('b.num', ils(avg('expense')))),
      h('span', { class: overN ? 'bad' : '' }, `${overN} overspend month${overN === 1 ? '' : 's'}`),
      attnN ? h('span.warn', `${attnN} more need${attnN === 1 ? 's' : ''} attention`) : null),
    incomeExpenseBars(rows, { height: 260 })));

  // --- category small multiples
  const multi = h('div.card.section', h('div.card-head', h('h2', 'Categories'), h('span.muted.small', 'Monthly spend per category · click for transactions')));
  if (tr?.error) multi.append(h('div.error-box', tr.error.message));
  else {
    const tmonths = asList(tr?.months, 'months').map((m) => (typeof m === 'string' ? m : m.month));
    const cats = asList(tr?.categories, 'categories')
      .map((c) => ({ ...c, series: (c.series || []).map((v) => Math.abs((typeof v === 'object' ? v?.amount : v) || 0)) }))
      .filter((c) => !['income', 'transfer'].includes(c.kind ?? catById(c.id)?.kind))
      .filter((c) => c.series.some((v) => v > 0))
      .sort((a, b) => b.series.reduce((s, v) => s + v, 0) - a.series.reduce((s, v) => s + v, 0));
    if (!cats.length) multi.append(empty('No category history yet'));
    else {
      multi.append(h('div.multiples', cats.map((c) => {
        const avg3 = c.series.slice(-3).reduce((s, v) => s + v, 0) / Math.min(3, c.series.length);
        const prior = c.series.slice(0, -3);
        const avgP = prior.length ? prior.reduce((s, v) => s + v, 0) / prior.length : 0;
        const delta = avgP ? ((avg3 - avgP) / avgP) * 100 : 0;
        return h('div.card.multiple', { tabindex: 0, onclick: () => openTxnDrawer(c.name, { category_id: c.id, month: tmonths.at(-1) }, { subtitle: monthLabel(tmonths.at(-1), true) }) },
          h('div.row', h('span.dot', { style: { background: c.color || 'var(--accent)' } }), h('b', c.name), h('span.spacer'),
            Math.abs(delta) >= 10 ? h('span.small', { class: delta > 0 ? 'bad' : 'good' }, `${delta > 0 ? '▲' : '▼'} ${Math.round(Math.abs(delta))}%`) : null),
          sparkBars(c.series, { labels: tmonths.map((m) => monthLabel(m)), color: c.color, highlight: c.series.length - 1 }),
          h('div.row.small.muted', { style: { marginTop: '4px' } }, h('span', shortMonth(tmonths[0])), h('span.spacer'), h('span', `3-mo avg ${ils(avg3)}`)));
      })));
    }
  }
  root.append(multi);

  // --- forecast
  const fcCard = h('div.card.section', h('div.card-head', h('h2', 'Forecast'), h('span.muted.small', 'Next 6 months from median income and spending')));
  if (fc?.error) fcCard.append(h('div.error-box', fc.error.message));
  else {
    const fm = asList(fc, 'months');
    if (!fm.length) fcCard.append(empty('Not enough history to forecast', 'Needs about 3 months of data.'));
    else {
      const bal = ov?.balance?.amount;
      const pts = [];
      if (bal != null) pts.push({ label: 'Now', short: 'Now', y: bal });
      fm.forEach((m) => pts.push({
        label: monthLabel(m.month, true), short: shortMonth(m.month),
        y: m.balance ?? m.projected_balance ?? m.free ?? 0, lo: m.p25 ?? m.balance_p25, hi: m.p75 ?? m.balance_p75,
        y2: m.safety_net,
      }));
      const hasNet = fm.some((m) => m.safety_net != null && m.safety_net !== m.balance);
      if (hasNet && pts[0].short === 'Now' && fc.safety_net_now != null) pts[0].y2 = fc.safety_net_now - (fc.end_of_current_month ?? 0) + bal;
      const hasPlanned = fm.some((m) => m.planned);
      fcCard.append(lineChart(pts, { height: 220, second: hasNet ? { label: 'safety net' } : null }),
        hasNet ? h('div.small.muted', h('span.good', '- - '), 'Safety net = projected balance + liquid investments (', h('a', { href: '#/networth' }, 'Net worth'), ')') : null,
        h('table.section', h('thead', h('tr', h('th', 'Month'), h('th.num', 'Income'), h('th.num', 'Spending'), hasPlanned ? h('th.num', 'Planned') : null, h('th.num', 'Net'), h('th.num', 'Projected balance'), hasNet ? h('th.num', 'Safety net') : null)),
          h('tbody', fm.map((m) => {
            const net = m.net ?? ((m.income || 0) - Math.abs(m.expense || 0));
            return h('tr', h('td', monthLabel(m.month, true)), h('td.num', ils(m.income)), h('td.num', ils(m.expense, { abs: true })),
              hasPlanned ? h('td.num', m.planned ? ils(m.planned, { sign: true }) : h('span.faint', '—')) : null,
              h('td.num', { class: net < 0 ? 'bad' : '' }, ils(net, { sign: true })), h('td.num', h('b', ils(m.balance ?? m.projected_balance))),
              hasNet ? h('td.num.muted', ils(m.safety_net)) : null);
          }))));
    }
  }
  root.append(fcCard);
}
