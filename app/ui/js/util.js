// Shared helpers: DOM builder, formatting, dates, toasts, drawer, modal, tooltip.

/** Tiny hyperscript: h('div.card#x', {onclick, dataset, ...}, children...) */
export function h(tag, attrs, ...children) {
  let [name, ...rest] = tag.split(/(?=[.#])/);
  const el = document.createElement(name || 'div');
  for (const r of rest) {
    if (r[0] === '.') el.classList.add(r.slice(1));
    else if (r[0] === '#') el.id = r.slice(1);
  }
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (k === 'class') el.className = [el.className, v].filter(Boolean).join(' ');
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k === 'html') el.innerHTML = v;
      else if (k in el && typeof v !== 'string') el[k] = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c == null || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

/** Element holding user data (Hebrew merchant names etc.) */
export const data = (text, cls = '') => h('bdi', { dir: 'auto', class: cls }, text ?? '');
/** English sentence that may embed Hebrew names (insights, anomalies): keep LTR paragraph direction. */
export function mixed(text, cls = '') {
  const el = h('span', { dir: 'ltr', class: cls });
  // Isolate each Hebrew run so neighbouring punctuation/numbers keep English order.
  const re = /[\u0590-\u05FF](?:[\u0590-\u05FF\sA-Z0-9"'\u05F3\u05F4./&\-]*[\u0590-\u05FF])?/g;
  const str = String(text ?? '');
  let last = 0, m;
  while ((m = re.exec(str))) {
    if (m.index > last) el.append(str.slice(last, m.index));
    el.append(h('bdi', { dir: 'rtl' }, m[0]));
    last = m.index + m[0].length;
  }
  if (last < str.length) el.append(str.slice(last));
  return el;
}

export function clear(el) { while (el.firstChild) el.firstChild.remove(); return el; }

// ---------- numbers ----------
const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** ₪1,234 (no decimals) — summaries */
export function ils(v, { sign = false, abs = false } = {}) {
  if (v == null || Number.isNaN(+v)) return '—';
  let n = +v;
  if (abs) n = Math.abs(n);
  const s = nf0.format(Math.abs(Math.round(n)));
  const neg = Math.round(n) < 0;
  return (neg ? '−' : sign && n > 0 ? '+' : '') + '₪' + s;
}
/** ₪1,234.50 — transaction rows */
export function ils2(v) {
  if (v == null) return '—';
  const n = +v;
  return (n < 0 ? '−' : '') + '₪' + nf2.format(Math.abs(n));
}
export function num(v) { return v == null ? '—' : nf0.format(Math.round(+v)); }
export function money(v, cur) {
  if (v == null) return '';
  const sym = { USD: '$', EUR: '€', GBP: '£', ILS: '₪' }[cur] || (cur ? cur + ' ' : '');
  return (v < 0 ? '−' : '') + sym + nf2.format(Math.abs(v));
}
export const pct = (v) => (v == null ? '—' : Math.round(v) + '%');

// ---------- dates / months ----------
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export function monthLabel(m, long = false) {
  if (!m) return '';
  const [y, mm] = m.split('-').map(Number);
  if (long) return new Date(y, mm - 1, 1).toLocaleString('en-US', { month: 'long', year: 'numeric' });
  return `${MONTHS[mm - 1]} ${String(y).slice(2)}`;
}
export function shortMonth(m) { return m ? MONTHS[+m.split('-')[1] - 1] : ''; }
export function addMonths(m, n) {
  const [y, mm] = m.split('-').map(Number);
  const d = new Date(y, mm - 1 + n, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}
export function thisMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}
export function today() { return new Date().toISOString().slice(0, 10); }
export function fmtDate(s, withYear = false) {
  if (!s) return '';
  const [y, m, d] = s.slice(0, 10).split('-').map(Number);
  return `${d} ${MONTHS[m - 1]}` + (withYear ? ` ${y}` : '');
}
export function relTime(s) {
  if (!s) return 'never';
  const t = new Date(s.replace(' ', 'T'));
  if (Number.isNaN(+t)) return s;
  const sec = (Date.now() - t) / 1000;
  if (sec < 60) return 'just now';
  if (sec < 3600) return Math.round(sec / 60) + ' min ago';
  if (sec < 86400) return Math.round(sec / 3600) + ' h ago';
  const days = Math.round(sec / 86400);
  return days === 1 ? 'yesterday' : days + ' days ago';
}
export function monthEnd(m) {
  const [y, mm] = m.split('-').map(Number);
  const d = new Date(y, mm, 0);
  return `${m}-${String(d.getDate()).padStart(2, '0')}`;
}

// ---------- misc ----------
export function debounce(fn, ms = 250) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
/** Accept either a bare list or {items:[...]} (or another list key). */
export function asList(r, key = 'items') {
  if (Array.isArray(r)) return r;
  if (!r) return [];
  if (Array.isArray(r[key])) return r[key];
  for (const v of Object.values(r)) if (Array.isArray(v)) return v;
  return [];
}
export const byId = (list) => new Map(list.map((x) => [x.id, x]));

// ---------- toasts ----------
export function toast(msg, { type = 'info', action, ms = 3500 } = {}) {
  const box = document.getElementById('toasts');
  const t = h('div.toast', { class: 'toast-' + type, role: 'status' }, h('span', msg));
  if (action) {
    t.append(h('button.link', { onclick: () => { action.fn(); t.remove(); } }, action.label));
  }
  box.append(t);
  setTimeout(() => t.classList.add('out'), ms);
  setTimeout(() => t.remove(), ms + 300);
}
export function toastError(e) {
  console.warn(e);
  toast(e?.message || String(e), { type: 'error', ms: 6000 });
}

// ---------- drawer ----------
let drawerOnClose = null;
export function openDrawer(title, body, { subtitle, onClose } = {}) {
  const d = document.getElementById('drawer');
  const bd = document.getElementById('backdrop');
  clear(d);
  d.append(
    h('header.drawer-head',
      h('div', h('h2', title), subtitle ? h('div.muted.small', subtitle) : null),
      h('button.icon-btn', { title: 'Close (Esc)', 'aria-label': 'Close', onclick: closeDrawer }, '✕')),
    h('div.drawer-body', body));
  d.classList.add('open');
  bd.classList.add('open');
  d.setAttribute('aria-hidden', 'false');
  drawerOnClose = onClose || null;
  return d;
}
export function closeDrawer() {
  const d = document.getElementById('drawer');
  if (!d.classList.contains('open')) return false;
  d.classList.remove('open');
  document.getElementById('backdrop').classList.remove('open');
  d.setAttribute('aria-hidden', 'true');
  const fn = drawerOnClose; drawerOnClose = null;
  if (fn) fn();
  return true;
}

// ---------- modal ----------
/** openModal(title, bodyNode, {actions:[{label, primary, danger, fn(close)}]}) → close fn */
export function openModal(title, body, { actions = [], width } = {}) {
  const host = document.getElementById('modal-host');
  const close = () => { wrap.remove(); };
  const foot = h('footer.modal-foot', actions.map((a) =>
    h('button', {
      class: a.primary ? 'btn primary' : a.danger ? 'btn danger' : 'btn',
      type: a.primary ? 'submit' : 'button',
      onclick: a.primary ? null : (e) => { e.preventDefault(); a.fn ? a.fn(close) : close(); },
    }, a.label)));
  const primary = actions.find((a) => a.primary);
  const form = h('form.modal', {
    style: width ? { width } : null,
    onsubmit: (e) => { e.preventDefault(); primary?.fn(close); },
    onkeydown: (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } },
  }, h('header.modal-head', h('h2', title)), h('div.modal-body', body), foot);
  const wrap = h('div.modal-wrap', { onmousedown: (e) => { if (e.target === wrap) close(); } }, form);
  host.append(wrap);
  setTimeout(() => form.querySelector('input,select,textarea,button')?.focus(), 0);
  return close;
}
export function closeTopModal() {
  const host = document.getElementById('modal-host');
  const last = host.lastElementChild;
  if (last) { last.remove(); return true; }
  return false;
}

/** Labelled form field */
export function field(label, input, hint) {
  return h('label.field', h('span.field-label', label), input, hint ? h('span.field-hint', hint) : null);
}
/** Read named inputs of a container into an object (numbers for type=number, booleans for checkboxes). */
export function readForm(root) {
  const out = {};
  root.querySelectorAll('[name]').forEach((el) => {
    if (el.type === 'checkbox') out[el.name] = el.checked;
    else if (el.type === 'number') out[el.name] = el.value === '' ? null : +el.value;
    else out[el.name] = el.value === '' ? null : el.value;
  });
  return out;
}

// ---------- tooltip ----------
let tipEl;
export function showTip(evt, content) {
  if (!tipEl) { tipEl = h('div.tip', { role: 'tooltip' }); document.body.append(tipEl); }
  clear(tipEl);
  tipEl.append(content instanceof Node ? content : document.createTextNode(content));
  tipEl.style.display = 'block';
  const pad = 12;
  const { innerWidth: W, innerHeight: H } = window;
  const r = tipEl.getBoundingClientRect();
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + r.width > W - 8) x = evt.clientX - r.width - pad;
  if (y + r.height > H - 8) y = evt.clientY - r.height - pad;
  tipEl.style.left = x + 'px';
  tipEl.style.top = y + 'px';
}
export function hideTip() { if (tipEl) tipEl.style.display = 'none'; }
/** Attach hover tooltip to element; content may be a function. */
export function tip(el, content) {
  el.addEventListener('mousemove', (e) => showTip(e, typeof content === 'function' ? content() : content));
  el.addEventListener('mouseleave', hideTip);
  return el;
}

/** Explanatory empty state */
export function empty(title, text, action) {
  return h('div.empty', h('div.empty-title', title), text ? h('p.muted', text) : null, action || null);
}
export function spinner(label = 'Loading…') { return h('div.loading', h('span.spin'), label); }

/** Progress bar 0..100 */
export function progress(p, cls = '') {
  const v = Math.max(0, Math.min(100, p || 0));
  return h('div.progress', { class: cls }, h('div.progress-fill', { style: { width: v + '%' } }));
}
export function badge(text, cls = '') { return h('span.badge', { class: cls }, text); }
