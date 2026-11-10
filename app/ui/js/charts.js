// Hand-drawn SVG charts (no dependencies). Colours come from CSS variables.
import { h, tip, ils, mixed, shortMonth, monthLabel } from './util.js';

const NS = 'http://www.w3.org/2000/svg';
export function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}

function niceMax(v) {
  if (v <= 0) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.2, 1.6, 2, 2.4, 3.2, 4, 6, 8, 10]) if (m * e >= v) return m * e;
  return 10 * e;
}
/** ~90th percentile of positive values: a scale top that ignores a single outlier. */
function robustTop(vals) {
  const v = vals.filter((x) => x > 0).sort((a, b) => a - b);
  if (!v.length) return 1;
  return v[Math.min(v.length - 1, Math.floor(v.length * 0.9))];
}
function kfmt(v) {
  const a = Math.abs(v);
  if (a >= 1000) return (v < 0 ? '−' : '') + '₪' + (a / 1000).toFixed(a >= 10000 ? 0 : 1).replace(/\.0$/, '') + 'k';
  return ils(v);
}

/**
 * Grouped bars: income vs expense per month.
 * rows: [{month, a (income), b (expense), red:bool, tip: Node|string, onclick}]
 */
export function incomeExpenseBars(rows, { height = 240, width = 900 } = {}) {
  const padL = 48, padB = 26, padT = 10, padR = 8;
  const W = width, H = height;
  const all = rows.flatMap((r) => [r.a || 0, r.b || 0]);
  const raw = Math.max(1, ...all);
  const p90 = robustTop(all);
  const capped = raw > p90 * 2.2;               // one outlier month must not flatten the rest
  const max = niceMax(capped ? p90 * 1.3 : raw);
  const iw = W - padL - padR, ih = H - padT - padB;
  const gw = iw / Math.max(rows.length, 1);
  const bw = Math.max(3, Math.min(16, gw * 0.32));
  const y = (v) => padT + ih - (Math.max(0, v) / max) * ih;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart', role: 'img' });
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i, yy = y(v);
    svg.append(s('line', { x1: padL, x2: W - padR, y1: yy, y2: yy, class: 'grid' }));
    svg.append(s('text', { x: padL - 6, y: yy + 4, class: 'axis', 'text-anchor': 'end' }, kfmt(v)));
  }
  rows.forEach((r, i) => {
    const gx = padL + i * gw;
    const g = s('g', { class: 'bar-group' + (r.red ? ' over' : '') + (r.onclick ? ' clickable' : '') });
    g.append(s('rect', { x: gx, y: padT, width: gw, height: ih, class: 'hover-bg' }));
    const ya = y(Math.min(r.a || 0, max)), yb = y(Math.min(r.b || 0, max));
    g.append(s('rect', { x: gx + gw / 2 - bw - 1, y: ya, width: bw, height: Math.max(0, padT + ih - ya), rx: 2, class: 'bar-income' }));
    g.append(s('rect', { x: gx + gw / 2 + 1, y: yb, width: bw, height: Math.max(0, padT + ih - yb), rx: 2, class: r.red ? 'bar-over' : 'bar-expense' }));
    if ((r.a || 0) > max) g.append(s('text', { x: gx + gw / 2 - bw / 2 - 1, y: padT - 1, class: 'axis clip-mark', 'text-anchor': 'middle' }, '▲'));
    if ((r.b || 0) > max) g.append(s('text', { x: gx + gw / 2 + bw / 2 + 1, y: padT - 1, class: 'axis clip-mark', 'text-anchor': 'middle' }, '▲'));
    const showLabel = rows.length <= 14 || i % 2 === (rows.length - 1) % 2;
    if (showLabel) {
      const lab = shortMonth(r.month) + (r.month.endsWith('-01') ? ' ' + r.month.slice(2, 4) : '');
      svg.append(s('text', { x: gx + gw / 2, y: H - 8, class: 'axis' + (r.red ? ' axis-red' : r.amber ? ' axis-amber' : ''), 'text-anchor': 'middle' }, lab));
    }
    if (r.amber && !r.red) g.append(s('circle', { cx: gx + gw / 2, cy: padT + ih + 4, r: 2.5, class: 'attn-dot' }));
    if (r.onclick) g.addEventListener('click', r.onclick);
    if (r.tip) tip(g, r.tip);
    svg.append(g);
  });
  return svg;
}

