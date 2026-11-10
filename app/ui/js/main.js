// App shell: hash router, left nav with badges, global keyboard shortcuts, theme.
import { h, clear, closeDrawer, closeTopModal, openModal, toastError, asList, spinner } from './util.js';
import { api, bus, categories } from './api.js';

const ROUTES = [
  { id: 'month', label: 'Month', ico: '◐', key: 'm', mod: './view-month.js' },
  { id: 'trends', label: 'Trends', ico: '↗', key: 't', mod: './view-trends.js' },
  { id: 'networth', label: 'Net worth', ico: '◇', key: 'n', mod: './view-networth.js' },
  { id: 'transactions', label: 'Transactions', ico: '≡', key: 'x', mod: './view-transactions.js' },
  { sep: true },
  { id: 'goals', label: 'Goals', ico: '◎', key: 'g', mod: './view-goals.js' },
  { id: 'freebies', label: 'Freebies', ico: '✦', key: 'f', mod: './view-freebies.js' },
  { id: 'tasks', label: 'Tasks', ico: '☐', key: 'k', mod: './view-tasks.js' },
  { id: 'insights', label: 'Review', ico: '✧', key: 'r', mod: './view-insights.js' },
  { sep: true },
  { id: 'rules', label: 'Rules & Categories', ico: '⚙', key: 'u', mod: './view-rules.js' },
  { id: 'import', label: 'Import & Sync', ico: '⇣', key: 'i', mod: './view-import.js' },
  { id: 'settings', label: 'Settings', ico: '⋯', key: 's', mod: './view-settings.js' },
];

/** Parse '#/route/arg?x=1' → {id, arg, params} */
export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, query = ''] = raw.split('?');
  const [id, ...rest] = path.split('/');
  return { id: id || 'month', arg: rest.join('/') || null, params: Object.fromEntries(new URLSearchParams(query)) };
}
export function go(id, arg, params) {
  const q = params ? new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== '')).toString() : '';
  location.hash = '#/' + id + (arg ? '/' + arg : '') + (q ? '?' + q : '');
}
/** Update hash params without re-rendering the view. */
export function setParams(params) {
  const { id, arg } = parseHash();
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== '' && v !== false)).toString();
  history.replaceState(null, '', '#/' + id + (arg ? '/' + arg : '') + (q ? '?' + q : ''));
}

const badgeEls = {};
function buildNav() {
  const ul = document.getElementById('nav-list');
  for (const r of ROUTES) {
    if (r.sep) { ul.append(h('li.nav-sep', { 'aria-hidden': 'true' })); continue; }
    const b = h('span.nav-badge', { hidden: true });
    badgeEls[r.id] = b;
    ul.append(h('li', h('a', { href: '#/' + r.id, dataset: { route: r.id }, title: `${r.label} (g ${r.key})` },
      h('span.nav-ico', { 'aria-hidden': 'true' }, r.ico), r.label, b)));
  }
}
function setBadge(id, n, hot = false) {
  const b = badgeEls[id];
  if (!b) return;
  b.hidden = !n;
  b.textContent = n > 99 ? '99+' : n;
  b.classList.toggle('hot', !!hot);
}

let badgeTimer;
async function refreshBadges() {
  try {
    const ov = await api.get('/overview');
    const [tasks, ins] = await Promise.all([
      ov?.open_tasks_count != null ? null : api.get('/tasks', { status: 'open' }).catch(() => []),
      api.get('/insights').catch(() => ({ items: [] })),
    ]);
    setBadge('transactions', ov?.uncategorized_count || 0, (ov?.uncategorized_count || 0) > 0);
    const exp = asList(ov?.freebies_expiring);
    setBadge('freebies', exp.length, exp.some((f) => (f.days_left ?? 99) < 14));
    setBadge('tasks', ov?.open_tasks_count ?? asList(tasks).filter((t) => (t.status || 'open') === 'open').length);
    const items = asList(ins);
    setBadge('insights', items.length, items.some((i) => i.severity === 'high'));
  } catch { /* server down: views show the error */ }
}
bus.addEventListener('changed', () => { clearTimeout(badgeTimer); badgeTimer = setTimeout(refreshBadges, 400); });

