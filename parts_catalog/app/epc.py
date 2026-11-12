"""Offline parts catalog (EPC): its own portable SQLite file at data/epc/epc.db, files under data/epc/files/.

Kept separate from garage.db so the catalog folder can be copied, shared or rebuilt on its own.
Data comes only from imports (JSON bundle, CSV, saved HTML pages, local images); nothing here fetches from the web.

Model (works for any BMW model, or any make):
  sources          where each record came from; is_sample=1 marks sample data
  source_files     original files (HTML, CSV, JSON, images) copied in, kept for reprocessing
  vehicles         one catalog variant, e.g. one model for one market
  categories       main groups ("11 Engine") and optional subgroups (parent_id)
  diagrams         one illustration; code is unique (e.g. "11_0050"); image is a stored file
  diagram_vehicles which vehicles a diagram applies to
  diagram_items    one row of a diagram's parts list: callout, OEM part number, description as printed,
                   supplement / fitment note, quantity (original text kept), production date range
  item_vehicles    optional: restricts an item to some of the diagram's vehicles
  parts            one record per part number (description, notes)
  hotspots         clickable callout boxes on a diagram, as fractions (0-1) of image width/height
  supersessions    old part number replaced by new part number
  expected_diagrams diagram codes known to exist for a vehicle, so missing ones can be reported
Item part numbers deliberately have no foreign key to parts: validation reports the gaps instead of import failing.
"""
import os
import re
import sqlite3
import threading

from . import db

EPC_DIR = os.path.join(db.DATA_DIR, "epc")
EPC_DB = os.path.join(EPC_DIR, "epc.db")
FILES_DIR = os.path.join(EPC_DIR, "files")
SCHEMA_VERSION = 2