/** Tiny bar sparkline: values[] (positive), optional labels[] for tooltips. */
export function sparkBars(values, { width = 220, height = 48, labels = [], color, maxV, highlight = -1 } = {}) {
  const max = maxV || Math.max(1, ...values.map((v) => Math.abs(v || 0)));
  const n = Math.max(values.length, 1);
  const gw = width / n;
  const bw = Math.max(1.5, gw * 0.7);
  const svg = s('svg', { viewBox: `0 0 ${width} ${height}`, class: 'chart spark', preserveAspectRatio: 'none' });
  values.forEach((v, i) => {
    const hh = (Math.abs(v || 0) / max) * (height - 2);
    const r = s('rect', {
      x: i * gw + (gw - bw) / 2, y: height - hh, width: bw, height: Math.max(hh, v ? 1 : 0), rx: 1,
      class: i === highlight ? 'spark-hi' : 'spark-bar', style: color ? `fill:${color}` : null,
    });
    if (labels[i]) tip(r, `${labels[i]}: ${ils(v, { abs: true })}`);
    svg.append(r);
  });
  return svg;
}

/**
 * Line chart with optional band.
 * points: [{label, y, lo?, hi?, y2?}]; opts.zero draws y=0; opts.ref {value,label} dashed line.
 * opts.second {label}: draws points[].y2 as a second (secondary-colour) line, e.g. a safety net.
 */
