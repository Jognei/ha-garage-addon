/* Offline parts catalog UI: vehicles, main groups, diagrams with clickable callouts, part search, import & validation.
   Uses the helpers from app.js ($, esc, api, toast, openDialog, options, view, routes). */
'use strict';

// Choices remembered on this device (null when never made).
const saved = {
  get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v || ''); } catch (e) { /* storage blocked */ } },
};
const EPC = {
  get vehicle() { return saved.get('epcVehicle') || ''; },
  set vehicle(v) { saved.set('epcVehicle', v); },
};
const vq = () => (EPC.vehicle ? `vehicle=${EPC.vehicle}` : '');
// the same rule as norm_pn in app/epc.py; app.js uses it to link garage parts to the catalog
const normPn = (v) => String(v || '').replace(/[\s\-./]/g, '').toUpperCase();
const missing = (label = 'missing') => `<span class="miss">${esc(label)}</span>`;
const sampleTag = (on) => (on ? ' <span class="sample-tag">SAMPLE</span>' : '');
const ym = (v) => (v ? `${v.slice(4, 6)}/${v.slice(0, 4)}` : '');
const range = (i) => (i.from_date || i.to_date ? `${i.from_date ? 'from ' + ym(i.from_date) : ''}${i.from_date && i.to_date ? ' ' : ''}${i.to_date ? 'to ' + ym(i.to_date) : ''}` : '');
const garageChip = (g) => (g && g.length ? g.map((p) => `<span class="badge ${sCls(p.status)}" title="${esc(p.car || 'Unassigned')}: ${esc(p.name)}">${esc(p.status)}</span>`).join(' ') : '');
// "Mine" cell and add button of a diagram or search row; addToGarage fills the cells of the number it adds
const mineCell = (i) => `<td class="mine" data-pn="${esc(i.part_number)}">${garageChip(i.garage)}</td>`;
const addBtn = (i) => (i.part_number ? `<button class="link add" data-id="${i.id}" title="Add to my parts list">＋</button>` : '');

// When the catalog is switched off (Settings > Features), its pages say so instead of showing an empty catalog.
const catalogGuard = (fn) => async (...args) => {
  if (!(REF && REF.features && REF.features.catalog)) {
    view.innerHTML = '<div class="panel empty">The parts catalog is turned off. <a href="#/settings">Turn it on in Settings</a>.</div>';
    return undefined;
  }
  return fn(...args);
};
routes.push(
  [/^\/catalog$/, catalogGuard(catalogHome)],
  [/^\/catalog\/group\/(\d+)$/, catalogGuard(catalogGroup)],
  [/^\/catalog\/diagram\/(\d+)$/, catalogGuard(catalogDiagram)],
  [/^\/catalog\/part\/([\w-]+)$/, catalogGuard(catalogPart)],
  [/^\/catalog\/search$/, catalogGuard(catalogSearch)],
  [/^\/catalog\/data$/, catalogGuard(catalogData)],
);

async function catalogHeader(vehicles, title, crumbs = '') {
  const vs = vehicles || await api('/epc/vehicles');
  if (EPC.vehicle && !vs.some((v) => String(v.id) === EPC.vehicle)) EPC.vehicle = '';
  return `<div class="spread"><div>${crumbs ? `<div class="small muted">${crumbs}</div>` : ''}<h1>${title}</h1></div>
    <div class="row">
      <label class="small muted">Vehicle <select id="epcVehicle">${options(vs.map((v) => [v.id, `${v.code}${v.is_sample ? ' (SAMPLE)' : ''} · ${v.diagrams} diagrams`]), EPC.vehicle, 'All vehicles')}</select></label>
      <form id="epcSearch" class="row"><input type="search" name="q" placeholder="Part number or description" style="min-width:240px"><button>Search</button></form>
      <a class="btn" href="#/catalog/data">Data &amp; import</a>
    </div></div>`;
}

function wireHeader() {
  $('#epcVehicle').onchange = (e) => { EPC.vehicle = e.target.value; rerender(); };
  $('#epcSearch').onsubmit = (e) => {
    e.preventDefault();
    location.hash = '#/catalog/search?q=' + encodeURIComponent(e.target.q.value.trim());
  };
}

