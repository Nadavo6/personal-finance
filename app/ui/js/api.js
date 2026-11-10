// JSON API client (contract: docs/ARCHITECTURE.md) + small shared caches.
import { asList } from './util.js';

async function request(method, path, body, { raw = false } = {}) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch('/api' + path, opts);
  } catch (e) {
    throw new Error('Cannot reach the local server. Is ./finance running?');
  }
  if (!res.ok) {
    let msg = `${method} ${path} failed (${res.status})`;
    try {
      const j = await res.json();
      if (j?.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
    } catch { /* not json */ }
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  if (raw) return res;
  if (res.status === 204) return null;
  const txt = await res.text();
  return txt ? JSON.parse(txt) : null;
}

export function qs(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === '' || v === false) continue;
    p.set(k, v === true ? '1' : v);
  }
  const s = p.toString();
  return s ? '?' + s : '';
}

export const api = {
  get: (p, params) => request('GET', p + qs(params)),
  post: (p, b) => request('POST', p, b ?? {}),
  patch: (p, b) => request('PATCH', p, b),
  del: (p) => request('DELETE', p),
  upload: (p, file) => { const fd = new FormData(); fd.append('file', file); return request('POST', p, fd); },
};

// ---------- shared caches ----------
let _cats = null, _accounts = null;
export async function categories(force = false) {
  if (!_cats || force) _cats = asList(await api.get('/categories'));
  return _cats;
}
export function categoriesCached() { return _cats || []; }
export function invalidateCategories() { _cats = null; }
export async function accounts(force = false) {
  if (!_accounts || force) _accounts = asList(await api.get('/accounts'));
  return _accounts;
}
export function invalidateAccounts() { _accounts = null; }
export function catById(id) { return (_cats || []).find((c) => c.id === id); }
/** The "Uncategorized" bucket: the backend uses a real category (source 'default'); null is accepted too. */
export const isUncatCategory = (c) => !!c && /^uncategori[sz]ed$/i.test(c.name || '');
export function isUncat(t) {
  if (!t.category_id) return true;
  return isUncatCategory(catById(t.category_id)) || /^uncategori[sz]ed$/i.test(t.category_name || '');
}

/** Fetch transactions by id list. Uses ?ids= (extension) and filters client-side as a fallback. */
export async function transactionsByIds(ids, extra = {}) {
  if (!ids?.length) return [];
  const want = new Set(ids);
  const r = await api.get('/transactions', { ids: ids.join(','), limit: 5000, include_transfers: 1, ...extra });
  return asList(r).filter((t) => want.has(t.id));
}

// Badge refresh bus: views call changed() after mutations; main.js refreshes nav badges.
export const bus = new EventTarget();
export function changed(what = 'data') { bus.dispatchEvent(new CustomEvent('changed', { detail: what })); }
