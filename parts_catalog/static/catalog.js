/* Offline parts catalog UI: vehicles, main groups, diagrams with clickable callouts, part search, import & validation.
   Uses the helpers from app.js ($, esc, api, toast, openDialog, options, view, routes). */
'use strict';

const EPC = {
  get vehicle() { try { return localStorage.getItem('epcVehicle') || ''; } catch (e) { return ''; } },
  set vehicle(v) { try { localStorage.setItem('epcVehicle', v || ''); } catch (e) { /* storage blocked */ } },
};
const vq = () => (EPC.vehicle ? `vehicle=${EPC.vehicle}` : '');
const missing = (label = 'missing') => `<span class="miss">${esc(label)}</span>`;
const sampleTag = (on) => (on ? ' <span class="sample-tag">SAMPLE</span>' : '');
const ym = (v) => (v ? `${v.slice(4, 6)}/${v.slice(0, 4)}` : '');
const range = (i) => (i.from_date || i.to_date ? `${i.from_date ? 'from ' + ym(i.from_date) : ''}${i.from_date && i.to_date ? ' ' : ''}${i.to_date ? 'to ' + ym(i.to_date) : ''}` : '');
const garageChip = (g) => (g && g.length ? g.map((p) => `<span class="badge ${sCls(p.status)}" title="${esc(p.car || 'Unassigned')}: ${esc(p.name)}">${esc(p.status)}</span>`).join(' ') : '');

