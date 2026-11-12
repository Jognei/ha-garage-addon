/* 3D workshop: one vehicle as a model you pull apart. Pick a part by clicking it; the panel shows its part number,
   fastener specs (thread, torque, tightening order) and the repair procedures it belongs to.
   Uses the helpers from app.js ($, esc, api, toast, openDialog, view, routes). three.js loads only when needed. */
'use strict';

let THREE = null;
const WS = { dispose: null };
const wsMissing = (label = 'missing') => `<span class="miss">${esc(label)}</span>`;
const ftlb = (nm) => (nm * 0.7376).toFixed(nm < 20 ? 1 : 0);

routes.push(
  [/^\/workshop$/, workshopHome],
  [/^\/workshop\/([a-z0-9-]+)$/, workshopView],
);

async function workshopHome(params) {
  if (WS.dispose) WS.dispose();
  const packs = await api('/workshop/packs');
  if (packs.length === 1 && !params.get('manage')) { location.replace('#/workshop/' + packs[0].id); return; }
  view.innerHTML = `<h1>3D workshop</h1>
    <p class="muted">A vehicle as a 3D model you can pull apart. Click a part to see its part number, fastener specs and repair steps.</p>
    ${packs.length ? `<div class="groups">${packs.map((p) => `<div class="panel"><h2><a href="#/workshop/${esc(p.id)}">${esc(p.title)}</a></h2>
      <div class="muted small">${esc(p.subtitle)}</div>
      <div class="small" style="margin:6px 0">${p.sections} sections · ${p.parts} parts · ${p.procedures} procedures</div>
      <button class="link danger" data-del="${esc(p.id)}">Remove</button></div>`).join('')}</div>` : '<div class="panel empty">No model imported yet.</div>'}
    <div class="panel" style="margin-top:14px"><h2>Import a model</h2>
      <p class="muted small">A model is a JSON file (sections, parts made of simple shapes, fasteners, procedures). Importing one with the same id replaces it; torque values you entered yourself are kept.</p>
      <input type="file" id="wsFile" accept=".json,application/json"></div>`;
  $('#wsFile').onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const r = await api('/workshop/packs', { json: JSON.parse(await f.text()) });
      REF = null;
      toast('Model imported');
      location.hash = '#/workshop/' + r.id;
    } catch (err) { toast(err.message, true); }
  };
  view.querySelectorAll('[data-del]').forEach((b) => {
    b.onclick = async () => {
      if (!confirm('Remove this model? Torque values you entered are kept in case you import it again.')) return;
      await api('/workshop/packs/' + b.dataset.del, { method: 'DELETE' });
      REF = null;
      router();
    };
  });
}

