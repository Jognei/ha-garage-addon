/* Parts Catalog single-page UI: garage, service tracking, parts, documents. Hash routes, no build step. */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const view = $('#view');
let REF = null;
// router() numbers each navigation; drawn is the newest one whose page is done. Only the newest draws into #view.
let navSeq = 0, drawn = 0;

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (v) => (v == null || v === '' ? '' : '$' + Number(v).toFixed(2));
const miles = (v) => (v == null || v === '' ? '' : Number(v).toLocaleString() + ' mi');
const num = (v) => (v == null || v === '' ? '' : Number(v).toLocaleString());
const datePlus = (days) => { const d = new Date(); d.setDate(d.getDate() + days); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };  // local, not UTC
const today = () => datePlus(0);
const sCls = (s) => 's-' + String(s || '').replace(/\s+/g, '-');
const fmtSize = (b) => (b > 1e6 ? (b / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1e3)) + ' KB');
const fileUrl = (d, page) => `files/${d.id || d.doc_id}/${encodeURIComponent(d.original_name || d.filename || d.doc_title || 'file')}${page ? '#page=' + page : ''}`;

const OFFLINE = "Couldn't reach the app. Check your connection and try again.";

// What a failed response says. data is its parsed body: {} when the server or a proxy answered with a page, not JSON.
const failMessage = (r, data) => data.error || (r.status === 413 ? 'That file is too big to send in one go' : `Something went wrong (HTTP ${r.status})`);

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = opts.method || 'POST';
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(opts.json);
  }
  const n = navSeq, loading = n !== drawn;  // asked while navigation n is drawing its page
  const r = await fetch('api' + path, init).catch(() => null);
  const data = r ? await r.json().catch(() => ({})) : {};
  if (loading && n !== navSeq) return new Promise(() => {});  // a newer navigation started: page n stops here, not over the new one
  if (!r) throw new Error(OFFLINE);
  if (!r.ok) throw new Error(failMessage(r, data));
  return data;
}

function toast(msg, bad) {
  const t = $('#toast');
  // an open modal dialog sits above everything else, so show the toast inside it
  const dlg = $('#dlg');
  const home = dlg.open ? dlg : document.body;
  if (t.parentNode !== home) home.appendChild(t);
  t.textContent = msg;
  t.className = 'toast' + (bad ? ' bad' : '');
  t.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (t.hidden = true), bad ? 6000 : 2200);
}
// a closed dialog hides what is inside it, so a toast raised in the dialog ("Part saved") moves back out when it closes
$('#dlg').addEventListener('close', (e) => {
  const t = $('#toast');
  if (!e.target.open && t.parentNode !== document.body) document.body.appendChild(t);
});

// Files go up in pieces: Home Assistant Ingress refuses a request body over 16 MiB. fields.purpose says what the
// upload is for (a document unless it says otherwise); this returns its id, for the request that uses it.
const CHUNK = 4 * 1024 * 1024;
async function uploadChunked(file, fields, onProgress) {
  const start = await api('/documents/chunked', { json: { ...fields, filename: file.name, size: file.size } });
  let sent = 0, tries = 0;
  onProgress(`Uploading ${file.name}… 0%`);
  while (sent < file.size) {
    const r = await fetch(`api/documents/chunked/${start.upload_id}?offset=${sent}`, { method: 'PUT', body: file.slice(sent, sent + CHUNK) }).catch(() => null);
    const d = r ? await r.json().catch(() => ({})) : {};
    if (r && r.status === 409 && d.received != null) { sent = d.received; continue; } // resume where the server is
    if (!r || !r.ok) {
      // a dropped connection or a failed piece: send it again a moment later (the 409 above finds where the server got to)
      if (++tries > 3) throw new Error(r ? failMessage(r, d) : OFFLINE);
      await new Promise((res) => setTimeout(res, 1000 * tries));
      continue;
    }
    tries = 0;
    sent = d.received;
    onProgress(`Uploading ${file.name}… ${Math.floor((100 * sent) / file.size)}%`);
  }
  return start.upload_id;
}

// A file that fits in one piece goes in the request as `field`; a bigger one goes up in pieces and the request names it.
async function attachFile(fd, field, file, purpose, onProgress) {
  if (file.size <= CHUNK) fd.append(field, file);
  else fd.append('upload', await uploadChunked(file, { purpose }, onProgress));
}

// Generic modal form. onSubmit(formData, form, okText) may throw to keep the dialog open; okText(t) relabels its OK button.
let dlgSeq = 0;  // counts openDialog calls: Cancel stays live while saving, so a save must leave a dialog opened after it alone
function openDialog(title, html, onSubmit, { ok = 'Save', wide = false } = {}) {
  const dlg = $('#dlg');
  const seq = ++dlgSeq, mine = () => seq === dlgSeq;
  $('#dlgTitle').textContent = title;
  $('#dlgBody').innerHTML = html;
  const okBtn = $('#dlgOk'), errLine = $('#dlgErr');
  okBtn.textContent = ok; okBtn.disabled = false;
  errLine.textContent = '';
  dlg.style.width = wide ? 'min(1000px, calc(100vw - 24px))' : '';
  const form = $('#dlgForm');
  let busy = false;
  form.onsubmit = async (e) => {
    e.preventDefault();
    if (busy) return;  // a second tap while saving would save twice
    busy = true; okBtn.disabled = true; okBtn.textContent = 'Saving…'; errLine.textContent = '';
    try {
      await onSubmit(Object.fromEntries(new FormData(form)), form, (t) => { if (mine()) okBtn.textContent = t; });
      if (mine()) dlg.close();
    } catch (err) {
      if (mine() && dlg.open) errLine.textContent = err.message;  // shown next to the buttons, inside the dialog
      else toast(err.message, true);
    } finally {
      busy = false;
      if (mine()) { okBtn.disabled = false; okBtn.textContent = ok; }
    }
  };
  $('#dlgCancel').onclick = $('#dlgX').onclick = () => dlg.close();
  dlg.showModal();
  const first = $('input:not([type=hidden]),select,textarea', $('#dlgBody'));
  if (first) first.focus();
  return form;
}

// A dialog's Delete button: the dialog closes once the delete is done, unless another one was opened meanwhile.
async function deleteInDialog(path) {
  const seq = dlgSeq;
  await api(path, { method: 'DELETE' });
  if (seq === dlgSeq) $('#dlg').close();
}

// A form outside a dialog: its button is off (and reads busyText) while the request runs, so a double tap can't send it twice.
async function submitting(form, work, busyText) {
  const btn = $('button:not([type=button])', form), idle = btn.textContent;
  if (btn.disabled) return;
  btn.disabled = true;
  if (busyText) btn.textContent = busyText;
  try { await work(); } finally { btn.disabled = false; btn.textContent = idle; }
}

const options = (list, sel, blank) => (blank !== undefined ? `<option value="">${esc(blank)}</option>` : '') +
  list.map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o]; return `<option value="${esc(v)}" ${String(v) === String(sel ?? '') ? 'selected' : ''}>${esc(l)}</option>`; }).join('');

// ---------- router ----------

const routes = [
  [/^\/?$/, home],
  [/^\/car\/(\d+)(?:\/(\w+))?$/, carPage],
  [/^\/parts$/, allPartsPage],
  [/^\/add-car$/, addCarPage],
  [/^\/settings$/, settingsPage],
];

async function router() {
  const mine = ++navSeq;
  view.setAttribute('aria-busy', 'true');
  try { await route(); } finally { if (mine === navSeq) { drawn = mine; view.removeAttribute('aria-busy'); } }
}

