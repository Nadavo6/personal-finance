// Import & Sync: this month's checklist (Leumi file, Max file, sheet snapshot) with per-source
// freshness, upload with a result card, inbox import, live sync, and how-to-export steps.
import { h, data, clear, num, relTime, fmtDate, monthLabel, toast, toastError, asList } from './util.js';
import { api, changed, invalidateAccounts } from './api.js';

const STATUS = {
  ok: ['Up to date', 'good'], stale: ['Getting old', 'warn'], missing: ['Needed', 'bad'],
  idle: ['No recent rows', ''], optional: ['Occasional', ''],
};
const HINT = {
  leumi: 'Leumi PDF or Excel statement', max: 'Max xlsx export (covers every Max card)',
  sheet: 'Personal Google Sheet → data/sheet/snapshot_<date>.json',
};

/** "Missing Oct–Nov" from ['2026-10','2026-11'] */
function missingText(ms) {
  if (!ms?.length) return '';
  const a = monthLabel(ms[0]).split(' ')[0], b = monthLabel(ms[ms.length - 1]).split(' ')[0];
  return `Missing ${ms.length > 1 ? `${a}–${b}` : a}`;
}
export function statusChip(status, label) {
  const [txt, cls] = STATUS[status] || [status, ''];
  return h('span.badge.fresh-chip', { class: cls }, label || txt);
}
/** "data through 2 Nov · 8 days ago" */
export function throughText(s) {
  if (!s?.last_date) return 'no data yet';
  const d = s.days;
  return `data through ${fmtDate(s.last_date, d > 300)} · ${d === 0 ? 'today' : d === 1 ? 'yesterday' : `${d} days ago`}`;
}