async function catalogHome() {
  const [ov, tree] = await Promise.all([api('/epc/overview'), api('/epc/categories?' + vq())]);
  const s = ov.stats;
  const sample = ov.sources.some((x) => x.is_sample);
  view.innerHTML = `${await catalogHeader(ov.vehicles, 'Parts catalog', '<a href="#/catalog">Catalog</a>')}
    ${sample ? '<div class="callout-box warn">Sample data is loaded. Records marked <span class="sample-tag">SAMPLE</span> are made up for testing. Remove them under Data &amp; import.</div>' : ''}
    <div class="row small muted" style="margin:10px 0">${num(s.diagrams)} diagrams (${num(s.diagrams_with_image)} with images, ${num(s.diagrams_with_hotspots)} with clickable callouts) · ${num(s.items)} parts-list rows · ${num(s.parts)} part numbers · ${num(s.supersessions)} supersessions</div>
    ${s.diagrams ? '' : `<div class="panel empty">No diagrams imported yet. <a href="#/catalog/data">Import data</a>${sample ? '' : ' or <button class="link" id="loadSample">load sample data</button> to try it out'}.</div>`}
    <div class="groups" style="margin-top:12px">${tree.filter((c) => c.total || !EPC.vehicle).map((c) => `
      <div class="panel group"><a class="row" href="#/catalog/group/${c.id}" style="gap:10px"><span class="mg">${esc(c.code)}</span><b>${esc(c.name)}</b>
        <span class="count">${c.total ? `${c.total} diagrams` : 'none yet'}</span></a>
        ${c.children.length ? `<ul>${c.children.map((ch) => `<li><a href="#/catalog/group/${ch.id}">${esc(ch.code)} ${esc(ch.name)}</a> <span class="muted small">${ch.diagrams}</span></li>`).join('')}</ul>` : ''}
      </div>`).join('') || '<div class="panel empty">No main groups for this vehicle yet.</div>'}</div>`;
  wireHeader();
  const ls = $('#loadSample');
  if (ls) ls.onclick = async () => { await api('/epc/sample', { method: 'POST' }); toast('Sample data loaded'); rerender(); };
}

// Group selector shown on group and diagram pages (main groups that have diagrams for the chosen vehicle).
async function groupSelect(currentId) {
  const tree = await api('/epc/categories?' + vq());
  const opts = tree.filter((c) => c.total).map((c) => [c.id, `${c.code} - ${c.name}`]);
  return { tree, html: `<select id="groupSel" aria-label="Main group">${options(opts, currentId, 'Main groups…')}</select>` };
}

function wireGroupSelect() {
  const g = $('#groupSel');
  if (g) g.onchange = () => { location.hash = g.value ? '#/catalog/group/' + g.value : '#/catalog'; };
}

const thumbUrl = (d) => (d.image_path ? 'epc-files/' + d.image_path : '');

async function catalogGroup(id) {
  const [{ tree, html: sel }, list] = await Promise.all([groupSelect(id), api(`/epc/diagrams?category=${id}&${vq()}`)]);
  const flat = tree.flatMap((c) => [c, ...c.children]);
  const cat = flat.find((c) => String(c.id) === id) || { code: '?', name: 'Unknown group' };
  view.innerHTML = `${await catalogHeader(null, `${esc(cat.code)} - ${esc(cat.name)}`, '<a href="#/catalog">Main groups</a>')}
    <div class="spread epc-bar"><span class="muted small">${list.length} diagram${list.length === 1 ? '' : 's'}</span>${sel}</div>
    ${list.length ? `<div class="thumbs">${list.map((d) => `<a class="thumb" href="#/catalog/diagram/${d.id}">
        <div class="thumb-img">${thumbUrl(d) ? `<img src="${esc(thumbUrl(d))}" alt="" loading="lazy">` : '<span class="muted small">no image</span>'}</div>
        <div class="thumb-title">${esc(d.title) || missing('no title')}${sampleTag(d.is_sample)}</div>
        <div class="thumb-code mono">${esc(d.code)}${d.items ? '' : ' · ' + missing('no parts list')}</div></a>`).join('')}</div>`
      : '<div class="panel empty">No diagrams in this group' + (EPC.vehicle ? ' for this vehicle' : '') + ' yet.</div>'}`;
  wireHeader();
  wireGroupSelect();
}