async function workshopView(id, params) {
  if (WS.dispose) WS.dispose();
  const pack = await api('/workshop/packs/' + id);
  view.innerHTML = '<div class="panel empty">Loading the 3D model…</div>';
  if (!THREE) THREE = await import('./vendor/three.module.min.js');
  const parts = new Map(pack.parts.map((p) => [p.id, p]));
  const procs = new Map((pack.procedures || []).map((p) => [p.id, p]));
  const secs = new Map(pack.sections.map((s) => [s.id, s]));
  const st = { sel: null, focus: null, hidden: new Set(pack.sections.filter((s) => s.hidden).map((s) => s.id)), proc: null, step: null };

  view.innerHTML = `<div class="spread"><div><h1>${esc(pack.title)}</h1><div class="muted small">${esc(pack.subtitle || '')}</div></div>
      <div class="row"><input type="search" id="wsFind" placeholder="Find a part by name or number" style="min-width:230px"><a class="btn" href="#/workshop?manage=1">Models</a></div></div>
    ${pack.notice ? `<div class="callout-box warn small">${esc(pack.notice)}</div>` : ''}
    <div class="ws">
      <div class="ws-side" id="wsSecs"></div>
      <div class="ws-stage" id="wsStage">
        <div class="ws-tip" id="wsTip" hidden></div>
        <div class="ws-bar">
          <label>Pull sections apart <input type="range" id="wsEx1" min="0" max="100" value="0"></label>
          <label>Explode parts <input type="range" id="wsEx2" min="0" max="100" value="0"></label>
          <button id="wsFit" type="button">Fit view</button>
        </div>
        <div class="ws-hint small">Drag to turn · scroll or pinch to zoom · right-drag or two fingers to move · click a part</div>
      </div>
      <div class="ws-side ws-detail" id="wsDetail"></div>
    </div>`;

  const V = wsViewer($('#wsStage'), pack, {
    onPick: (pid) => select(pid),
    onHover: (pid, x, y) => {
      const tip = $('#wsTip');
      if (!tip) return;
      tip.hidden = !pid;
      if (pid) { tip.textContent = parts.get(pid).name; tip.style.left = x + 12 + 'px'; tip.style.top = y + 12 + 'px'; }
    },
  });
  WS.dispose = () => { V.dispose(); WS.dispose = null; };

  function applyVisibility() {
    V.setVisible((sid) => (st.focus ? sid === st.focus : !st.hidden.has(sid)));
  }

  function drawSections() {
    $('#wsSecs').innerHTML = `<h2>Sections</h2>
      ${st.focus ? '<button id="wsAll" type="button" class="primary" style="width:100%;margin-bottom:8px">← Whole car</button>' : ''}
      <ul class="ws-list">${pack.sections.map((s) => `<li class="${st.focus === s.id ? 'on' : ''}">
        <input type="checkbox" data-vis="${esc(s.id)}" ${st.hidden.has(s.id) ? '' : 'checked'} title="Show or hide" ${st.focus ? 'disabled' : ''}>
        <button class="link" data-focus="${esc(s.id)}" title="Show only this section">${esc(s.title)}</button>
        <span class="muted small">${pack.parts.filter((p) => p.section === s.id).length}</span></li>`).join('')}</ul>
      ${st.focus ? `<h2 style="margin-top:12px">Parts</h2><ul class="ws-list ws-parts">${pack.parts.filter((p) => p.section === st.focus).map((p) => `<li class="${st.sel === p.id ? 'on' : ''}">
        <button class="link" data-part="${esc(p.id)}">${esc(p.name)}</button></li>`).join('')}</ul>` : '<p class="muted small">Tick to show or hide. Click a name to work on that section alone.</p>'}`;
    const all = $('#wsAll');
    if (all) all.onclick = () => focus(null);
    $('#wsSecs').querySelectorAll('[data-vis]').forEach((c) => {
      c.onchange = () => { c.checked ? st.hidden.delete(c.dataset.vis) : st.hidden.add(c.dataset.vis); applyVisibility(); };
    });
    $('#wsSecs').querySelectorAll('[data-focus]').forEach((b) => { b.onclick = () => focus(b.dataset.focus); });
    $('#wsSecs').querySelectorAll('[data-part]').forEach((b) => { b.onclick = () => select(b.dataset.part); });
  }

  function focus(sid) {
    st.focus = sid;
    applyVisibility();
    V.fit();
    drawSections();
  }

  function highlight() {
    const step = st.proc && st.step != null ? (procs.get(st.proc).steps[st.step].parts || []) : [];
    V.highlight(st.sel, step);
  }

  function select(pid) {
    st.sel = pid;
    st.proc = null;
    st.step = null;
    highlight();
    drawDetail();
    if (st.focus) drawSections();
  }

  const pnLink = (p) => (p.part_number
    ? (REF.features.catalog ? `<a href="#/catalog/part/${esc(p.part_number)}">${esc(p.part_number_fmt || p.part_number)}</a>` : esc(p.part_number_fmt || p.part_number))
    : (p.alternatives || []).length
      ? `<div class="small muted">${p.alternatives.length} versions in the catalog; pick the one for your car:</div>${p.alternatives.map((a) => `<div>${REF.features.catalog
        ? `<a href="#/catalog/part/${esc(a.part_number)}">${esc(a.part_number_fmt)}</a>` : esc(a.part_number_fmt)} <span class="small muted">${esc(a.description)}${a.supplement ? ' · ' + esc(a.supplement) : ''}</span></div>`).join('')}`
      : wsMissing());
  const torque = (f) => (f && f.torque_nm != null ? `<b>${esc(f.torque_nm)} Nm</b> (${ftlb(f.torque_nm)} ft-lb)${f.torque_note ? ' · ' + esc(f.torque_note) : ''}` : wsMissing('missing'));

  function fastenerRows(p) {
    const f = p.fastener || {};
    const n = (p.instances || []).length || 1;
    const hasSeq = (p.instances || []).some((i) => i.seq != null);
    const mine = new Set(f.owner_entered || []);
    return `<table class="ws-spec">
      <tr><th>Thread</th><td>${f.thread ? esc(f.thread) : wsMissing()}</td></tr>
      ${f.grade ? `<tr><th>Grade</th><td>${esc(f.grade)}</td></tr>` : ''}
      ${f.tool ? `<tr><th>Tool</th><td>${esc(f.tool)}</td></tr>` : ''}
      <tr><th>Shown on model</th><td>${n}${p.count_note ? ' · ' + esc(p.count_note) : ''}</td></tr>
      <tr><th>Torque</th><td>${torque(f)}${mine.has('torque_nm') ? ' <span class="muted small">(entered by you)</span>' : ''}</td></tr>
      <tr><th>Tightening order</th><td>${f.sequence_note ? esc(f.sequence_note) : (hasSeq ? '' : wsMissing())}${hasSeq ? `${f.sequence_note ? '<br>' : ''}<span class="small">Numbers are shown on the model.</span>` : ''}</td></tr>
      <tr><th>Source</th><td>${f.source ? esc(f.source) : wsMissing('none recorded')}</td></tr>
    </table><button type="button" id="wsEditSpec">${f.torque_nm != null ? 'Edit torque' : 'Enter torque from your manual'}</button>`;
  }

  function drawDetail() {
    const el = $('#wsDetail');
    if (st.proc) { drawProc(el); return; }
    const p = st.sel && parts.get(st.sel);
    if (!p) {
      el.innerHTML = `<h2>Pick a part</h2><p class="muted">Click anything on the model. Use the sliders to pull the car apart and get at what's underneath.</p>
        ${procs.size ? `<h2>Procedures</h2><ul class="ws-list">${[...procs.values()].map((r) => `<li><button class="link" data-proc="${esc(r.id)}">${esc(r.title)}</button></li>`).join('')}</ul>` : ''}`;
    } else {
      const held = (p.held_by || []).map((i) => parts.get(i)).filter(Boolean);
      el.innerHTML = `<div class="small muted">${esc(secs.get(p.section).title)}</div><h2>${esc(p.name)}</h2>
        <table class="ws-spec"><tr><th>Part number</th><td>${pnLink(p)}</td></tr>
          ${p.qty ? `<tr><th>Quantity</th><td>${esc(p.qty)}</td></tr>` : ''}
          ${p.diagram_code ? `<tr><th>Catalog</th><td>${esc(p.diagram_code)}${p.callout ? ' · position ' + esc(p.callout) : ''}</td></tr>` : ''}</table>
        ${p.note ? `<p class="small">${esc(p.note)}</p>` : ''}
        ${p.fastener ? `<h3>Fastener</h3>${fastenerRows(p)}` : ''}
        ${held.length ? `<h3>Held on by</h3><table class="ws-spec">${held.map((h) => `<tr><th><button class="link" data-part="${esc(h.id)}">${esc(h.name)}</button></th>
          <td>${(h.instances || []).length || 1} × ${h.fastener && h.fastener.thread ? esc(h.fastener.thread) : wsMissing('thread missing')} · ${torque(h.fastener)}</td></tr>`).join('')}</table>` : ''}
        <h3>Procedures</h3>${(p.procedures || []).length ? `<ul class="ws-list">${p.procedures.map((i) => `<li><button class="link" data-proc="${esc(i)}">${esc(procs.get(i).title)}</button></li>`).join('')}</ul>`
          : `<p class="small">${wsMissing('No procedure written for this part yet')}</p>`}
        ${!st.focus || st.focus !== p.section ? `<button type="button" id="wsFocusSel">Work on ${esc(secs.get(p.section).title)} alone</button>` : ''}`;
    }
    el.querySelectorAll('[data-proc]').forEach((b) => { b.onclick = () => { st.proc = b.dataset.proc; st.step = null; drawDetail(); }; });
    el.querySelectorAll('[data-part]').forEach((b) => { b.onclick = () => select(b.dataset.part); });
    const fs = $('#wsFocusSel');
    if (fs) fs.onclick = () => focus(p.section);
    const ed = $('#wsEditSpec');
    if (ed) ed.onclick = () => editSpec(p);
  }

  function drawProc(el) {
    const r = procs.get(st.proc);
    el.innerHTML = `<button class="link" id="wsBack">← Back</button><h2>${esc(r.title)}</h2>
      <div class="small muted">${[r.difficulty, r.time].filter(Boolean).map(esc).join(' · ')}</div>
      ${r.basis ? `<div class="callout-box warn small">${esc(r.basis)}</div>` : ''}
      ${(r.tools || []).length ? `<h3>Tools</h3><ul class="small">${r.tools.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
      ${(r.needs || []).length ? `<h3>Parts to have ready</h3><ul class="small">${r.needs.map((i) => { const p = parts.get(i); return p ? `<li><button class="link" data-part="${esc(i)}">${esc(p.name)}</button> ${pnLink(p)}</li>` : ''; }).join('')}</ul>` : ''}
      <h3>Steps</h3><ol class="ws-steps">${r.steps.map((s, i) => { const tp = s.torque_of && parts.get(s.torque_of); return `<li data-step="${i}" class="${st.step === i ? 'on' : ''}">${esc(s.text)}
        ${tp ? `<div class="small">Torque (${esc(tp.name)}${tp.fastener && tp.fastener.thread ? ', ' + esc(tp.fastener.thread) : ''}): ${torque(tp.fastener)}</div>` : ''}
        ${s.warn ? `<div class="small ws-warn">${esc(s.warn)}</div>` : ''}</li>`; }).join('')}</ol>
      <p class="muted small">Click a step to light up the parts it involves.</p>`;
    $('#wsBack').onclick = () => { st.proc = null; st.step = null; highlight(); drawDetail(); };
    el.querySelectorAll('[data-step]').forEach((li) => {
      li.onclick = () => { st.step = Number(li.dataset.step); highlight(); drawProc(el); };
    });
    el.querySelectorAll('[data-part]').forEach((b) => { b.onclick = (e) => { e.stopPropagation(); select(b.dataset.part); }; });
  }

  function editSpec(p) {
    const f = p.fastener || {};
    openDialog(`Torque: ${p.name}`, `
      <p class="small muted">Enter the value from your service manual. Leave a field empty to clear it.</p>
      <label>Torque (Nm) <input name="torque_nm" type="number" step="0.1" min="0" value="${esc(f.torque_nm ?? '')}"></label>
      <label>Torque note (stages, angle, new bolts…) <input name="torque_note" value="${esc(f.torque_note || '')}"></label>
      <label>Tightening order <input name="sequence_note" value="${esc(f.sequence_note || '')}"></label>
      <label>Source (manual and page) <input name="source" value="${esc(f.source || '')}"></label>`,
    async (fd) => {
      const fresh = await api(`/workshop/packs/${id}/specs/${p.id}`, { method: 'PUT', json: Object.fromEntries(fd.entries()) });
      fresh.parts.forEach((np) => { parts.get(np.id).fastener = np.fastener; });
      drawDetail();
      toast('Saved');
    });
  }

  $('#wsEx1').oninput = (e) => V.explode(e.target.value / 100, null);
  $('#wsEx2').oninput = (e) => V.explode(null, e.target.value / 100);
  $('#wsFit').onclick = () => V.fit();
  $('#wsFind').onchange = (e) => {
    const q = e.target.value.trim().toLowerCase();
    if (!q) return;
    const hit = pack.parts.find((p) => p.name.toLowerCase().includes(q) || (p.part_number || '').includes(q.replace(/\s+/g, '')));
    if (!hit) { toast('Nothing on the model matches that', true); return; }
    if (st.focus && st.focus !== hit.section) focus(hit.section);
    st.hidden.delete(hit.section);
    applyVisibility();
    drawSections();
    select(hit.id);
  };
  applyVisibility();
  V.fit();
  drawSections();
  drawDetail();
  if (params.get('part') && parts.has(params.get('part'))) select(params.get('part'));
}

/* ---- the three.js scene ---- */
function wsViewer(stage, pack, { onPick, onHover }) {
  const T = THREE;
  const dark = matchMedia('(prefers-color-scheme: dark)').matches;
  const renderer = new T.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.setClearColor(dark ? 0x15181d : 0xeef1f5);
  const canvas = renderer.domElement;
  canvas.className = 'ws-canvas';
  stage.prepend(canvas);
  const scene = new T.Scene();
  scene.add(new T.HemisphereLight(0xffffff, 0x7a8696, 2.1));
  const sun = new T.DirectionalLight(0xffffff, 1.6);
  sun.position.set(3, 6, 4);
  scene.add(sun);
  const fill = new T.DirectionalLight(0xffffff, 0.6);
  fill.position.set(-4, 2, -5);
  scene.add(fill);
  const grid = new T.GridHelper(8, 32, dark ? 0x3a424d : 0xb9c1cc, dark ? 0x262b33 : 0xd9dee5);
  grid.position.set(-1.4, 0, 0);
  scene.add(grid);
  const camera = new T.PerspectiveCamera(40, 1, 0.005, 200);
  const root = new T.Group();
  scene.add(root);
  let dirty = true;
  const touch = () => { dirty = true; };

  // geometry cache: identical shapes share one geometry
  const cache = new Map();
  const turn = (g, axis) => { if (axis === 'x') g.rotateZ(-Math.PI / 2); else if (axis === 'z') g.rotateX(Math.PI / 2); return g; };
  function geoms(s) {
    const key = JSON.stringify(s);
    if (cache.has(key)) return cache.get(key);
    let out;
    const d = s.d;
    switch (s.type) {
      case 'box': out = [new T.BoxGeometry(...s.size)]; break;
      case 'cyl': out = [turn(new T.CylinderGeometry(s.r2 ?? s.r, s.r, s.h, s.seg || 28), s.axis)]; break;
      case 'sphere': { const g = new T.SphereGeometry(s.r, 24, 16); if (s.scale) g.scale(...s.scale); out = [g]; break; }
      case 'bolt': {
        const head = s.head === 'socket' ? new T.CylinderGeometry(d * 0.78, d * 0.78, d, 16) : new T.CylinderGeometry(d * 0.92, d * 0.92, d * 0.66, 6);
        head.translate(0, (s.head === 'socket' ? d : d * 0.66) / 2, 0);
        out = [new T.CylinderGeometry(d / 2, d / 2, s.len, 10).translate(0, -s.len / 2, 0), head];
        if (s.washer) out.push(new T.CylinderGeometry(d * 1.15, d * 1.15, d * 0.14, 18).translate(0, 0, 0));
        out.forEach((g) => turn(g, s.axis));
        break;
      }
      case 'capnut': out = [new T.CylinderGeometry(d * 0.92, d * 0.92, d * 0.8, 6).translate(0, d * 0.4, 0), new T.SphereGeometry(d * 0.72, 12, 8).translate(0, d * 0.85, 0)].map((g) => turn(g, s.axis)); break;
      case 'stud': out = [turn(new T.CylinderGeometry(d / 2, d / 2, s.len, 10).translate(0, s.len / 2, 0), s.axis)]; break;
      case 'ring': { const g = new T.TorusGeometry(s.r, s.tube, 10, 36); if (s.axis === 'y' || !s.axis) g.rotateX(Math.PI / 2); else if (s.axis === 'x') g.rotateY(Math.PI / 2); out = [g]; break; }
      case 'frame': {
        const [w, l] = s.size; const k = s.wall;
        const sh = new T.Shape([[-w / 2, -l / 2], [w / 2, -l / 2], [w / 2, l / 2], [-w / 2, l / 2]].map((p) => new T.Vector2(...p)));
        sh.holes.push(new T.Path([[-w / 2 + k, -l / 2 + k], [-w / 2 + k, l / 2 - k], [w / 2 - k, l / 2 - k], [w / 2 - k, -l / 2 + k]].map((p) => new T.Vector2(...p))));
        out = [new T.ExtrudeGeometry(sh, { depth: s.h, bevelEnabled: false }).rotateX(Math.PI / 2).translate(0, s.h / 2, 0)];
        break;
      }
      case 'extrude': {
        const sh = new T.Shape(s.pts.map((p) => new T.Vector2(...p)));
        (s.holes || []).forEach((h) => sh.holes.push(new T.Path(h.map((p) => new T.Vector2(...p)))));
        const b = s.bevel || 0;
        const g = new T.ExtrudeGeometry(sh, { depth: s.depth - 2 * b, bevelEnabled: b > 0, bevelSize: b, bevelThickness: b, bevelSegments: 3, curveSegments: 10 });
        g.translate(0, 0, -(s.depth - 2 * b) / 2);
        if (s.plane === 'xz') g.rotateX(Math.PI / 2);
        else if (s.plane === 'zy') g.rotateY(-Math.PI / 2);
        out = [g];
        break;
      }
      case 'tube': {
        const pts = s.path.map((p) => new T.Vector3(...p));
        let curve;
        if (s.smooth === false) { curve = new T.CurvePath(); for (let i = 1; i < pts.length; i++) curve.add(new T.LineCurve3(pts[i - 1], pts[i])); } else curve = new T.CatmullRomCurve3(pts, !!s.closed, 'catmullrom', 0.4);
        out = [new T.TubeGeometry(curve, Math.max(8, pts.length * 10), s.r, s.seg || 12, !!s.closed)];
        break;
      }
      default: out = [new T.BoxGeometry(0.05, 0.05, 0.05)];
    }
    cache.set(key, out);
    return out;
  }

  const rad = (a) => (a || [0, 0, 0]).map((v) => (v * Math.PI) / 180);
  const secGroups = new Map();
  const frameGroups = new Map();
  function frameGroup(sid, name) {
    const sg = secGroups.get(sid);
    if (!name) return sg;
    const key = sid + '|' + name;
    if (frameGroups.has(key)) return frameGroups.get(key);
    const f = pack.frames[name];
    const g = new T.Group();
    g.position.set(...(f.pos || [0, 0, 0]));
    g.rotation.set(...rad(f.rot));
    frameGroup(sid, f.parent).add(g);
    frameGroups.set(key, g);
    return g;
  }
  pack.sections.forEach((s) => { const g = new T.Group(); g.userData.section = s; secGroups.set(s.id, g); root.add(g); });
  const partObjs = new Map(); // part id -> { mat, nodes: [Group] }
  const pickable = [];
  pack.parts.forEach((p) => {
    const opacity = p.opacity ?? 1;
    const mat = new T.MeshStandardMaterial({ color: p.color || '#9aa3ad', metalness: p.metal ?? 0.25, roughness: p.rough ?? 0.6,
      transparent: opacity < 1, opacity, side: opacity < 1 || p.shape.type === 'extrude' ? T.DoubleSide : T.FrontSide, depthWrite: opacity >= 1 });
    const parent = frameGroup(p.section, p.frame);
    const nodes = (p.instances && p.instances.length ? p.instances : [{ pos: p.pos, rot: p.rot }]).map((inst) => {
      const node = new T.Group();
      node.position.set(...(inst.pos || [0, 0, 0]));
      node.rotation.set(...rad(inst.rot || p.rot));
      node.userData = { base: node.position.clone(), seq: inst.seq, explode: inst.explode || p.explode };
      geoms(p.shape).forEach((g) => {
        const m = new T.Mesh(g, mat);
        m.userData.part = p.id;
        node.add(m);
        pickable.push(m);
      });
      parent.add(node);
      return node;
    });
    partObjs.set(p.id, { mat, nodes, part: p });
  });

  // camera: orbit around a target
  const cam = { target: new T.Vector3(-1.4, 0.6, 0), r: 7, theta: -0.9, phi: 1.15 };
  function placeCamera() {
    cam.phi = Math.min(Math.PI - 0.05, Math.max(0.05, cam.phi));
    const s = Math.sin(cam.phi);
    camera.position.set(cam.target.x + cam.r * s * Math.cos(cam.theta), cam.target.y + cam.r * Math.cos(cam.phi), cam.target.z + cam.r * s * Math.sin(cam.theta));
    camera.near = Math.max(0.002, cam.r / 400);
    camera.far = cam.r * 60 + 50;
    camera.updateProjectionMatrix();
    camera.lookAt(cam.target);
    touch();
  }
  function fit() {
    const box = new T.Box3();
    secGroups.forEach((g) => { if (g.visible) box.expandByObject(g); });
    if (box.isEmpty()) return;
    box.getCenter(cam.target);
    const radius = box.getSize(new T.Vector3()).length() / 2;
    const v = (camera.fov * Math.PI) / 360;
    const half = Math.min(v, Math.atan(Math.tan(v) * camera.aspect));
    cam.r = Math.max(0.15, (radius / Math.sin(half)) * 0.78);
    placeCamera();
  }
  function resize() {
    const w = stage.clientWidth; const h = stage.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    touch();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(stage);

  // pointer handling: one finger/left = turn, two fingers/right/shift = move, wheel/pinch = zoom
  const ptrs = new Map();
  let moved = 0; let pinch = 0;
  const pan = (dx, dy) => {
    const k = cam.r * 0.0016;
    const right = new T.Vector3().setFromMatrixColumn(camera.matrix, 0);
    const up = new T.Vector3().setFromMatrixColumn(camera.matrix, 1);
    cam.target.addScaledVector(right, -dx * k).addScaledVector(up, dy * k);
  };
  canvas.addEventListener('contextmenu', (e) => e.preventDefault());
  canvas.addEventListener('pointerdown', (e) => {
    canvas.setPointerCapture(e.pointerId);
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY, btn: e.button, shift: e.shiftKey });
    moved = 0; pinch = 0;
  });
  canvas.addEventListener('pointermove', (e) => {
    const p = ptrs.get(e.pointerId);
    if (!p) { hover(e); return; }
    const dx = e.clientX - p.x; const dy = e.clientY - p.y;
    moved += Math.abs(dx) + Math.abs(dy);
    if (ptrs.size === 2) {
      p.x = e.clientX; p.y = e.clientY;
      const [a, b] = [...ptrs.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinch) cam.r *= pinch / dist;
      pinch = dist;
      pan(dx / 2, dy / 2);
    } else {
      p.x = e.clientX; p.y = e.clientY;
      if (p.btn === 2 || p.shift) pan(dx, dy);
      else { cam.theta += dx * 0.008; cam.phi -= dy * 0.008; }
    }
    placeCamera();
  });
  const up = (e) => {
    const p = ptrs.get(e.pointerId);
    ptrs.delete(e.pointerId);
    pinch = 0;
    if (p && moved < 6 && p.btn === 0) onPick(pickAt(e));
  };
  canvas.addEventListener('pointerup', up);
  canvas.addEventListener('pointercancel', (e) => { ptrs.delete(e.pointerId); });
  canvas.addEventListener('pointerleave', () => onHover(null));
  canvas.addEventListener('wheel', (e) => { e.preventDefault(); cam.r *= Math.exp(e.deltaY * 0.0012); cam.r = Math.min(60, Math.max(0.03, cam.r)); placeCamera(); }, { passive: false });

  const ray = new T.Raycaster();
  function pickAt(e) {
    const r = canvas.getBoundingClientRect();
    ray.setFromCamera(new T.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
    const hit = ray.intersectObjects(pickable, false).find((h) => { for (let o = h.object; o; o = o.parent) if (!o.visible) return false; return true; });
    return hit ? hit.object.userData.part : null;
  }
  let hovered = null; let selected = null; let stepSet = [];
  function paint() {
    partObjs.forEach((o, id) => {
      const c = id === selected ? 0xb35900 : stepSet.includes(id) ? 0x0d6b3a : id === hovered ? 0x1f3f66 : 0x000000;
      o.mat.emissive.setHex(c);
      o.mat.emissiveIntensity = c ? 1 : 0;
    });
    touch();
  }
  function hover(e) {
    const id = pickAt(e);
    const r = stage.getBoundingClientRect();
    onHover(id, e.clientX - r.left, e.clientY - r.top);
    if (id !== hovered) { hovered = id; canvas.style.cursor = id ? 'pointer' : 'grab'; paint(); }
  }

  // tightening-order numbers on the selected fastener
  const labels = new T.Group();
  scene.add(labels);
  function numberSprite(n) {
    const c = document.createElement('canvas');
    c.width = c.height = 64;
    const x = c.getContext('2d');
    x.fillStyle = '#b35900'; x.beginPath(); x.arc(32, 32, 30, 0, 7); x.fill();
    x.fillStyle = '#fff'; x.font = 'bold 34px sans-serif'; x.textAlign = 'center'; x.textBaseline = 'middle'; x.fillText(String(n), 32, 34);
    const sp = new T.Sprite(new T.SpriteMaterial({ map: new T.CanvasTexture(c), depthTest: false }));
    sp.renderOrder = 10;
    return sp;
  }
  function relabel() {
    labels.children.slice().forEach((s) => { s.material.map.dispose(); s.material.dispose(); labels.remove(s); });
    const o = selected && partObjs.get(selected);
    if (!o) return;
    scene.updateMatrixWorld(true);
    o.nodes.forEach((n) => {
      if (n.userData.seq == null) return;
      const sp = numberSprite(n.userData.seq);
      n.getWorldPosition(sp.position);
      sp.position.y += 0.02;
      sp.userData.node = n;
      labels.add(sp);
    });
  }

  const ex = { sections: 0, parts: 0 };
  function applyExplode() {
    secGroups.forEach((g) => { const e = g.userData.section.explode || [0, 0, 0]; g.position.set(e[0] * ex.sections, e[1] * ex.sections, e[2] * ex.sections); });
    partObjs.forEach((o) => {
      o.nodes.forEach((n) => {
        const e = n.userData.explode;
        if (e) n.position.set(n.userData.base.x + e[0] * ex.parts, n.userData.base.y + e[1] * ex.parts, n.userData.base.z + e[2] * ex.parts);
      });
    });
    relabel();
    touch();
  }

  let alive = true;
  (function loop() {
    if (!alive) return;
    if (!canvas.isConnected) { dispose(); return; }
    if (dirty) {
      dirty = false;
      labels.children.forEach((s) => { const k = camera.position.distanceTo(s.position) * 0.022; s.scale.set(k, k, k); });
      renderer.render(scene, camera);
    }
    requestAnimationFrame(loop);
  }());
  function dispose() {
    if (!alive) return;
    alive = false;
    ro.disconnect();
    cache.forEach((gs) => gs.forEach((g) => g.dispose()));
    partObjs.forEach((o) => o.mat.dispose());
    renderer.dispose();
    renderer.forceContextLoss();
    canvas.remove();
  }
  resize();
  placeCamera();
  return {
    fit,
    dispose,
    explode(a, b) { if (a != null) ex.sections = a; if (b != null) ex.parts = b; applyExplode(); },
    setVisible(fn) { secGroups.forEach((g, id) => { g.visible = fn(id); }); relabel(); touch(); },
    highlight(sel, step) { selected = sel; stepSet = step || []; paint(); relabel(); },
  };
}