# Diagram codes (e.g. "11_0123") repeat between vehicles, so they aren't unique on their own (schema v2).
DIAGRAMS_COLS = """
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL,
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
  title TEXT DEFAULT '',
  image_file_id INTEGER REFERENCES source_files(id) ON DELETE SET NULL,
  image_w INTEGER, image_h INTEGER,
  notes TEXT DEFAULT '',
  sort INTEGER DEFAULT 0,
  source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT DEFAULT '',            -- json / csv / html / images / manual / reference / sample
  is_sample INTEGER DEFAULT 0,
  note TEXT DEFAULT '',
  imported_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS source_files (
  id INTEGER PRIMARY KEY,
  source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,              -- html / csv / json / image
  original_name TEXT,
  stored_path TEXT NOT NULL,       -- relative to the epc folder
  sha256 TEXT,
  size INTEGER,
  imported_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS vehicles (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  make TEXT DEFAULT 'BMW',
  series TEXT DEFAULT '',          -- chassis, e.g. DEMO
  model TEXT DEFAULT '',           -- e.g. Sedan
  body TEXT DEFAULT '',
  market TEXT DEFAULT '',
  engine TEXT DEFAULT '',
  transmission TEXT DEFAULT '',
  steering TEXT DEFAULT '',
  prod_from TEXT,                  -- YYYYMM
  prod_to TEXT,
  notes TEXT DEFAULT '',
  source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,       -- "11", or "11.12" for a subgroup
  name TEXT NOT NULL,
  parent_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
  sort INTEGER DEFAULT 0,
  source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS diagrams ({DIAGRAMS_COLS});
CREATE TABLE IF NOT EXISTS diagram_vehicles (
  diagram_id INTEGER NOT NULL REFERENCES diagrams(id) ON DELETE CASCADE,
  vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
  PRIMARY KEY (diagram_id, vehicle_id)
);
CREATE TABLE IF NOT EXISTS parts (
  part_number TEXT PRIMARY KEY,    -- normalized: no spaces (BMW: 11 digits)
  description TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS diagram_items (
  id INTEGER PRIMARY KEY,
  diagram_id INTEGER NOT NULL REFERENCES diagrams(id) ON DELETE CASCADE,
  callout TEXT DEFAULT '',
  part_number TEXT DEFAULT '',     -- normalized; '' when the row has none (e.g. "not available")
  part_number_raw TEXT DEFAULT '', -- exactly as in the source
  description TEXT DEFAULT '',
  supplement TEXT DEFAULT '',      -- e.g. "ENGINE-A", "For vehicles with A/C"
  qty TEXT,                        -- original text ("1", "6", "X"); NULL = not given in the source
  qty_num REAL,
  from_date TEXT, to_date TEXT,    -- YYYYMM production range
  fitment_note TEXT DEFAULT '',
  sort INTEGER DEFAULT 0,
  source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
  source_file_id INTEGER REFERENCES source_files(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS item_vehicles (
  item_id INTEGER NOT NULL REFERENCES diagram_items(id) ON DELETE CASCADE,
  vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
  PRIMARY KEY (item_id, vehicle_id)
);
CREATE TABLE IF NOT EXISTS hotspots (
  id INTEGER PRIMARY KEY,
  diagram_id INTEGER NOT NULL REFERENCES diagrams(id) ON DELETE CASCADE,
  callout TEXT NOT NULL,
  x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
  source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS supersessions (
  old_pn TEXT NOT NULL,
  new_pn TEXT NOT NULL,
  date TEXT, note TEXT DEFAULT '',
  source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
  PRIMARY KEY (old_pn, new_pn)
);
CREATE TABLE IF NOT EXISTS expected_diagrams (
  vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
  code TEXT NOT NULL,
  title TEXT DEFAULT '',
  source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
  PRIMARY KEY (vehicle_id, code)
);
CREATE INDEX IF NOT EXISTS ix_diagrams_cat ON diagrams(category_id);
CREATE INDEX IF NOT EXISTS ix_diagrams_code ON diagrams(code);
CREATE INDEX IF NOT EXISTS ix_items_diagram ON diagram_items(diagram_id);
CREATE INDEX IF NOT EXISTS ix_items_pn ON diagram_items(part_number);
CREATE INDEX IF NOT EXISTS ix_items_source ON diagram_items(source_id);
CREATE INDEX IF NOT EXISTS ix_hotspots_diagram ON hotspots(diagram_id);
CREATE INDEX IF NOT EXISTS ix_dv_vehicle ON diagram_vehicles(vehicle_id);
CREATE INDEX IF NOT EXISTS ix_sup_new ON supersessions(new_pn);
CREATE INDEX IF NOT EXISTS ix_categories_parent ON categories(parent_id);
"""

SCHEMA = SCHEMA.replace("{DIAGRAMS_COLS}", DIAGRAMS_COLS)

_local = threading.local()


