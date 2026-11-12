"""Importers for the offline catalog: JSON bundle, CSV, saved HTML pages, local diagram images.

Every importer works on local files only (no network access). Original files are copied into
data/epc/files/<source>/ and recorded in source_files so they can be reprocessed later.
Values that a source doesn't give stay empty/NULL (shown as missing), never guessed.

Re-importing the same diagram from the same source replaces that source's rows for the diagram, so
imports are repeatable. Rows from a different source are kept and show up as duplicates in validation.
"""
import csv
import hashlib
import html
import io
import json
import os
import re
import shutil
from html.parser import HTMLParser

from . import epc

BUNDLE_FORMAT = "garage-epc"
BUNDLE_VERSION = 1
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff"}


class Report:
    """What an import did, plus problems found in the input itself."""

    def __init__(self, source):
        self.source = source
        self.counts = {}
        self.problems = []

    def count(self, k, n=1):
        self.counts[k] = self.counts.get(k, 0) + n

    def problem(self, msg):
        self.problems.append(msg)

    def as_dict(self):
        return {"source": self.source, "counts": self.counts, "problems": self.problems}


# ---------- helpers ----------

def slug(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")[:80] or "source"


def clean(v):
    if v is None:
        return ""
    return re.sub(r"\s+", " ", html.unescape(str(v))).strip()


def opt(v):
    v = clean(v)
    return v or None


def yyyymm(v):
    """'1991-02', '02/1991', '199102', '02.91' -> '199102'. Unparseable -> None (left missing)."""
    v = clean(v)
    if not v:
        return None
    m = re.match(r"^(\d{4})[-/.]?(\d{2})$", v)
    if m:
        return m.group(1) + m.group(2)
    m = re.match(r"^(\d{1,2})[-/.](\d{4})$", v)
    if m:
        return f"{m.group(2)}{int(m.group(1)):02d}"
    m = re.match(r"^(\d{1,2})[-/.](\d{2})$", v)
    if m:
        y = int(m.group(2))
        return f"{1900 + y if y > 30 else 2000 + y}{int(m.group(1)):02d}"
    return None


def qty_num(q):
    try:
        return float(q.replace(",", "."))
    except (AttributeError, ValueError):
        return None


def get_source(name, kind="", is_sample=False, note=""):
    conn = epc.connect()
    r = conn.execute("SELECT id FROM sources WHERE name=?", (name,)).fetchone()
    if r:
        conn.execute("UPDATE sources SET kind=COALESCE(NULLIF(?,''),kind), is_sample=?, note=COALESCE(NULLIF(?,''),note) WHERE id=?",
                     (kind, 1 if is_sample else 0, note, r[0]))
        return r[0]
    return conn.execute("INSERT INTO sources (name, kind, is_sample, note) VALUES (?,?,?,?)",
                        (name, kind, 1 if is_sample else 0, note)).lastrowid


def store_file(source_id, path, kind, data=None, name=None):
    """Copy a local file (or bytes) into data/epc/files/<source>/. Same content from the same source is stored once."""
    conn = epc.connect()
    if data is None:
        with open(path, "rb") as f:
            data = f.read()
    name = name or os.path.basename(path)
    sha = hashlib.sha256(data).hexdigest()
    r = conn.execute("SELECT id FROM source_files WHERE source_id=? AND sha256=?", (source_id, sha)).fetchone()
    if r:
        return r[0]
    src_name = conn.execute("SELECT name FROM sources WHERE id=?", (source_id,)).fetchone()[0]
    rel_dir = os.path.join("files", slug(src_name))
    os.makedirs(epc.file_path(rel_dir), exist_ok=True)
    base, ext = os.path.splitext(slug(name))
    rel = os.path.join(rel_dir, base + ext)
    if os.path.exists(epc.file_path(rel)):
        rel = os.path.join(rel_dir, f"{base}-{sha[:8]}{ext}")
    with open(epc.file_path(rel), "wb") as f:
        f.write(data)
    return conn.execute("INSERT INTO source_files (source_id, kind, original_name, stored_path, sha256, size) VALUES (?,?,?,?,?,?)",
                        (source_id, kind, name, rel.replace("\\", "/"), sha, len(data))).lastrowid


def image_size(path):
    """(w, h) in pixels, or (None, None). SVG: width/height or viewBox."""
    try:
        if path.lower().endswith(".svg"):
            with open(path, encoding="utf-8", errors="replace") as f:
                head = f.read(4000)
            m = re.search(r'viewBox="\s*[\d.\-]+[\s,]+[\d.\-]+[\s,]+([\d.]+)[\s,]+([\d.]+)', head)
            if m:
                return int(float(m.group(1))), int(float(m.group(2)))
            w, h = re.search(r'\bwidth="([\d.]+)', head), re.search(r'\bheight="([\d.]+)', head)
            return (int(float(w.group(1))), int(float(h.group(1)))) if w and h else (None, None)
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:  # noqa: BLE001 - an unreadable image just has unknown size
        return None, None


def get_category(code, name=None, parent_code=None, sort=None, source_id=None):
    conn = epc.connect()
    code = clean(code)
    if not code:
        return None
    r = conn.execute("SELECT id FROM categories WHERE code=?", (code,)).fetchone()
    parent_id = get_category(parent_code, source_id=source_id) if parent_code else None
    if parent_id is None and "." in code:
        parent_id = get_category(code.split(".")[0], source_id=source_id)
    if r:
        if name:
            conn.execute("UPDATE categories SET name=? WHERE id=?", (clean(name), r[0]))
        if parent_id:
            conn.execute("UPDATE categories SET parent_id=? WHERE id=?", (parent_id, r[0]))
        if sort is not None:
            conn.execute("UPDATE categories SET sort=? WHERE id=?", (sort, r[0]))
        return r[0]
    if sort is None:
        digits = re.sub(r"\D", "", code)
        sort = int(digits[:4]) if digits else 9999
    # A category seen only as a reference gets its code as name until a real name is imported.
    return conn.execute("INSERT INTO categories (code, name, parent_id, sort, source_id) VALUES (?,?,?,?,?)",
                        (code, clean(name) or f"Group {code}", parent_id, sort, source_id)).lastrowid


def vehicle_id(code, rep=None):
    r = epc.connect().execute("SELECT id FROM vehicles WHERE code=?", (clean(code),)).fetchone()
    if not r and rep:
        rep.problem(f"Unknown vehicle code {code!r} (import the vehicle first)")
    return r[0] if r else None


VEHICLE_FIELDS = ["make", "series", "model", "body", "market", "engine", "transmission", "steering", "notes"]


def upsert_vehicle(v, source_id, rep):
    code = clean(v.get("code"))
    if not code:
        rep.problem(f"Vehicle without code skipped: {v}")
        return None
    vals = {k: clean(v.get(k)) for k in VEHICLE_FIELDS if v.get(k) is not None}
    for k in ("prod_from", "prod_to"):
        if v.get(k) is not None:
            vals[k] = yyyymm(v.get(k))
    conn = epc.connect()
    r = conn.execute("SELECT id FROM vehicles WHERE code=?", (code,)).fetchone()
    if r:
        if vals:
            conn.execute(f"UPDATE vehicles SET {', '.join(k + '=?' for k in vals)} WHERE id=?", [*vals.values(), r[0]])
        rep.count("vehicles_updated")
        return r[0]
    vals.update(code=code, source_id=source_id)
    rep.count("vehicles_added")
    return conn.execute(f"INSERT INTO vehicles ({','.join(vals)}) VALUES ({','.join('?' * len(vals))})", list(vals.values())).lastrowid


def upsert_part(pn, description, notes, source_id, rep, overwrite=True):
    pn = epc.norm_pn(pn)
    if not pn:
        return
    conn = epc.connect()
    r = conn.execute("SELECT description FROM parts WHERE part_number=?", (pn,)).fetchone()
    if r:
        if description and (overwrite or not r[0]):
            conn.execute("UPDATE parts SET description=? WHERE part_number=?", (clean(description), pn))
        if notes:
            conn.execute("UPDATE parts SET notes=? WHERE part_number=?", (clean(notes), pn))
        return
    conn.execute("INSERT INTO parts (part_number, description, notes, source_id) VALUES (?,?,?,?)",
                 (pn, clean(description), clean(notes), source_id))
    rep.count("parts_added")


def find_diagram(code, source_id, vehicle_ids):
    """The existing diagram this import means, or None. Codes repeat between vehicles (BMW reuses "71_0342" and
    friends), so with vehicles given only a diagram linked to one of them matches (or an unlinked one from the
    same source). Without vehicle info: same source first, then any diagram with that code."""
    conn = epc.connect()
    cands = conn.execute("SELECT id, source_id FROM diagrams WHERE code=? ORDER BY id", (code,)).fetchall()
    if not cands:
        return None
    if vehicle_ids:
        for did, sid in cands:
            linked = {r[0] for r in conn.execute("SELECT vehicle_id FROM diagram_vehicles WHERE diagram_id=?", (did,))}
            if linked & set(vehicle_ids) or (not linked and sid == source_id):
                return did
        return None
    same = [c for c in cands if c[1] == source_id]
    return (same or cands)[0][0]


def upsert_diagram(code, source_id, rep, title=None, category=None, vehicles=(), notes=None, image_file_id=None, sort=None):
    conn = epc.connect()
    code = clean(code)
    cat_id = get_category(category, source_id=source_id) if category else None
    vids = [v for v in (vehicle_id(v, rep) for v in vehicles or ()) if v]
    r = find_diagram(code, source_id, vids)
    if r:
        did = r
        sets = {}
        if title:
            sets["title"] = clean(title)
        if cat_id:
            sets["category_id"] = cat_id
        if notes:
            sets["notes"] = clean(notes)
        if sort is not None:
            sets["sort"] = sort
        if sets:
            conn.execute(f"UPDATE diagrams SET {', '.join(k + '=?' for k in sets)} WHERE id=?", [*sets.values(), did])
        rep.count("diagrams_updated")
    else:
        did = conn.execute("INSERT INTO diagrams (code, title, category_id, notes, sort, source_id) VALUES (?,?,?,?,?,?)",
                           (code, clean(title), cat_id, clean(notes), sort or 0, source_id)).lastrowid
        rep.count("diagrams_added")
    for vid in vids:
        conn.execute("INSERT OR IGNORE INTO diagram_vehicles (diagram_id, vehicle_id) VALUES (?,?)", (did, vid))
    if image_file_id:
        attach_image(did, image_file_id)
    return did


def attach_image(diagram_id, file_id):
    conn = epc.connect()
    path = conn.execute("SELECT stored_path FROM source_files WHERE id=?", (file_id,)).fetchone()[0]
    w, h = image_size(epc.file_path(path))
    conn.execute("UPDATE diagrams SET image_file_id=?, image_w=?, image_h=? WHERE id=?", (file_id, w, h, diagram_id))


def replace_items(diagram_id, items, source_id, rep, source_file_id=None):
    """Replace this source's items on a diagram. Exact duplicates within the input are reported and skipped."""
    conn = epc.connect()
    conn.execute("DELETE FROM diagram_items WHERE diagram_id=? AND source_id IS ?", (diagram_id, source_id))
    seen = set()
    for n, it in enumerate(items):
        raw = clean(it.get("part_number"))
        pn = epc.norm_pn(raw)
        if pn and not re.search(r"\d", pn):  # "—", "N/A" etc. are not part numbers
            pn = ""
        q = it.get("qty")
        q = None if q is None or clean(q) == "" else clean(q)
        rec = dict(diagram_id=diagram_id, callout=clean(it.get("callout")), part_number=pn, part_number_raw=raw,
                   description=clean(it.get("description")), supplement=clean(it.get("supplement")), qty=q, qty_num=qty_num(q),
                   from_date=yyyymm(it.get("from")), to_date=yyyymm(it.get("to")), fitment_note=clean(it.get("fitment_note")),
                   sort=it.get("sort", n), source_id=source_id, source_file_id=source_file_id)
        key = (rec["callout"], pn, rec["description"], rec["supplement"], rec["from_date"], rec["to_date"], rec["qty"], rec["fitment_note"])
        if key in seen:
            rep.problem(f"Duplicate row skipped: diagram {diagram_id} callout {rec['callout']} {epc.fmt_pn(pn)}")
            rep.count("duplicates_skipped")
            continue
        seen.add(key)
        if not pn and not rec["description"]:
            rep.count("blank_rows_skipped")
            continue
        iid = conn.execute(f"INSERT INTO diagram_items ({','.join(rec)}) VALUES ({','.join('?' * len(rec))})", list(rec.values())).lastrowid
        rep.count("items_added")
        for v in it.get("vehicles") or ():
            vid = vehicle_id(v, rep)
            if vid:
                conn.execute("INSERT OR IGNORE INTO item_vehicles (item_id, vehicle_id) VALUES (?,?)", (iid, vid))
        if pn and it.get("create_part", True):
            upsert_part(pn, rec["description"], "", source_id, rep, overwrite=False)


def replace_hotspots(diagram_id, spots, source_id, rep, units="auto"):
    """Hotspots in fractions (0-1) or pixels; pixels are converted using the diagram image size."""
    conn = epc.connect()
    w, h = conn.execute("SELECT image_w, image_h FROM diagrams WHERE id=?", (diagram_id,)).fetchone()
    vals = []
    for s in spots:
        try:
            box = [float(s[k]) for k in ("x", "y", "w", "h")]
        except (KeyError, TypeError, ValueError):
            rep.problem(f"Hotspot with bad coordinates skipped: {s}")
            continue
        vals.append((clean(s.get("callout")), box))
    px = units == "px" or (units == "auto" and any(v > 1.0 for _, b in vals for v in b))
    if px and not (w and h):
        rep.problem(f"Diagram {diagram_id}: hotspots are in pixels but the image size is unknown; skipped")
        return
    conn.execute("DELETE FROM hotspots WHERE diagram_id=? AND source_id IS ?", (diagram_id, source_id))
    for callout, (x, y, bw, bh) in vals:
        if px:
            x, y, bw, bh = x / w, y / h, bw / w, bh / h
        conn.execute("INSERT INTO hotspots (diagram_id, callout, x, y, w, h, source_id) VALUES (?,?,?,?,?,?,?)",
                     (diagram_id, callout, x, y, bw, bh, source_id))
        rep.count("hotspots_added")


def add_supersession(old, new, source_id, rep, date=None, note=""):
    old, new = epc.norm_pn(old), epc.norm_pn(new)
    if not old or not new or old == new:
        rep.problem(f"Bad supersession skipped: {old} -> {new}")
        return
    epc.connect().execute("INSERT OR REPLACE INTO supersessions (old_pn, new_pn, date, note, source_id) VALUES (?,?,?,?,?)",
                          (old, new, opt(date), clean(note), source_id))
    rep.count("supersessions_added")


# ---------- JSON bundle ----------

def import_bundle(path_or_obj, source_name=None, base_dir=None, is_sample=None):
    """Import the portable JSON format (see Catalog > Data & import). Image paths are relative to the JSON file."""
    if isinstance(path_or_obj, dict):
        b, raw = path_or_obj, None
    else:
        with open(path_or_obj, "rb") as f:
            raw = f.read()
        b = json.loads(raw.decode("utf-8-sig"))
        base_dir = base_dir or os.path.dirname(os.path.abspath(path_or_obj))
    if b.get("format") not in (None, BUNDLE_FORMAT):
        raise ValueError(f"Not a {BUNDLE_FORMAT} file (format={b.get('format')!r})")
    src = b.get("source") or {}
    name = source_name or src.get("name") or (os.path.basename(path_or_obj) if raw else "JSON import")
    sample = bool(src.get("is_sample")) if is_sample is None else is_sample
    conn = epc.connect()
    sid = get_source(name, src.get("kind") or "json", sample, src.get("note", ""))
    rep = Report(name)
    fid = store_file(sid, path_or_obj, "json", data=raw) if raw else None
    for v in b.get("vehicles", []):
        upsert_vehicle(v, sid, rep)
    for c in b.get("categories", []):
        get_category(c.get("code"), c.get("name"), c.get("parent"), c.get("sort"), sid)
        rep.count("categories")
    for p in b.get("parts", []):
        upsert_part(p.get("part_number"), p.get("description"), p.get("notes"), sid, rep)
    for d in b.get("diagrams", []):
        if not clean(d.get("code")):
            rep.problem(f"Diagram without code skipped: {d.get('title')}")
            continue
        img_fid = None
        if d.get("image"):
            ip = d["image"] if os.path.isabs(d["image"]) else os.path.join(base_dir or ".", d["image"])
            if os.path.exists(ip):
                img_fid = store_file(sid, ip, "image")
            else:
                rep.problem(f"Diagram {d['code']}: image {d['image']} not found")
        did = upsert_diagram(d["code"], sid, rep, d.get("title"), d.get("category"), d.get("vehicles", []), d.get("notes"), img_fid, d.get("sort"))
        if "items" in d:
            replace_items(did, d["items"], sid, rep, fid)
        if d.get("hotspots"):
            replace_hotspots(did, d["hotspots"], sid, rep, d.get("hotspot_units", "auto"))
    for s in b.get("supersessions", []):
        add_supersession(s.get("old"), s.get("new"), sid, rep, s.get("date"), s.get("note", ""))
    for e in b.get("expected_diagrams", []):
        vid = vehicle_id(e.get("vehicle"), rep)
        if vid:
            conn.execute("INSERT OR REPLACE INTO expected_diagrams (vehicle_id, code, title, source_id) VALUES (?,?,?,?)",
                         (vid, clean(e.get("code")), clean(e.get("title")), sid))
            rep.count("expected_diagrams")
    conn.commit()
    return rep


def export_bundle(out_dir, include_samples=False):
    """Write catalog.json + images/ to out_dir: the whole catalog in the portable format."""
    os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
    skip = "" if include_samples else " AND IFNULL(s.is_sample,0)=0"
    b = {"format": BUNDLE_FORMAT, "version": BUNDLE_VERSION, "source": {"name": "Export", "kind": "json"}}
    b["vehicles"] = epc.rows(f"""SELECT v.code, v.make, v.series, v.model, v.body, v.market, v.engine, v.transmission, v.steering,
                                 v.prod_from, v.prod_to, v.notes FROM vehicles v LEFT JOIN sources s ON s.id=v.source_id WHERE 1=1{skip}""")
    b["categories"] = epc.rows("""SELECT c.code, c.name, p.code AS parent, c.sort FROM categories c
                                  LEFT JOIN categories p ON p.id=c.parent_id ORDER BY c.sort, c.code""")
    b["parts"] = epc.rows(f"""SELECT p.part_number, p.description, p.notes FROM parts p LEFT JOIN sources s ON s.id=p.source_id WHERE 1=1{skip}""")
    b["diagrams"] = []
    for d in epc.rows(f"""SELECT d.*, c.code AS category, f.stored_path FROM diagrams d LEFT JOIN categories c ON c.id=d.category_id
                          LEFT JOIN source_files f ON f.id=d.image_file_id LEFT JOIN sources s ON s.id=d.source_id WHERE 1=1{skip} ORDER BY d.code"""):
        out = {"code": d["code"], "title": d["title"], "category": d["category"], "notes": d["notes"], "sort": d["sort"],
               "vehicles": [r["code"] for r in epc.rows("SELECT v.code FROM vehicles v JOIN diagram_vehicles dv ON dv.vehicle_id=v.id WHERE dv.diagram_id=?", (d["id"],))]}
        if d["stored_path"] and os.path.exists(epc.file_path(d["stored_path"])):
            fn = slug(d["code"]) + os.path.splitext(d["stored_path"])[1]
            shutil.copyfile(epc.file_path(d["stored_path"]), os.path.join(out_dir, "images", fn))
            out["image"] = "images/" + fn
        items = epc.rows("SELECT * FROM diagram_items WHERE diagram_id=? ORDER BY sort, id", (d["id"],))
        out["items"] = [{"callout": i["callout"], "part_number": i["part_number_raw"] or i["part_number"], "description": i["description"],
                         "supplement": i["supplement"], "qty": i["qty"], "from": i["from_date"], "to": i["to_date"], "fitment_note": i["fitment_note"],
                         "vehicles": [r["code"] for r in epc.rows("SELECT v.code FROM vehicles v JOIN item_vehicles iv ON iv.vehicle_id=v.id WHERE iv.item_id=?", (i["id"],))]}
                        for i in items]
        out["hotspots"] = epc.rows("SELECT callout, x, y, w, h FROM hotspots WHERE diagram_id=?", (d["id"],))
        out["hotspot_units"] = "fraction"
        b["diagrams"].append(out)
    b["supersessions"] = epc.rows("SELECT old_pn AS old, new_pn AS new, date, note FROM supersessions")
    b["expected_diagrams"] = epc.rows("SELECT v.code AS vehicle, e.code, e.title FROM expected_diagrams e JOIN vehicles v ON v.id=e.vehicle_id")
    with open(os.path.join(out_dir, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump(b, f, indent=1, ensure_ascii=False)
    return os.path.join(out_dir, "catalog.json")


# ---------- CSV ----------

ALIASES = {
    "diagram": ["diagram", "diagram_code", "diagram id", "diagid", "illustration", "bildtafel"],
    "callout": ["callout", "ref", "ref no", "no", "no.", "nr", "#", "item", "pos", "position", "key"],
    "part_number": ["part_number", "part number", "part no", "part no.", "partno", "part #", "p/n", "pn", "oem", "oem number", "number", "part"],
    "description": ["description", "desc", "name", "designation", "part name", "title"],
    "supplement": ["supplement", "supplementary info", "info", "additional info", "remark", "remarks"],
    "qty": ["qty", "quantity", "qty.", "pcs", "amount", "count"],
    "from": ["from", "from_date", "prod from", "valid from", "start"],
    "to": ["to", "to_date", "up to", "prod to", "valid to", "until", "end"],
    "fitment_note": ["fitment", "fitment_note", "fitment note", "notes", "note", "comment"],
    "vehicles": ["vehicles", "vehicle", "vehicle_code", "model code"],
    "category": ["category", "category_code", "group", "main group", "mg"],
    "title": ["title", "diagram title", "diagram name"],
    "x": ["x", "left"], "y": ["y", "top"], "w": ["w", "width"], "h": ["h", "height"],
    "old": ["old", "old_pn", "old part number", "replaced", "superseded"],
    "new": ["new", "new_pn", "new part number", "replacement", "replaced by", "superseded by"],
    "date": ["date"],
    "code": ["code", "vehicle code"],
    "series": ["series", "chassis"],
    "name": ["name"],
    "parent": ["parent", "parent_code"],
}


def map_headers(headers, wanted):
    """Header text -> canonical field, for the fields in `wanted` (first alias match wins)."""
    norm = [re.sub(r"\s+", " ", h.strip().lower()) for h in headers]
    out = {}
    for field in wanted:
        for i, h in enumerate(norm):
            if i in out.values():
                continue
            if h in ALIASES.get(field, [field]):
                out[field] = i
                break
    return out


def csv_kind(headers):
    h = {re.sub(r"\s+", " ", x.strip().lower()) for x in headers}
    has = lambda f: bool(h & set(ALIASES[f]))  # noqa: E731
    if has("old") and has("new"):
        return "supersessions"
    if has("x") and has("y") and has("w") and has("h"):
        return "hotspots"
    if has("diagram") and (has("part_number") or has("callout")):
        return "items"
    if "series" in h and "code" in h:
        return "vehicles"
    if has("diagram") and (has("title") or has("category")):
        return "diagrams"
    if has("part_number") and has("description"):
        return "parts"
    if "code" in h and "name" in h:
        return "categories"
    return None


def import_csv(path, source_name=None, kind=None, vehicle=None, category=None, is_sample=False):
    """One CSV per kind; the kind is detected from its headers (items, hotspots, supersessions, parts,
    vehicles, categories, diagrams). `vehicle` links every diagram in an items/diagrams CSV to that vehicle."""
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|")
    except csv.Error:
        dialect = "excel"
    reader = list(csv.reader(io.StringIO(text), dialect=dialect))
    if not reader:
        raise ValueError("Empty CSV")
    headers, data = reader[0], [r for r in reader[1:] if any(c.strip() for c in r)]
    kind = kind or csv_kind(headers)
    if not kind:
        raise ValueError(f"Can't tell what this CSV holds from its headers: {headers}")
    name = source_name or os.path.basename(path)
    sid = get_source(name, "csv", is_sample)
    rep = Report(name)
    rep.count("kind_" + kind)
    fid = store_file(sid, path, "csv", data=raw)
    m = map_headers(headers, list(ALIASES))
    val = lambda r, k: r[m[k]] if k in m and m[k] < len(r) else None  # noqa: E731
    conn = epc.connect()
    if kind == "items":
        by_diag = {}
        for r in data:
            code = clean(val(r, "diagram"))
            if not code:
                rep.problem(f"Row without diagram code skipped: {r}")
                continue
            d = by_diag.setdefault(code, {"items": [], "category": clean(val(r, "category")) or category, "title": clean(val(r, "title"))})
            vs = [v for v in re.split(r"[;,]", clean(val(r, "vehicles"))) if v.strip()] if "vehicles" in m else []
            d["items"].append({k: val(r, k) for k in ("callout", "part_number", "description", "supplement", "qty", "from", "to", "fitment_note")}
                              | {"vehicles": vs})
        for code, d in by_diag.items():
            did = upsert_diagram(code, sid, rep, d["title"], d["category"], [vehicle] if vehicle else [])
            replace_items(did, d["items"], sid, rep, fid)
    elif kind == "hotspots":
        by_diag = {}
        for r in data:
            by_diag.setdefault(clean(val(r, "diagram")), []).append({k: val(r, k) for k in ("callout", "x", "y", "w", "h")})
        for code, spots in by_diag.items():
            vid = vehicle_id(vehicle) if vehicle else None
            did = find_diagram(clean(code), sid, [vid] if vid else [])
            if not did:
                rep.problem(f"Hotspots for unknown diagram {code} skipped")
                continue
            replace_hotspots(did, spots, sid, rep)
    elif kind == "supersessions":
        for r in data:
            add_supersession(val(r, "old"), val(r, "new"), sid, rep, val(r, "date"), val(r, "fitment_note") or "")
    elif kind == "parts":
        for r in data:
            upsert_part(val(r, "part_number"), val(r, "description"), val(r, "fitment_note"), sid, rep)
    elif kind == "vehicles":
        for r in data:
            upsert_vehicle({h.strip().lower(): r[i] for i, h in enumerate(headers) if i < len(r)}, sid, rep)
    elif kind == "categories":
        for r in data:
            get_category(val(r, "code"), val(r, "name"), clean(val(r, "parent")) or None, None, sid)
            rep.count("categories")
    elif kind == "diagrams":
        for r in data:
            upsert_diagram(val(r, "diagram"), sid, rep, val(r, "title"), clean(val(r, "category")) or category, [vehicle] if vehicle else [])
    conn.commit()
    return rep


# ---------- saved HTML pages ----------

class _PageParser(HTMLParser):
    """Collects tables (rows of cell text), <img> tags and <area> image-map regions from any HTML page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._stack = [], []
        self.imgs, self.areas, self.title = [], [], ""
        self._cell, self._row, self._in_title = None, None, False
        self.maps, self._map = {}, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")
        elif tag == "img":
            self.imgs.append(a)
        elif tag == "map":
            self._map = a.get("name") or a.get("id") or ""
            self.maps[self._map] = []
        elif tag == "area":
            area = {**a, "_map": self._map}
            self.areas.append(area)
            if self._map is not None:
                self.maps[self._map].append(area)
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._stack:
            if self._row:
                self._stack[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())
        elif tag == "map":
            self._map = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._in_title:
            self.title += data


PN_CELL = re.compile(r"^\d{2}\s?\d{2}\s?\d\s?\d{3}\s?\d{3}$")


def find_parts_table(tables):
    """The table that looks most like a parts list: a part-number header, or a column of 11-digit numbers."""
    best, best_score = None, 0
    for t in tables:
        for hi in range(min(3, len(t))):
            head, body = t[hi], t[hi + 1:]
            m = map_headers(head, ["callout", "part_number", "description", "supplement", "qty", "from", "to", "fitment_note"])
            pn_col = m.get("part_number")
            hits = 0
            for col in ([pn_col] if pn_col is not None else range(max((len(r) for r in body), default=0))):
                h = sum(1 for r in body if col < len(r) and PN_CELL.match(r[col]))
                if h > hits:
                    hits, pn_col = h, col
            score = hits * 2 + len(m)
            if hits and score > best_score:
                m["part_number"] = pn_col
                best, best_score = (m, body), score
    return best


def import_html(path, source_name=None, diagram=None, category=None, vehicle=None, image=None, title=None, is_sample=False):
    """A locally saved web page holding one diagram: parts table, diagram image, optional <map>/<area> hotspots.
    Generic: any page with a parts table works. Only local files are read; remote image URLs are ignored."""
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    p = _PageParser()
    p.feed(text)
    name = source_name or "Saved HTML pages"
    sid = get_source(name, "html", is_sample)
    rep = Report(name)
    fid = store_file(sid, path, "html", data=raw)
    rep.count("html_files")
    code = clean(diagram) or slug(os.path.splitext(os.path.basename(path))[0])
    page_title = clean(title) or clean(p.title)
    found = find_parts_table(p.tables)
    items = []
    if found:
        m, body = found
        for r in body:
            it = {k: (r[i] if i < len(r) else "") for k, i in m.items()}
            if not epc.norm_pn(it.get("part_number")) and not it.get("description"):
                continue
            items.append(it)
    else:
        rep.problem(f"{os.path.basename(path)}: no parts table found")
    # diagram image: explicit, else the largest local <img> the page references
    img_path = image
    usemap = None
    if not img_path:
        base = os.path.dirname(os.path.abspath(path))
        cands = []
        for a in p.imgs:
            src = html.unescape(a.get("src") or "")
            if not src or re.match(r"^(https?:|data:|//)", src, re.I):
                continue
            fp = os.path.normpath(os.path.join(base, src.split("?")[0].replace("/", os.sep)))
            if os.path.exists(fp) and os.path.splitext(fp)[1].lower() in IMAGE_EXT:
                cands.append((os.path.getsize(fp), fp, a.get("usemap")))
        if cands:
            _, img_path, usemap = max(cands)
    img_fid = store_file(sid, img_path, "image") if img_path else None
    if not img_fid:
        rep.problem(f"{os.path.basename(path)}: no local diagram image found (diagram left without image)")
    did = upsert_diagram(code, sid, rep, page_title, category, [vehicle] if vehicle else [], image_file_id=img_fid)
    replace_items(did, items, sid, rep, fid)
    # image-map hotspots (pixel coordinates of the image)
    areas = p.maps.get((usemap or "").lstrip("#"), []) if usemap else p.areas
    spots = []
    callouts = {it.get("callout") for it in items}
    for a in areas:
        coords = [c for c in re.split(r"[,\s]+", a.get("coords") or "") if c]
        shape = (a.get("shape") or "rect").lower()
        label = clean(a.get("alt") or a.get("title") or "")
        if not label:
            m2 = re.search(r"(?:#|=)(\w+)$", a.get("href") or "")
            label = m2.group(1) if m2 else ""
        try:
            nums = [float(c) for c in coords]
        except ValueError:
            continue
        if shape.startswith("rect") and len(nums) == 4:
            x1, y1, x2, y2 = nums
        elif shape.startswith("circ") and len(nums) == 3:
            x1, y1, x2, y2 = nums[0] - nums[2], nums[1] - nums[2], nums[0] + nums[2], nums[1] + nums[2]
        elif shape.startswith("poly") and len(nums) >= 6:
            xs, ys = nums[0::2], nums[1::2]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        else:
            continue
        if label:
            spots.append({"callout": label, "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1})
    if spots:
        replace_hotspots(did, spots, sid, rep, "px")
        unmatched = sorted({s["callout"] for s in spots} - callouts)
        if unmatched:
            rep.problem(f"{os.path.basename(path)}: hotspot labels not in the parts table: {', '.join(unmatched[:20])}")
    epc.connect().commit()
    return rep


# ---------- local diagram images ----------

def _code_key(s):
    return re.sub(r"[^0-9a-z]", "", s.lower())


def import_images(paths, source_name=None, create=False, category=None, vehicle=None, is_sample=False):
    """Attach image files to diagrams whose code matches the file name (11_0050.png -> diagram 11_0050).
    Unmatched files are reported, or with create=True become new diagrams (no parts list yet)."""
    name = source_name or "Diagram images"
    sid = get_source(name, "images", is_sample)
    rep = Report(name)
    files = []
    for p in paths:
        if os.path.isdir(p):
            for dp, _, fns in os.walk(p):
                files += [os.path.join(dp, fn) for fn in sorted(fns)]
        else:
            files.append(p)
    conn = epc.connect()
    codes = {_code_key(r[1]): r[0] for r in conn.execute("SELECT id, code FROM diagrams")}
    for fp in files:
        stem, ext = os.path.splitext(os.path.basename(fp))
        if ext.lower() not in IMAGE_EXT:
            continue
        did = codes.get(_code_key(stem))
        if not did:
            if not create:
                rep.problem(f"{os.path.basename(fp)}: no diagram with code {stem}")
                rep.count("images_unmatched")
                continue
            did = upsert_diagram(stem, sid, rep, "", category, [vehicle] if vehicle else [])
            codes[_code_key(stem)] = did
        attach_image(did, store_file(sid, fp, "image"))
        rep.count("images_attached")
    conn.commit()
    return rep


def import_path(path, **kw):
    """Pick the importer from the file extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return import_bundle(path, kw.get("source_name"), is_sample=kw.get("is_sample") or None)
    if ext in (".csv", ".tsv", ".txt"):
        return import_csv(path, kw.get("source_name"), vehicle=kw.get("vehicle"), category=kw.get("category"), is_sample=kw.get("is_sample", False))
    if ext in (".html", ".htm", ".xhtml", ".mhtml"):
        return import_html(path, kw.get("source_name"), kw.get("diagram"), kw.get("category"), kw.get("vehicle"), kw.get("image"), kw.get("title"), kw.get("is_sample", False))
    if ext in IMAGE_EXT or os.path.isdir(path):
        return import_images([path], kw.get("source_name"), kw.get("create", False), kw.get("category"), kw.get("vehicle"), kw.get("is_sample", False))
    raise ValueError(f"Don't know how to import {path}")


SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "epc_sample")
REFERENCE_DIR = os.path.join(os.path.dirname(__file__), "epc_reference")


def load_reference():
    """Reference data shipped with the app, e.g. BMW main group numbers (not sample data)."""
    reps = []
    for fn in sorted(os.listdir(REFERENCE_DIR)):
        if fn.endswith(".json"):
            reps.append(import_bundle(os.path.join(REFERENCE_DIR, fn)))
    return reps


def load_sample():
    return import_bundle(os.path.join(SAMPLE_DIR, "sample.json"), is_sample=True)