routes.push(
  [/^\/catalog$/, catalogHome],
  [/^\/catalog\/group\/(\d+)$/, catalogGroup],
  [/^\/catalog\/diagram\/(\d+)$/, catalogDiagram],
  [/^\/catalog\/part\/([\w-]+)$/, catalogPart],
  [/^\/catalog\/search$/, catalogSearch],
  [/^\/catalog\/data$/, catalogData],
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

async function catalogGroup(id) {
  const [tree, list] = await Promise.all([api('/epc/categories'), api(`/epc/diagrams?category=${id}&${vq()}`)]);
  const flat = tree.flatMap((c) => [c, ...c.children]);
  const cat = flat.find((c) => String(c.id) === id) || { code: '?', name: 'Unknown group' };
  view.innerHTML = `${await catalogHeader(null, `${esc(cat.code)} ${esc(cat.name)}`, '<a href="#/catalog">Catalog</a>')}
    <div class="panel table-wrap" style="padding:0;margin-top:12px">${list.length ? `<table><thead><tr><th>Diagram</th><th>Title</th><th class="num">Parts</th><th>Image</th><th>Callouts</th></tr></thead><tbody>
      ${list.map((d) => `<tr class="click" data-href="#/catalog/diagram/${d.id}"><td class="mono">${esc(d.code)}</td><td><a href="#/catalog/diagram/${d.id}">${esc(d.title) || missing('no title')}</a>${sampleTag(d.is_sample)}</td>
        <td class="num">${d.items || missing('none')}</td><td>${d.has_image ? '✓' : missing('no image')}</td><td>${d.hotspots ? '✓ clickable' : '<span class="muted small">list only</span>'}</td></tr>`).join('')}
    </tbody></table>` : '<div class="empty">No diagrams in this group' + (EPC.vehicle ? ' for this vehicle' : '') + ' yet.</div>'}</div>`;
  wireHeader();
  $$('tr[data-href]').forEach((tr) => (tr.onclick = (e) => { if (!e.target.closest('a')) location.hash = tr.dataset.href; }));
}

async function catalogDiagram(id, params) {
  const d = await api(`/epc/diagrams/${id}?${vq()}`);
  const imgUrl = d.image_path ? 'epc-files/' + d.image_path : '';
  const cat = d.category_id ? `<a href="#/catalog/group/${d.category_id}">${esc(d.category_code)} ${esc(d.category_name)}</a>` : missing('no category');
  const hasSpots = d.hotspots.length > 0;
  const showAll = params.get('all') === '1';
  const items = d.items.filter((i) => showAll || i.fits);
  const hidden = d.items.length - items.length;
  view.innerHTML = `${await catalogHeader(null, `${esc(d.title) || esc(d.code)}${sampleTag(d.is_sample)}`, `<a href="#/catalog">Catalog</a> › ${cat} › <span class="mono">${esc(d.code)}</span>`)}
    <div class="diagram" style="margin-top:12px">
      <div class="pic">
        ${imgUrl && d.image_exists ? `<div class="pic-inner" id="pic"><img src="${esc(imgUrl)}" alt="Diagram ${esc(d.code)}">
          ${d.hotspots.map((h, n) => `<a class="callout" data-c="${esc(h.callout)}" title="${esc(h.callout)}" style="left:${h.x * 100}%;top:${h.y * 100}%;width:${h.w * 100}%;height:${h.h * 100}%"></a>`).join('')}</div>`
          : `<div class="pic-inner noimg">${imgUrl ? 'Image file is missing on disk' : 'No diagram image imported yet'}</div>`}
        <div class="small muted" style="margin-top:6px">${hasSpots ? 'Click a callout number to find its parts.' : (imgUrl ? 'No callout coordinates for this diagram: the parts list isn\'t linked to the picture.' : '')}
          Source: ${esc(d.source_name || '?')}${d.image_name ? ` · image ${esc(d.image_name)}` : ''} · vehicles: ${d.vehicles.map((v) => esc(v.code)).join(', ') || missing('none')}</div>
        ${d.notes ? `<div class="note small">${esc(d.notes)}</div>` : ''}
      </div>
      <div class="panel table-wrap" style="padding:0">
        ${hidden ? `<div class="small muted" style="padding:8px 10px">${hidden} rows don't fit the selected vehicle's production dates. <a href="#/catalog/diagram/${id}?all=1">Show all</a></div>` : ''}
        ${showAll && d.items.some((i) => !i.fits) ? `<div class="small muted" style="padding:8px 10px">Showing all rows, including ones that don't fit. <a href="#/catalog/diagram/${id}">Hide them</a></div>` : ''}
        ${items.length ? `<table id="items"><thead><tr><th>No.</th><th>Description</th><th>Part number</th><th class="num">Qty</th><th>Fitment</th><th>Mine</th><th></th></tr></thead><tbody>
        ${items.map((i) => `<tr data-c="${esc(i.callout)}" class="${i.fits ? '' : 'nofit'}">
          <td class="mono"><b>${esc(i.callout) || '–'}</b></td>
          <td>${esc(i.description) || missing('no description')}${i.supplement ? `<div class="small muted">${esc(i.supplement)}</div>` : ''}</td>
          <td class="mono" style="white-space:nowrap">${i.part_number ? `<a href="#/catalog/part/${esc(i.part_number)}">${esc(i.part_number_fmt)}</a>` : missing(i.part_number_raw || 'none')}
            ${i.part_number && !i.has_part_record ? '<div class="small">' + missing('no part record') + '</div>' : ''}
            ${i.superseded_by ? `<div class="small">→ replaced by <a href="#/catalog/part/${esc(i.superseded_by)}">${esc(i.superseded_by_fmt)}</a></div>` : ''}</td>
          <td class="num">${i.qty == null ? missing('?') : esc(i.qty)}</td>
          <td class="small">${esc(range(i))}${i.fitment_note ? `<div class="muted">${esc(i.fitment_note)}</div>` : ''}</td>
          <td>${garageChip(i.garage)}</td>
          <td>${i.part_number ? `<button class="link add" data-id="${i.id}" title="Add to my parts list">＋</button>` : ''}</td></tr>`).join('')}
        </tbody></table>` : '<div class="empty">No parts list imported for this diagram yet.</div>'}
      </div>
    </div>`;
  wireHeader();
  // callout <-> row highlighting
  const hl = (c, scroll) => {
    $$('.callout').forEach((a) => a.classList.toggle('hl', a.dataset.c === c));
    const rowsHit = $$('#items tbody tr').filter((tr) => tr.dataset.c === c);
    $$('#items tbody tr').forEach((tr) => tr.classList.toggle('hl', tr.dataset.c === c));
    if (scroll && rowsHit[0]) rowsHit[0].scrollIntoView({ block: 'center', behavior: 'smooth' });
  };
  $$('.callout').forEach((a) => (a.onclick = (e) => { e.preventDefault(); hl(a.dataset.c, true); }));
  $$('#items tbody tr').forEach((tr) => (tr.onclick = (e) => { if (!e.target.closest('a,button')) hl(tr.dataset.c, false); }));
  $$('button.add').forEach((b) => (b.onclick = () => addToGarage(d, d.items.find((i) => String(i.id) === b.dataset.id))));
  if (params.get('c')) hl(params.get('c'), true);
}

async function addToGarage(d, item) {
  const cars = await api('/cars');
  openDialog('Add to my parts list', `<div class="form">
      <label class="wide">Name<input name="name" value="${esc(item.description || item.part_description || 'Part')}" required></label>
      <label>Part #<input name="part_number" value="${esc(item.part_number)}"></label>
      <label>Vehicle<select name="car_id">${options(cars.map((c) => [c.id, c.name]), cars[0] && cars[0].id, 'Not assigned')}</select></label>
      <label>Status<select name="status">${options(REF.statuses, 'Needed')}</select></label>
      <label>Qty<input name="qty" value="${esc(item.qty_num || 1)}"></label>
      <label class="wide">Notes<input name="notes" value="${esc(`Catalog diagram ${d.code}${item.callout ? ' #' + item.callout : ''}${item.supplement ? ' · ' + item.supplement : ''}`)}"></label></div>`,
  async (data) => {
    await api('/parts', { json: { ...data, category: d.category_name || '' } });
    toast('Added to your parts list');
    rerender();
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
      <div>In my parts list: ${garageChip(p.garage) || '<span class="muted">no</span>'}</div>
      ${p.record ? `<div class="small muted">Record source: ${esc(p.record.source_name || '?')}</div>` : ''}
    </div>
    <h2>Used in</h2>
    <div class="panel table-wrap" style="padding:0">${p.used_in.length ? `<table><thead><tr><th>Diagram</th><th>No.</th><th>Description</th><th class="num">Qty</th><th>Fitment</th></tr></thead><tbody>
      ${p.used_in.map((u) => `<tr><td><a href="#/catalog/diagram/${u.diagram_id}?c=${encodeURIComponent(u.callout)}"><span class="mono">${esc(u.diagram_code)}</span> ${esc(u.diagram_title)}</a></td>
        <td class="mono">${esc(u.callout)}</td><td>${esc(u.description)}${u.supplement ? `<div class="small muted">${esc(u.supplement)}</div>` : ''}</td>
        <td class="num">${u.qty == null ? missing('?') : esc(u.qty)}</td><td class="small">${esc(range(u))}</td></tr>`).join('')}
    </tbody></table>` : '<div class="empty">Not on any imported diagram.</div>'}</div>`;
  wireHeader();
}

async function catalogSearch(params) {
  const q = params.get('q') || '';
  const res = q ? await api(`/epc/search?q=${encodeURIComponent(q)}&${vq()}`) : [];
  view.innerHTML = `${await catalogHeader(null, `Search: ${esc(q)}`, '<a href="#/catalog">Catalog</a>')}
    <div class="panel table-wrap" style="padding:0;margin-top:12px">${res.length ? `<table><thead><tr><th>Part number</th><th>Description</th><th class="num">Qty</th><th>Diagram</th><th>Fitment</th><th>Mine</th></tr></thead><tbody>
      ${res.map((r) => `<tr><td class="mono" style="white-space:nowrap"><a href="#/catalog/part/${esc(r.part_number)}">${esc(r.part_number_fmt) || missing('none')}</a></td>
        <td>${esc(r.description) || missing('no description')}${sampleTag(r.is_sample)}${r.supplement ? `<div class="small muted">${esc(r.supplement)}</div>` : ''}</td>
        <td class="num">${r.qty == null ? missing('?') : esc(r.qty)}</td>
        <td><a href="#/catalog/diagram/${r.diagram_id}?c=${encodeURIComponent(r.callout)}"><span class="mono">${esc(r.diagram_code)}</span> #${esc(r.callout)} ${esc(r.diagram_title)}</a></td>
        <td class="small">${esc(range(r))}</td><td>${garageChip(r.garage)}</td></tr>`).join('')}
    </tbody></table>${res.length >= 200 ? '<div class="small muted" style="padding:8px">Showing the first 200 matches.</div>' : ''}` : `<div class="empty">${q ? 'No matches.' : 'Type a part number (any spacing) or words from the description.'}</div>`}</div>`;
  wireHeader();
  $('#epcSearch').q.value = q;
}

async function catalogData() {
  const [ov, val] = await Promise.all([api('/epc/overview'), api('/epc/validation')]);
  const sev = { error: 0, warning: 1, info: 2 };
  const summ = Object.entries(val.summary).sort((a, b) => sev[a[1].severity] - sev[b[1].severity]);
  view.innerHTML = `${await catalogHeader(ov.vehicles, 'Catalog data &amp; import', '<a href="#/catalog">Catalog</a>')}
    <h2>Import local files</h2>
    <form class="panel form" id="impForm">
      <label class="wide">Files: JSON bundle, CSV, saved HTML pages (with their image), or diagram images
        <input type="file" name="file" multiple required accept=".json,.csv,.tsv,.txt,.html,.htm,image/*"></label>
      <label>Link to vehicle<select name="vehicle">${options(ov.vehicles.map((v) => [v.code, v.code]), ov.vehicles.find((v) => !v.is_sample)?.code, 'None')}</select></label>
      <label>Main group for new diagrams<input name="category" placeholder="e.g. 11"></label>
      <label>Diagram code (one HTML page)<input name="diagram" placeholder="default: file name"></label>
      <label>Source name<input name="source" placeholder="e.g. My saved pages"></label>
      <label class="row" style="flex-direction:row"><input type="checkbox" name="create" value="1"> Images with no matching diagram become new diagrams</label>
      <div><button class="primary">Import</button></div>
    </form>
    <div id="impResult"></div>
    <p class="small muted">Everything stays on this PC. Originals are kept in <code>data/epc/files/</code> so they can be re-imported. Formats: <code>docs/catalog.md</code>. Command line: <code>tools\\epc_import.py</code>.</p>
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