def connect():
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(FILES_DIR, exist_ok=True)
        conn = sqlite3.connect(EPC_DB, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        _local.conn = conn
    return conn


def close():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def init():
    conn = connect()
    conn.executescript(SCHEMA)
    drop_unique_diagram_code(conn)
    conn.executescript(SCHEMA)  # recreates indexes dropped with the old table
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()


def drop_unique_diagram_code(conn):
    """v1 -> v2: rebuild `diagrams` without UNIQUE(code). Ids are kept, so nothing that points at a diagram changes."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='diagrams'").fetchone()
    if not sql or "UNIQUE" not in sql[0].upper():
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(f"CREATE TABLE diagrams_v2 ({DIAGRAMS_COLS})")
        conn.execute("""INSERT INTO diagrams_v2 (id, code, category_id, title, image_file_id, image_w, image_h, notes, sort, source_id)
                        SELECT id, code, category_id, title, image_file_id, image_w, image_h, notes, sort, source_id FROM diagrams""")
        conn.execute("DROP TABLE diagrams")
        conn.execute("ALTER TABLE diagrams_v2 RENAME TO diagrams")
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError(f"catalog migration left broken references: {bad[:5]}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def rows(sql, args=()):
    return [dict(r) for r in connect().execute(sql, args).fetchall()]


def row(sql, args=()):
    r = connect().execute(sql, args).fetchone()
    return dict(r) if r else None


def file_path(stored_path):
    return os.path.join(EPC_DIR, stored_path)


# ---------- part numbers ----------

PN_RE = re.compile(r"^\d{11}$")


def norm_pn(v):
    """'11 53 1 740 996' / '11-53-1-740-996' -> '11531740996'. Non-BMW numbers keep letters, lose separators."""
    return re.sub(r"[\s\-./]", "", str(v or "")).upper()


def looks_like_pn(v):
    """Normalised, 3+ letters/digits with at least one digit."""
    pn = norm_pn(v)
    return bool(re.match(r"^[0-9A-Z]{3,}$", pn)) and any(ch.isdigit() for ch in pn)


def fmt_pn(pn):
    """BMW 11-digit numbers display as 11 53 1 740 996."""
    if pn and PN_RE.match(pn):
        return f"{pn[0:2]} {pn[2:4]} {pn[4]} {pn[5:8]} {pn[8:11]}"
    return pn or ""


def supersession_chain(pn):
    """Part numbers that replaced pn (newest last) and that pn replaced."""
    newer, seen, cur = [], {pn}, pn
    while True:
        r = row("SELECT new_pn, date, note FROM supersessions WHERE old_pn=? ORDER BY date DESC LIMIT 1", (cur,))
        if not r or r["new_pn"] in seen:
            break
        newer.append(r)
        seen.add(r["new_pn"])
        cur = r["new_pn"]
    older = rows("SELECT old_pn, date, note FROM supersessions WHERE new_pn=?", (pn,))
    return newer, older


# ---------- queries for the UI ----------

def vehicle_codes(diagram_id):
    return [r["code"] for r in rows("""SELECT v.code FROM vehicles v JOIN diagram_vehicles dv ON dv.vehicle_id=v.id
                                       WHERE dv.diagram_id=? ORDER BY v.code""", (diagram_id,))]


def vehicles():
    return rows("""SELECT v.*, s.name AS source_name, s.is_sample,
                   (SELECT COUNT(*) FROM diagram_vehicles dv WHERE dv.vehicle_id=v.id) AS diagrams
                   FROM vehicles v LEFT JOIN sources s ON s.id=v.source_id ORDER BY v.make, v.series, v.model, v.code""")


def category_tree(vehicle_id=None):
    """Main groups with subgroups and diagram counts (for a vehicle, if given)."""
    cats = rows("SELECT * FROM categories ORDER BY sort, code")
    if vehicle_id:
        counts = dict(connect().execute("""SELECT d.category_id, COUNT(*) FROM diagrams d
            JOIN diagram_vehicles dv ON dv.diagram_id=d.id AND dv.vehicle_id=? GROUP BY d.category_id""", (vehicle_id,)).fetchall())
    else:
        counts = dict(connect().execute("SELECT category_id, COUNT(*) FROM diagrams GROUP BY category_id").fetchall())
    by_id = {c["id"]: {**c, "diagrams": counts.get(c["id"], 0), "children": []} for c in cats}
    roots = []
    for c in by_id.values():
        (by_id[c["parent_id"]]["children"] if c["parent_id"] in by_id else roots).append(c)
    for c in by_id.values():
        c["total"] = c["diagrams"] + sum(ch["diagrams"] for ch in c["children"])
    return roots


def diagrams_in(category_id=None, vehicle_id=None):
    sql = """SELECT d.id, d.code, d.title, d.category_id, d.image_file_id IS NOT NULL AS has_image, s.is_sample,
             (SELECT stored_path FROM source_files f WHERE f.id=d.image_file_id) AS image_path,
             (SELECT COUNT(*) FROM diagram_items i WHERE i.diagram_id=d.id) AS items,
             (SELECT COUNT(*) FROM hotspots h WHERE h.diagram_id=d.id) AS hotspots
             FROM diagrams d LEFT JOIN sources s ON s.id=d.source_id WHERE 1=1"""
    args = []
    if category_id:
        sql += " AND (d.category_id=? OR d.category_id IN (SELECT id FROM categories WHERE parent_id=?))"
        args += [category_id, category_id]
    if vehicle_id:
        sql += " AND d.id IN (SELECT diagram_id FROM diagram_vehicles WHERE vehicle_id=?)"
        args.append(vehicle_id)
    out = rows(sql + " ORDER BY d.sort, d.code", args)
    for r in out:
        r["vehicles"] = vehicle_codes(r["id"])
    return out


def item_fits(item, vehicle):
    """Production-date and vehicle restrictions. Unknown vehicle dates never exclude an item."""
    if not vehicle:
        return True
    if item.get("_vehicles") and vehicle["id"] not in item["_vehicles"]:
        return False
    vf, vt = vehicle.get("prod_from"), vehicle.get("prod_to")
    if item.get("to_date") and vf and item["to_date"] < vf:
        return False
    if item.get("from_date") and vt and item["from_date"] > vt:
        return False
    return True


def diagram_detail(diagram_id, vehicle_id=None):
    d = row("""SELECT d.*, c.code AS category_code, c.name AS category_name, s.name AS source_name, s.is_sample,
               f.stored_path AS image_path, f.original_name AS image_name
               FROM diagrams d LEFT JOIN categories c ON c.id=d.category_id LEFT JOIN sources s ON s.id=d.source_id
               LEFT JOIN source_files f ON f.id=d.image_file_id WHERE d.id=?""", (diagram_id,))
    if not d:
        return None
    vehicle = row("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)) if vehicle_id else None
    items = rows("""SELECT i.*, p.description AS part_description, p.notes AS part_notes, sf.original_name AS source_file
                    FROM diagram_items i LEFT JOIN parts p ON p.part_number=i.part_number
                    LEFT JOIN source_files sf ON sf.id=i.source_file_id
                    WHERE i.diagram_id=? ORDER BY i.sort, i.id""", (diagram_id,))
    restr = {}
    for r in connect().execute("""SELECT iv.item_id, iv.vehicle_id FROM item_vehicles iv JOIN diagram_items i ON i.id=iv.item_id
                                  WHERE i.diagram_id=?""", (diagram_id,)):
        restr.setdefault(r[0], set()).add(r[1])
    for it in items:
        it["_vehicles"] = restr.get(it["id"])
        it["fits"] = item_fits(it, vehicle)
        it["vehicles"] = sorted(it.pop("_vehicles") or [])
        it["part_number_fmt"] = fmt_pn(it["part_number"])
        it["has_part_record"] = it["part_description"] is not None
        newer, _ = supersession_chain(it["part_number"]) if it["part_number"] else ([], [])
        it["superseded_by"] = newer[-1]["new_pn"] if newer else None
        it["superseded_by_fmt"] = fmt_pn(it["superseded_by"])
    d["items"] = items
    d["hotspots"] = rows("SELECT callout, x, y, w, h FROM hotspots WHERE diagram_id=? ORDER BY id", (diagram_id,))
    d["vehicles"] = rows("""SELECT v.id, v.code FROM vehicles v JOIN diagram_vehicles dv ON dv.vehicle_id=v.id
                            WHERE dv.diagram_id=? ORDER BY v.code""", (diagram_id,))
    d["image_exists"] = bool(d["image_path"]) and os.path.exists(file_path(d["image_path"]))
    return d


def like(w):
    """A LIKE pattern (ESCAPE '\\') that finds w anywhere, its own % and _ taken literally."""
    return "%" + re.sub(r"([\\%_])", r"\\\1", w) + "%"


def search(q, vehicle_id=None, limit=200):
    """Part numbers (any formatting, incl. superseded ones) first, then diagrams whose title or code holds every word (item fields None), then items."""
    q = (q or "").strip()
    if not q:
        return []
    # a number typed with spaces ("11 53 1 740 996") is one token
    tokens = [q] if re.match(r"^[\d\s\-./]+$", q) else q.split()
    in_vehicle, vargs = (" AND d.id IN (SELECT diagram_id FROM diagram_vehicles WHERE vehicle_id=?)", [vehicle_id]) if vehicle_id else ("", [])
    items = """SELECT i.id, i.callout, i.part_number, i.description, i.supplement, i.qty, i.from_date, i.to_date,
               d.id AS diagram_id, d.code AS diagram_code, d.title AS diagram_title, c.code AS category_code, s.is_sample
               FROM diagram_items i JOIN diagrams d ON d.id=i.diagram_id LEFT JOIN categories c ON c.id=d.category_id
               LEFT JOIN parts p ON p.part_number=i.part_number LEFT JOIN sources s ON s.id=i.source_id WHERE """
    out = []
    where, args = [], []
    for pn in (norm_pn(t) for t in tokens if looks_like_pn(t)):
        alt = [r["old_pn"] for r in rows("SELECT old_pn FROM supersessions WHERE new_pn LIKE ?", (f"%{pn}%",))]
        alt += [r["new_pn"] for r in rows("SELECT new_pn FROM supersessions WHERE old_pn LIKE ?", (f"%{pn}%",))]
        where.append("i.part_number LIKE ?" + "".join(" OR i.part_number=?" for _ in alt))
        args += [f"%{pn}%"] + alt
    if where:
        out += rows(items + f"({' OR '.join(where)}){in_vehicle} ORDER BY i.part_number, d.code LIMIT ?", args + vargs + [limit])
    item = "(i.description LIKE ? ESCAPE '\\' OR i.supplement LIKE ? ESCAPE '\\' OR p.description LIKE ? ESCAPE '\\')"
    diagram = "(d.title LIKE ? ESCAPE '\\' OR d.code LIKE ? ESCAPE '\\')"
    pats = [like(t) for t in tokens]
    out += rows(f"""SELECT NULL AS id, NULL AS callout, NULL AS part_number, NULL AS description, NULL AS supplement, NULL AS qty,
                    NULL AS from_date, NULL AS to_date, d.id AS diagram_id, d.code AS diagram_code, d.title AS diagram_title,
                    c.code AS category_code, s.is_sample FROM diagrams d LEFT JOIN categories c ON c.id=d.category_id
                    LEFT JOIN sources s ON s.id=d.source_id WHERE {' AND '.join([diagram] * len(pats))}{in_vehicle} ORDER BY d.code LIMIT ?""",
                [a for w in pats for a in [w] * 2] + vargs + [limit])
    # every word somewhere in the row, at least one in the item itself: a word only in the title already gave the diagram's row
    seen = {r["id"] for r in out}
    out += [r for r in rows(items + " AND ".join([f"({item} OR {diagram})"] * len(pats)) + f" AND ({' OR '.join([item] * len(pats))})"
                            + f"{in_vehicle} ORDER BY i.part_number, d.code LIMIT ?",
                            [a for w in pats for a in [w] * 5] + [a for w in pats for a in [w] * 3] + vargs + [limit])
            if r["id"] not in seen]
    out = out[:limit]
    for r in out:
        r["part_number_fmt"] = fmt_pn(r["part_number"])
        r["vehicles"] = vehicle_codes(r["diagram_id"])
    return out


def part_detail(pn):
    pn = norm_pn(pn)
    p = row("SELECT p.*, s.name AS source_name, s.is_sample FROM parts p LEFT JOIN sources s ON s.id=p.source_id WHERE part_number=?", (pn,))
    used = rows("""SELECT i.callout, i.description, i.supplement, i.qty, i.from_date, i.to_date, d.id AS diagram_id,
                   d.code AS diagram_code, d.title AS diagram_title FROM diagram_items i JOIN diagrams d ON d.id=i.diagram_id
                   WHERE i.part_number=? ORDER BY d.code""", (pn,))
    for r in used:
        r["vehicles"] = vehicle_codes(r["diagram_id"])
    newer, older = supersession_chain(pn)
    for r in newer:
        r["new_pn_fmt"] = fmt_pn(r["new_pn"])
    for r in older:
        r["old_pn_fmt"] = fmt_pn(r["old_pn"])
    return {"part_number": pn, "part_number_fmt": fmt_pn(pn), "record": p, "used_in": used,
            "superseded_by": newer, "replaces": older}


def stats():
    c = connect()
    one = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "vehicles": one("SELECT COUNT(*) FROM vehicles"),
        "categories": one("SELECT COUNT(*) FROM categories"),
        "diagrams": one("SELECT COUNT(*) FROM diagrams"),
        "diagrams_with_image": one("SELECT COUNT(*) FROM diagrams WHERE image_file_id IS NOT NULL"),
        "diagrams_with_hotspots": one("SELECT COUNT(DISTINCT diagram_id) FROM hotspots"),
        "items": one("SELECT COUNT(*) FROM diagram_items"),
        "parts": one("SELECT COUNT(*) FROM parts"),
        "supersessions": one("SELECT COUNT(*) FROM supersessions"),
        "source_files": one("SELECT COUNT(*) FROM source_files"),
        "sample_records": one("""SELECT (SELECT COUNT(*) FROM diagrams d JOIN sources s ON s.id=d.source_id AND s.is_sample=1)
                                 + (SELECT COUNT(*) FROM diagram_items i JOIN sources s ON s.id=i.source_id AND s.is_sample=1)"""),
    }


def sources():
    return rows("""SELECT s.*,
        (SELECT COUNT(*) FROM diagrams WHERE source_id=s.id) AS diagrams,
        (SELECT COUNT(*) FROM diagram_items WHERE source_id=s.id) AS items,
        (SELECT COUNT(*) FROM source_files WHERE source_id=s.id) AS files
        FROM sources s ORDER BY s.imported_at, s.id""")


def delete_source(source_id):
    """Remove a source and everything it imported (diagrams, items, hotspots, files)."""
    conn = connect()
    paths = [r[0] for r in conn.execute("SELECT stored_path FROM source_files WHERE source_id=?", (source_id,))]
    conn.execute("DELETE FROM parts WHERE source_id=? AND part_number NOT IN (SELECT part_number FROM diagram_items WHERE source_id IS NOT ?)",
                 (source_id, source_id))
    conn.execute("DELETE FROM vehicles WHERE source_id=? AND id NOT IN (SELECT vehicle_id FROM diagram_vehicles dv JOIN diagrams d ON d.id=dv.diagram_id WHERE d.source_id IS NOT ?)",
                 (source_id, source_id))
    conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
    conn.commit()
    for p in paths:
        try:
            os.remove(file_path(p))
        except OSError:
            pass


def delete_vehicle(vehicle_id):
    """Remove a vehicle and its links (diagram/item links, expected diagrams). Diagrams stay; a diagram left
    with no vehicle and no items from other sources goes too."""
    conn = connect()
    dids = [r[0] for r in conn.execute("SELECT diagram_id FROM diagram_vehicles WHERE vehicle_id=?", (vehicle_id,))]
    conn.execute("DELETE FROM vehicles WHERE id=?", (vehicle_id,))
    for did in dids:
        conn.execute("""DELETE FROM diagrams WHERE id=? AND NOT EXISTS (SELECT 1 FROM diagram_vehicles WHERE diagram_id=?)
                        AND NOT EXISTS (SELECT 1 FROM diagram_items WHERE diagram_id=?)""", (did, did, did))
    conn.commit()


# ---------- validation report ----------

def validate():
    """Import validation report: list of {severity, check, message, refs}. Severities: error / warning / info."""
    c = connect()
    out = []

    def add(sev, check, message, refs=None):
        out.append({"severity": sev, "check": check, "message": message, "refs": refs or []})

    q = lambda sql, a=(): [dict(r) for r in c.execute(sql, a).fetchall()]  # noqa: E731

    # missing diagrams
    for r in q("""SELECT v.code AS vehicle, e.code, e.title FROM expected_diagrams e JOIN vehicles v ON v.id=e.vehicle_id
                  WHERE NOT EXISTS (SELECT 1 FROM diagrams d JOIN diagram_vehicles dv ON dv.diagram_id=d.id
                                    WHERE d.code=e.code AND dv.vehicle_id=e.vehicle_id) ORDER BY v.code, e.code"""):
        add("error", "missing_diagram", f"{r['vehicle']}: expected diagram {r['code']} {r['title'] or ''} has not been imported".strip(),
            [{"diagram_code": r["code"]}])
    for v in q("""SELECT code FROM vehicles WHERE id NOT IN (SELECT vehicle_id FROM diagram_vehicles)"""):
        add("warning", "vehicle_without_diagrams", f"Vehicle {v['code']} has no diagrams")
    for d in q("SELECT id, code FROM diagrams WHERE image_file_id IS NULL ORDER BY code"):
        add("warning", "missing_image", f"Diagram {d['code']} has no image", [{"diagram_id": d["id"]}])
    for d in q("""SELECT d.id, d.code, f.stored_path FROM diagrams d JOIN source_files f ON f.id=d.image_file_id ORDER BY d.code"""):
        if not os.path.exists(file_path(d["stored_path"])):
            add("error", "image_file_missing", f"Diagram {d['code']}: image file {d['stored_path']} is missing on disk", [{"diagram_id": d["id"]}])
    for d in q("SELECT id, code FROM diagrams WHERE id NOT IN (SELECT diagram_id FROM diagram_items) ORDER BY code"):
        add("warning", "diagram_without_items", f"Diagram {d['code']} has no parts list", [{"diagram_id": d["id"]}])
    for d in q("SELECT id, code FROM diagrams WHERE category_id IS NULL ORDER BY code"):
        add("warning", "diagram_without_category", f"Diagram {d['code']} has no category", [{"diagram_id": d["id"]}])
    for d in q("SELECT id, code FROM diagrams WHERE id NOT IN (SELECT diagram_id FROM diagram_vehicles) ORDER BY code"):
        add("warning", "diagram_without_vehicle", f"Diagram {d['code']} isn't linked to any vehicle", [{"diagram_id": d["id"]}])

    # missing part records / bad part numbers
    for r in q("""SELECT i.part_number, COUNT(*) AS n, MIN(d.code) AS diagram, MIN(d.id) AS diagram_id FROM diagram_items i
                  JOIN diagrams d ON d.id=i.diagram_id WHERE i.part_number<>''
                  AND i.part_number NOT IN (SELECT part_number FROM parts) GROUP BY i.part_number ORDER BY i.part_number"""):
        add("warning", "missing_part_record", f"Part {fmt_pn(r['part_number'])} (used {r['n']}x, e.g. diagram {r['diagram']}) has no part record",
            [{"part_number": r["part_number"], "diagram_id": r["diagram_id"]}])
    for r in q("""SELECT i.id, i.callout, i.part_number_raw, d.code, d.id AS diagram_id FROM diagram_items i JOIN diagrams d ON d.id=i.diagram_id
                  WHERE i.part_number='' ORDER BY d.code"""):
        add("info", "item_without_part_number", f"Diagram {r['code']} callout {r['callout'] or '?'} has no part number"
            + (f" (source says: {r['part_number_raw']})" if r["part_number_raw"] else ""), [{"diagram_id": r["diagram_id"]}])
    for r in q("""SELECT DISTINCT i.part_number FROM diagram_items i JOIN vehicles v ON v.make='BMW'
                  JOIN diagram_vehicles dv ON dv.vehicle_id=v.id AND dv.diagram_id=i.diagram_id WHERE i.part_number<>''"""):
        if not PN_RE.match(r["part_number"]):
            add("warning", "malformed_part_number", f"Part number {r['part_number']} is not 11 digits", [{"part_number": r["part_number"]}])
    n = c.execute("SELECT COUNT(*) FROM diagram_items WHERE qty IS NULL AND part_number<>''").fetchone()[0]
    if n:
        add("info", "missing_quantity", f"{n} items have no quantity in their source")
    n = c.execute("SELECT COUNT(*) FROM diagram_items WHERE description='' AND part_number<>''").fetchone()[0]
    if n:
        add("info", "missing_description", f"{n} items have no description in their source")

    # duplicates
    for r in q("""SELECT d.code, d.id AS diagram_id, i.callout, i.part_number, i.supplement, COUNT(*) AS n
                  FROM diagram_items i JOIN diagrams d ON d.id=i.diagram_id
                  GROUP BY i.diagram_id, i.callout, i.part_number, i.supplement, IFNULL(i.from_date,''), IFNULL(i.to_date,'')
                  HAVING n > 1 ORDER BY d.code"""):
        add("warning", "duplicate_item", f"Diagram {r['code']} lists callout {r['callout']} / {fmt_pn(r['part_number'])} {r['n']} times",
            [{"diagram_id": r["diagram_id"]}])
    for r in q("""SELECT sha256, COUNT(*) AS n, GROUP_CONCAT(original_name, ', ') AS names FROM source_files
                  WHERE sha256 IS NOT NULL GROUP BY sha256 HAVING n > 1"""):
        add("info", "duplicate_file", f"Same file imported {r['n']} times: {r['names']}")

    # broken relationships
    for r in q("""SELECT d.code, d.id AS diagram_id, h.callout FROM hotspots h JOIN diagrams d ON d.id=h.diagram_id
                  WHERE NOT EXISTS (SELECT 1 FROM diagram_items i WHERE i.diagram_id=h.diagram_id AND i.callout=h.callout)
                  ORDER BY d.code"""):
        add("warning", "hotspot_without_item", f"Diagram {r['code']}: hotspot {r['callout']} matches no item", [{"diagram_id": r["diagram_id"]}])
    for r in q("""SELECT d.code, d.id AS diagram_id, GROUP_CONCAT(DISTINCT i.callout) AS callouts FROM diagram_items i
                  JOIN diagrams d ON d.id=i.diagram_id
                  WHERE i.callout<>'' AND i.diagram_id IN (SELECT diagram_id FROM hotspots)
                  AND NOT EXISTS (SELECT 1 FROM hotspots h WHERE h.diagram_id=i.diagram_id AND h.callout=i.callout)
                  GROUP BY d.id ORDER BY d.code"""):
        add("info", "item_without_hotspot", f"Diagram {r['code']}: callouts without a hotspot: {r['callouts']}", [{"diagram_id": r["diagram_id"]}])
    for r in q("""SELECT d.code, COUNT(*) AS n FROM hotspots h JOIN diagrams d ON d.id=h.diagram_id
                  WHERE h.x<0 OR h.y<0 OR h.w<=0 OR h.h<=0 OR h.x+h.w>1.001 OR h.y+h.h>1.001 GROUP BY d.id"""):
        add("error", "hotspot_out_of_range", f"Diagram {r['code']}: {r['n']} hotspots fall outside the image")
    for r in q("""SELECT old_pn, new_pn FROM supersessions WHERE new_pn NOT IN (SELECT part_number FROM parts)
                  AND new_pn NOT IN (SELECT part_number FROM diagram_items)"""):
        add("info", "supersession_target_unknown", f"{fmt_pn(r['old_pn'])} is replaced by {fmt_pn(r['new_pn'])}, which has no record")
    graph = {}
    for r in q("SELECT old_pn, new_pn FROM supersessions"):
        graph.setdefault(r["old_pn"], []).append(r["new_pn"])
    reported = set()
    for start in graph:
        stack, seen = [(start, [start])], set()
        while stack:
            node, path = stack.pop()
            for nxt in graph.get(node, []):
                if nxt == start and start not in reported:
                    reported.update(path)
                    add("error", "supersession_cycle", "Supersession loop: " + " → ".join(fmt_pn(p) for p in path + [start]))
                elif nxt not in seen:
                    seen.add(nxt)
                    stack.append((nxt, path + [nxt]))
    for r in q("SELECT id, kind, stored_path FROM source_files ORDER BY id"):
        if not os.path.exists(file_path(r["stored_path"])):
            add("error", "source_file_missing", f"Stored {r['kind']} file {r['stored_path']} is missing on disk")
    for r in q("""SELECT c.code FROM categories c WHERE c.parent_id IS NOT NULL AND c.parent_id NOT IN (SELECT id FROM categories)"""):
        add("error", "category_parent_missing", f"Category {r['code']} points to a missing parent")
    return out


def validation_summary(report):
    counts = {}
    for r in report:
        counts.setdefault(r["check"], {"severity": r["severity"], "count": 0})["count"] += 1
    return counts
