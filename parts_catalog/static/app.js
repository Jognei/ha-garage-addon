/* Parts Catalog single-page UI: garage, service tracking, parts, documents. Hash routes, no build step. */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const view = $('#view');
let REF = null;

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (v) => (v == null || v === '' ? '' : '$' + Number(v).toFixed(2));
const miles = (v) => (v == null || v === '' ? '' : Number(v).toLocaleString() + ' mi');
const num = (v) => (v == null || v === '' ? '' : Number(v).toLocaleString());
const today = () => new Date().toISOString().slice(0, 10);
const sCls = (s) => 's-' + String(s || '').replace(/\s+/g, '-');
const fmtSize = (b) => (b > 1e6 ? (b / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1e3)) + ' KB');
const fileUrl = (d, page) => `files/${d.id || d.doc_id}/${encodeURIComponent(d.original_name || d.filename || d.doc_title || 'file')}${page ? '#page=' + page : ''}`;

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = opts.method || 'POST';
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch('api' + path, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function toast(msg, bad) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (bad ? ' bad' : '');
  t.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (t.hidden = true), bad ? 6000 : 2200);
}

// Generic modal form. onSubmit(formData, form) may throw to keep the dialog open.
function openDialog(title, html, onSubmit, { ok = 'Save', wide = false } = {}) {
  const dlg = $('#dlg');
  $('#dlgTitle').textContent = title;
  $('#dlgBody').innerHTML = html;
  $('#dlgOk').textContent = ok;
  dlg.style.width = wide ? 'min(1000px, calc(100vw - 24px))' : '';
  const form = $('#dlgForm');
  form.onsubmit = async (e) => {
    e.preventDefault();
    try {
      await onSubmit(Object.fromEntries(new FormData(form)), form);
      dlg.close();
    } catch (err) { toast(err.message, true); }
  };
  $('#dlgCancel').onclick = $('#dlgX').onclick = () => dlg.close();
  dlg.showModal();
  const first = $('input:not([type=hidden]),select,textarea', $('#dlgBody'));
  if (first) first.focus();
  return form;
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
  const hash = location.hash.slice(1) || '/';
  const [path, qs] = hash.split('?');
  const params = new URLSearchParams(qs || '');
  const key = path.split('/')[1] || 'home';
  $$('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.nav === (['car', 'add-car'].includes(key) ? 'home' : key)));
  if (!REF) REF = await api('/refdata');
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
$('#searchForm').addEventListener('submit', (e) => {
  e.preventDefault();
  location.hash = '#/parts?q=' + encodeURIComponent($('#searchBox').value.trim());
});
const rerender = () => router();

// ---------- shared bits ----------

function stateChip(u) {
  const label = { overdue: 'Overdue', soon: 'Due soon', ok: 'OK', unknown: 'Set last done' }[u.state];
  return `<span class="chip ${u.state}">${label}</span>`;
}

function dueText(u) {
  const bits = [];
  if (u.miles_left != null) bits.push(u.miles_left < 0 ? `${num(-u.miles_left)} mi over` : `${num(u.miles_left)} mi left`);
  if (u.days_left != null) bits.push(u.days_left < 0 ? `${-u.days_left} days over` : `${u.days_left} days left`);
  if (u.est_date) bits.push('≈ ' + new Date(u.est_date + 'T12:00').toLocaleDateString(undefined, { month: 'short', year: 'numeric' }));
  return bits.join(' · ');
}

function intervalText(s) {
  return [s.interval_miles ? `every ${num(s.interval_miles)} mi` : '', s.interval_months ? `${s.interval_months} mo` : ''].filter(Boolean).join(' / ');
}

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
  view.innerHTML = `
    <div class="spread"><h1>Garage</h1><a class="btn" href="#/add-car">＋ Add a vehicle</a></div>
    ${cars.length > 1 ? '<p class="muted small" style="margin:4px 0 0">Drag a card by its ⠿ handle (or use ↑ ↓) to change the order.</p>' : ''}
    <div class="grid" id="carGrid" style="margin-top:12px">
      ${cars.map((c, n) => `
        <div class="panel car-card" data-car="${c.id}">
          ${c.photo_id ? `<a href="#/car/${c.id}"><img class="car-photo" src="${fileUrl({ id: c.photo_id, original_name: c.photo_name })}" alt="" loading="lazy"></a>` : ''}
          <div class="spread"><div class="row" style="gap:8px;align-items:flex-start">
              ${cars.length > 1 ? `<span class="drag-handle" draggable="true" title="Drag to reorder">⠿</span>` : ''}
              <div><h3><a href="#/car/${c.id}">${esc(c.name)}</a></h3>
              <div class="muted small">${esc([c.year, c.make, c.model, c.kind !== 'Car' ? c.kind : ''].filter(Boolean).join(' · '))}</div></div></div>
            <div class="row" style="gap:6px">${c.overdue ? `<span class="chip overdue">${c.overdue} overdue</span>` : ''}${c.due_soon ? `<span class="chip soon">${c.due_soon} due soon</span>` : ''}
              ${cars.length > 1 ? `<span class="order-btns"><button class="link up" title="Move up" ${n === 0 ? 'disabled' : ''}>↑</button><button class="link down" title="Move down" ${n === cars.length - 1 ? 'disabled' : ''}>↓</button></span>` : ''}</div></div>
          <div class="row" style="margin:12px 0">
            <div><div class="mileage-big">${c.mileage != null ? num(c.mileage) : '—'}</div><div class="muted small">miles${c.mileage_date ? ' · ' + esc(c.mileage_date) : ''}</div></div>
            <form class="row mileage-form" style="margin-left:auto"><input name="m" inputmode="numeric" placeholder="New reading" style="width:120px"><button>Update</button></form>
          </div>
          ${c.upcoming.length ? `<table class="small"><tbody>${c.upcoming.map((u) => `<tr data-sid="${u.id}"><td>${esc(u.title)}</td><td class="num">${stateChip(u)}</td><td class="num muted">${esc(dueText(u))}</td>
            <td class="num"><button class="link log" title="Log this service as done">✓</button></td></tr>`).join('')}</tbody></table>`
            : `<p class="muted small">No service schedule yet. <a href="#/car/${c.id}">Set one up</a>.</p>`}
          <div class="row" style="margin-top:12px">
            <a class="btn" href="#/car/${c.id}">Services</a>
            <a class="btn" href="#/car/${c.id}/parts">Parts <span class="muted">${c.parts_open} to get · ${c.parts_on_hand} on hand</span></a>
            ${c.documents ? `<a class="btn" href="#/car/${c.id}/documents">Manuals (${c.documents})</a>` : ''}
          </div>
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
    $('.mileage-form', card).onsubmit = (e) => { e.preventDefault(); updateMileage(car, e.target.m.value); };
    $$('button.log', card).forEach((b) => (b.onclick = async () => {
      const full = await api('/cars/' + car.id);
      serviceDialog(full, full.upcoming, null, [+b.closest('tr').dataset.sid]);
    }));
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
  $$('button.unhide').forEach((b) => (b.onclick = async () => {
    await api('/cars/' + b.dataset.id, { method: 'PUT', json: { archived: false } });
    toast('Back in the garage'); home();
  }));
}

// ---------- car ----------

async function carPage(id, tab = 'services') {
  const car = await api('/cars/' + id);
  const tabs = [['services', 'Services'], ['parts', 'Parts'], ['documents', 'Manuals & files'], ['details', 'Details']];
  view.innerHTML = `
    <div class="spread">
      <div><div class="small"><a href="#/">Garage</a> ›</div><h1>${esc(car.name)}</h1>
        <div class="muted small">${esc([car.year, car.make, car.model, car.engine, car.transmission].filter(Boolean).join(' · '))}</div></div>
      <div class="panel row" style="padding:10px 14px">
        <div><div class="mileage-big">${car.mileage != null ? num(car.mileage) : '—'}</div><div class="muted small">miles${car.mileage_date ? ' · ' + esc(car.mileage_date) : ''}</div></div>
        <form class="row" id="mileForm"><input name="m" inputmode="numeric" placeholder="New reading" style="width:120px"><button>Update</button></form>
      </div>
    </div>
    <div class="tabs">${tabs.map(([k, l]) => `<a href="#/car/${id}${k === 'services' ? '' : '/' + k}" class="${k === tab ? 'active' : ''}">${l}</a>`).join('')}</div>
    <div id="tab"></div>`;
  $('#mileForm').onsubmit = (e) => { e.preventDefault(); updateMileage(car, e.target.m.value); };
  const el = $('#tab');
  if (tab === 'parts') return partsSection(el, { car });
  if (tab === 'documents') return documentsTab(el, car);
  if (tab === 'details') return carDetails(el, car);
  return servicesTab(el, car);
}

// ----- services & upcoming -----

async function servicesTab(el, car) {
  const [services, docs] = await Promise.all([api(`/cars/${car.id}/services`), api(`/documents?car_id=${car.id}`)]);
  const up = car.upcoming;
  el.innerHTML = `
    <div class="spread"><h2 style="margin-top:0">Upcoming services</h2>
      <div class="row"><button id="addPresets">＋ Common services</button><button id="addSched">＋ Custom schedule</button><button class="primary" id="logSvc">Log a service</button></div></div>
    ${car.archived ? '<div class="callout-box" style="margin-bottom:12px">This vehicle is hidden from the garage and Home Assistant. <a href="#/car/' + car.id + '/details">Show it again</a> under Details.</div>' : ''}
    <div class="panel table-wrap" style="padding:0">${up.length ? `<table><thead><tr><th>Service</th><th>Interval</th><th>Last done</th><th>Due</th><th></th><th>Manual</th><th></th></tr></thead><tbody>
      ${up.map((u) => `<tr class="upcoming-row" data-sid="${u.id}">
        <td><b>${esc(u.title)}</b>${u.notes ? `<div class="small muted">${esc(u.notes)}</div>` : ''}</td>
        <td class="small">${esc(intervalText(u))}</td>
        <td class="small">${u.last_date || u.last_mileage != null ? `${esc(u.last_date || '')}${u.last_mileage != null ? ' @ ' + miles(u.last_mileage) : ''}` : '<span class="muted">never logged</span>'}</td>
        <td class="small">${[u.due_mileage != null ? miles(u.due_mileage) : '', esc(u.due_date || '')].filter(Boolean).join(' / ')}<div class="muted">${esc(dueText(u))}</div></td>
        <td>${stateChip(u)}</td>
        <td class="small">${u.doc_id ? `<a href="${fileUrl({ id: u.doc_id, original_name: u.doc_title + '.pdf' }, u.doc_page)}" target="_blank">${esc(u.doc_title)}${u.doc_page ? ' p.' + u.doc_page : ''}</a>` : ''}</td>
        <td class="num" style="white-space:nowrap"><button class="done">✓ Done</button> <button class="link edit">Edit</button></td></tr>`).join('')}
    </tbody></table>` : `<div class="empty">No schedules yet. Click <b>＋ Common services</b> to pick oil changes, filters, fluids and so on.</div>`}</div>

    <div class="spread" style="align-items:center"><h2>Service history</h2>${services.length ? `<a class="btn" href="api/cars/${car.id}/export/services.csv">⬇ Export CSV</a>` : ''}</div>
    <div class="panel table-wrap" style="padding:0">${services.length ? `<table><thead><tr><th>Date</th><th class="num">Mileage</th><th>Work</th><th>Parts used</th><th class="num">Cost</th><th>By</th><th></th></tr></thead><tbody>
      ${services.map((s) => `<tr data-svc="${s.id}"><td style="white-space:nowrap">${esc(s.date)}</td><td class="num">${num(s.mileage)}</td>
        <td><b>${esc(s.title)}</b>${s.notes ? `<div class="small muted" style="white-space:pre-wrap">${esc(s.notes)}</div>` : ''}</td>
        <td class="small">${s.parts.map((p) => esc(p.name) + (p.part_number ? ` <span class="muted mono">${esc(p.part_number)}</span>` : '')).join('<br>')}</td>
        <td class="num">${money(s.cost)}</td><td class="small">${esc(s.done_by)}</td>
        <td class="num"><button class="link edit">Edit</button></td></tr>`).join('')}
    </tbody></table>` : '<div class="empty">Nothing logged yet.</div>'}</div>`;

  $('#logSvc').onclick = () => serviceDialog(car, up, null, []);
  $('#addSched').onclick = () => scheduleDialog(car, docs, null);
  $('#addPresets').onclick = () => presetsDialog(car, up);
  $$('tr[data-sid]', el).forEach((tr) => {
    const s = up.find((u) => u.id === +tr.dataset.sid);
    $('.done', tr).onclick = () => serviceDialog(car, up, null, [s.id]);
    $('.edit', tr).onclick = () => scheduleDialog(car, docs, s);
  });
  $$('tr[data-svc]', el).forEach((tr) => {
    const s = services.find((x) => x.id === +tr.dataset.svc);
    $('.edit', tr).onclick = () => serviceDialog(car, up, s, s.schedule_ids);
  });
}

async function serviceDialog(car, schedules, svc, checked) {
  const parts = (await api(`/parts?car_id=${car.id}`)).filter((p) => p.status !== 'Installed' || (svc && p.service_id === svc.id));
  const used = new Set(svc ? svc.parts.map((p) => p.id) : []);
  const titleFrom = () => schedules.filter((s) => checked.includes(s.id)).map((s) => s.title).join(', ');
  const form = openDialog(svc ? 'Edit service' : 'Log a service', `
    <div class="form">
      <label>Date<input type="date" name="date" value="${esc(svc ? svc.date : today())}" required></label>
      <label>Mileage<input name="mileage" inputmode="numeric" value="${esc(svc ? svc.mileage ?? '' : car.mileage ?? '')}"></label>
      <label>Done by<input name="done_by" value="${esc(svc ? svc.done_by : 'DIY')}"></label>
      <label>Cost<input name="cost" inputmode="decimal" value="${esc(svc ? svc.cost ?? '' : '')}" placeholder="0.00"></label>
      <label class="wide">What was done<input name="title" required value="${esc(svc ? svc.title : titleFrom())}" placeholder="Oil change, new thermostat…"></label>
      <label class="wide">Notes<textarea name="notes" placeholder="Oil used, torque specs, what you found…">${esc(svc ? svc.notes : '')}</textarea></label>
    </div>
    ${schedules.length ? `<h3>This counts as (resets the schedule)</h3><div class="checks">${schedules.map((s) => `<label><input type="checkbox" name="sched" value="${s.id}" ${checked.includes(s.id) ? 'checked' : ''}> ${esc(s.title)}</label>`).join('')}</div>` : ''}
    ${parts.length ? `<h3>Parts used (marked installed at this mileage)</h3><div class="checks">${parts.map((p) => `<label><input type="checkbox" name="part" value="${p.id}" ${used.has(p.id) ? 'checked' : ''}> ${esc(p.name)} <span class="small ${sCls(p.status)}">${esc(p.status)}</span></label>`).join('')}</div>` : ''}
    ${svc ? '<div style="margin-top:14px"><button type="button" class="danger" id="delSvc">Delete this service</button></div>' : ''}`,
  async (data, f) => {
    const payload = { ...data, schedule_ids: $$('input[name=sched]:checked', f).map((i) => +i.value), part_ids: $$('input[name=part]:checked', f).map((i) => +i.value) };
    if (svc) await api('/services/' + svc.id, { method: 'PUT', json: payload });
    else await api(`/cars/${car.id}/services`, { json: payload });
    toast('Service saved');
    rerender();
  });
  $$('input[name=sched]', form).forEach((cb) => (cb.onchange = () => {
    checked = $$('input[name=sched]:checked', form).map((i) => +i.value);
    if (!svc) form.elements.title.value = titleFrom();
  }));
  if (svc) $('#delSvc').onclick = async () => {
    if (!confirm('Delete this service record?')) return;
    await api('/services/' + svc.id, { method: 'DELETE' });
    $('#dlg').close(); rerender();
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
    await api('/schedules/' + s.id, { method: 'DELETE' });
    $('#dlg').close(); rerender();
  };
}

function presetsDialog(car, existing) {
  const have = new Set(existing.map((u) => u.title.toLowerCase()));
  const list = REF.presets.filter((p) => !have.has(p.title.toLowerCase()));
  openDialog('Add common services', `
    <p class="muted small">Tick what applies to this vehicle. Change intervals to match your manual. If you know when each was last done, fill it in so “upcoming” is accurate (you can also do that later).</p>
    <div class="table-wrap"><table class="items-table"><thead><tr><th></th><th>Service</th><th>Miles</th><th>Months</th><th>Last done date</th><th>Last done mi</th></tr></thead><tbody>
      ${list.map((p, i) => `<tr data-i="${i}"><td><input type="checkbox" class="pick"></td><td>${esc(p.title)}</td>
        <td><input class="im" value="${p.interval_miles ?? ''}" style="width:80px"></td><td><input class="imo" value="${p.interval_months ?? ''}" style="width:60px"></td>
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
  el.innerHTML = `
    <div class="spread" style="margin-bottom:12px">
      <div class="row">
        ${[['all', 'All'], ['get', 'To get'], ['onhand', 'On hand'], ['installed', 'Installed']].map(([k, l]) => `<button class="${k === filter ? 'primary' : ''}" data-f="${k}">${l}</button>`).join('')}
        ${!car ? `<select id="carF">${options([['', 'All vehicles'], ['none', 'Not assigned'], ...cars.map((c) => [c.id, c.name])], carFilter)}</select>` : ''}
        ${q ? `<span class="muted">matching “${esc(q)}” <a href="#/parts">clear</a></span>` : ''}
      </div>
      <div class="row"><a class="btn" href="api/export/parts.csv${carFilter && carFilter !== 'none' ? '?car_id=' + carFilter : ''}" title="Download as a spreadsheet">⬇ CSV</a><button id="addPart">＋ Add part</button><button class="primary" id="addPurchase">＋ Add purchase / receipt</button></div>
    </div>
    ${total ? `<p class="muted small">To get: <b>${money(total)}</b> at listed prices.</p>` : ''}
    <div class="panel table-wrap" style="padding:0">${parts.length ? `<table id="partsTable"><thead><tr>
      <th title="Installed">✓</th><th>Part</th>${car ? '' : '<th>Vehicle</th>'}<th class="num">Qty</th><th>Status</th><th>Installed at</th><th>Installed on</th><th>Location</th><th>Bought</th><th></th></tr></thead><tbody>
      ${parts.map((p) => `<tr data-pid="${p.id}" class="${p.status === 'Installed' ? 'installed' : ''}">
        <td><input type="checkbox" class="inst" ${p.status === 'Installed' ? 'checked' : ''} title="Installed"></td>
        <td><b>${esc(p.name)}</b><div class="small muted">${esc([p.category, p.brand, p.part_number].filter(Boolean).join(' · '))}</div>${p.notes ? `<div class="small muted" title="${esc(p.notes)}">${esc(p.notes.length > 90 ? p.notes.slice(0, 90) + '…' : p.notes)}</div>` : ''}</td>
        ${car ? '' : `<td class="small">${esc(p.car || '—')}</td>`}
        <td class="num"><input class="inline qty" inputmode="numeric" value="${p.qty ?? 1}" style="width:52px;text-align:right"></td>
        <td><select class="status ${sCls(p.status)}">${options(REF.statuses, p.status)}</select></td>
        <td><input class="inline mi" inputmode="numeric" value="${p.installed_mileage ?? ''}" placeholder="miles"></td>
        <td><input class="inline idate" type="date" value="${esc(p.installed_date || '')}" style="width:140px"></td>
        <td><input class="inline loc" value="${esc(p.location)}" placeholder="Garage > Shelf A"></td>
        <td class="small">${[esc(p.supplier), money(p.price), esc(p.purchased_date || '')].filter(Boolean).join(' · ')}${p.url ? ` <a href="${esc(p.url)}" target="_blank" rel="noopener">↗</a>` : ''}${p.document_id ? `<div><a href="${fileUrl({ id: p.document_id, original_name: p.document_file })}" target="_blank">receipt</a></div>` : ''}</td>
        <td class="num"><button class="link edit">Edit</button></td></tr>`).join('')}
    </tbody></table>` : '<div class="empty">No parts here yet.</div>'}</div>`;

  $$('[data-f]', el).forEach((b) => (b.onclick = () => { localStorage.setItem('partsFilter', b.dataset.f); partsSection(el, { car, params: withParam(params, 'status', b.dataset.f) }); }));
  if (!car) $('#carF').onchange = (e) => partsSection(el, { params: withParam(params, 'car', e.target.value) });
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
    await api('/parts/' + p.id, { method: 'DELETE' });
    $('#dlg').close(); rerender();
  };
}

function purchaseDialog(cars, car) {
  const row = () => `<tr><td><input class="n" placeholder="Name"></td><td><input class="pn" placeholder="Part #"></td><td><input class="b" placeholder="Brand"></td>
    <td><input class="q" value="1" style="width:50px"></td><td><input class="pr" placeholder="0.00" style="width:80px"></td></tr>`;
  const form = openDialog('Add a purchase', `
    <div class="form">
      <label>Vehicle<select name="car_id">${options(cars.map((c) => [c.id, c.name]), car && car.id, 'Not assigned')}</select></label>
      <label>Supplier<input name="supplier" placeholder="FCP Euro, RockAuto…"></label>
      <label>Date<input type="date" name="purchased_date" value="${today()}"></label>
      <label>Order #<input name="order_ref"></label>
      <label>Status<select name="status">${options(['On hand', 'Ordered'], 'On hand')}</select></label>
      <label>Receipt / screenshot<input type="file" name="receipt" accept="image/*,.pdf"></label>
    </div>
    <h3>Items</h3>
    <div class="table-wrap"><table class="items-table"><thead><tr><th>Name</th><th>Part #</th><th>Brand</th><th>Qty</th><th>Price each</th></tr></thead><tbody id="items">${row()}${row()}${row()}</tbody></table></div>
    <button type="button" class="link" id="moreRows">＋ more rows</button>`,
  async (data, f) => {
    const items = $$('#items tr', f).map((tr) => ({ name: $('.n', tr).value.trim(), part_number: $('.pn', tr).value.trim(), brand: $('.b', tr).value.trim(), qty: $('.q', tr).value, price: $('.pr', tr).value }))
      .filter((i) => i.name || i.part_number);
    if (!items.length) throw new Error('Add at least one item');
    const fd = new FormData();
    const { receipt, ...rest } = data;
    fd.append('data', JSON.stringify({ ...rest, items }));
    if (f.elements.receipt.files[0]) fd.append('receipt', f.elements.receipt.files[0]);
    const r = await fetch('api/purchases', { method: 'POST', body: fd });
    if (!r.ok) throw new Error((await r.json()).error || 'Failed');
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

async function documentsTab(el, car) {
  const docs = await api(`/documents?car_id=${car.id}`);
  el.innerHTML = `
    <form class="panel form" id="upForm" style="margin-bottom:16px">
      <label>Type<select name="kind">${options(REF.doc_kinds, 'Manual')}</select></label>
      <label>Title (for a single file)<input name="title" placeholder="Bentley service manual"></label>
      <label class="wide">Files<input type="file" name="file" multiple required></label>
      <div><button class="primary">Upload</button></div>
    </form>
    <div class="panel table-wrap" style="padding:0">${docs.length ? `<table><thead><tr><th>Title</th><th>Type</th><th>File</th><th class="num">Size</th><th>Added</th><th></th></tr></thead><tbody>
      ${docs.map((d) => `<tr data-did="${d.id}"><td><a href="${fileUrl(d)}" target="_blank"><b>${esc(d.title)}</b></a>${d.notes ? `<div class="small muted">${esc(d.notes)}</div>` : ''}${d.parts ? `<div class="small muted">${d.parts} parts from this receipt</div>` : ''}</td>
        <td>${esc(d.kind)}</td><td class="small muted">${esc(d.original_name)}</td><td class="num small">${fmtSize(d.size || 0)}</td><td class="small muted">${esc((d.uploaded_at || '').slice(0, 10))}</td>
        <td class="num" style="white-space:nowrap"><a class="btn" href="${fileUrl(d)}?download=1">⬇</a> <button class="link edit">Edit</button></td></tr>`).join('')}
    </tbody></table>` : '<div class="empty">No manuals or files yet.</div>'}</div>`;
  $('#upForm').onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    fd.append('car_id', car.id);
    const btn = $('button', e.target); btn.disabled = true; btn.textContent = 'Uploading…';
    const r = await fetch('api/documents', { method: 'POST', body: fd });
    btn.disabled = false;
    if (!r.ok) return toast((await r.json().catch(() => ({}))).error || 'Upload failed', true);
    toast('Uploaded');
    documentsTab(el, car);
  };
  $$('tr[data-did]', el).forEach((tr) => {
    const d = docs.find((x) => x.id === +tr.dataset.did);
    $('.edit', tr).onclick = () => {
      const f = openDialog('Edit file', `<div class="form">
          <label class="wide">Title<input name="title" value="${esc(d.title)}" required></label>
          <label>Type<select name="kind">${options(REF.doc_kinds, d.kind)}</select></label>
          <label class="wide">Notes<textarea name="notes">${esc(d.notes)}</textarea></label></div>
          <button type="button" class="danger" id="delDoc">Delete file</button>`,
      async (data) => { await api('/documents/' + d.id, { method: 'PUT', json: data }); documentsTab(el, car); });
      $('#delDoc', f).onclick = async () => {
        if (!confirm(`Delete ${d.title}? The file is removed from the app (backups keep a copy).`)) return;
        await api('/documents/' + d.id, { method: 'DELETE' });
        $('#dlg').close(); documentsTab(el, car);
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
    <div class="panel">${car.mileage_log.length ? `<table class="small"><tbody>${car.mileage_log.map((m) => `<tr><td>${esc(m.date)}</td><td class="num">${num(m.mileage)}</td><td class="muted">${esc(m.source)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">No readings yet.</p>'}</div>`;
  $('#carForm').onsubmit = async (e) => {
    e.preventDefault();
    await api('/cars/' + car.id, { method: 'PUT', json: Object.fromEntries(new FormData(e.target)) });
    toast('Saved'); rerender();
  };
  $('#hideCar').onclick = async () => {
    await api('/cars/' + car.id, { method: 'PUT', json: { archived: !car.archived } });
    toast(car.archived ? 'Back in the garage' : 'Hidden. Find it under "Hidden vehicles" on the garage page.');
    location.hash = '#/';
  };
  $('#photoForm').onsubmit = async (e) => {
    e.preventDefault();
    const btn = $('button', e.target); btn.disabled = true; btn.textContent = 'Uploading…';
    const r = await fetch(`api/cars/${car.id}/photo`, { method: 'POST', body: new FormData(e.target) });
    if (!r.ok) { toast((await r.json().catch(() => ({}))).error || 'Upload failed', true); btn.disabled = false; return; }
    toast('Photo saved'); rerender();
  };
  if ($('#delPhoto')) $('#delPhoto').onclick = async () => {
    await api(`/cars/${car.id}/photo`, { method: 'DELETE' }); toast('Photo removed'); rerender();
  };
  $('#delCar').onclick = async () => {
    if (!confirm(`Delete ${car.name} with its services and schedules? Its parts and files are kept, unassigned.`)) return;
    await api('/cars/' + car.id, { method: 'DELETE' });
    location.hash = '#/';
  };
}

function addCarPage() {
  view.innerHTML = `<div class="small"><a href="#/">Garage</a> ›</div><h1>Add a vehicle</h1>
    <form class="panel form" id="newCar">${carFormHtml({})}
      <label>Current mileage<input name="mileage" inputmode="numeric"></label>
      <div class="wide"><button class="primary">Add vehicle</button></div></form>`;
  $('#newCar').onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await api('/cars', { json: Object.fromEntries(new FormData(e.target)) });
      location.hash = '#/car/' + r.id;
    } catch (err) { toast(err.message, true); }
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
    <div class="panel stack small">
      <p>The add-on keeps these sensors up to date in Home Assistant (turn off with the <b>publish_sensors</b> option):
        <code>sensor.garage_overdue</code>, <code>sensor.garage_due_soon</code>, <code>sensor.garage_parts_to_get</code>, and per vehicle
        <code>sensor.garage_&lt;vehicle&gt;_odometer</code> / <code>_status</code> / <code>_next_service</code> / <code>_next_service_due</code> / <code>_parts_to_get</code>.</p>
      <p>To add a <b>Garage</b> tab: open your dashboard, <b>Edit dashboard › ⋮ › Raw configuration editor</b>, and paste this under <code>views:</code>.</p>
      <pre class="code" id="dashYaml">Loading…</pre>
      <div><button type="button" id="copyDash">Copy</button></div>
    </div>` : `<h2>Home Assistant</h2>
    <div class="panel stack small">
      <p>Ready for later. Home Assistant needs phone access turned on (so it can reach this PC). Vehicle ids: ${cars.map((c) => `<b>${c.id}</b> = ${esc(c.name)}`).join(', ')}.</p>
      <p><b>Summary</b> (mileage, overdue / due-soon counts, next service, parts needed): <code>GET http://${esc(host)}:${port}/api/ha/summary</code></p>
      <p><b>Update mileage</b>: <code>POST http://${esc(host)}:${port}/api/cars/&lt;id&gt;/mileage</code> with <code>{"mileage": 123456}</code></p>
    </div>`}`;
  const waitForRestart = async () => {
    await new Promise((res) => setTimeout(res, 1500));
    for (let i = 0; i < 60; i++) {
      try { await api('/ping'); return; } catch (err) { await new Promise((res) => setTimeout(res, 500)); }
    }
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
      await waitForRestart();
      settingsPage();
    }
  };
  $('#restoreForm').onsubmit = async (e) => {
    e.preventDefault();
    const file = e.target.backup.files[0];
    if (!file || !confirm(`Replace ALL data here with ${file.name}? The current data is saved as a before-restore zip first.`)) return;
    const btn = $('button', e.target); btn.disabled = true;
    $('#restoreMsg').textContent = 'Uploading and checking the backup…';
    try {
      const r = await fetch('api/restore', { method: 'POST', body: new FormData(e.target) });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || 'Restore failed');
      $('#restoreMsg').textContent = 'Restoring and restarting…';
      await waitForRestart();
      toast('Backup restored');
      location.hash = '#/';
      router();
    } catch (err) { $('#restoreMsg').textContent = ''; toast(err.message, true); btn.disabled = false; }
  };
}

// catalog.js adds its routes first; both scripts have run by DOMContentLoaded
window.addEventListener('DOMContentLoaded', router);