let cleanup = null;
let renderSeq = 0;
async function route() {
  const { id, arg, params } = parseHash();
  const r = ROUTES.find((x) => x.id === id) || ROUTES[0];
  document.querySelectorAll('#nav-list a').forEach((a) => a.classList.toggle('active', a.dataset.route === r.id));
  document.title = r.label + ' · Finance';
  closeDrawer();
  if (typeof cleanup === 'function') { try { cleanup(); } catch { /* ignore */ } }
  cleanup = null;
  const view = document.getElementById('view');
  const seq = ++renderSeq;
  clear(view).append(spinner());
  try {
    const mod = await import(r.mod);
    if (seq !== renderSeq) return;
    const root = h('div.page');
    clear(view).append(root);
    cleanup = await mod.render(root, { arg, params });
  } catch (e) {
    if (seq !== renderSeq) return;
    clear(view).append(h('div.error-box', h('b', 'Could not load this page. '), e.message || String(e)));
    console.error(e);
  }
}

// ---------- keyboard ----------
let gPending = false, gTimer;
function isTyping(e) {
  const t = e.target;
  return t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName));
}
function showShortcuts() {
  const rows = [
    ['g then m/t/n/x/g/f/k/r/u/i/s', 'Go to Month / Trends / Net worth / Transactions / Goals / Freebies / Tasks / Review / Rules / Import / Settings'],
    ['[ and ]', 'Previous / next month (Month, Transactions)'],
    ['/', 'Focus search (Transactions)'],
    ['1–9', 'Review mode: assign top category'],
    ['Enter', 'Review mode: accept suggestion'],
    ['S', 'Review mode: skip'],
    ['Esc', 'Close drawer / dialog'],
  ];
  openModal('Keyboard shortcuts', h('table.shortcuts', rows.map(([k, d]) => h('tr', h('td', h('kbd', k)), h('td.muted', d)))),
    { actions: [{ label: 'Close', primary: true, fn: (c) => c() }] });
}
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (document.querySelector('.combo')) return; // combobox handles its own Esc
    if (closeTopModal() || closeDrawer()) { e.preventDefault(); }
    return;
  }
  if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey) return;
  if (document.querySelector('.modal-wrap')) return;
  if (gPending) {
    gPending = false; clearTimeout(gTimer);
    const r = ROUTES.find((x) => x.key === e.key.toLowerCase());
    if (r) { e.preventDefault(); go(r.id); }
    return;
  }
  if (e.key === 'g') { gPending = true; gTimer = setTimeout(() => { gPending = false; }, 1200); return; }
  if (e.key === '?') { e.preventDefault(); showShortcuts(); }
});

// ---------- theme ----------
function initTheme() {
  const saved = (() => { try { return localStorage.getItem('theme'); } catch { return null; } })();
  if (saved) document.documentElement.dataset.theme = saved;
  document.getElementById('theme-toggle').addEventListener('click', () => {
    const cur = document.documentElement.dataset.theme
      || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('theme', next); } catch { /* ignore */ }
  });
}

async function boot() {
  const useMock = new URLSearchParams(location.search).has('mock');
  if (useMock) {
    const { installMock } = await import('../mock/mock.js');
    installMock();
    document.getElementById('mock-flag').hidden = false;
  }
  buildNav();
  initTheme();
  document.getElementById('help-btn').addEventListener('click', showShortcuts);
  document.getElementById('backdrop').addEventListener('click', closeDrawer);
  window.addEventListener('hashchange', route);
  categories().catch(toastError);
  if (!location.hash) history.replaceState(null, '', '#/month');
  await route();
  refreshBadges();
}
boot();