async function route() {
  const hash = location.hash.slice(1) || '/';
  const [path, qs] = hash.split('?');
  const params = new URLSearchParams(qs || '');
  const key = path.split('/')[1] || 'home';
  $$('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.nav === (['car', 'add-car'].includes(key) ? 'home' : key)));
  if (!REF) {
    try { REF = await api('/refdata'); } catch (e) {
      view.innerHTML = `<div class="panel empty">${esc(e.message)} <button class="link" id="retry">Retry</button></div>`;
      $('#retry').onclick = () => router();
      return;
    }
  }
  applyFeatures();
  for (const [re, fn] of routes) {
    const m = path.match(re);
    if (m) {
      try { await fn(...m.slice(1), params); } catch (e) { view.innerHTML = `<div class="panel empty">Something went wrong: ${esc(e.message)}</div>`; }
      return;
    }
  }
  view.innerHTML = '<div class="panel empty">Page not found. <a href="#/">Go home</a></div>';
}
// Optional parts of the UI (Settings switches).
function applyFeatures() {
  const on = !!(REF && REF.features && REF.features.catalog);
  $$('[data-nav="catalog"]').forEach((a) => { a.hidden = !on; });
}
window.addEventListener('hashchange', () => { router(); window.scrollTo(0, 0); });
// anything that fails without its own handler (a status change, a delete, a toggle) still says so
window.addEventListener('unhandledrejection', (e) => {
  const m = (e.reason && e.reason.message) || String(e.reason || 'Something went wrong');
  // a fetch() of our own that never got an answer, in Chrome's, Safari's and Firefox's words
  toast(/^(Failed to fetch|Load failed|NetworkError when attempting to fetch resource\.?)$/.test(m) ? OFFLINE : m, true);
});
$('#searchForm').addEventListener('submit', (e) => {
  e.preventDefault();
  location.hash = '#/parts?q=' + encodeURIComponent($('#searchBox').value.trim());
});
const rerender = () => router();

// ---------- shared bits ----------

function stateChip(u) {
  const label = { overdue: 'Overdue', soon: 'Due soon', ok: 'OK', unknown: 'Set last done', snoozed: 'Dismissed' }[u.state];
  return `<span class="chip ${u.state}">${label}</span>`;
}

function dueText(u) {
  const bits = [];
  if (u.miles_left != null) bits.push(u.miles_left < 0 ? `${num(-u.miles_left)} mi over` : `${num(u.miles_left)} mi left`);
  if (u.days_left != null) bits.push(u.days_left < 0 ? `${-u.days_left} days over` : `${u.days_left} days left`);
  if (u.est_date) bits.push('≈ ' + new Date(u.est_date + 'T12:00').toLocaleDateString(undefined, { month: 'short', year: 'numeric' }));
  return bits.join(' · ');
}

// Small line icons (drawn here so the app needs no icon font or network).
const ICONS = {
  drop: '<path d="M12 3c3 4 6 7.2 6 11a6 6 0 0 1-12 0c0-3.8 3-7 6-11z"/>',
  air: '<path d="M3 8h10a3 3 0 1 0-3-3"/><path d="M3 12h15a3 3 0 1 1-3 3"/><path d="M3 16h7"/>',
  bolt: '<path d="M13 2 5 14h6l-1 8 8-12h-6z"/>',
  temp: '<path d="M10 5a2 2 0 0 1 4 0v8.5a4 4 0 1 1-4 0z"/><path d="M12 10v7"/>',
  disc: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 5.5v1M12 17.5v1M5.5 12h1M17.5 12h1"/>',
  tire: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="M12 3v5M12 16v5M3 12h5M16 12h5"/>',
  gear: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="7"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
  battery: '<rect x="3" y="7" width="16" height="10" rx="2"/><path d="M21.5 10.5v3M7 12h3M8.5 10.5v3M13 12h3"/>',
  wiper: '<path d="M3 16a12.5 12.5 0 0 1 18 0"/><path d="M12 20 7.5 8.5"/><circle cx="12" cy="20" r="1"/>',
  wheel: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2"/><path d="M12 14v7M10.2 11.2 3.4 9.6M13.8 11.2l6.8-1.6"/>',
  belt: '<circle cx="7" cy="12" r="4"/><circle cx="18" cy="12" r="2.5"/><path d="M7 8l11 1.5M7 16l11-1.5"/>',
  fuel: '<path d="M5 20V5.5A1.5 1.5 0 0 1 6.5 4h6A1.5 1.5 0 0 1 14 5.5V20"/><path d="M3.5 20h12M7.5 8h4"/><path d="M14 11h1.5a1.5 1.5 0 0 1 1.5 1.5V16a1.5 1.5 0 0 0 3 0V9l-2.5-2.5"/>',
  snow: '<path d="M12 3v18M4.2 7.5l15.6 9M19.8 7.5l-15.6 9"/>',
  calendar: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M8 3v4M16 3v4"/>',
  shock: '<path d="M12 3v3M12 18v3"/><path d="M8 6h8l-8 3 8 3-8 3 8 3H8"/>',
  wrench: '<path d="M14.5 6.5a4 4 0 0 0 5 5L11 20a2.1 2.1 0 0 1-3-3z"/><path d="M14.5 6.5 17 4a5 5 0 0 0-5.6 6.6"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
};
const ico = (name) => `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.wrench}</svg>`;
const ICON_RULES = [
  [/wiper/, 'wiper'], [/brake|rotor|pads?\b/, 'disc'], [/tire|tyre/, 'tire'], [/alignment|steering|tie rod/, 'wheel'],
  [/coolant|radiator|thermostat|water pump/, 'temp'], [/a\/c|air con|cabin|microfilter/, 'snow'], [/air filter|intake/, 'air'],
  [/spark|plug|battery.*test|alternator|starter|bulb|electrical/, 'bolt'], [/battery/, 'battery'], [/fuel|diesel/, 'fuel'],
  [/belt|tensioner|pulley/, 'belt'], [/transmission|differential|transfer case|clutch|gear|drivetrain/, 'gear'],
  [/shock|strut|suspension|control arm|bushing/, 'shock'], [/inspection|registration|insurance|emission/, 'calendar'],
  [/oil|fluid|flush|lube/, 'drop'],
];
function svcIcon(title) {
  const t = String(title || '').toLowerCase();
  const hit = ICON_RULES.find(([re]) => re.test(t));
  return ico(hit ? hit[1] : 'wrench');
}

// How much of the interval is used up, as a thin bar (nothing for schedules that were never done).
function meter(u) {
  if (u.state === 'unknown' || u.rank == null || u.rank > 1e6) return '';
  const used = Math.round(100 * Math.min(1, Math.max(0.03, 1 - u.rank)));
  return `<div class="meter ${u.state}"><i style="width:${used}%"></i></div>`;
}

function intervalText(s) {
  return [s.interval_miles ? `every ${num(s.interval_miles)} mi` : '', s.interval_months ? `${s.interval_months} mo` : ''].filter(Boolean).join(' / ');
}

// Drag items by their handle to reorder them (mouse and touch). onDone() runs after a move.
function sortable(root, itemSel, handleSel, onDone) {
  $$(handleSel, root).forEach((h) => {
    h.onpointerdown = (e) => {
      const item = h.closest(itemSel);
      if (!item) return;
      e.preventDefault();
      item.classList.add('dragging');
      let moved = false;
      // listen on the window: moving the item in the page would drop a pointer capture on the handle
      const move = (ev) => {
        const over = document.elementFromPoint(ev.clientX, ev.clientY);
        const target = over && over.closest(itemSel);
        if (!target || target === item || target.parentNode !== item.parentNode) return;
        const r = target.getBoundingClientRect();
        const after = ev.clientY > r.top + r.height / 2;
        if ((after ? target.nextElementSibling : target) === item) return;
        target.parentNode.insertBefore(item, after ? target.nextElementSibling : target);
        moved = true;
      };
      const up = () => {
        ['pointermove', 'pointerup', 'pointercancel'].forEach((t, n) => window.removeEventListener(t, n ? up : move));
        item.classList.remove('dragging');
        if (moved) onDone();
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', up);
    };
  });
}

// "Dismiss for now": the schedule stops counting as due soon / overdue until a date.
function snoozeDialog(u) {
  const form = openDialog('Dismiss for now', `
    <p><b>${esc(u.title)}</b> stops showing as ${u.state === 'overdue' ? 'overdue' : 'due soon'} until the date you pick. Logging it as done clears this.</p>
    <div class="row">${[[7, '1 week'], [14, '2 weeks'], [30, '1 month'], [90, '3 months']].map(([d, l]) => `<button type="button" data-days="${d}">${l}</button>`).join('')}</div>
    <div class="form" style="margin-top:12px"><label>Until<input type="date" name="snoozed_until" value="${datePlus(14)}" required></label></div>`,
  async (data) => {
    await api('/schedules/' + u.id, { method: 'PUT', json: data });
    toast('Dismissed until ' + data.snoozed_until);
    rerender();
  }, { ok: 'Dismiss' });
  $$('[data-days]', form).forEach((b) => (b.onclick = () => { form.elements.snoozed_until.value = datePlus(+b.dataset.days); }));
}

// Parts that can still be used on this vehicle: its own that aren't installed, plus unassigned ones.
async function usableParts(car, keep = () => false) {
  const [own, loose] = await Promise.all([api(`/parts?car_id=${car.id}`), api('/parts?car_id=none')]);
  return own.concat(loose).filter((p) => p.status !== 'Installed' || keep(p));
}

// `projectId`: the project being edited, whose own parts need no "for" mark
const partSub = (p, projectId) => [p.brand, p.part_number, p.location, p.car_id ? '' : 'not assigned',
  p.project_title && p.project_id !== projectId ? 'for ' + p.project_title : ''].filter(Boolean).join(' · ');

async function updateMileage(car, value) {
  const m = parseInt(String(value).replace(/\D/g, ''), 10);
  if (!m) return toast('Enter the odometer reading', true);
  if (car.mileage && m < car.mileage && !confirm(`${num(m)} is less than the current ${num(car.mileage)}. Correct the odometer to ${num(m)}?`)) return;
  await api(`/cars/${car.id}/mileage`, { json: { mileage: m, correct: car.mileage && m < car.mileage } });
  toast(`Mileage set to ${num(m)}`);
  rerender();
}

// ---------- home ----------

async function home() {
  const all = await api('/cars');
  const cars = all.filter((c) => !c.archived);
  const hidden = all.filter((c) => c.archived);
  const K = REF.card;
  const projLine = (c) => {
    const p = c.projects[0];
    if (!K.project || !p) return '';
    const bits = [];
    if (p.tasks.length) bits.push(`${p.tasks.filter((t) => t.done).length}/${p.tasks.length} steps`);
    const toGet = p.parts.filter((x) => ['Needed', 'Check', 'Ordered'].includes(x.status)).length;
    if (p.parts.length) bits.push(toGet ? `${toGet} part${toGet > 1 ? 's' : ''} to get` : 'parts on hand');
    if (p.target_date) bits.push('by ' + p.target_date);
    if (c.projects.length > 1) bits.push(`+${c.projects.length - 1} more`);
    return `<a class="proj-line" href="#/car/${c.id}/projects"><span class="svc-ico">${ico('list')}</span><span class="pl-main"><span class="muted small">${p.status === 'In progress' ? 'Project in progress' : 'Next project'}</span>
      <b>${esc(p.title)}</b>${bits.length ? `<span class="muted small">${esc(bits.join(' · '))}</span>` : ''}</span></a>`;
  };
  view.innerHTML = `
    <div class="spread"><h1>Garage</h1><div class="row"><button id="cardOpts">Customize cards</button><a class="btn" href="#/add-car">＋ Add a vehicle</a></div></div>
    ${cars.length > 1 ? '<p class="muted small" style="margin:4px 0 0">Drag a card by its ⠿ handle (or use ↑ ↓) to change the order.</p>' : ''}
    <div class="grid" id="carGrid" style="margin-top:12px">
      ${cars.map((c, n) => `
        <div class="panel car-card ${c.overdue ? 'st-overdue' : c.due_soon ? 'st-soon' : c.schedules ? 'st-ok' : ''}" data-car="${c.id}">
          ${K.photo && c.photo_id ? `<a href="#/car/${c.id}"><img class="car-photo" src="${fileUrl({ id: c.photo_id, original_name: c.photo_name })}" alt="" loading="lazy"></a>` : ''}
          <div class="card-head">
              ${cars.length > 1 ? `<span class="drag-handle" draggable="true" title="Drag to reorder">⠿</span>` : ''}
              <div class="card-title"><h3><a href="#/car/${c.id}">${esc(c.name)}</a></h3>
              <div class="muted small">${esc([c.year, c.make, c.model, c.kind !== 'Car' ? c.kind : ''].filter(Boolean).join(' · '))}</div>
              ${c.overdue || c.due_soon ? `<div class="card-chips">${c.overdue ? `<span class="chip overdue">${c.overdue} overdue</span>` : ''}${c.due_soon ? `<span class="chip soon">${c.due_soon} due soon</span>` : ''}</div>` : ''}</div>
              ${cars.length > 1 ? `<span class="order-btns"><button class="link up" title="Move up" ${n === 0 ? 'disabled' : ''}>↑</button><button class="link down" title="Move down" ${n === cars.length - 1 ? 'disabled' : ''}>↓</button></span>` : ''}</div>
          ${K.mileage ? `<div class="card-miles">
            <div class="mileage-big">${c.mileage != null ? num(c.mileage) : '—'}<span class="unit">mi</span></div><div class="muted small">${c.mileage_date ? 'as of ' + esc(c.mileage_date) : 'no reading yet'}</div>
            <form class="mileage-form"><input name="m" inputmode="numeric" placeholder="New reading"><button>Update</button></form>
          </div>` : `<div class="muted small" style="margin:8px 0">${c.mileage != null ? miles(c.mileage) : 'No mileage yet'}${c.mileage_date ? ' · ' + esc(c.mileage_date) : ''}</div>`}
          ${c.upcoming.length ? `<div class="up-list">${c.upcoming.map((u) => `<div class="up-item ${u.state}" data-sid="${u.id}">
              <span class="svc-ico${c.upcoming.length > 1 ? ' grip" title="Drag to reorder' : ''}">${svcIcon(u.title)}</span>
              <div class="up-main"><div class="up-line"><span class="up-name">${esc(u.title)}</span><span class="up-due">${esc(u.state === 'unknown' ? 'Set last done' : dueText(u))}</span></div>${meter(u)}</div>
              <div class="up-actions">${['overdue', 'soon'].includes(u.state) ? '<button class="link snooze" title="Dismiss for now">✕</button>' : ''}<button class="link log" title="Log this service as done">✓</button></div>
            </div>`).join('')}</div>`
            : !c.schedules ? `<p class="muted small">No service schedule yet. <a href="#/car/${c.id}">Set one up</a>.</p>`
            : K.upcoming_rows && !c.snoozed ? '<p class="muted small">Nothing due.</p>' : ''}
          ${c.snoozed ? `<div class="muted small" style="margin-top:6px">${c.snoozed} dismissed for now · <a href="#/car/${c.id}">show</a></div>` : ''}
          ${projLine(c)}
          ${K.buttons ? `<div class="row" style="margin-top:12px">
            <button class="logsvc">＋ Log service</button>
            <a class="btn" href="#/car/${c.id}">Services</a>
            <a class="btn" href="#/car/${c.id}/parts">Parts <span class="muted">${c.parts_open} to get · ${c.parts_on_hand} on hand</span></a>
            ${c.documents ? `<a class="btn" href="#/car/${c.id}/documents">Manuals (${c.documents})</a>` : ''}
          </div>` : ''}
        </div>`).join('')}
    </div>
    ${hidden.length ? `<details class="hidden-cars"><summary>Hidden vehicles (${hidden.length})</summary>
      <div class="panel" style="margin-top:8px">${hidden.map((c) => `<div class="spread" style="padding:4px 0"><a href="#/car/${c.id}">${esc(c.name)}</a>
        <button class="link unhide" data-id="${c.id}">Show in garage</button></div>`).join('')}</div></details>` : ''}`;

  const saveOrder = async () => {
    const ids = $$('#carGrid [data-car]').map((el) => +el.dataset.car);
    await api('/cars/order', { json: { ids } });
    home();
  };
  $$('[data-car]').forEach((card) => {
    const car = cars.find((c) => c.id === +card.dataset.car);
    if ($('.mileage-form', card)) $('.mileage-form', card).onsubmit = (e) => { e.preventDefault(); submitting(e.target, () => updateMileage(car, e.target.m.value)); };
    const logFor = async (ids) => {
      const full = await api('/cars/' + car.id);
      serviceDialog(full, full.upcoming, null, ids);
    };
    $$('button.log', card).forEach((b) => (b.onclick = () => logFor([+b.closest('[data-sid]').dataset.sid])));
    $$('button.snooze', card).forEach((b) => (b.onclick = () => snoozeDialog(car.upcoming.find((u) => u.id === +b.closest('[data-sid]').dataset.sid))));
    if ($('.logsvc', card)) $('.logsvc', card).onclick = () => logFor([]);
    sortable(card, '.up-item', '.grip', async () => {
      await api(`/cars/${car.id}/schedules/order`, { json: { ids: $$('.up-item', card).map((el) => +el.dataset.sid) } });
      toast('Order saved');
    });
    const up = $('.up', card), down = $('.down', card);
    if (up) up.onclick = () => { card.previousElementSibling && card.parentNode.insertBefore(card, card.previousElementSibling); saveOrder(); };
    if (down) down.onclick = () => { card.nextElementSibling && card.parentNode.insertBefore(card.nextElementSibling, card); saveOrder(); };
    const handle = $('.drag-handle', card);
    if (handle) {
      handle.ondragstart = (e) => { e.dataTransfer.setData('text/plain', card.dataset.car); e.dataTransfer.setDragImage(card, 20, 20); card.classList.add('dragging'); };
      handle.ondragend = () => card.classList.remove('dragging');
    }
    card.ondragover = (e) => { e.preventDefault(); card.classList.add('drop-target'); };
    card.ondragleave = () => card.classList.remove('drop-target');
    card.ondrop = (e) => {
      e.preventDefault();
      card.classList.remove('drop-target');
      const moving = $(`#carGrid [data-car="${e.dataTransfer.getData('text/plain')}"]`);
      if (!moving || moving === card) return;
      const cards = $$('#carGrid [data-car]');
      card.parentNode.insertBefore(moving, cards.indexOf(moving) < cards.indexOf(card) ? card.nextSibling : card);
      saveOrder();
    };
  });
  $('#cardOpts').onclick = cardOptionsDialog;
  $$('button.unhide').forEach((b) => (b.onclick = async () => {
    await api('/cars/' + b.dataset.id, { method: 'PUT', json: { archived: false } });
    toast('Back in the garage'); home();
  }));
}

function cardOptionsDialog() {
  const K = REF.card;
  const sw = (k, label) => `<label><input type="checkbox" name="${k}" ${K[k] ? 'checked' : ''}> ${label}</label>`;
  openDialog('Customize cards', `
    <div class="checks" style="grid-template-columns:1fr">
      ${sw('photo', 'Vehicle photo')}
      ${sw('mileage', 'Large odometer with the “New reading” box')}
      ${sw('project', 'Next project')}
      ${sw('buttons', 'Buttons (Log service, Services, Parts, Manuals)')}
      ${sw('hide_ok', 'Hide services that are OK (keep due soon, overdue and “set last done”)')}
    </div>
    <div class="form" style="margin-top:12px"><label>Upcoming services shown (0 to 12)<input name="upcoming_rows" type="number" min="0" max="12" value="${K.upcoming_rows}"></label></div>
    <p class="muted small">Applies to every card, on every device.</p>`,
  async (data) => {
    const card = { upcoming_rows: data.upcoming_rows };
    ['photo', 'mileage', 'project', 'buttons', 'hide_ok'].forEach((k) => { card[k] = k in data; });
    await api('/settings', { method: 'PUT', json: { card } });
    REF = await api('/refdata');
    rerender();
  });
}

// ---------- car ----------

async function carPage(id, tab = 'services', params) {
  const car = await api('/cars/' + id);
  const tabs = [['services', 'Services'], ['projects', 'Projects'], ['parts', 'Parts'], ['documents', 'Manuals & files'], ['details', 'Details']];
  view.innerHTML = `
    <div class="spread">
      <div><div class="small"><a href="#/">Garage</a> ›</div><h1>${esc(car.name)}</h1>
        <div class="muted small">${esc([car.year, car.make, car.model, car.engine, car.transmission].filter(Boolean).join(' · '))}</div></div>
      <div class="panel row" style="padding:10px 14px">
        <div><div class="mileage-big">${car.mileage != null ? num(car.mileage) : '—'}</div><div class="muted small">miles${car.mileage_date ? ' · ' + esc(car.mileage_date) : ''}</div></div>
        <form class="row" id="mileForm"><input name="m" inputmode="numeric" placeholder="New reading" style="width:120px"><button>Update</button></form>
      </div>
    </div>
    <div class="panel car-notes" id="carNotes"></div>
    <div class="tabs">${tabs.map(([k, l]) => `<a href="#/car/${id}${k === 'services' ? '' : '/' + k}" class="${k === tab ? 'active' : ''}">${l}</a>`).join('')}</div>
    <div id="tab"></div>`;
  $('#mileForm').onsubmit = (e) => { e.preventDefault(); submitting(e.target, () => updateMileage(car, e.target.m.value)); };
  carNotes($('#carNotes'), car);
  const el = $('#tab');
  if (tab === 'projects') return projectsTab(el, car);
  if (tab === 'parts') return partsSection(el, { car, params });
  if (tab === 'documents') return documentsTab(el, car);
  if (tab === 'details') return carDetails(el, car);
  return servicesTab(el, car);
}

// Overall notes for a vehicle (history, quirks, what's been done to it), editable in place.
function carNotes(el, car) {
  const show = () => {
    el.innerHTML = car.notes
      ? `<div class="spread" style="align-items:flex-start"><div class="note-text small">${esc(car.notes)}</div><button class="link" id="editNotes">Edit</button></div>`
      : `<button class="link" id="editNotes">＋ Add notes about this vehicle</button>`;
    $('#editNotes', el).onclick = edit;
  };
  const edit = () => {
    el.innerHTML = `<form class="form" id="notesForm"><label class="wide">Vehicle notes<textarea name="notes" placeholder="History, quirks, mods, things to remember…">${esc(car.notes || '')}</textarea></label>
      <div class="row wide"><button class="primary">Save</button><button type="button" id="cancelNotes">Cancel</button></div></form>`;
    const f = $('#notesForm', el);
    f.elements.notes.focus();
    $('#cancelNotes', el).onclick = show;
    f.onsubmit = (e) => {
      e.preventDefault();
      submitting(f, async () => {
        const notes = f.elements.notes.value.trim();
        await api('/cars/' + car.id, { method: 'PUT', json: { notes } });
        car.notes = notes; toast('Notes saved'); show();
      });
    };
  };
  show();
}

// ----- services & upcoming -----

async function servicesTab(el, car) {
  const [services, docs] = await Promise.all([api(`/cars/${car.id}/services`), api(`/documents?car_id=${car.id}`)]);
  const up = car.upcoming;
  el.innerHTML = `
    <div class="spread"><h2 style="margin-top:0">Upcoming services</h2>
      <div class="row"><button id="addPresets">＋ Common services</button><button id="addSched">＋ Custom schedule</button><button class="primary" id="logSvc">Log a service</button></div></div>
    ${car.archived ? '<div class="callout-box" style="margin-bottom:12px">This vehicle is hidden from the garage and Home Assistant. <a href="#/car/' + car.id + '/details">Show it again</a> under Details.</div>' : ''}
    <div class="panel table-wrap" style="padding:0">${up.length ? `<table><thead><tr><th></th><th>Service</th><th class="hide-sm">Interval</th><th class="hide-sm">Last done</th><th>Due</th><th class="hide-sm"></th><th class="hide-sm">Manual</th><th></th></tr></thead><tbody>
      ${up.map((u) => `<tr class="upcoming-row" data-sid="${u.id}">
        <td style="width:1%"><span class="svc-ico ${u.state}${up.length > 1 ? ' grip" title="Drag to reorder' : ''}">${svcIcon(u.title)}</span></td>
        <td><b>${esc(u.title)}</b>${u.notes ? `<div class="small muted">${esc(u.notes)}</div>` : ''}</td>
        <td class="small hide-sm">${esc(intervalText(u))}</td>
        <td class="small hide-sm">${u.last_date || u.last_mileage != null ? `${esc(u.last_date || '')}${u.last_mileage != null ? ' @ ' + miles(u.last_mileage) : ''}` : '<span class="muted">never logged</span>'}</td>
        <td class="small">${[u.due_mileage != null ? miles(u.due_mileage) : '', esc(u.due_date || '')].filter(Boolean).join(' / ')}<div class="muted">${esc(dueText(u))}</div></td>
        <td class="hide-sm">${stateChip(u)}${u.state === 'snoozed' ? `<div class="small muted">until ${esc(u.snoozed_until)}</div>` : ''}</td>
        <td class="small hide-sm">${u.doc_id ? `<a href="${fileUrl({ id: u.doc_id, original_name: u.doc_title + '.pdf' }, u.doc_page)}" target="_blank">${esc(u.doc_title)}${u.doc_page ? ' p.' + u.doc_page : ''}</a>` : ''}</td>
        <td class="num up-act"><button class="done">✓ Done</button>
          ${['overdue', 'soon'].includes(u.state) ? '<button class="link snooze">Dismiss</button>' : ''}${u.state === 'snoozed' ? '<button class="link unsnooze">Bring back</button>' : ''}
          <button class="link edit">Edit</button></td></tr>`).join('')}
    </tbody></table>${up.some((u) => u.sort_order != null) ? '<div class="small muted" style="padding:8px 10px">In your own order. <button class="link" id="autoOrder">Back to most urgent first</button></div>' : ''}` : `<div class="empty">No schedules yet. Click <b>＋ Common services</b> to pick oil changes, filters, fluids and so on.</div>`}</div>

    <div class="spread" style="align-items:center"><h2>Service history</h2>${services.length ? `<a class="btn" href="api/cars/${car.id}/export/services.csv">⬇ Export CSV</a>` : ''}</div>
    <div class="panel table-wrap" style="padding:0">${services.length ? `<table><thead><tr><th>Date</th><th class="num">Mileage</th><th>Work</th><th class="hide-sm">Parts used</th><th class="num">Cost</th><th class="hide-sm">By</th><th></th></tr></thead><tbody>
      ${services.map((s) => `<tr data-svc="${s.id}"><td style="white-space:nowrap">${esc(s.date)}</td><td class="num">${num(s.mileage)}</td>
        <td><b>${esc(s.title)}</b>${s.notes ? `<div class="small muted" style="white-space:pre-wrap">${esc(s.notes)}</div>` : ''}</td>
        <td class="small hide-sm">${s.parts.map((p) => (p.qty > 1 ? p.qty + '× ' : '') + esc(p.name) + (p.part_number ? ` <span class="muted mono">${esc(p.part_number)}</span>` : '')).join('<br>')}</td>
        <td class="num">${money(s.cost)}</td><td class="small hide-sm">${esc(s.done_by)}</td>
        <td class="num"><button class="link edit">Edit</button></td></tr>`).join('')}
    </tbody></table>` : '<div class="empty">Nothing logged yet.</div>'}</div>`;

  $('#logSvc').onclick = () => serviceDialog(car, up, null, []);
  $('#addSched').onclick = () => scheduleDialog(car, docs, null);
  $('#addPresets').onclick = () => presetsDialog(car, up);
  $$('tr[data-sid]', el).forEach((tr) => {
    const s = up.find((u) => u.id === +tr.dataset.sid);
    $('.done', tr).onclick = () => serviceDialog(car, up, null, [s.id]);
    $('.edit', tr).onclick = () => scheduleDialog(car, docs, s);
    if ($('.snooze', tr)) $('.snooze', tr).onclick = () => snoozeDialog(s);
    if ($('.unsnooze', tr)) $('.unsnooze', tr).onclick = async () => { await api('/schedules/' + s.id, { method: 'PUT', json: { snoozed_until: null } }); rerender(); };
  });
  sortable(el, 'tr[data-sid]', '.grip', async () => {
    await api(`/cars/${car.id}/schedules/order`, { json: { ids: $$('tr[data-sid]', el).map((tr) => +tr.dataset.sid) } });
    rerender();
  });
  if ($('#autoOrder')) $('#autoOrder').onclick = async () => { await api(`/cars/${car.id}/schedules/order`, { json: { ids: [] } }); rerender(); };
  $$('tr[data-svc]', el).forEach((tr) => {
    const s = services.find((x) => x.id === +tr.dataset.svc);
    $('.edit', tr).onclick = () => serviceDialog(car, up, s, s.schedule_ids);
  });
}

async function serviceDialog(car, schedules, svc, checked, pre = {}) {
  const used = new Set(svc ? svc.parts.map((p) => p.id) : pre.partIds || []);
  const linked = new Set(svc ? svc.parts.map((p) => p.id) : []);
  const parts = await usableParts(car, (p) => svc && p.service_id === svc.id);
  const titleFrom = () => schedules.filter((s) => checked.includes(s.id)).map((s) => s.title).join(', ');
  const form = openDialog(svc ? 'Edit service' : 'Log a service', `
    <div class="form">
      <label>Date<input type="date" name="date" value="${esc(svc ? svc.date : today())}" required></label>
      <label>Mileage<input name="mileage" inputmode="numeric" value="${esc(svc ? svc.mileage ?? '' : car.mileage ?? '')}"></label>
      <label>Done by<input name="done_by" value="${esc(svc ? svc.done_by : 'DIY')}"></label>
      <label>Cost<input name="cost" inputmode="decimal" value="${esc(svc ? svc.cost ?? '' : '')}" placeholder="0.00"></label>
      <label class="wide">What was done<input name="title" required value="${esc(svc ? svc.title : pre.title || titleFrom())}" placeholder="Oil change, new thermostat…"></label>
    </div>
    ${svc ? '' : `<div class="quick">
      ${REF.quick_services.length ? `<div class="quick-list">${REF.quick_services.map((q) => `<button type="button" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div>` : ''}
      <a class="small muted quick-edit-link" href="#/settings?go=quick">${REF.quick_services.length ? 'Change these' : 'Add one-tap jobs (wash, fuel…)'}</a></div>`}
    <div class="form" style="margin-top:10px">
      <label class="wide">Notes<textarea name="notes" placeholder="Oil used, torque specs, what you found…">${esc(svc ? svc.notes : pre.notes || '')}</textarea></label>
    </div>
    ${schedules.length ? `<h3>This counts as (resets the schedule)</h3><div class="checks">${schedules.map((s) => `<label><input type="checkbox" name="sched" value="${s.id}" ${checked.includes(s.id) ? 'checked' : ''}> ${esc(s.title)}</label>`).join('')}</div>` : ''}
    ${parts.length ? `<h3>Parts used <span class="muted small" id="partsAt" style="font-weight:400"></span></h3>
      ${parts.length > 8 ? '<input id="partFilter" type="search" placeholder="Filter parts…" style="width:100%;margin-bottom:6px">' : ''}
      <div class="use-list">${parts.map((p) => `<label class="use-row" data-text="${esc([p.name, p.brand, p.part_number, p.location, p.category].join(' ').toLowerCase())}">
        <input type="checkbox" name="part" value="${p.id}" ${used.has(p.id) ? 'checked' : ''}>
        <span class="use-main"><b>${esc(p.name)}</b><span class="muted small">${esc(partSub(p))}</span></span>
        ${p.qty > 1 ? (linked.has(p.id) ? `<span class="small muted">×${p.qty}</span>` : `<span class="use-qty small">use <input class="useq" inputmode="numeric" value="${p.qty}"> of ${p.qty}</span>`) : ''}
        <span class="small ${sCls(p.status)}">${esc(p.status)}</span></label>`).join('')}</div>` : ''}
    ${svc ? '<div style="margin-top:14px"><button type="button" class="danger" id="delSvc">Delete this service</button></div>' : ''}`,
  async (data, f) => {
    const usedParts = $$('.use-row', f).filter((r) => $('input[name=part]', r).checked)
      .map((r) => {
        const n = $('.useq', r) ? parseInt($('.useq', r).value, 10) : NaN;
        return { id: +$('input[name=part]', r).value, qty: Number.isNaN(n) ? null : n };
      });
    const payload = { ...data, schedule_ids: $$('input[name=sched]:checked', f).map((i) => +i.value), parts: usedParts };
    delete payload.part;
    if (svc) await api('/services/' + svc.id, { method: 'PUT', json: payload });
    else await api(`/cars/${car.id}/services`, { json: payload });
    toast('Service saved');
    // the service is in: if what follows fails, the dialog still closes, or Save would post it a second time
    if (pre.onSaved) { try { await pre.onSaved(); } catch (e) { toast(e.message, true); } }
    rerender();
  });
  $$('input[name=sched]', form).forEach((cb) => (cb.onchange = () => {
    checked = $$('input[name=sched]:checked', form).map((i) => +i.value);
    if (!svc && !pre.title) form.elements.title.value = [titleFrom(), ...$$('.quick-list .on[data-q]', form).map((b) => b.dataset.q)].filter(Boolean).join(', ');
  }));
  // quick picks add to (or take out of) "What was done", only as a whole entry between commas
  // (the name is matched whole, not by splitting the title on commas: some job names contain one)
  const tidy = (t) => t.replace(/\s*,\s*(,\s*)+/g, ', ').replace(/^\s*,\s*|\s*,\s*$/g, '').trim();
  const entry = (q) => new RegExp(`(^|,)\\s*${q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*(?=,|$)`, 'g');
  if ($('.quick-edit-link', form)) $('.quick-edit-link', form).onclick = () => $('#dlg').close();
  const quickToggle = (b) => {
    const t = form.elements.title.value, q = b.dataset.q;
    const on = b.classList.contains('on');
    form.elements.title.value = on ? tidy(t.replace(entry(q), '$1')) : tidy(t ? `${t}, ${q}` : q);
    b.classList.toggle('on', !on);
  };
  $$('.quick-list button[data-q]', form).forEach((b) => {
    b.classList.toggle('on', entry(b.dataset.q).test(form.elements.title.value));
    b.onclick = () => quickToggle(b);
  });
  const at = () => {
    if (!$('#partsAt', form)) return;
    const m = parseInt(String(form.elements.mileage.value).replace(/\D/g, ''), 10);
    $('#partsAt', form).textContent = `ticked parts are marked installed${m ? ' at ' + num(m) + ' mi' : ''} on ${form.elements.date.value}`;
  };
  form.elements.mileage.oninput = form.elements.date.oninput = at;
  at();
  $$('.useq', form).forEach((i) => {
    i.onclick = (e) => e.preventDefault();  // clicking into the box shouldn't toggle the tick
    i.oninput = () => { $('input[name=part]', i.closest('.use-row')).checked = true; };
    i.onblur = () => {  // 1 up to the row's quantity, which the box starts at
      const n = parseInt(i.value, 10), max = +i.defaultValue;
      i.value = Number.isNaN(n) ? max : Math.min(max, Math.max(1, n));
    };
  });
  if ($('#partFilter', form)) $('#partFilter', form).oninput = (e) => {
    const q = e.target.value.trim().toLowerCase();
    $$('.use-row', form).forEach((r) => { r.hidden = !!q && !r.dataset.text.includes(q) && !$('input[name=part]', r).checked; });
  };
  if (svc) $('#delSvc').onclick = async () => {
    if (!confirm('Delete this service record?')) return;
    await deleteInDialog('/services/' + svc.id);
    rerender();
  };
}

// ----- projects: bigger jobs with steps to tick off and parts set aside -----

async function projectsTab(el, car) {
  const projects = await api(`/cars/${car.id}/projects`);
  const active = projects.filter((p) => p.status !== 'Done');
  const done = projects.filter((p) => p.status === 'Done');
  const chip = { Planned: 'unknown', 'In progress': 'soon', Done: 'ok' };
  const card = (p) => `<div class="panel proj" data-pid="${p.id}">
    <div class="spread"><div class="row" style="gap:8px">${p.status !== 'Done' && active.length > 1 ? '<span class="grip" title="Drag to reorder">⠿</span>' : ''}
        <h3 style="margin:0">${esc(p.title)}</h3><span class="chip ${chip[p.status]}">${esc(p.status)}</span></div>
      <div class="row">${p.status === 'Planned' ? '<button class="start">Start</button>' : ''}
        ${p.status !== 'Done' ? '<button class="finish">✓ Finish &amp; log</button>' : '<button class="link reopen">Reopen</button>'}<button class="link edit">Edit</button></div></div>
    ${p.target_date && p.status !== 'Done' ? `<div class="small muted">Target: ${esc(p.target_date)}</div>` : ''}${p.done_date ? `<div class="small muted">Finished ${esc(p.done_date)}</div>` : ''}
    ${p.notes ? `<div class="note small">${esc(p.notes)}</div>` : ''}
    ${p.tasks.length ? `<div class="checks">${p.tasks.map((t) => `<label class="${t.done ? 'done-task' : ''}"><input type="checkbox" data-tid="${t.id}" ${t.done ? 'checked' : ''}> ${esc(t.title)}</label>`).join('')}</div>` : ''}
    ${p.parts.length ? `<h3>Parts</h3><table class="small"><tbody>${p.parts.map((x) => `<tr><td>${x.qty > 1 ? x.qty + '× ' : ''}${esc(x.name)} <span class="muted mono">${esc(x.part_number)}</span></td>
      <td class="${sCls(x.status)}">${esc(x.status)}</td><td class="muted">${esc(x.location)}</td></tr>`).join('')}</tbody></table>` : ''}
  </div>`;
  el.innerHTML = `
    <div class="spread"><h2 style="margin-top:0">Projects</h2><button class="primary" id="addProj">＋ New project</button></div>
    ${active.length ? `<div class="proj-list">${active.map(card).join('')}</div>`
      : '<div class="panel empty">Nothing planned. A project is a bigger job (brake overhaul, suspension refresh) with steps to tick off and the parts you have set aside for it. The first one shows on the garage card as “Next project”.</div>'}
    ${done.length ? `<details class="hidden-cars"><summary>Finished (${done.length})</summary><div class="proj-list" style="margin-top:8px">${done.map(card).join('')}</div></details>` : ''}`;
  $('#addProj').onclick = () => projectDialog(car, null);
  const put = (p, json) => api('/projects/' + p.id, { method: 'PUT', json });
  $$('[data-pid]', el).forEach((box) => {
    const p = projects.find((x) => x.id === +box.dataset.pid);
    $('.edit', box).onclick = () => projectDialog(car, p);
    if ($('.start', box)) $('.start', box).onclick = async () => { await put(p, { status: 'In progress' }); rerender(); };
    if ($('.reopen', box)) $('.reopen', box).onclick = async () => { await put(p, { status: 'In progress' }); rerender(); };
    if ($('.finish', box)) $('.finish', box).onclick = () => {
      // the project only becomes Done when the service is saved; Cancel leaves it as it was.
      // Notes list the steps that were done, plus the project's own notes; only parts on hand are pre-ticked.
      const done = p.tasks.filter((t) => t.done).map((t) => '- ' + t.title);
      serviceDialog(car, car.upcoming, null, [], { title: p.title, notes: [done.join('\n'), p.notes].filter(Boolean).join('\n\n'),
        partIds: p.parts.filter((x) => x.status === 'On hand').map((x) => x.id),
        onSaved: () => put(p, { status: 'Done' }).catch((e) => { throw new Error(`Service saved, but the project isn't marked done (${e.message}). Use Edit to set it to Done.`); }) });
    };
    $$('[data-tid]', box).forEach((cb) => (cb.onchange = async () => {
      await api('/project-tasks/' + cb.dataset.tid, { method: 'PUT', json: { done: cb.checked } });
      cb.parentNode.classList.toggle('done-task', cb.checked);
    }));
  });
  sortable(el, '.proj', '.grip', async () => {
    await api(`/cars/${car.id}/projects/order`, { json: { ids: $$('.proj', el).map((b) => +b.dataset.pid) } });
    toast('Order saved');
  });
}

async function projectDialog(car, p) {
  const mine = new Set(p ? p.parts.map((x) => x.id) : []);
  const parts = await usableParts(car, (x) => mine.has(x.id));
  // each step is its own box carrying its id, so retyping a step keeps its tick
  const stepRow = (t) => `<div class="row" style="gap:6px"><input class="step" aria-label="Step" data-id="${t.id || ''}" value="${esc(t.title || '')}" placeholder="${esc(t.hint || '')}" style="flex:1">
    <button type="button" class="link" title="Remove this step" aria-label="Remove this step">✕</button></div>`;
  const form = openDialog(p ? 'Edit project' : 'New project', `
    <div class="form">
      <label class="wide">Project<input name="title" required value="${esc(p ? p.title : '')}" placeholder="e.g. Suspension refresh"></label>
      <label>Status<select name="status">${options(REF.project_statuses, p ? p.status : 'Planned')}</select></label>
      <label>Target date<input type="date" name="target_date" value="${esc(p ? p.target_date || '' : '')}"></label>
      <label class="wide">Steps<div id="steps" style="display:grid;gap:6px">${(p && p.tasks.length ? p.tasks : [{ hint: 'e.g. Order parts' }]).map(stepRow).join('')}</div>
        <button type="button" class="link" id="addStep" style="align-self:flex-start;color:var(--accent)">＋ Add a step</button></label>
      <label class="wide">Notes<textarea name="notes" placeholder="Plan, torque specs, things to order…">${esc(p ? p.notes : '')}</textarea></label>
    </div>
    ${parts.length ? `<h3>Parts for this project</h3>
      ${parts.length > 8 ? '<input id="partFilter" type="search" placeholder="Filter parts…" style="width:100%;margin-bottom:6px">' : ''}
      <div class="use-list">${parts.map((x) => `<label class="use-row" data-text="${esc([x.name, x.brand, x.part_number, x.location, x.category].join(' ').toLowerCase())}">
        <input type="checkbox" name="part" value="${x.id}" ${mine.has(x.id) ? 'checked' : ''}>
        <span class="use-main"><b>${esc(x.name)}</b><span class="muted small">${esc(partSub(x, p && p.id))}</span></span>
        <span class="small ${sCls(x.status)}">${esc(x.status)}</span></label>`).join('')}</div>` : ''}
    ${p ? '<div style="margin-top:14px"><button type="button" class="danger" id="delProj">Delete project</button></div>' : ''}`,
  async (data, f) => {
    const payload = { title: data.title, status: data.status, target_date: data.target_date, notes: data.notes,
      tasks: $$('.step', f).map((i) => ({ id: +i.dataset.id || undefined, title: i.value.trim() })).filter((t) => t.title),
      part_ids: $$('input[name=part]:checked', f).map((i) => +i.value) };
    if (p) await api('/projects/' + p.id, { method: 'PUT', json: payload });
    else await api(`/cars/${car.id}/projects`, { json: payload });
    toast('Project saved');
    rerender();
  });
  const steps = $('#steps', form);
  const addStep = (row) => {
    if (row) row.insertAdjacentHTML('afterend', stepRow({})); else steps.insertAdjacentHTML('beforeend', stepRow({}));
    $('.step', row ? row.nextElementSibling : steps.lastElementChild).focus();
  };
  $('#addStep', form).onclick = () => addStep();
  steps.onkeydown = (e) => {
    if (e.key !== 'Enter' || !e.target.classList.contains('step')) return;
    e.preventDefault();  // Enter adds a step after this one instead of saving the project
    addStep(e.target.parentNode);
  };
  steps.onclick = (e) => { if (e.target.matches('button')) e.target.parentNode.remove(); };
  if ($('#partFilter', form)) $('#partFilter', form).oninput = (e) => {
    const q = e.target.value.trim().toLowerCase();
    $$('.use-row', form).forEach((r) => { r.hidden = !!q && !r.dataset.text.includes(q) && !$('input[name=part]', r).checked; });
  };
  if (p) $('#delProj', form).onclick = async () => {
    if (!confirm(`Delete the project “${p.title}”? Its parts stay in your parts list.`)) return;
    await deleteInDialog('/projects/' + p.id);
    rerender();
  };
}

function scheduleDialog(car, docs, s) {
  const manuals = docs.map((d) => [d.id, d.title]);
  const form = openDialog(s ? 'Edit schedule' : 'New service schedule', `
    <div class="form">
      <label class="wide">Service<input name="title" required value="${esc(s ? s.title : '')}" placeholder="e.g. Valve adjustment"></label>
      <label>Every (miles)<input name="interval_miles" inputmode="numeric" value="${esc(s ? s.interval_miles ?? '' : '')}"></label>
      <label>Every (months)<input name="interval_months" inputmode="numeric" value="${esc(s ? s.interval_months ?? '' : '')}"></label>
      <label>Last done (date)<input type="date" name="last_date" value="${esc(s ? s.last_date ?? '' : '')}"></label>
      <label>Last done (mileage)<input name="last_mileage" inputmode="numeric" value="${esc(s ? s.last_mileage ?? '' : '')}"></label>
      <label>Manual<select name="doc_id">${options(manuals, s && s.doc_id, '—')}</select></label>
      <label>Page<input name="doc_page" inputmode="numeric" value="${esc(s ? s.doc_page ?? '' : '')}"></label>
      <label class="wide">Notes<input name="notes" value="${esc(s ? s.notes : '')}" placeholder="Oil type, capacity, part numbers…"></label>
    </div>
    <p class="small muted">Logging a service that counts as this one updates “last done” automatically.</p>
    ${s ? '<button type="button" class="danger" id="delSched">Delete schedule</button>' : ''}`,
  async (data) => {
    if (s) await api('/schedules/' + s.id, { method: 'PUT', json: data });
    else await api(`/cars/${car.id}/schedules`, { json: data });
    rerender();
  });
  if (s) $('#delSched', form).onclick = async () => {
    if (!confirm(`Delete the “${s.title}” schedule? Service history stays.`)) return;
    await deleteInDialog('/schedules/' + s.id);
    rerender();
  };
}

function presetsDialog(car, existing) {
  const have = new Set(existing.map((u) => u.title.toLowerCase()));
  const list = REF.presets.filter((p) => !have.has(p.title.toLowerCase()));
  const form = openDialog('Add common services', `
    <p class="muted small">These are repeating services. For a one-off job (brake job, battery…) use <b>Log a service</b>; for anything not listed, <b>＋ Custom schedule</b>.</p>
    <p class="muted small">Tick what applies to this vehicle. Change intervals to match your manual. If you know when each was last done, fill it in so “upcoming” is accurate (you can also do that later).</p>
    <div class="table-wrap"><table class="items-table"><thead><tr><th></th><th>Service</th><th>Miles</th><th>Months</th><th>Last done date</th><th>Last done mi</th></tr></thead><tbody>
      ${list.map((p, i) => `${i === 0 || list[i - 1].group !== p.group ? `<tr class="group-row"><td colspan="6">${esc(p.group)}</td></tr>` : ''}<tr data-i="${i}"><td><input type="checkbox" class="pick"></td><td>${esc(p.title)}</td>
        <td><input class="im" inputmode="numeric" value="${p.interval_miles ?? ''}" style="width:80px"></td><td><input class="imo" inputmode="numeric" value="${p.interval_months ?? ''}" style="width:60px"></td>
        <td><input type="date" class="ld"></td><td><input class="lm" inputmode="numeric" style="width:90px"></td></tr>`).join('')}
    </tbody></table></div>`,
  async (_, f) => {
    const items = $$('tr[data-i]', f).filter((tr) => $('.pick', tr).checked).map((tr) => ({
      title: list[+tr.dataset.i].title, interval_miles: $('.im', tr).value, interval_months: $('.imo', tr).value,
      last_date: $('.ld', tr).value, last_mileage: $('.lm', tr).value,
    }));
    if (!items.length) throw new Error('Tick at least one service');
    await api(`/cars/${car.id}/schedules`, { json: { items } });
    toast(`Added ${items.length} schedules`);
    rerender();
  }, { ok: 'Add selected', wide: true });
  // filling in a row picks it, so what was typed isn't dropped
  $('.items-table', form).oninput = (e) => {
    const tr = e.target.closest('tr[data-i]');
    if (tr && !e.target.classList.contains('pick')) $('.pick', tr).checked = true;
  };
}

// ----- parts (per car or all) -----

async function partsSection(el, { car = null, params = new URLSearchParams() } = {}) {
  const cars = await api('/cars');
  const filter = params.get('status') || localStorage.getItem('partsFilter') || 'all';
  const q = params.get('q') || '';
  const carFilter = car ? String(car.id) : (params.get('car') || '');
  let qs = [];
  if (carFilter) qs.push('car_id=' + carFilter);
  if (q) qs.push('q=' + encodeURIComponent(q));
  const groups = { all: '', get: 'Needed,Check,Ordered', onhand: 'On hand', installed: 'Installed' };
  if (groups[filter]) qs.push('status=' + encodeURIComponent(groups[filter]));
  const parts = await api('/parts?' + qs.join('&'));
  const total = parts.filter((p) => ['Needed', 'Check', 'Ordered'].includes(p.status)).reduce((n, p) => n + (p.price || 0) * (p.qty || 1), 0);
  const filtered = !!groups[filter] || (!car && !!carFilter);
  const carName = (cars.find((c) => String(c.id) === carFilter) || {}).name || 'this vehicle';
  const none = !filtered && !q ? 'No parts here yet.' : [
    `No ${!car && carFilter === 'none' ? 'unassigned ' : ''}parts`,
    { get: ' to get', onhand: ' on hand', installed: ' installed' }[filter] || '',
    !car && carFilter && carFilter !== 'none' ? ' for ' + esc(carName) : '',
    q ? ` matching “${esc(q)}”.` : '.',
    filtered ? ' <button class="link" id="showAll">Show all</button>' : '',
  ].join('');
  el.innerHTML = `
    <div class="spread" style="margin-bottom:12px">
      <div class="row">
        ${[['all', 'All'], ['get', 'To get'], ['onhand', 'On hand'], ['installed', 'Installed']].map(([k, l]) => `<button class="${k === filter ? 'primary' : ''}" data-f="${k}">${l}</button>`).join('')}
        ${!car ? `<select id="carF">${options([['', 'All vehicles'], ['none', 'Not assigned'], ...cars.map((c) => [c.id, c.name])], carFilter)}</select>` : ''}
        ${q ? `<span class="muted">matching “${esc(q)}” <a href="#/parts">clear</a></span>` : ''}
      </div>
      <div class="row"><a class="btn" href="api/export/parts.csv${qs.length ? '?' + qs.join('&') : ''}" title="Download as a spreadsheet">⬇ CSV</a><button id="addPart">＋ Add part</button><button class="primary" id="addPurchase">＋ Add purchase / receipt</button></div>
    </div>
    ${total ? `<p class="muted small">To get: <b>${money(total)}</b> at listed prices.</p>` : ''}
    <div class="panel table-wrap" style="padding:0">${parts.length ? `<table id="partsTable"><thead><tr>
      <th title="Installed">✓</th><th>Part</th>${car ? '' : '<th class="hide-sm">Vehicle</th>'}<th class="num">Qty</th><th>Status</th><th class="hide-sm">Installed at</th><th class="hide-sm">Installed on</th><th class="hide-sm">Location</th><th class="hide-sm">Bought</th><th></th></tr></thead><tbody>
      ${parts.map((p) => `<tr data-pid="${p.id}" class="${p.status === 'Installed' ? 'installed' : ''}">
        <td><input type="checkbox" class="inst" ${p.status === 'Installed' ? 'checked' : ''} title="Installed"></td>
        <td><b>${esc(p.name)}</b><div class="small muted">${[esc(p.category), esc(p.brand), REF.features.catalog && /^\w+$/.test(normPn(p.part_number)) ? `<a href="#/catalog/part/${normPn(p.part_number)}">${esc(p.part_number)}</a>` : esc(p.part_number), p.project_title ? esc('for ' + p.project_title) : ''].filter(Boolean).join(' · ')}</div>${p.notes ? `<div class="small muted" title="${esc(p.notes)}">${esc(p.notes.length > 90 ? p.notes.slice(0, 90) + '…' : p.notes)}</div>` : ''}</td>
        ${car ? '' : `<td class="small hide-sm">${esc(p.car || '—')}</td>`}
        <td class="num"><input class="inline qty" inputmode="numeric" value="${p.qty ?? 1}" style="width:52px;text-align:right"></td>
        <td><select class="status ${sCls(p.status)}">${options(REF.statuses, p.status)}</select></td>
        <td class="hide-sm"><input class="inline mi" inputmode="numeric" value="${p.installed_mileage ?? ''}" placeholder="miles"></td>
        <td class="hide-sm"><input class="inline idate" type="date" value="${esc(p.installed_date || '')}" style="width:140px"></td>
        <td class="hide-sm"><input class="inline loc" value="${esc(p.location)}" placeholder="Garage > Shelf A"></td>
        <td class="small hide-sm">${[esc(p.supplier), money(p.price), esc(p.purchased_date || '')].filter(Boolean).join(' · ')}${p.url ? ` <a href="${esc(p.url)}" target="_blank" rel="noopener">↗</a>` : ''}${p.document_id ? `<div><a href="${fileUrl({ id: p.document_id, original_name: p.document_file })}" target="_blank">receipt</a></div>` : ''}</td>
        <td class="num"><button class="link edit">Edit</button></td></tr>`).join('')}
    </tbody></table>` : `<div class="empty">${none}</div>`}</div>
    ${carFilter === 'none' ? '<h2>Unassigned files</h2><div id="looseFiles"></div>' : ''}`;

  // filters go into the address, replaced rather than added so Back still leaves the page; the router draws it, so the last tap wins
  const refilter = (next) => {
    history.replaceState(null, '', `#${car ? '/car/' + car.id : ''}/parts${String(next) ? '?' + next : ''}`);
    rerender();
  };
  const pick = (f) => { localStorage.setItem('partsFilter', f); return withParam(params, 'status', f); };
  $$('[data-f]', el).forEach((b) => (b.onclick = () => refilter(pick(b.dataset.f))));
  if (!car) $('#carF').onchange = (e) => refilter(withParam(params, 'car', e.target.value));
  if ($('#showAll', el)) $('#showAll', el).onclick = () => refilter(withParam(pick('all'), 'car', ''));
  $('#addPart').onclick = () => partDialog(null, cars, car);
  $('#addPurchase').onclick = () => purchaseDialog(cars, car);
  const reload = () => partsSection(el, { car, params });
  $$('tr[data-pid]', el).forEach((tr) => {
    const p = parts.find((x) => x.id === +tr.dataset.pid);
    const save = async (vals, msg) => { await api('/parts/' + p.id, { method: 'PUT', json: vals }); if (msg) toast(msg); };
    $('.inst', tr).onchange = async (e) => {
      if (e.target.checked) {
        const carMi = (cars.find((c) => c.id === p.car_id) || {}).mileage;
        await save({ status: 'Installed', installed_mileage: p.installed_mileage ?? carMi ?? null, installed_date: p.installed_date || today() }, 'Marked installed');
      } else {
        const mi = $('.mi', tr).value.trim(), on = $('.idate', tr).value;
        const lost = [mi && `mileage (${mi})`, on && `date (${on})`].filter(Boolean).join(' and ');
        if (lost && !confirm(`Move ${p.name} back to on hand? Its install ${lost} will be cleared.`)) { e.target.checked = true; return; }
        await save({ status: 'On hand', installed_mileage: null, installed_date: null }, 'Back to on hand');
      }
      reload();
    };
    $('.status', tr).onchange = async (e) => { await save({ status: e.target.value }, 'Status saved'); reload(); };
    $('.qty', tr).onchange = (e) => save({ qty: e.target.value }, 'Saved');
    $('.mi', tr).onchange = (e) => save({ installed_mileage: e.target.value }, 'Mileage saved');
    $('.idate', tr).onchange = (e) => save({ installed_date: e.target.value }, 'Saved');
    $('.loc', tr).onchange = (e) => save({ location: e.target.value }, 'Location saved');
    $('.edit', tr).onclick = () => partDialog(p, cars, car);
  });
  if (carFilter === 'none') await documentsTab($('#looseFiles', el), null);
}

function withParam(params, k, v) {
  const p = new URLSearchParams(params);
  if (v && v !== 'all') p.set(k, v); else p.delete(k);
  return p;
}

function partDialog(p, cars, car) {
  const v = p || { status: 'Needed', qty: 1, car_id: car ? car.id : '' };
  const form = openDialog(p ? 'Edit part' : 'Add part', `
    <div class="form">
      <label class="wide">Name<input name="name" required value="${esc(v.name || '')}" placeholder="e.g. Thermostat"></label>
      <label>Category<input name="category" value="${esc(v.category || '')}" placeholder="Engine, Brakes…"></label>
      <label>Part number<input name="part_number" value="${esc(v.part_number || '')}"></label>
      <label>Brand<input name="brand" value="${esc(v.brand || '')}"></label>
      <label>Vehicle<select name="car_id">${options(cars.map((c) => [c.id, c.name]), v.car_id, 'Not assigned')}</select></label>
      <label>Qty<input name="qty" inputmode="numeric" value="${esc(v.qty ?? 1)}"></label>
      <label>Status<select name="status">${options(REF.statuses, v.status)}</select></label>
      <label>Location<input name="location" value="${esc(v.location || '')}" placeholder="Garage > Shelf A > Bin 3"></label>
      <label>Supplier<input name="supplier" value="${esc(v.supplier || '')}"></label>
      <label>Price (each)<input name="price" inputmode="decimal" value="${esc(v.price ?? '')}"></label>
      <label>Bought on<input type="date" name="purchased_date" value="${esc(v.purchased_date || '')}"></label>
      <label>Order #<input name="order_ref" value="${esc(v.order_ref || '')}"></label>
      <label>Installed at (miles)<input name="installed_mileage" inputmode="numeric" value="${esc(v.installed_mileage ?? '')}"></label>
      <label>Installed on<input type="date" name="installed_date" value="${esc(v.installed_date || '')}"></label>
      <label class="wide">Link<input name="url" type="url" value="${esc(v.url || '')}" placeholder="https://"></label>
      <label class="wide">Notes<textarea name="notes">${esc(v.notes || '')}</textarea></label>
    </div>
    ${p ? '<button type="button" class="danger" id="delPart">Delete part</button>' : ''}`,
  async (data) => {
    if (p) await api('/parts/' + p.id, { method: 'PUT', json: data });
    else await api('/parts', { json: data });
    toast('Part saved');
    rerender();
  });
  if (p) $('#delPart', form).onclick = async () => {
    if (!confirm(`Delete ${p.name}?`)) return;
    await deleteInDialog('/parts/' + p.id);
    rerender();
  };
}

function purchaseDialog(cars, car) {
  const row = () => `<tr><td><input class="n" placeholder="Name"></td><td><input class="pn" placeholder="Part #"></td><td><input class="b" placeholder="Brand"></td>
    <td><input class="q" inputmode="numeric" value="1" style="width:50px"></td><td><input class="pr" inputmode="decimal" placeholder="0.00" style="width:80px"></td></tr>`;
  const form = openDialog('Add a purchase', `
    <div class="form">
      <label>Vehicle<select name="car_id">${options(cars.map((c) => [c.id, c.name]), car && car.id, 'Not assigned')}</select></label>
      <label>Supplier<input name="supplier" placeholder="Store or website"></label>
      <label>Date<input type="date" name="purchased_date" value="${today()}"></label>
      <label>Order #<input name="order_ref"></label>
      <label>Status<select name="status">${options(['On hand', 'Ordered'], 'On hand')}</select></label>
      <label>Receipt / screenshot<input type="file" name="receipt" accept="image/*,.pdf"></label>
    </div>
    <h3>Items</h3>
    <div class="table-wrap"><table class="items-table"><thead><tr><th>Name</th><th>Part #</th><th>Brand</th><th>Qty</th><th>Price each</th></tr></thead><tbody id="items">${row()}${row()}${row()}</tbody></table></div>
    <button type="button" class="link" id="moreRows">＋ more rows</button>`,
  async (data, f, okText) => {
    const items = $$('#items tr', f).map((tr) => ({ name: $('.n', tr).value.trim(), part_number: $('.pn', tr).value.trim(), brand: $('.b', tr).value.trim(), qty: $('.q', tr).value, price: $('.pr', tr).value }))
      .filter((i) => i.name || i.part_number);
    if (!items.length) throw new Error('Add at least one item');
    const fd = new FormData();
    const { receipt, ...rest } = data;
    fd.append('data', JSON.stringify({ ...rest, items }));
    if (f.elements.receipt.files[0]) await attachFile(fd, 'receipt', f.elements.receipt.files[0], 'receipt', okText);
    okText('Saving…');
    await api('/purchases', { method: 'POST', body: fd });
    toast(`Added ${items.length} parts`);
    rerender();
  }, { ok: 'Add parts', wide: true });
  $('#moreRows', form).onclick = () => $('#items', form).insertAdjacentHTML('beforeend', row() + row());
}

async function allPartsPage(params) {
  $('#searchBox').value = params.get('q') || '';
  view.innerHTML = '<h1>All parts</h1><div id="tab"></div>';
  await partsSection($('#tab'), { params });
}

// ----- documents -----

let docFilter = '';  // file type shown in "Manuals & files" ('' = all); kept while moving between vehicles
// the upload types, then any type uploads no longer offer that these files still have (Photo, from older versions)
const docKinds = (have) => [...REF.doc_kinds, ...have.filter((k) => !REF.doc_kinds.includes(k))];

// car = null lists the files that have no vehicle (All parts › Not assigned)
async function documentsTab(el, car) {
  const all = await api(`/documents?car_id=${car ? car.id : 'none'}`);
  const counts = {};
  all.forEach((d) => (counts[d.kind] = (counts[d.kind] || 0) + 1));
  if (docFilter && !counts[docFilter]) docFilter = '';
  const docs = docFilter ? all.filter((d) => d.kind === docFilter) : all;
  const kinds = docKinds(Object.keys(counts)).filter((k) => counts[k]);
  const linked = (d) => !d.parts ? '' : `<div class="small muted">${d.parts} linked part${d.parts === 1 ? '' : 's'}${d.kind === 'Receipt' ? ' from this receipt' : ''}</div>`;
  el.innerHTML = `
    <div class="doc-head">
      <div class="doc-filter">${kinds.length > 1 ? `
        <button type="button" class="${docFilter ? '' : 'active'}" data-kind="">All <span class="muted">${all.length}</span></button>
        ${kinds.map((k) => `<button type="button" class="${k === docFilter ? 'active' : ''}" data-kind="${esc(k)}">${esc(k)} <span class="muted">${counts[k]}</span></button>`).join('')}` : ''}</div>
      ${car ? '<button type="button" class="primary" id="upBtn">＋ Upload</button>' : ''}
    </div>
    <div class="panel table-wrap" style="padding:0">${docs.length ? `<table class="doc-table"><thead><tr><th>Title</th><th>Type</th><th>File</th><th class="num">Size</th><th>Added</th><th></th></tr></thead><tbody>
      ${docs.map((d) => `<tr data-did="${d.id}"><td><a href="${fileUrl(d)}" target="_blank"><b>${esc(d.title)}</b></a>${d.notes ? `<div class="small muted">${esc(d.notes)}</div>` : ''}${linked(d)}</td>
        <td>${esc(d.kind)}</td><td class="small muted">${esc(d.original_name)}</td><td class="num small">${fmtSize(d.size || 0)}</td><td class="small muted">${esc((d.uploaded_at || '').slice(0, 10))}</td>
        <td class="num" style="white-space:nowrap"><a class="btn" href="${fileUrl(d)}?download=1">⬇</a> <button class="link edit">Edit</button></td></tr>`).join('')}
    </tbody></table>` : `<div class="empty">${car ? 'No manuals or files yet.' : 'No files without a vehicle.'}</div>`}</div>`;
  if (car) $('#upBtn', el).onclick = () => {
    const form = openDialog('Upload files', `<div class="form">
        <label class="wide">Files<input type="file" name="file" multiple required></label>
        <label>Type<select name="kind">${options(REF.doc_kinds, docFilter || 'Manual')}</select></label>
        <label>Title <span class="muted small">(one file only)</span><input name="title" placeholder="Service manual"></label></div>`,
    async (data, f, okText) => {
      const files = Array.from(f.elements.file.files);
      const title = files.length === 1 ? data.title : '';
      for (const x of files) {  // one file per request: small ones together could go over Ingress's limit
        if (x.size <= CHUNK) {
          okText(`Uploading ${x.name}…`);
          const fd = new FormData();
          fd.append('file', x); fd.append('car_id', car.id); fd.append('kind', data.kind);
          if (title) fd.append('title', title);
          await api('/documents', { method: 'POST', body: fd });
        } else {
          const id = await uploadChunked(x, { car_id: car.id, kind: data.kind, title }, okText);
          await api(`/documents/chunked/${id}/finish`, { method: 'POST', json: {} });
        }
      }
      toast(files.length === 1 ? 'Uploaded' : `Uploaded ${files.length} files`);
      docFilter = data.kind;  // show what was just added
      documentsTab(el, car);
    }, { ok: 'Upload' });
    $('input[type=file]', form).focus();
  };
  $$('.doc-filter button', el).forEach((b) => (b.onclick = () => { docFilter = b.dataset.kind; documentsTab(el, car); }));
  $$('tr[data-did]', el).forEach((tr) => {
    const d = docs.find((x) => x.id === +tr.dataset.did);
    $('.edit', tr).onclick = async () => {
      const cars = await api('/cars');
      const f = openDialog('Edit file', `<div class="form">
          <label class="wide">Title<input name="title" value="${esc(d.title)}" required></label>
          <label>Type<select name="kind">${options(docKinds([d.kind]), d.kind)}</select></label>
          <label>Vehicle<select name="car_id">${options(cars.map((c) => [c.id, c.name]), d.car_id, 'Not assigned')}</select></label>
          <label class="wide">Notes<textarea name="notes">${esc(d.notes)}</textarea></label></div>
          <button type="button" class="danger" id="delDoc">Delete file</button>`,
      async (data) => { await api('/documents/' + d.id, { method: 'PUT', json: data }); documentsTab(el, car); });
      $('#delDoc', f).onclick = async () => {
        if (!confirm(`Delete ${d.title}? The file is removed from the app (backups keep a copy).`)) return;
        await deleteInDialog('/documents/' + d.id);
        documentsTab(el, car);
      };
    };
  });
}

// ----- details / add -----

function carFormHtml(c) {
  return `
    <label class="wide">Name<input name="name" required value="${esc(c.name || '')}" placeholder="e.g. My truck"></label>
    <label>Type<select name="kind">${options(REF.kinds, c.kind || 'Car')}</select></label>
    <label>Year<input name="year" inputmode="numeric" value="${esc(c.year ?? '')}"></label>
    <label>Make<input name="make" value="${esc(c.make || '')}"></label>
    <label>Model<input name="model" value="${esc(c.model || '')}"></label>
    <label>Engine<input name="engine" value="${esc(c.engine || '')}"></label>
    <label>Transmission<input name="transmission" value="${esc(c.transmission || '')}"></label>
    <label>VIN<input name="vin" value="${esc(c.vin || '')}"></label>
    <label>Plate<input name="plate" value="${esc(c.plate || '')}"></label>
    ${c.id ? `<label>Sensor id (sensor.garage_&lt;this&gt;_odometer)<input name="ha_slug" value="${esc(c.ha_slug || '')}" placeholder="from the name"></label>` : ''}
    <label class="wide">Notes<textarea name="notes">${esc(c.notes || '')}</textarea></label>`;
}

function carDetails(el, car) {
  el.innerHTML = `<form class="panel form" id="carForm">${carFormHtml(car)}
      <div class="row wide"><button class="primary">Save changes</button><span style="flex:1"></span>
        <button type="button" id="hideCar">${car.archived ? 'Show in garage' : 'Hide vehicle (sold / stored)'}</button>
        <button type="button" class="danger" id="delCar">Delete vehicle</button></div></form>
    <h2>Photo</h2>
    <div class="panel row" style="align-items:flex-start">
      ${car.photo_id ? `<img class="car-photo-preview" src="${fileUrl({ id: car.photo_id, original_name: 'photo.jpg' })}" alt="">` : '<p class="muted" style="margin:0">No photo yet. It shows on the vehicle\'s card in the garage.</p>'}
      <form id="photoForm" class="row"><input type="file" name="photo" accept="image/*" required><button>${car.photo_id ? 'Replace' : 'Add photo'}</button>
        ${car.photo_id ? '<button type="button" class="link danger" id="delPhoto">Remove</button>' : ''}</form>
    </div>
    <h2>Mileage history</h2>
    <div class="panel">${car.mileage_log.length ? `<table class="small"><tbody>${car.mileage_log.map((m) => `<tr data-mid="${m.id}"><td>${esc(m.date)}</td><td class="num">${num(m.mileage)}</td><td class="muted">${esc(m.source)}</td>
      <td class="num"><button class="link" title="Delete this reading" aria-label="Delete this reading">✕</button></td></tr>`).join('')}</tbody></table>` : '<p class="muted">No readings yet.</p>'}</div>`;
  $('#carForm').onsubmit = (e) => {
    e.preventDefault();
    submitting(e.target, async () => {
      await api('/cars/' + car.id, { method: 'PUT', json: Object.fromEntries(new FormData(e.target)) });
      toast('Saved'); rerender();
    });
  };
  $('#hideCar').onclick = async () => {
    await api('/cars/' + car.id, { method: 'PUT', json: { archived: !car.archived } });
    toast(car.archived ? 'Back in the garage' : 'Hidden. Find it under "Hidden vehicles" on the garage page.');
    location.hash = '#/';
  };
  $('#photoForm').onsubmit = (e) => {
    e.preventDefault();
    const btn = $('button:not([type=button])', e.target);
    submitting(e.target, async () => {
      const fd = new FormData();
      await attachFile(fd, 'photo', e.target.photo.files[0], 'photo', (t) => { btn.textContent = t; });
      await api(`/cars/${car.id}/photo`, { method: 'POST', body: fd });
      toast('Photo saved'); rerender();
    }, 'Uploading…');
  };
  if ($('#delPhoto')) $('#delPhoto').onclick = async () => {
    if (!confirm(`Remove the photo of ${car.name}? The file is deleted.`)) return;
    await api(`/cars/${car.id}/photo`, { method: 'DELETE' }); toast('Photo removed'); rerender();
  };
  $$('tr[data-mid]', el).forEach((tr) => {
    const m = car.mileage_log.find((x) => x.id === +tr.dataset.mid);
    $('button', tr).onclick = async () => {
      if (!confirm(`Delete the ${num(m.mileage)} mi reading of ${m.date}?`)) return;
      await api(`/cars/${car.id}/mileage/${m.id}`, { method: 'DELETE' });
      toast('Reading deleted'); rerender();
    };
  });
  $('#delCar').onclick = async () => {
    if (!confirm(`Delete ${car.name} with its service history, schedules, projects, odometer history and photo? Its parts and files are kept: find them under All parts › Not assigned.`)) return;
    await api('/cars/' + car.id, { method: 'DELETE' });
    location.hash = '#/';
  };
}

function addCarPage() {
  view.innerHTML = `<div class="small"><a href="#/">Garage</a> ›</div><h1>Add a vehicle</h1>
    <form class="panel form" id="newCar">${carFormHtml({})}
      <label>Current mileage<input name="mileage" inputmode="numeric"></label>
      <div class="wide"><button class="primary">Add vehicle</button></div></form>`;
  $('#newCar').onsubmit = (e) => {
    e.preventDefault();
    submitting(e.target, async () => {
      try {
        const r = await api('/cars', { json: Object.fromEntries(new FormData(e.target)) });
        location.hash = '#/car/' + r.id;
      } catch (err) { toast(err.message, true); }
    });
  };
}

// Settings › Quick services: tick which one-tap jobs show in "Log a service"; add or delete your own.
const QUICK_EVERYDAY = 7;  // the first built-ins (wash, fuel…) are always visible; repair jobs fold away

function quickPanel(el) {
  const own = REF.quick_own.slice();
  const shown = new Set(REF.quick_services.map((q) => q.toLowerCase()));
  const everyday = REF.quick_builtin.slice(0, QUICK_EVERYDAY), repairs = REF.quick_builtin.slice(QUICK_EVERYDAY);
  const box = (q, mine) => `<label class="qp-item"><input type="checkbox" value="${esc(q)}" ${shown.has(q.toLowerCase()) ? 'checked' : ''}> <span>${esc(q)}</span>
    ${mine ? `<button type="button" class="link qp-del" data-q="${esc(q)}" title="Delete ${esc(q)}" aria-label="Delete ${esc(q)}">✕</button>` : ''}</label>`;
  const repairsOn = repairs.filter((q) => shown.has(q.toLowerCase())).length;
  el.innerHTML = `
    <p class="muted small">Ticked jobs show as one-tap buttons in <b>Log a service</b>. Keep it to the few you log often.</p>
    <div class="qp-grid">${own.map((q) => box(q, true)).join('')}${everyday.map((q) => box(q)).join('')}</div>
    <details class="qp-more"><summary>Repair jobs <span class="muted small">${repairsOn ? repairsOn + ' shown' : ''}</span></summary>
      <div class="qp-grid">${repairs.map((q) => box(q)).join('')}</div></details>
    <form class="row" id="quickAdd"><input name="q" placeholder="Add your own, e.g. Car wash (automatic)" maxlength="80" style="flex:1;min-width:180px" required><button>Add</button></form>
    <div><button type="button" class="link" id="quickReset">Back to the defaults</button></div>`;
  const save = async (ownList) => {
    const order = [...ownList, ...REF.quick_builtin];
    const ticked = new Set($$('.qp-item input:checked', el).map((i) => i.value.toLowerCase()));
    await api('/settings', { method: 'PUT', json: { quick_own: ownList, quick_list: order.filter((q) => ticked.has(q.toLowerCase())) } });
    REF = await api('/refdata');
  };
  $$('.qp-item input', el).forEach((i) => (i.onchange = () => save(own)));
  $$('.qp-del', el).forEach((b) => (b.onclick = async (e) => {
    e.preventDefault();
    await save(own.filter((q) => q !== b.dataset.q)); quickPanel(el);
  }));
  $('#quickAdd', el).onsubmit = async (e) => {
    e.preventDefault();
    const q = e.target.q.value.trim();
    if ([...own, ...REF.quick_builtin].some((x) => x.toLowerCase() === q.toLowerCase())) return toast('Already in the list', true);
    el.querySelector('.qp-grid').insertAdjacentHTML('afterbegin', box(q, true));  // ticked by save below
    el.querySelector('.qp-item input').checked = true;
    await save([q, ...own]); quickPanel(el);
    toast(`Added "${q}"`);
  };
  $('#quickReset', el).onclick = async () => {
    if (!confirm('Show only the default quick services again? Your own jobs are deleted.')) return;
    await api('/settings', { method: 'PUT', json: { quick_own: [], quick_list: null } });
    REF = await api('/refdata'); quickPanel(el);
  };
}

// Settings › Car wash weather: a phone nudge when it's nice out and dry for the next couple of days.
const notifyName = (s) => s.replace(/^mobile_app_/, '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

async function washPanel(el) {
  el.innerHTML = '<p class="muted small">Loading…</p>';
  let w;
  try { w = await api('/wash'); } catch (e) { el.innerHTML = `<p class="muted small">${esc(e.message)}</p>`; return; }
  if (!w.available) {
    el.innerHTML = '<p class="muted small">Get a notification when it\'s a good day to wash the car. This needs Home Assistant (it uses HA\'s weather forecast and phone notifications), so it works when the app runs as a Home Assistant add-on.</p>';
    return;
  }
  const c = w.config, v = w.verdict;
  const unit = (v && v.unit) || '°';
  const hourBar = v && v.hours.length ? `<div class="wash-hours" title="Next ${c.hours} hours (red = rain)">${v.hours.map((h) => `<i class="${h.wet ? 'bad' : ''}" style="height:${h.wet ? 100 : 35}%"></i>`).join('')}</div>
    <div class="spread small muted"><span>now</span><span>+${c.hours} h</span></div>` : '';
  el.innerHTML = `
    <label class="switch"><input type="checkbox" id="washOn" ${c.enabled ? 'checked' : ''}> Tell me when it's a good day to wash the car</label>
    <p class="muted small" style="margin-top:-4px">Sends a notification when it's warm enough and no rain is forecast for the next ${c.hours} hours. Skips days soon after you log a wash.</p>
    ${w.error ? `<div class="callout-box small">${esc(w.error)}</div>` : ''}
    ${v ? `<div class="wash-verdict"><span class="big">${v.good ? '☀️' : '🌧️'}</span><div><b>${v.good ? 'Good day to wash' : 'Not today'}</b>
      <div class="small muted">${esc(v.good ? v.summary : v.reasons.join(' · '))}</div></div></div>${hourBar}` : ''}
    <div id="washOpts" ${c.enabled ? '' : 'hidden'}>
      <div class="form">
        <label class="wide">Weather<select name="weather_entity">${options((w.weather_entities || []).map((e) => [e.entity_id, e.name]), c.weather_entity || w.suggested_entity, (w.weather_entities || []).length ? undefined : 'No weather entity in Home Assistant')}</select></label>
        <label>Warmer than (${esc(unit)})<input name="min_temp" inputmode="numeric" value="${esc(c.min_temp)}"></label>
        <label>Not before<select name="notify_hour">${options(Array.from({ length: 24 }, (_, h) => [h, `${(h % 12) || 12}:00 ${h < 12 ? 'am' : 'pm'}`]), c.notify_hour)}</select></label>
        <label>At most every<select name="every_days">${options([1, 2, 3, 5, 7, 10, 14].map((d) => [d, d === 1 ? 'day' : `${d} days`]), c.every_days)}</select></label>
        <label>Dry for<select name="hours">${options([24, 36, 48, 72].map((h) => [h, `${h} hours`]), c.hours)}</select></label>
      </div>
      <h3 style="margin-bottom:4px">Send to</h3>
      <div class="qp-grid">${(w.notify_services || []).map((n) => `<label class="qp-item"><input type="checkbox" name="notify" value="${esc(n)}" ${c.notify.includes(n) ? 'checked' : ''}> <span>${esc(notifyName(n))}</span></label>`).join('')}</div>
      <p class="muted small">${(w.notify_services || []).length ? 'Nothing ticked = a notification in Home Assistant\'s bell instead.' : 'No phones found. Install the Home Assistant app on your phone to get push notifications; until then it shows in Home Assistant\'s bell.'}</p>
      <div class="row"><button type="button" id="washTest">Send a test</button>${w.last_sent ? `<span class="small muted">Last reminder: ${esc(w.last_sent.replace('T', ' '))}</span>` : ''}</div>
    </div>`;
  const save = async (extra = {}) => {
    const opts = $('#washOpts', el);
    const val = (n) => $(`[name=${n}]`, opts).value;
    const body = { enabled: $('#washOn', el).checked, weather_entity: val('weather_entity'), min_temp: val('min_temp'),
      notify_hour: val('notify_hour'), every_days: val('every_days'), hours: val('hours'),
      notify: $$('input[name=notify]:checked', opts).map((i) => i.value), ...extra };
    try { await api('/wash', { method: 'PUT', json: body }); washPanel(el); } catch (e) { toast(e.message, true); }
  };
  $('#washOn', el).onchange = () => save();
  $$('#washOpts select, #washOpts input', el).forEach((i) => (i.onchange = () => save()));
  $('#washTest', el).onclick = async (e) => {
    e.target.disabled = true;
    try {
      const r = await api('/wash/test', { method: 'POST', json: {} });
      toast('Sent to ' + r.sent.map(notifyName).join(', '));
    } catch (err) { toast(err.message, true); }
    e.target.disabled = false;
  };
}

// ---------- settings ----------

async function settingsPage() {
  const [s, cars] = await Promise.all([api('/settings'), api('/cars')]);
  const port = s.settings.port;
  const host = s.lan_active && s.lan_addresses[0] ? s.lan_addresses[0] : 'THIS-PC-IP';
  view.innerHTML = `
    <h1>Settings</h1>
    ${s.addon ? `<h2>Home Assistant add-on</h2>
    <div class="panel stack small">
      <p>Running inside Home Assistant. Open it from the HA sidebar or the HA app, at home or away. Home Assistant handles sign-in.</p>
      <p class="muted">To let this PC's tools (e.g. adding parts from a screenshot) reach it directly, turn on <b>Direct network access</b> in the add-on's Configuration tab and set a port in its Network section.</p>
    </div>` : `<h2>Phone access</h2>
    <div class="panel stack">
      <label class="switch"><input type="checkbox" id="lan" ${s.settings.lan ? 'checked' : ''}> Allow phones and other devices on my home network</label>
      <p class="muted small">Off by default. When on, anyone on your Wi-Fi can open and edit this (there's no password).</p>
      ${s.lan_active ? `<div class="callout-box">On your phone, open: ${s.lan_addresses.map((a) => `<b class="mono">http://${a}:${port}</b>`).join(' or ')}
        <div class="small muted">If it doesn't load, Windows Firewall may have asked whether to allow Python. Allow it on <b>Private networks</b>.</div></div>` : ''}
      <div id="lanMsg" class="small"></div>
    </div>`}
    <h2>Features</h2>
    <div class="panel stack">
      <label class="switch"><input type="checkbox" id="catalogOn" ${s.settings.catalog ? 'checked' : ''}> Parts catalog</label>
      <p class="muted small">An offline parts catalog with diagrams, filled from files you import (JSON, CSV, saved HTML pages, images). Off by default: it starts empty.</p>
    </div>
    <h2 id="quick">Quick services</h2>
    <div class="panel stack" id="quickPanel"></div>
    <h2 id="wash">Car wash weather</h2>
    <div class="panel stack" id="washPanel"></div>
    <h2>AI agents</h2>
    <div class="panel stack small">
      <p>Let an AI assistant (Claude, or any app that supports MCP) read and update your garage: vehicles, what's due, service history, odometer and parts.</p>
      ${s.settings.agent_key ? `
        <div class="kv"><dt>Address</dt><dd><code>http://${s.addon ? '&lt;home-assistant-ip&gt;:&lt;port&gt;' : esc(location.host)}/mcp</code></dd>
          <dt>Agent key</dt><dd><code id="agentKey" data-key="${esc(s.settings.agent_key)}">••••••••••••</code>
            <button type="button" class="link" id="showKey">Show</button> <button type="button" class="link" id="copyKey">Copy</button></dd></div>
        ${s.addon ? '<p class="muted">From another device this needs <b>Direct network access</b> on in the add-on Configuration, with a port set under Network. Home Assistant itself can use <code>http://&lt;add-on hostname&gt;:8765/mcp</code>.</p>' : ''}
        <p>In the agent app, add an MCP server of type <b>HTTP</b> with that address and the header <code>Authorization: Bearer &lt;agent key&gt;</code>. For Claude Code, in a project's <code>.mcp.json</code>:</p>
        <pre class="code">{"mcpServers": {"garage": {"type": "http", "url": "http://HOST:PORT/mcp",
  "headers": {"Authorization": "Bearer AGENT_KEY"}}}}</pre>
        <div class="row"><button type="button" id="newKey">New key</button><button type="button" class="danger" id="offKey">Turn off agent access</button></div>
        <p class="muted">Anyone with the key can change your garage data. Make a new key to lock out the old one.</p>`
      : '<div><button type="button" class="primary" id="newKey">Turn on: create an agent key</button></div>'}
    </div>
    <h2>Backup</h2>
    <div class="panel stack">
      <p>Download everything (database, catalog, manuals, receipts) as one zip file.</p>
      <a class="btn primary" href="api/backup">⬇ Download backup</a>
      <p class="small muted">Data lives in <code>${esc(s.data_dir)}</code>.</p>
    </div>
    <h2>Restore from a backup</h2>
    <form class="panel stack" id="restoreForm">
      <p>Replace <b>everything here</b> with a backup zip, e.g. to move your data from the PC into Home Assistant. The current data is saved first as a <code>before-restore</code> zip in the data folder.</p>
      <div class="row"><input type="file" name="backup" accept=".zip" required><button class="danger">Restore</button></div>
      <div id="restoreMsg" class="small"></div>
    </form>
    ${s.addon ? `<h2>Home Assistant dashboard</h2>
    <div class="panel stack small">${s.publish_sensors ? `
      <p>The add-on keeps these sensors up to date in Home Assistant (turn off with the <b>Publish sensors</b> option):
        <code>sensor.garage_overdue</code>, <code>sensor.garage_due_soon</code>, <code>sensor.garage_parts_to_get</code>, and per vehicle
        <code>sensor.garage_&lt;vehicle&gt;_odometer</code> / <code>_status</code> / <code>_next_service</code> / <code>_next_service_due</code> / <code>_parts_to_get</code>.</p>
      <p>To add a <b>Garage</b> tab: open your dashboard, <b>Edit dashboard › ⋮ › Raw configuration editor</b>, and paste this under <code>views:</code>.</p>
      <pre class="code" id="dashYaml">Loading…</pre>
      <div><button type="button" id="copyDash">Copy</button></div>`
      : '<p>Sensors are off (the <b>Publish sensors</b> option in the add-on\'s Configuration tab), so there is no dashboard to add.</p>'}
    </div>
    <h2>Update the odometer from Home Assistant</h2>
    <div class="panel stack small">
      <p>If a sensor in Home Assistant knows a vehicle's odometer (from the car's own integration, for example), Home Assistant can send it here whenever it changes. The sensor must report miles, the unit the garage uses. Vehicle ids: ${cars.map((c) => `<b>${c.id}</b> = ${esc(c.name)}`).join(', ')}.</p>
      <p>Add this to <code>configuration.yaml</code> and restart Home Assistant:</p>
      <pre class="code">rest_command:
  garage_odometer:
    url: "http://${esc(s.hostname || 'ADD-ON-HOSTNAME')}:8765/api/cars/{{ car_id }}/mileage"
    method: post
    content_type: "application/json"
    payload: '{"mileage": {{ mileage }}, "source": "home assistant"}'</pre>
      <p>Then an automation for each vehicle (paste it in the automation editor's YAML mode), with your sensor and the vehicle's id:</p>
      <pre class="code">alias: Garage odometer
triggers:
  - trigger: state
    entity_id: sensor.YOUR_ODOMETER
    to: null
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.state | is_number }}"
actions:
  - action: rest_command.garage_odometer
    data:
      car_id: ${cars.length ? cars[0].id : 'VEHICLE_ID'}
      mileage: "{{ trigger.to_state.state | int }}"</pre>
    </div>` : `<h2>Home Assistant</h2>
    <div class="panel stack small">
      <p>Ready for later. Home Assistant needs phone access turned on (so it can reach this PC). Vehicle ids: ${cars.map((c) => `<b>${c.id}</b> = ${esc(c.name)}`).join(', ')}.</p>
      <p><b>Summary</b> (mileage, overdue / due-soon counts, next service, parts needed): <code>GET http://${esc(host)}:${port}/api/ha/summary</code></p>
      <p><b>Update mileage</b>: <code>POST http://${esc(host)}:${port}/api/cars/&lt;id&gt;/mileage</code> with <code>{"mileage": 123456}</code></p>
    </div>`}`;
  // true once the app answers again; when it doesn't, msg says so and the page is left as it is
  const waitForRestart = async (msg) => {
    await new Promise((res) => setTimeout(res, 1500));
    for (let i = 0; i < 60; i++) {
      try { await api('/ping'); return true; } catch (err) { await new Promise((res) => setTimeout(res, 500)); }
    }
    msg.textContent = 'Still restarting. Reload this page in a moment.';
    return false;
  };
  if ($('#dashYaml')) {
    fetch('api/ha/dashboard').then((r) => r.text()).then((t) => { $('#dashYaml').textContent = t; });
    $('#copyDash').onclick = async () => {
      try { await navigator.clipboard.writeText($('#dashYaml').textContent); toast('Copied'); } catch (e) {
        const r = document.createRange(); r.selectNodeContents($('#dashYaml')); getSelection().removeAllRanges(); getSelection().addRange(r);
        toast('Selected: press Ctrl+C to copy');
      }
    };
  }
  $('#catalogOn').onchange = async (e) => {
    await api('/settings', { method: 'PUT', json: { catalog: e.target.checked } });
    REF = await api('/refdata'); applyFeatures();
    toast(e.target.checked ? 'Catalog turned on' : 'Catalog hidden');
  };
  quickPanel($('#quickPanel'));
  washPanel($('#washPanel'));
  if (location.hash.includes('?')) { const id = new URLSearchParams(location.hash.split('?')[1]).get('go'); if (id && $('#' + id)) $('#' + id).scrollIntoView(); }
  $('#newKey').onclick = async () => {
    if (s.settings.agent_key && !confirm('Make a new key? Agents using the old key will stop working until you give them the new one.')) return;
    await api('/settings', { method: 'PUT', json: { agent_key: 'new' } }); settingsPage();
  };
  if ($('#offKey')) $('#offKey').onclick = async () => {
    if (!confirm('Turn off agent access? The key stops working.')) return;
    await api('/settings', { method: 'PUT', json: { agent_key: null } }); settingsPage();
  };
  if ($('#showKey')) $('#showKey').onclick = () => { const k = $('#agentKey'); k.textContent = k.dataset.key; };
  if ($('#copyKey')) $('#copyKey').onclick = async () => {
    try { await navigator.clipboard.writeText($('#agentKey').dataset.key); toast('Key copied'); } catch (e) { $('#agentKey').textContent = $('#agentKey').dataset.key; toast('Select the key and press Ctrl+C', true); }
  };
  if ($('#lan')) $('#lan').onchange = async (e) => {
    const r = await api('/settings', { method: 'PUT', json: { lan: e.target.checked } });
    if (r.restarting) {
      $('#lanMsg').textContent = 'Restarting the server…';
      if (await waitForRestart($('#lanMsg'))) settingsPage();
    }
  };
  $('#restoreForm').onsubmit = async (e) => {
    e.preventDefault();
    const file = e.target.backup.files[0];
    if (!file || !confirm(`Replace ALL data here with ${file.name}? The current data is saved as a before-restore zip first.`)) return;
    const btn = $('button', e.target); btn.disabled = true;
    try {
      const fd = new FormData();
      fd.append('upload', await uploadChunked(file, { purpose: 'restore' }, (t) => { $('#restoreMsg').textContent = t; }));
      $('#restoreMsg').textContent = 'Checking the backup…';
      await api('/restore', { method: 'POST', body: fd });
      $('#restoreMsg').textContent = 'Restoring and restarting…';
      if (!(await waitForRestart($('#restoreMsg')))) return;
      REF = null;  // the backup brought its own settings: route() fetches them again
      toast('Backup restored');
      location.hash = '#/';
      router();
    } catch (err) { $('#restoreMsg').textContent = ''; toast(err.message, true); btn.disabled = false; }
  };
}

// catalog.js adds its routes first; both scripts have run by DOMContentLoaded
window.addEventListener('DOMContentLoaded', router);