export async function render(root) {
  root.append(h('div.page-head', h('h1', 'Import & Sync'), h('span.sub', 'Everything stays on this Mac')));
  let ov = null;
  try { ov = await api.get('/overview'); } catch { /* shown below */ }

  // ---------- shared upload ----------
  const fileIn = h('input', { type: 'file', multiple: true, accept: '.pdf,.xlsx,.xls,.csv,.html', hidden: true });
  const results = h('div.stack.import-results');
  fileIn.addEventListener('change', () => { upload([...fileIn.files]); fileIn.value = ''; });
  const dropTarget = (el) => {
    ['dragenter', 'dragover'].forEach((ev) => el.addEventListener(ev, (e) => { e.preventDefault(); el.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach((ev) => el.addEventListener(ev, (e) => { e.preventDefault(); el.classList.remove('drag'); }));
    el.addEventListener('drop', (e) => upload([...(e.dataTransfer?.files || [])]));
    return el;
  };

  async function upload(files) {
    for (const f of files) {
      const card = h('div.card.result-card', h('div.row', h('span.status-dot.run'), data(f.name), h('span.spacer'), h('span.muted.small', 'Importing…')));
      results.prepend(card);
      try {
        const r = await api.upload('/import', f);
        card.replaceWith(resultCard(f.name, r));
        changed('import'); invalidateAccounts();
      } catch (e) {
        card.replaceWith(h('div.card.result-card', h('div.row', h('span.status-dot.err'), data(f.name), h('span.spacer'), h('span.bad.small', e.message))));
      }
    }
    drawChecklist();
  }
  function resultCard(name, r) {
    const prov = { leumi: 'Leumi', max: 'Max', cal: 'Cal', riseup: 'RiseUp', generic: 'CSV' }[r?.provider] || r?.provider || 'File';
    const range = r?.first_date ? `covers ${fmtDate(r.first_date, true)} – ${fmtDate(r.last_date, true)}` : null;
    const n = r?.new_rows || 0;
    return h('div.card.result-card',
      h('div.row.wrap', h('span.status-dot.ok'), h('b', prov), data(name, 'muted small'), h('span.spacer'),
        n ? h('a.btn.sm.primary', { href: `#/transactions?import_id=${r.import_id}` }, `Review ${num(n)} new transaction${n === 1 ? '' : 's'} →`) : null),
      h('div.small', { style: { marginTop: '6px' } },
        `${num(r?.rows)} rows scanned · `, h('b', { class: n ? 'good' : '' }, `${num(n)} new`),
        r?.dup_rows ? ` · ${num(r.dup_rows)} already here` : '', range ? ` · ${range}` : ''),
      n ? null : h('div.small.muted', { style: { marginTop: '2px' } }, '✓ All caught up: everything in this file was already imported.'));
  }

  // ---------- this month's checklist ----------
  const checklist = h('div', h('div.loading', h('span.spin'), 'Checking your data…'));
  const sheetBtn = h('button.btn.sm', { onclick: importSheet }, 'Import snapshot');
  async function importSheet() {
    sheetBtn.disabled = true; sheetBtn.textContent = 'Importing…';
    try {
      const r = await api.post('/networth/import-sheet', {});
      const added = (r.assets_added || 0) + (r.planned_added || 0) + (r.tasks_added || 0) + (r.freebies_added || 0);
      toast(`${r.file}: ${num(r.values_written)} values updated, ${num(added)} new items`, { type: 'success', ms: 5000 });
      changed('networth');
    } catch (e) { toastError(e); }
    sheetBtn.disabled = false; sheetBtn.textContent = 'Import snapshot';
    drawChecklist();
  }
  async function drawChecklist() {
    let f;
    try { f = await api.get('/freshness'); } catch (e) { clear(checklist).append(h('div.error-box', e.message)); return; }
    const monthly = asList(f.sources, 'sources').filter((s) => s.monthly);
    const others = asList(f.sources, 'sources').filter((s) => !s.monthly);
    const row = (s, action) => {
      const done = s.status === 'ok';
      const li = s.last_import;
      const idle = asList(s.accounts).filter((a) => a.status === 'idle');
      return dropTarget(h('div.check-row', { class: done ? 'done' : '' },
        h('span.check-mark', { class: done ? 'good' : s.status === 'stale' ? 'warn' : 'bad', 'aria-hidden': 'true' }, done ? '✓' : s.status === 'stale' ? '!' : '○'),
        h('div.check-main',
          h('div.row.wrap', h('b', s.label), statusChip(s.status),
            s.status === 'missing' && s.missing_months?.length ? h('span.small.bad', missingText(s.missing_months)) : null),
          h('div.small', { class: done ? 'muted' : '' },
            s.key === 'sheet' ? (s.last_date ? `sheet updated ${fmtDate(s.last_date, true)} · ${s.days} days ago` : li ? 'imported' : 'not imported yet') : throughText(s),
            s.key === 'sheet' && s.days > 35 ? h('span.warn', ` · ⚠ ${s.days} days old: refresh the sheet snapshot (ask Claude: “finance monthly”), then import it`) : null),
          h('div.small.faint', HINT[s.key] || '', li ? [' · last import ', data(li.file_name || ''), ` ${relTime(li.imported_at)}`] : ''),
          idle.length ? h('div.small.faint', idle.map((a) => `${a.name || a.key}: no rows since ${fmtDate(a.last_date, true)} (replaced or unused card?)`).join(' · ')) : null),
        action));
    };
    const fileBtn = () => h('button.btn.sm', { onclick: () => fileIn.click() }, 'Upload file…');
    clear(checklist).append(...[
      ...monthly.map((s) => row(s, fileBtn())),
      row(f.sheet, sheetBtn),
      others.length ? h('div.small.muted', { style: { marginTop: '8px' } },
        others.map((s) => [s.label, ': ', throughText(s)]).flat(), ' · occasional, not needed monthly') : null].filter(Boolean));
    const left = (f.needs_update || []).length;
    clear(checkHead).append(left ? `${left} of ${monthly.length + 1} need an update` : '✓ All up to date');
    checkHead.className = 'small ' + (left ? 'muted' : 'good');
  }
  const checkHead = h('span.small.muted');
  root.append(h('div.card',
    h('div.card-head', h('h2', 'This month’s checklist'), checkHead),
    h('p.small.muted', { style: { margin: '-6px 0 10px' } }, 'Do these once a month (around the 2nd). Drop a file on its row or anywhere below; the app detects which bank it is and skips duplicates.'),
    checklist, fileIn));
  drawChecklist();

  // ---------- drop zone + inbox ----------
  const drop = dropTarget(h('div.dropzone', { tabindex: 0, role: 'button', onclick: () => fileIn.click(), onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileIn.click(); } } },
    h('div.big', 'Drop statements here'),
    h('div', 'Leumi PDF / Excel, Max xlsx, Cal xlsx or any CSV. Duplicates are skipped automatically.'),
    h('div.small.faint', { style: { marginTop: '6px' } }, 'or click to choose files')));
  const inboxBtn = h('button.btn.sm', {
    onclick: async () => {
      inboxBtn.disabled = true; inboxBtn.textContent = 'Importing inbox…';
      try {
        const r = await api.post('/import/inbox');
        const files = asList(r, 'files');
        if (!files.length) toast('Inbox is empty. Put files in data/inbox/ first.');
        files.forEach((f) => results.prepend(f.ok === false
          ? h('div.card.result-card', h('div.row', h('span.status-dot.err'), data(f.file_name), h('span.spacer'), h('span.bad.small', f.error)))
          : resultCard(f.file_name || 'file', f)));
        changed('import'); invalidateAccounts(); drawChecklist();
      } catch (e) { toastError(e); }
      inboxBtn.disabled = false; inboxBtn.textContent = 'Import inbox folder';
    },
  }, 'Import inbox folder');
  root.append(h('div.card.section',
    h('div.card-head', h('h2', 'Import files'), h('span.small.muted', 'Inbox folder: ', h('code', 'data/inbox/')), inboxBtn),
    drop, results));

  // ---------- sync (one line until credentials exist) ----------
  const status = h('div');
  const syncBtn = h('button.btn.sm', { onclick: startSync }, 'Sync now');
  const syncCard = h('div.card.section.sync-card');
  root.append(syncCard);
  let timer = null;
  function drawStatus(s) {
    clear(status);
    const provs = asList(s?.providers, 'providers');
    const ls = ov?.last_sync;
    clear(syncCard);
    if (!provs.length && !s?.running && !ls) {
      // never synced: no credentials yet, nothing to act on from here
      syncCard.append(h('div.row.small.muted', h('b', 'Live sync'), h('span', 'is not set up. It needs a one-time ', h('code', './finance sync'), ' in a terminal to store passwords in the Keychain. Until then, the monthly files above are all you need.'), h('span.spacer'), syncBtn));
      return;
    }
    syncCard.append(h('div.card-head', h('h2', 'Live sync'), h('span.muted.small', 'Leumi + Max via israeli-bank-scrapers. Passwords stay in the macOS Keychain.'), syncBtn), status);
    if (s?.running) status.append(h('div.provider-status', h('span.status-dot.run'), h('b', 'Syncing…'), h('span.muted.small', 'This can take a minute or two.')));
    if (!provs.length && !s?.running) status.append(h('div.muted', `Last sync ${relTime(ls.at)}${ls.ok === false ? ' (failed)' : ''}`));
    provs.forEach((p) => status.append(h('div.provider-status',
      h('span.status-dot', { class: p.ok ? 'ok' : p.ok === false ? 'err' : 'run' }),
      h('b', p.name), p.ok ? h('span.small', `${num(p.new_rows ?? 0)} new`) : null,
      p.error ? h('span.small.bad', p.error) : null,
      h('span.spacer'), h('span.small.faint', relTime(p.at)))));
    syncBtn.disabled = !!s?.running;
  }
  async function poll() {
    try {
      const s = await api.get('/sync/status');
      drawStatus(s);
      if (s?.running) timer = setTimeout(poll, 2000);
      else if (timer !== null) { timer = null; toast('Sync finished', { type: 'success' }); changed('sync'); invalidateAccounts(); drawChecklist(); }
    } catch (e) { clear(status).append(h('div.error-box', e.message)); }
  }
  async function startSync() {
    syncBtn.disabled = true;
    try { await api.post('/sync', {}); timer = 0; drawStatus({ running: true, providers: [] }); timer = setTimeout(poll, 1200); }
    catch (e) {
      if (e.status === 409) { toast('A sync is already running. Following it.'); timer = setTimeout(poll, 300); return; }
      toastError(e); syncBtn.disabled = false;
    }
  }
  try { const s = await api.get('/sync/status'); drawStatus(s); if (s?.running) { timer = setTimeout(poll, 2000); } }
  catch { drawStatus(null); }

  // ---------- how to export (collapsed) ----------
  root.append(h('details.card.section.howto',
    h('summary', h('b', 'How to export'), h('span.small.muted', ' · Leumi, Max, sheet snapshot, Cal')),
    h('div.grid-2', { style: { marginTop: '12px' } },
      h('div', h('h3', '1 · Leumi'),
        h('ol.steps',
          h('li', 'Log in to Leumi online banking (leumi.co.il) on the computer.'),
          h('li', 'Go to ', h('b', 'עו"ש ← תנועות בחשבון'), '.'),
          h('li', 'Pick the date range since the last import (overlap is fine; duplicates are skipped).'),
          h('li', 'Export to ', h('b', 'Excel'), ' (preferred) or save as PDF.'),
          h('li', 'Drop the file above, or move it to ', h('code', 'data/inbox/'), ' and click “Import inbox folder”.'))),
      h('div', h('h3', '2 · Max (all cards)'),
        h('ol.steps',
          h('li', 'Log in to max.co.il. One login covers every Max-issued card, bank-branded ones included.'),
          h('li', 'Open ', h('b', 'פירוט עסקאות'), ' and choose ', h('b', 'all cards'), '.'),
          h('li', 'Pick the billing months since the last import.'),
          h('li', 'Click ', h('b', 'ייצוא לאקסל'), '. The xlsx includes the foreign-currency sheet.'),
          h('li', 'Drop the .xlsx above.')),
        h('p.small.muted', 'Card bill lines on the bank side (מקס איט פיננ / לאומי ויזה) are treated as transfers, so nothing is counted twice.')),
      h('div', h('h3', '3 · Sheet snapshot (net worth)'),
        h('p.small', { style: { margin: '6px 0' } }, 'Write ', h('code', 'data/sheet/snapshot_<date>.json'), ' (format: ', h('code', 'examples/sheet_snapshot.example.json'), '; the optional Claude “finance monthly” skill can do it)',
          '. Then click “Import snapshot” in the checklist. Re-importing is safe: values are dated, and tasks and freebies are not duplicated.')),
      h('div', h('h3', '4 · Visa Cal'),
        h('ol.steps',
          h('li', 'Log in to cal-online.co.il and export the card’s transactions to Excel.'),
          h('li', 'Drop the file above. For a prepaid gift card, link the gift account to the freebie on the Freebies page.')),
        h('p.small.muted', 'By default a Cal card counts as a prepaid gift card (purchases excluded from your budget). Untick that in Settings for a regular Cal card.')))));

  return () => { if (timer) clearTimeout(timer); };
}