async function catalogDiagram(id, params) {
  const d = await api(`/epc/diagrams/${id}?${vq()}`);
  const [{ html: sel }, siblings] = await Promise.all([
    groupSelect(d.category_id),
    d.category_id ? api(`/epc/diagrams?category=${d.category_id}&${vq()}`) : Promise.resolve([]),
  ]);
  const pos = siblings.findIndex((x) => String(x.id) === String(id));
  const prev = pos > 0 ? siblings[pos - 1] : null;
  const next = pos >= 0 && pos < siblings.length - 1 ? siblings[pos + 1] : null;
  const imgUrl = d.image_path ? 'epc-files/' + d.image_path : '';
  const cat = d.category_id ? `<a href="#/catalog/group/${d.category_id}">${esc(d.category_code)} - ${esc(d.category_name)}</a>` : missing('no category');
  const hasSpots = d.hotspots.length > 0;
  const showAll = params.get('all') === '1';
  const items = d.items.filter((i) => showAll || i.fits);
  const hidden = d.items.length - items.length;
  view.innerHTML = `${await catalogHeader(null, `${esc(d.title) || esc(d.code)}${sampleTag(d.is_sample)}`, `<a href="#/catalog">Main groups</a> › ${cat} › <span class="mono">${esc(d.code)}</span>`)}
    <div class="spread epc-bar">
      <div class="row">
        <a class="btn ${prev ? '' : 'disabled'}" ${prev ? `href="#/catalog/diagram/${prev.id}"` : ''} title="Previous diagram (←)">←</a>
        <span class="small muted">${pos >= 0 ? `${pos + 1} / ${siblings.length}` : ''}</span>
        <a class="btn ${next ? '' : 'disabled'}" ${next ? `href="#/catalog/diagram/${next.id}"` : ''} title="Next diagram (→)">→</a>
        ${d.category_id ? `<a class="btn" href="#/catalog/group/${d.category_id}">All diagrams</a>` : ''}
      </div>${sel}</div>
    <div class="epc-viewer" id="viewer">
      <section class="epc-pane">
        <div class="epc-pane-head"><span><b>Diagram</b> <span class="muted small">${hasSpots ? 'Click a number to find its parts' : (imgUrl ? 'No clickable numbers on this diagram' : '')}</span></span>
          ${imgUrl && d.image_exists ? '<button type="button" id="enlarge">Enlarge</button>' : ''}</div>
        <div class="epc-pane-body">
          ${imgUrl && d.image_exists ? `<div class="pic-inner" id="pic"><img src="${esc(imgUrl)}" alt="Diagram ${esc(d.code)}">
            ${d.hotspots.map((h) => `<a class="callout" data-c="${esc(h.callout)}" title="${esc(h.callout)}" style="left:${h.x * 100}%;top:${h.y * 100}%;width:${h.w * 100}%;height:${h.h * 100}%"></a>`).join('')}</div>`
            : `<div class="pic-inner noimg">${imgUrl ? 'Image file is missing on disk' : 'No diagram image imported yet'}</div>`}
          ${d.notes ? `<div class="note small">${esc(d.notes)}</div>` : ''}
        </div>
        <div class="epc-pane-foot small" id="posStatus">${hasSpots ? 'Click a number on the diagram, or a row in the list.' : ''}</div>
      </section>
      <section class="epc-pane">
        <div class="epc-pane-head"><span><b>Parts list</b> <span class="muted small">${items.length} row${items.length === 1 ? '' : 's'} · Click a row to highlight its number</span></span></div>
        ${hidden ? `<div class="small muted epc-note">${hidden} rows don't fit the selected vehicle's production dates. <a href="#/catalog/diagram/${id}?all=1">Show all</a></div>` : ''}
        ${showAll && d.items.some((i) => !i.fits) ? `<div class="small muted epc-note">Showing all rows, including ones that don't fit. <a href="#/catalog/diagram/${id}">Hide them</a></div>` : ''}
        <div class="epc-pane-body epc-table">
        ${items.length ? `<table id="items"><thead><tr><th>No.</th><th>Description</th><th>Supp.</th><th class="num">Qty</th><th>From</th><th>Up to</th><th>Part number</th><th>Notes</th><th>Mine</th><th></th></tr></thead><tbody>
        ${items.map((i) => `<tr data-c="${esc(i.callout)}" class="${i.fits ? '' : 'nofit'}">
          <td class="mono"><b>${esc(i.callout) || '–'}</b></td>
          <td>${esc(i.description) || missing('no description')}</td>
          <td class="small">${esc(i.supplement)}</td>
          <td class="num">${i.qty == null ? missing('?') : esc(i.qty)}</td>
          <td class="small nowrap">${esc(ym(i.from_date))}</td>
          <td class="small nowrap">${esc(ym(i.to_date))}</td>
          <td class="mono nowrap">${i.part_number ? `<a href="#/catalog/part/${esc(i.part_number)}">${esc(i.part_number_fmt)}</a>` : missing(i.part_number_raw || 'none')}
            ${i.superseded_by ? `<div class="small">→ <a href="#/catalog/part/${esc(i.superseded_by)}">${esc(i.superseded_by_fmt)}</a></div>` : ''}</td>
          <td class="small">${esc(i.fitment_note)}</td>
          ${mineCell(i)}
          <td>${addBtn(i)}</td></tr>`).join('')}
        </tbody></table>` : '<div class="empty">No parts list for this diagram.</div>'}
        </div>
      </section>
    </div>`;
  wireHeader();
  wireGroupSelect();
  const status = $('#posStatus');
  const hl = (c, scroll) => {
    $$('.callout').forEach((a) => a.classList.toggle('hl', a.dataset.c === c));
    const rowsHit = $$('#items tbody tr').filter((tr) => tr.dataset.c === c);
    $$('#items tbody tr').forEach((tr) => tr.classList.toggle('hl', tr.dataset.c === c));
    if (status) status.textContent = `Position ${c || '–'} · ${rowsHit.length} matching row${rowsHit.length === 1 ? '' : 's'}`;
    if (scroll && rowsHit[0]) rowsHit[0].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  };
  $$('.callout').forEach((a) => (a.onclick = (e) => { e.preventDefault(); hl(a.dataset.c, true); }));
  $$('#items tbody tr').forEach((tr) => (tr.onclick = (e) => { if (!e.target.closest('a,button')) hl(tr.dataset.c, false); }));
  $$('button.add').forEach((b) => (b.onclick = () => addToGarage(d, d.items.find((i) => String(i.id) === b.dataset.id))));
  const enlarge = $('#enlarge');
  if (enlarge) enlarge.onclick = () => { const v = $('#viewer'); v.classList.toggle('big'); enlarge.textContent = v.classList.contains('big') ? 'Smaller' : 'Enlarge'; };
  // arrow keys step through the group's diagrams (not while typing)
  document.onkeydown = (e) => {
    if (!location.hash.startsWith('#/catalog/diagram/') || e.target.closest('input,select,textarea') || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === 'ArrowLeft' && prev) location.hash = '#/catalog/diagram/' + prev.id;
    if (e.key === 'ArrowRight' && next) location.hash = '#/catalog/diagram/' + next.id;
  };
  if (params.get('c')) hl(params.get('c'), true);
}

// d: the diagram the row is on ({ code, category_name }), or null from the part page.
async function addToGarage(d, item) {
  const [cars, p] = await Promise.all([api('/cars'), api('/epc/parts/' + encodeURIComponent(item.part_number))]);
  const newer = p.superseded_by[p.superseded_by.length - 1];
  const lastCar = saved.get('epcCar');  // '' = Not assigned
  const car = lastCar === '' || cars.some((c) => String(c.id) === lastCar) ? lastCar : cars[0] && cars[0].id;
  const notes = [d ? `Catalog diagram ${d.code}${item.callout ? ' #' + item.callout : ''}${item.supplement ? ' · ' + item.supplement : ''}` : '',
    newer ? `Replaces ${p.part_number_fmt}` : ''].filter(Boolean).join(' · ');
  openDialog('Add to my parts list', `<div class="form">
      <label class="wide">Name<input name="name" value="${esc(item.description || (p.record && p.record.description) || 'Part')}" required></label>
      <label>Part #<input name="part_number" value="${esc(newer ? newer.new_pn_fmt : p.part_number_fmt)}"></label>
      <label>Vehicle<select name="car_id">${options(cars.map((c) => [c.id, c.name]), car, 'Not assigned')}</select></label>
      <label>Status<select name="status">${options(REF.statuses, 'Needed')}</select></label>
      <label>Qty<input name="qty" inputmode="numeric" value="${esc(item.qty_num || Number(item.qty) || 1)}"></label>
      <label class="wide">Notes<input name="notes" value="${esc(notes)}"></label></div>`,
  async (data) => {
    await api('/parts', { json: { ...data, category: (d && d.category_name) || '' } });
    saved.set('epcCar', data.car_id);
    toast('Added to your parts list');
    const pn = normPn(data.part_number);
    const c = cars.find((x) => String(x.id) === data.car_id);
    const chip = garageChip([{ status: data.status, car: c ? c.name : null, name: data.name || data.part_number }]);
    $$('.mine').filter((el) => pn && el.dataset.pn === pn).forEach((el) => { el.innerHTML = $('.badge', el) ? el.innerHTML + ' ' + chip : chip; });
  }, { ok: 'Add' });
}

async function catalogPart(pn) {
  const p = await api('/epc/parts/' + encodeURIComponent(pn));
  view.innerHTML = `${await catalogHeader(null, `<span class="mono">${esc(p.part_number_fmt)}</span>${sampleTag(p.record && p.record.is_sample)}`, '<a href="#/catalog">Catalog</a> › part')}
    <div class="panel stack" style="margin-top:12px">
      <div><b>${p.record ? (esc(p.record.description) || missing('no description')) : missing('no part record')}</b></div>
      ${p.record && p.record.notes ? `<div class="note">${esc(p.record.notes)}</div>` : ''}
      ${p.superseded_by.length ? `<div>Replaced by: ${p.superseded_by.map((s) => `<a class="mono" href="#/catalog/part/${esc(s.new_pn)}">${esc(s.new_pn_fmt)}</a>${s.date ? ` <span class="small muted">(${esc(s.date)})</span>` : ''}`).join(' → ')}</div>` : ''}
      ${p.replaces.length ? `<div>Replaces: ${p.replaces.map((s) => `<a class="mono" href="#/catalog/part/${esc(s.old_pn)}">${esc(s.old_pn_fmt)}</a>`).join(', ')}</div>` : ''}
      <div>In my parts list: <span class="mine" data-pn="${esc(p.part_number)}">${garageChip(p.garage) || '<span class="muted">no</span>'}</span>
        ${p.part_number ? '<button class="link add" title="Add to my parts list">＋ Add</button>' : ''}</div>
      ${p.record ? `<div class="small muted">Record source: ${esc(p.record.source_name || '?')}</div>` : ''}
    </div>
    <h2>Used in</h2>
    <div class="panel table-wrap" style="padding:0">${p.used_in.length ? `<table><thead><tr><th>Diagram</th><th>No.</th><th>Description</th><th class="num">Qty</th><th>Fitment</th></tr></thead><tbody>
      ${p.used_in.map((u) => `<tr><td><a href="#/catalog/diagram/${u.diagram_id}?c=${encodeURIComponent(u.callout)}"><span class="mono">${esc(u.diagram_code)}</span> ${esc(u.diagram_title)}</a></td>
        <td class="mono">${esc(u.callout)}</td><td>${esc(u.description)}${u.supplement ? `<div class="small muted">${esc(u.supplement)}</div>` : ''}</td>
        <td class="num">${u.qty == null ? missing('?') : esc(u.qty)}</td><td class="small">${esc(range(u))}</td></tr>`).join('')}
    </tbody></table>` : '<div class="empty">Not on any imported diagram.</div>'}</div>`;
  wireHeader();
  const add = $('button.add');
  if (add) add.onclick = () => addToGarage(null, { part_number: p.part_number });
}

async function catalogSearch(params) {
  const q = params.get('q') || '';
  const res = q ? await api(`/epc/search?q=${encodeURIComponent(q)}&${vq()}`) : [];
  view.innerHTML = `${await catalogHeader(null, `Search: ${esc(q)}`, '<a href="#/catalog">Catalog</a>')}
    <div class="panel table-wrap" style="padding:0;margin-top:12px">${res.length ? `<table><thead><tr><th>Part number</th><th>Description</th><th class="num">Qty</th><th>Diagram</th><th>Fitment</th><th>Mine</th><th></th></tr></thead><tbody>
      ${res.map((r) => `<tr><td class="mono" style="white-space:nowrap">${r.part_number ? `<a href="#/catalog/part/${esc(r.part_number)}">${esc(r.part_number_fmt)}</a>` : missing('none')}</td>
        <td>${esc(r.description) || missing('no description')}${sampleTag(r.is_sample)}${r.supplement ? `<div class="small muted">${esc(r.supplement)}</div>` : ''}</td>
        <td class="num">${r.qty == null ? missing('?') : esc(r.qty)}</td>
        <td><a href="#/catalog/diagram/${r.diagram_id}?c=${encodeURIComponent(r.callout)}"><span class="mono">${esc(r.diagram_code)}</span> #${esc(r.callout)} ${esc(r.diagram_title)}</a></td>
        <td class="small">${esc(range(r))}</td>${mineCell(r)}<td>${addBtn(r)}</td></tr>`).join('')}
    </tbody></table>${res.length >= 200 ? '<div class="small muted" style="padding:8px">Showing the first 200 matches.</div>' : ''}` : `<div class="empty">${q ? `No matches.${EPC.vehicle ? ' <button class="link" id="searchAll">Search all vehicles</button>' : ''}` : 'Type a part number (any spacing) or words from the description.'}</div>`}</div>`;
  wireHeader();
  $('#epcSearch').q.value = q;
  const all = $('#searchAll');
  if (all) all.onclick = () => { EPC.vehicle = ''; rerender(); };
  $$('button.add').forEach((b) => (b.onclick = () => { const r = res.find((x) => String(x.id) === b.dataset.id); addToGarage({ code: r.diagram_code }, r); }));
}

async function catalogData() {
  const [ov, val, tree, set] = await Promise.all([api('/epc/overview'), api('/epc/validation'), api('/epc/categories'), api('/settings')]);
  const sev = { error: 0, warning: 1, info: 2 };
  const summ = Object.entries(val.summary).sort((a, b) => sev[a[1].severity] - sev[b[1].severity]);
  // The import form starts on the header's vehicle (or the only real one); its last choices come back while the header is unchanged.
  let last = {};
  try { last = JSON.parse(saved.get('epcImport')) || {}; } catch (e) { /* none */ }
  const real = ov.vehicles.filter((v) => !v.is_sample);
  const shown = EPC.vehicle ? ov.vehicles.find((v) => String(v.id) === EPC.vehicle) : real.length === 1 ? real[0] : null;
  const impVehicle = last.header === EPC.vehicle && (last.vehicle === '' || ov.vehicles.some((v) => v.code === last.vehicle)) ? last.vehicle : shown && shown.code;
  const groups = tree.flatMap((c) => [c, ...c.children]).map((c) => [c.code, `${c.code} - ${c.name}`]);
  view.innerHTML = `${await catalogHeader(ov.vehicles, 'Catalog data &amp; import', '<a href="#/catalog">Catalog</a>')}
    <h2>Import local files</h2>
    <form class="panel form" id="impForm">
      <label class="wide">Files: JSON bundle, CSV, saved HTML pages (with their image), or diagram images
        <input type="file" name="file" multiple required accept=".json,.csv,.tsv,.txt,.html,.htm,image/*"></label>
      <label>Link to vehicle<select name="vehicle">${options(ov.vehicles.map((v) => [v.code, v.code]), impVehicle, 'None')}</select></label>
      <label>Main group for new diagrams<select name="category">${options(groups, last.category, 'None')}</select></label>
      <label>Diagram code (one HTML page)<input name="diagram" placeholder="default: file name"></label>
      <label>Source name<input name="source" value="${esc(last.source || '')}" placeholder="e.g. My saved pages"></label>
      <label class="row" style="flex-direction:row"><input type="checkbox" name="create" value="1"> Images with no matching diagram become new diagrams</label>
      <div><button class="primary">Import</button></div>
    </form>
    <div id="impResult"></div>
    <p class="small muted">Everything stays on ${set.addon ? 'your Home Assistant' : 'this PC'}. Originals are kept in the data folder (<code>${esc(set.data_dir)}</code>) so they can be re-imported.${set.addon ? '' : ' Command line: <code>tools/epc_import.py</code>.'}</p>
    <details class="epc-formats small">
      <summary>File formats</summary>
      <p><b>JSON bundle</b> (.json): <code>{"format": "garage-epc", "source": {"name": …}, "vehicles": […], "categories": […], "parts": […], "diagrams": […], "supersessions": […], "expected_diagrams": […]}</code>, any list left out.
        Vehicles, categories (main groups), parts and supersessions take the CSV column names below as keys.
        A diagram has <code>code</code>, <code>title</code>, <code>category</code>, <code>vehicles</code> (a list of codes), <code>image</code> (path from the JSON file),
        <code>items</code> (parts-list rows with the CSV keys below, <code>vehicles</code> as a list) and <code>hotspots</code> (<code>callout</code>, <code>x</code>, <code>y</code>, <code>w</code>, <code>h</code>).
        Expected diagrams (<code>vehicle</code>, <code>code</code>, <code>title</code>) are reported as missing until they are imported.</p>
      <p><b>CSV</b> (.csv, .tsv, .txt; UTF-8, headers in the first row, columns split by comma, semicolon, tab or |): one kind per file, told apart by its headers.</p>
      <ul>
        <li>Parts-list rows: <code>diagram</code> with <code>part_number</code> or <code>callout</code>, and <code>description</code>, <code>supplement</code>, <code>qty</code>, <code>from</code>, <code>to</code>, <code>fitment_note</code>, <code>vehicles</code> (codes split by ;, limits the row to them), <code>category</code>, <code>title</code></li>
        <li>Hotspots: <code>diagram</code>, <code>callout</code>, <code>x</code>, <code>y</code>, <code>w</code>, <code>h</code>, as fractions of the image (0 to 1) or pixels</li>
        <li>Supersessions: <code>old</code>, <code>new</code>, <code>date</code>, <code>note</code></li>
        <li>Vehicles: <code>code</code>, <code>series</code>, and <code>make</code>, <code>model</code>, <code>body</code>, <code>market</code>, <code>engine</code>, <code>transmission</code>, <code>steering</code>, <code>prod_from</code>, <code>prod_to</code>, <code>notes</code></li>
        <li>Diagrams: <code>diagram</code>, <code>title</code>, <code>category</code></li>
        <li>Parts: <code>part_number</code>, <code>description</code>, <code>notes</code></li>
        <li>Main groups: <code>code</code>, <code>name</code>, <code>parent</code></li>
      </ul>
      <p>Headers ignore case, and common variants work (Part No., Ref, Pos, Quantity, Remarks). Production dates read as 1991-02, 02/1991 or 199102.</p>
      <p><b>Saved web page</b> (.html, .htm): the parts list is the table that holds 11-digit BMW part numbers, its columns read by their headers (No., Part number, Description, Qty, From, Up to).
        The diagram image is the one image sent with a single page, else the largest image saved with the page; its <code>&lt;map&gt;</code> areas become clickable numbers.
        The file name without .html is the diagram code (unless Diagram code is filled in for a single page), and the page title is the diagram title.</p>
      <p><b>Diagram images</b>: an image named after a diagram's code is attached to it, ignoring case and anything but letters and digits (<code>11-0050.PNG</code> fits <code>11_0050</code>).
        Images with no such diagram are reported, or become new diagrams when the box above is ticked.</p>
    </details>
    <h2>Validation report</h2>
    <div class="panel">${summ.length ? `<div class="row" style="margin-bottom:10px">${summ.map(([k, v]) => `<span class="chip ${v.severity === 'error' ? 'overdue' : v.severity === 'warning' ? 'soon' : 'unknown'}">${esc(k.replace(/_/g, ' '))}: ${v.count}</span>`).join(' ')}</div>
      <div class="table-wrap" style="max-height:420px;overflow:auto"><table><thead><tr><th>Severity</th><th>Check</th><th>Issue</th></tr></thead><tbody>
      ${val.issues.map((r) => { const did = (r.refs.find((x) => x.diagram_id) || {}).diagram_id; const pn = (r.refs.find((x) => x.part_number) || {}).part_number;
        return `<tr><td>${esc(r.severity)}</td><td class="small mono">${esc(r.check)}</td><td>${esc(r.message)} ${did ? `<a href="#/catalog/diagram/${did}">open</a>` : pn ? `<a href="#/catalog/part/${esc(pn)}">open</a>` : ''}</td></tr>`; }).join('')}
      </tbody></table></div>` : '<div class="empty">No issues found.</div>'}</div>
    <h2>Sources</h2>
    <div class="panel table-wrap" style="padding:0"><table><thead><tr><th>Source</th><th>Kind</th><th class="num">Diagrams</th><th class="num">Rows</th><th class="num">Files</th><th>Imported</th><th></th></tr></thead><tbody>
      ${ov.sources.map((s) => `<tr><td><b>${esc(s.name)}</b>${sampleTag(s.is_sample)}${s.note ? `<div class="small muted">${esc(s.note)}</div>` : ''}</td><td>${esc(s.kind)}</td>
        <td class="num">${s.diagrams}</td><td class="num">${s.items}</td><td class="num">${s.files}</td><td class="small muted">${esc((s.imported_at || '').slice(0, 10))}</td>
        <td><button class="link danger del" data-id="${s.id}" data-name="${esc(s.name)}">Remove</button></td></tr>`).join('')}
    </tbody></table></div>
    ${ov.sources.some((s) => s.is_sample) ? '' : '<p><button id="loadSample">Load sample data</button> <span class="small muted">(made-up records, clearly marked, for trying the catalog)</span></p>'}`;
  wireHeader();
  $('#impForm').onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target.elements;
    saved.set('epcImport', JSON.stringify({ header: EPC.vehicle, vehicle: f.vehicle.value, category: f.category.value, source: f.source.value.trim() }));
    const btn = $('button.primary', e.target); btn.disabled = true; btn.textContent = 'Importing…';
    try {
      const r = await fetch('api/epc/import', { method: 'POST', body: new FormData(e.target) });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || 'Import failed');
      sessionStorage.setItem('epcLastImport', JSON.stringify(data.reports));
      toast('Import finished');
      await catalogData();
      showImportResult();
    } catch (err) { toast(err.message, true); btn.disabled = false; btn.textContent = 'Import'; }
  };
  $$('button.del').forEach((b) => (b.onclick = async () => {
    if (!confirm(`Remove "${b.dataset.name}" and everything it imported (diagrams, rows, stored files)?`)) return;
    await api('/epc/sources/' + b.dataset.id, { method: 'DELETE' });
    toast('Source removed'); rerender();
  }));
  const ls = $('#loadSample');
  if (ls) ls.onclick = async () => { await api('/epc/sample', { method: 'POST' }); toast('Sample data loaded'); rerender(); };
}

function showImportResult() {
  let reps = [];
  try { reps = JSON.parse(sessionStorage.getItem('epcLastImport') || '[]'); } catch (e) { /* none */ }
  if (!reps.length) return;
  $('#impResult').innerHTML = `<div class="panel stack small" style="margin-top:10px"><b>Last import</b>${reps.map((r) => `<div><b>${esc(r.source)}</b>: ${r.error ? `<span class="miss">${esc(r.error)}</span>`
    : Object.entries(r.counts).map(([k, v]) => `${esc(k.replace(/_/g, ' '))} ${v}`).join(' · ')}
    ${(r.problems || []).length ? `<ul>${r.problems.map((p) => `<li>${esc(p)}</li>`).join('')}</ul>` : ''}</div>`).join('')}</div>`;
}