export function lineChart(points, { height = 220, width = 900, ref, zero = true, area = true, splitAt = -1, second } = {}) {
  const padL = 56, padB = 26, padT = 12, padR = 12;
  const W = width, H = height;
  const vals = points.flatMap((p) => [p.y, p.lo, p.hi, second ? p.y2 : null]).filter((v) => v != null);
  if (ref) vals.push(ref.value);
  if (zero) vals.push(0);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) { hi += 1; lo -= 1; }
  const span = hi - lo; hi += span * 0.08; lo -= span * 0.08;
  const iw = W - padL - padR, ih = H - padT - padB;
  const x = (i) => padL + (points.length <= 1 ? iw / 2 : (i / (points.length - 1)) * iw);
  const y = (v) => padT + ih - ((v - lo) / (hi - lo)) * ih;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart', role: 'img' });
  for (let i = 0; i <= 4; i++) {
    const v = lo + ((hi - lo) / 4) * i, yy = y(v);
    svg.append(s('line', { x1: padL, x2: W - padR, y1: yy, y2: yy, class: 'grid' }));
    svg.append(s('text', { x: padL - 6, y: yy + 4, class: 'axis', 'text-anchor': 'end' }, kfmt(v)));
  }
  if (zero && lo < 0 && hi > 0) svg.append(s('line', { x1: padL, x2: W - padR, y1: y(0), y2: y(0), class: 'zero' }));
  const band = points.filter((p) => p.lo != null && p.hi != null);
  if (band.length === points.length && points.length > 1) {
    const top = points.map((p, i) => `${x(i)},${y(p.hi)}`).join(' ');
    const bot = points.map((p, i) => `${x(i)},${y(p.lo)}`).reverse().join(' ');
    svg.append(s('polygon', { points: `${top} ${bot}`, class: 'band' }));
  }
  if (ref) {
    svg.append(s('line', { x1: padL, x2: W - padR, y1: y(ref.value), y2: y(ref.value), class: 'ref' }));
    svg.append(s('text', { x: W - padR, y: y(ref.value) - 5, class: 'axis ref-label', 'text-anchor': 'end' }, ref.label));
  }
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${x(i)},${y(p.y)}`).join(' ');
  if (area && points.length > 1) {
    svg.append(s('path', { d: `${d} L${x(points.length - 1)},${y(Math.max(lo, 0))} L${x(0)},${y(Math.max(lo, 0))} Z`, class: 'area' }));
  }
  if (splitAt > 0 && splitAt < points.length) {
    const d1 = points.slice(0, splitAt + 1).map((p, i) => `${i ? 'L' : 'M'}${x(i)},${y(p.y)}`).join(' ');
    const d2 = points.slice(splitAt).map((p, i) => `${i ? 'L' : 'M'}${x(i + splitAt)},${y(p.y)}`).join(' ');
    svg.append(s('path', { d: d1, class: 'line' }));
    svg.append(s('path', { d: d2, class: 'line dashed' }));
  } else {
    svg.append(s('path', { d, class: 'line' }));
  }
  if (second && points.some((p) => p.y2 != null)) {
    const d2 = points.map((p, i) => (p.y2 == null ? null : `${x(i)},${y(p.y2)}`)).filter(Boolean);
    svg.append(s('path', { d: 'M' + d2.join(' L'), class: 'line line2' }));
    const last = points.length - 1;
    if (points[last].y2 != null) svg.append(s('text', { x: x(last) - 4, y: y(points[last].y2) - 7, class: 'axis line2-label', 'text-anchor': 'end' }, second.label));
  }
  const step = Math.ceil(points.length / 12);
  points.forEach((p, i) => {
    const dot = s('circle', { cx: x(i), cy: y(p.y), r: 3.5, class: 'dot' + (p.y < 0 ? ' neg' : '') });
    tip(dot, () => h('div', h('b', p.label), h('div', ils(p.y)),
      p.lo != null ? h('div.muted', `range ${ils(p.lo)} – ${ils(p.hi)}`) : null,
      second && p.y2 != null ? h('div.muted', `${second.label}: ${ils(p.y2)}`) : null));
    svg.append(dot);
    if (i % step === 0 || i === points.length - 1) {
      svg.append(s('text', { x: x(i), y: H - 8, class: 'axis', 'text-anchor': 'middle' }, p.short ?? p.label));
    }
  });
  return svg;
}

const attn = (m) => (Array.isArray(m.attention) ? m.attention : []);
const reasonsOf = (m) => (Array.isArray(m.reasons) ? m.reasons : []);
/** Tooltip body for a month: totals, overspend reasons (red), attention reasons (amber). */
export function monthTip(m, extra) {
  return h('div', h('b', monthLabel(m.month, true)),
    h('div', `Income ${ils(m.income)} · Spent ${ils(m.expense, { abs: true })}`),
    m.net != null ? h('div', { class: m.net < 0 ? 'bad' : 'good' }, `Net ${ils(m.net, { sign: true })}`) : null,
    m.savings ? h('div.muted', `Saved ${ils(m.savings, { abs: true })}`) : null,
    m.overspend ? h('div.bad', { style: { marginTop: '4px' } }, h('b', 'Overspend'), reasonsOf(m).length ? h('ul', reasonsOf(m).map((r) => h('li', mixed(r)))) : null) : null,
    attn(m).length ? h('div.warn', { style: { marginTop: '4px' } }, h('b', 'Needs attention'), h('ul', attn(m).map((r) => h('li', mixed(r))))) : null,
    extra || null);
}
/** Month strip: mini bars (spent vs income) for quick navigation. */
export function monthStrip(months, current, onPick) {
  const vals = months.flatMap((m) => [Math.abs(m.expense || 0), m.income || 0]);
  const raw = Math.max(1, ...vals), top = robustTop(vals);
  const max = raw > top * 2.2 ? top * 1.2 : raw;
  const hpct = (v) => Math.min(100, (v / max) * 100) + '%';
  return h('div.month-strip', months.map((m) => {
    const exp = Math.abs(m.expense || 0);
    const cell = h('button.ms-cell', {
      class: (m.month === current ? 'active ' : '') + (m.overspend ? 'over' : attn(m).length ? 'attn' : ''),
      'aria-label': `${monthLabel(m.month, true)}: in ${ils(m.income)}, out ${ils(exp)}`,
      onclick: () => onPick(m.month),
    },
    h('div.ms-bars',
      h('span.ms-in', { style: { height: hpct(m.income || 0) } }),
      h('span.ms-out', { style: { height: hpct(exp) } })),
    h('span.ms-label', shortMonth(m.month)));
    tip(cell, () => monthTip(m));
    return cell;
  }));
}

/** Horizontal actual-vs-budget bar (HTML). */
export function budgetBar(actual, budget, { over } = {}) {
  const a = Math.abs(actual || 0);
  const b = budget ? Math.abs(budget) : 0;
  const scale = Math.max(a, b, 1);
  const isOver = over ?? (b > 0 && a > b);
  return h('div.bbar', { class: isOver ? 'over' : b ? '' : 'nob' },
    h('div.bbar-fill', { style: { width: (a / scale) * 100 + '%' } }),
    b ? h('div.bbar-budget', { style: { left: `calc(${(b / scale) * 100}% - 1px)` }, title: 'Budget ' + ils(b) }) : null);
}
