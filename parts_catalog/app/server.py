"""Flask app: JSON API under /api, the single-page UI, uploaded files, backup."""
import calendar
import datetime as dt
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile

from flask import Flask, jsonify, request, send_from_directory, send_file

from . import db
from .epc_api import bp as epc_bp

STATIC = os.path.join(db.ROOT, "static")
SETTINGS_PATH = os.path.join(db.DATA_DIR, "settings.json")
DEFAULT_SETTINGS = {"lan": False, "port": 8765}
RESTART_CODE = 3
# Set by the Home Assistant add-on (ha-addon/run.sh): no phone-access toggle, and optionally only
# accept requests from HA's ingress proxy.
ADDON = os.environ.get("PARTS_CATALOG_ADDON") == "1"
ALLOWED_CLIENTS = {a.strip() for a in os.environ.get("PARTS_CATALOG_ALLOW", "").split(",") if a.strip()}

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # service manuals are big
app.register_blueprint(epc_bp)

# Common service intervals offered as one-click presets (miles, months).
PRESETS = [
    ("Engine oil & filter", 5000, 12),
    ("Engine air filter", 15000, 24),
    ("Cabin / microfilter", 15000, 24),
    ("Fuel filter", 30000, 48),
    ("Spark plugs", 30000, 48),
    ("Coolant flush", 30000, 24),
    ("Brake fluid flush", None, 24),
    ("Valve adjustment", 15000, None),
    ("Manual transmission fluid", 60000, None),
    ("Automatic transmission fluid & filter", 60000, None),
    ("Differential fluid", 60000, None),
    ("Power steering fluid", 60000, 48),
    ("Brake pads (inspect)", 15000, 12),
    ("Tire rotation", 6000, 6),
    ("Wiper blades", None, 12),
    ("Inspection / registration", None, 12),
]


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return {**DEFAULT_SETTINGS, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)


def save_settings(s):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


def body():
    return request.get_json(silent=True) or {}


def err(msg, code=400):
    return jsonify(error=msg), code


def to_int(v):
    if v in (None, ""):
        return None
    try:
        return int(float(re.sub(r"[^\d.\-]", "", str(v))))
    except ValueError:
        return None


def to_money(v):
    if v in (None, ""):
        return None
    try:
        return round(float(re.sub(r"[^\d.\-]", "", str(v))), 2)
    except ValueError:
        return None


def today():
    return dt.date.today().isoformat()


def add_months(date_str, months):
    d = dt.date.fromisoformat(date_str[:10])
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1])).isoformat()


@app.before_request
def only_allowed_clients():
    if ALLOWED_CLIENTS and request.remote_addr not in ALLOWED_CLIENTS:
        return err("Open this through Home Assistant", 403)


@app.after_request
def no_cache(resp):
    if request.path.startswith("/api/") or request.path == "/":
        resp.headers["Cache-Control"] = "no-store"
    elif request.path.startswith("/static/"):
        # revalidate (cheap 304) so UI updates show up without a hard refresh
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.route("/static/<path:p>")
def static_files(p):
    return send_from_directory(STATIC, p)


@app.route("/api/ping")
def ping():
    return jsonify(ok=True, app="parts-catalog")


@app.route("/api/refdata")
def api_refdata():
    return jsonify(statuses=db.STATUSES, kinds=db.KINDS, doc_kinds=db.DOC_KINDS,
                   presets=[{"title": t, "interval_miles": m, "interval_months": mo} for t, m, mo in PRESETS])


# ---------- cars & mileage ----------

CAR_FIELDS = ["name", "kind", "year", "make", "model", "engine", "transmission", "vin", "plate", "notes"]


def car_values(b):
    vals = {k: (str(b[k]).strip() or None) if b.get(k) is not None else None for k in CAR_FIELDS if k in b}
    if "year" in vals:
        vals["year"] = to_int(vals["year"])
    return vals


def record_mileage(car_id, mileage, date=None, source="manual"):
    """Log an odometer reading; the car's current mileage only moves forward."""
    mileage = to_int(mileage)
    if mileage is None:
        return
    date = (date or today())[:10]
    db.execute("INSERT INTO mileage_log (car_id, date, mileage, source) VALUES (?,?,?,?)",
               (car_id, date, mileage, source))
    db.execute("""UPDATE cars SET mileage=?, mileage_date=? WHERE id=? AND (mileage IS NULL OR mileage <= ?)""",
               (mileage, date, car_id, mileage))


@app.route("/api/cars", methods=["GET", "POST"])
def api_cars():
    if request.method == "POST":
        b = body()
        vals = car_values(b)
        if not vals.get("name"):
            return err("Name is required.")
        cur = db.execute(f"INSERT INTO cars ({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
                         list(vals.values()))
        if b.get("mileage"):
            record_mileage(cur.lastrowid, b["mileage"])
        return jsonify(id=cur.lastrowid)
    cars = db.rows("""SELECT c.*,
        (SELECT COUNT(*) FROM parts p WHERE p.car_id=c.id AND p.status IN ('Needed','Check','Ordered')) AS parts_open,
        (SELECT COUNT(*) FROM parts p WHERE p.car_id=c.id AND p.status='On hand') AS parts_on_hand,
        (SELECT COUNT(*) FROM documents d WHERE d.car_id=c.id) AS documents,
        (SELECT MAX(date) FROM services s WHERE s.car_id=c.id) AS last_service
        FROM cars c ORDER BY c.id""")
    for c in cars:
        up = upcoming(c)
        c["upcoming"] = up[:3]
        c["overdue"] = sum(1 for u in up if u["state"] == "overdue")
        c["due_soon"] = sum(1 for u in up if u["state"] == "soon")
    return jsonify(cars)


@app.route("/api/cars/<int:car_id>", methods=["GET", "PUT", "DELETE"])
def api_car(car_id):
    car = db.row("SELECT * FROM cars WHERE id=?", (car_id,))
    if not car:
        return err("No such car", 404)
    if request.method == "PUT":
        b = body()
        vals = car_values(b)
        if vals:
            db.execute(f"UPDATE cars SET {','.join(k + '=?' for k in vals)} WHERE id=?",
                       list(vals.values()) + [car_id])
        return jsonify(ok=True)
    if request.method == "DELETE":
        db.execute("DELETE FROM cars WHERE id=?", (car_id,))
        return jsonify(ok=True)
    car["upcoming"] = upcoming(car)
    car["mileage_log"] = db.rows("SELECT * FROM mileage_log WHERE car_id=? ORDER BY date DESC, id DESC LIMIT 20",
                                 (car_id,))
    return jsonify(car)


@app.route("/api/cars/<int:car_id>/mileage", methods=["POST"])
def api_mileage(car_id):
    """Also the Home Assistant hook: POST {"mileage": 123456}."""
    b = body() or request.form
    m = to_int(b.get("mileage"))
    if m is None:
        return err("mileage required")
    car = db.row("SELECT mileage FROM cars WHERE id=?", (car_id,))
    if not car:
        return err("No such car", 404)
    if b.get("correct"):  # explicit correction may go backwards
        db.execute("UPDATE cars SET mileage=?, mileage_date=? WHERE id=?", (m, today(), car_id))
        db.execute("INSERT INTO mileage_log (car_id, date, mileage, source) VALUES (?,?,?,?)",
                   (car_id, today(), m, "correction"))
    else:
        record_mileage(car_id, m, b.get("date"), b.get("source") or "manual")
    return jsonify(ok=True, mileage=db.row("SELECT mileage FROM cars WHERE id=?", (car_id,))["mileage"])


# ---------- service schedules & upcoming ----------

def upcoming(car):
    """Every active schedule for a car with its due point and state, most urgent first."""
    out = []
    today_d = dt.date.today()
    for s in db.rows("""SELECT s.*, d.title AS doc_title FROM schedules s LEFT JOIN documents d ON d.id=s.doc_id
                        WHERE s.car_id=? AND s.active=1""", (car["id"],)):
        due_m = (s["last_mileage"] + s["interval_miles"]) if (s["interval_miles"] and s["last_mileage"] is not None) else None
        due_d = add_months(s["last_date"], s["interval_months"]) if (s["interval_months"] and s["last_date"]) else None
        miles_left = (due_m - car["mileage"]) if (due_m is not None and car.get("mileage") is not None) else None
        days_left = (dt.date.fromisoformat(due_d) - today_d).days if due_d else None
        if s["last_date"] is None and s["last_mileage"] is None:
            state = "unknown"
        elif (miles_left is not None and miles_left < 0) or (days_left is not None and days_left < 0):
            state = "overdue"
        elif ((miles_left is not None and miles_left <= max(500, (s["interval_miles"] or 0) // 10))
              or (days_left is not None and days_left <= 30)):
            state = "soon"
        else:
            state = "ok"
        # rank: fraction of interval remaining (smallest first)
        fracs = []
        if miles_left is not None and s["interval_miles"]:
            fracs.append(miles_left / s["interval_miles"])
        if days_left is not None and s["interval_months"]:
            fracs.append(days_left / (s["interval_months"] * 30.4))
        out.append({**s, "due_mileage": due_m, "due_date": due_d, "miles_left": miles_left,
                    "days_left": days_left, "state": state, "rank": min(fracs) if fracs else 9e9})
    order = {"overdue": 0, "soon": 1, "ok": 2, "unknown": 3}
    out.sort(key=lambda u: (order[u["state"]], u["rank"], u["title"]))
    return out


SCHEDULE_FIELDS = ["title", "interval_miles", "interval_months", "last_date", "last_mileage", "doc_id", "doc_page",
                   "notes", "active"]


def schedule_values(b):
    vals = {k: b[k] for k in SCHEDULE_FIELDS if k in b}
    for k in ("interval_miles", "interval_months", "last_mileage", "doc_id", "doc_page", "active"):
        if k in vals:
            vals[k] = to_int(vals[k])
    if "last_date" in vals:
        vals["last_date"] = (vals["last_date"] or None) and vals["last_date"][:10]
    # what the user types as "last done" is the baseline that logged services build on
    if "last_date" in vals:
        vals["base_date"] = vals["last_date"]
    if "last_mileage" in vals:
        vals["base_mileage"] = vals["last_mileage"]
    return vals


@app.route("/api/cars/<int:car_id>/schedules", methods=["POST"])
def api_schedules_add(car_id):
    b = body()
    items = b.get("items") or [b]
    ids = []
    for it in items:
        vals = schedule_values(it)
        if not vals.get("title"):
            continue
        vals["car_id"] = car_id
        ids.append(db.execute(f"INSERT INTO schedules ({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
                              list(vals.values())).lastrowid)
    return jsonify(ids=ids)


@app.route("/api/schedules/<int:sid>", methods=["PUT", "DELETE"])
def api_schedule(sid):
    if request.method == "DELETE":
        db.execute("DELETE FROM schedules WHERE id=?", (sid,))
        return jsonify(ok=True)
    vals = schedule_values(body())
    if vals:
        db.execute(f"UPDATE schedules SET {','.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [sid])
    return jsonify(ok=True)


# ---------- services ----------

@app.route("/api/cars/<int:car_id>/services", methods=["GET", "POST"])
def api_services(car_id):
    if request.method == "POST":
        return save_service(car_id, None, body())
    services = db.rows("SELECT * FROM services WHERE car_id=? ORDER BY date DESC, mileage DESC, id DESC", (car_id,))
    for s in services:
        s["parts"] = db.rows("SELECT id, name, part_number, qty, brand FROM parts WHERE service_id=?", (s["id"],))
        s["schedule_ids"] = [r["schedule_id"] for r in
                             db.rows("SELECT schedule_id FROM service_schedules WHERE service_id=?", (s["id"],))]
    return jsonify(services)


def refresh_schedule(sid):
    """A schedule's 'last done' is its most recent linked service, or the hand-entered
    baseline if that's newer or there's no linked service (e.g. after deleting one)."""
    sched = db.row("SELECT base_date, base_mileage FROM schedules WHERE id=?", (sid,))
    if not sched:
        return
    last = db.row("""SELECT s.date, s.mileage FROM services s JOIN service_schedules ss ON ss.service_id=s.id
                     WHERE ss.schedule_id=? ORDER BY s.date DESC, s.mileage DESC LIMIT 1""", (sid,))
    if last and (sched["base_date"] is None or last["date"] >= sched["base_date"]):
        date, mileage = last["date"], last["mileage"]
    else:
        date, mileage = sched["base_date"], sched["base_mileage"]
    db.execute("UPDATE schedules SET last_date=?, last_mileage=? WHERE id=?", (date, mileage, sid))


def save_service(car_id, service_id, b):
    if not (b.get("title") or "").strip():
        return err("What was done? (title required)")
    date = (b.get("date") or today())[:10]
    mileage = to_int(b.get("mileage"))
    vals = dict(date=date, mileage=mileage, title=b["title"].strip(), notes=b.get("notes") or "",
                cost=to_money(b.get("cost")), done_by=b.get("done_by") or "DIY")
    if service_id:
        db.execute(f"UPDATE services SET {','.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [service_id])
        old_scheds = [r["schedule_id"] for r in db.rows("SELECT schedule_id FROM service_schedules WHERE service_id=?", (service_id,))]
        db.execute("DELETE FROM service_schedules WHERE service_id=?", (service_id,))
        db.execute("UPDATE parts SET service_id=NULL WHERE service_id=?", (service_id,))
    else:
        vals["car_id"] = car_id
        service_id = db.execute(f"INSERT INTO services ({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
                                list(vals.values())).lastrowid
        old_scheds = []
    for sid in b.get("schedule_ids") or []:
        db.execute("INSERT OR IGNORE INTO service_schedules (service_id, schedule_id) VALUES (?,?)", (service_id, int(sid)))
    for sid in set(old_scheds) | {int(x) for x in b.get("schedule_ids") or []}:
        refresh_schedule(sid)
    for pid in b.get("part_ids") or []:
        db.execute("""UPDATE parts SET service_id=?, status='Installed', installed_date=?, installed_mileage=COALESCE(?, installed_mileage),
                      updated_at=CURRENT_TIMESTAMP WHERE id=?""", (service_id, date, mileage, int(pid)))
    if mileage is not None:
        record_mileage(car_id, mileage, date, "service")
    return jsonify(id=service_id)


@app.route("/api/services/<int:service_id>", methods=["PUT", "DELETE"])
def api_service(service_id):
    s = db.row("SELECT * FROM services WHERE id=?", (service_id,))
    if not s:
        return err("No such service", 404)
    if request.method == "DELETE":
        scheds = [r["schedule_id"] for r in db.rows("SELECT schedule_id FROM service_schedules WHERE service_id=?", (service_id,))]
        db.execute("DELETE FROM services WHERE id=?", (service_id,))
        for sid in scheds:
            refresh_schedule(sid)
        return jsonify(ok=True)
    return save_service(s["car_id"], service_id, body())


# ---------- parts ----------

PART_FIELDS = ["car_id", "name", "category", "part_number", "brand", "qty", "status", "supplier", "price", "url", "order_ref",
               "purchased_date", "location", "installed_mileage", "installed_date", "service_id", "document_id", "notes"]


def part_values(b):
    vals = {k: b[k] for k in PART_FIELDS if k in b}
    for k in ("car_id", "qty", "installed_mileage", "service_id", "document_id"):
        if k in vals:
            vals[k] = to_int(vals[k])
    if "price" in vals:
        vals["price"] = to_money(vals["price"])
    for k in ("purchased_date", "installed_date"):
        if k in vals:
            vals[k] = (vals[k] or None) and str(vals[k])[:10]
    for k in ("name", "category", "part_number", "brand", "supplier", "url", "order_ref", "location", "notes"):
        if k in vals:
            vals[k] = (vals[k] or "").strip()
    if "status" in vals and vals["status"] not in db.STATUSES:
        vals["status"] = "Needed"
    return vals


def insert_part(b):
    vals = part_values(b)
    if not vals.get("name") and vals.get("part_number"):
        vals["name"] = vals["part_number"]
    if not vals.get("name"):
        raise ValueError("Part name is required.")
    if vals.get("status") == "Installed" and not vals.get("installed_date"):
        vals["installed_date"] = today()
    return db.execute(f"INSERT INTO parts ({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
                      list(vals.values())).lastrowid


PART_SELECT = """SELECT p.*, c.name AS car, d.title AS document_title, d.filename AS document_file,
                 s.title AS service_title FROM parts p LEFT JOIN cars c ON c.id=p.car_id
                 LEFT JOIN documents d ON d.id=p.document_id LEFT JOIN services s ON s.id=p.service_id"""


@app.route("/api/parts", methods=["GET", "POST"])
def api_parts():
    if request.method == "POST":
        try:
            return jsonify(id=insert_part(body()))
        except ValueError as e:
            return err(str(e))
    where, args = [], []
    if request.args.get("car_id"):
        cid = request.args["car_id"]
        if cid == "none":
            where.append("p.car_id IS NULL")
        else:
            where.append("p.car_id=?")
            args.append(int(cid))
    if request.args.get("status"):
        sts = request.args["status"].split(",")
        where.append(f"p.status IN ({','.join('?' * len(sts))})")
        args += sts
    q = (request.args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        digits = re.sub(r"\D", "", q)
        where.append("(p.name LIKE ? OR p.part_number LIKE ? OR p.brand LIKE ? OR p.notes LIKE ? OR p.supplier LIKE ? OR p.category LIKE ?"
                     + (" OR REPLACE(p.part_number,' ','') LIKE ?" if len(digits) >= 3 else "") + ")")
        args += [like] * 6 + ([f"%{digits}%"] if len(digits) >= 3 else [])
    sql = PART_SELECT + (" WHERE " + " AND ".join(where) if where else "") + """
        ORDER BY CASE p.status WHEN 'Needed' THEN 0 WHEN 'Check' THEN 1 WHEN 'Ordered' THEN 2
                 WHEN 'On hand' THEN 3 ELSE 4 END, p.category COLLATE NOCASE, p.name COLLATE NOCASE"""
    return jsonify(db.rows(sql, args))


@app.route("/api/parts/<int:pid>", methods=["GET", "PUT", "DELETE"])
def api_part(pid):
    if request.method == "DELETE":
        db.execute("DELETE FROM parts WHERE id=?", (pid,))
        return jsonify(ok=True)
    if request.method == "PUT":
        vals = part_values(body())
        if vals.get("status") == "Installed":
            cur = db.row("SELECT installed_date FROM parts WHERE id=?", (pid,))
            if cur and not cur["installed_date"] and "installed_date" not in vals:
                vals["installed_date"] = today()
        if vals:
            db.execute(f"UPDATE parts SET {','.join(k + '=?' for k in vals)}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                       list(vals.values()) + [pid])
        return jsonify(ok=True)
    p = db.row(PART_SELECT + " WHERE p.id=?", (pid,))
    return jsonify(p) if p else err("No such part", 404)


@app.route("/api/purchases", methods=["POST"])
def api_purchase():
    """Add several bought parts at once, optionally with the receipt/screenshot.
    Multipart: field "data" (JSON) + optional file "receipt". Or plain JSON.
    data = {car_id, supplier, purchased_date, order_ref, status, items: [{name, part_number, brand, qty, price, ...}]}"""
    if request.files or request.form:
        data = json.loads(request.form.get("data") or "{}")
    else:
        data = body()
    items = data.get("items") or []
    if not items:
        return err("No items")
    doc_id = None
    f = request.files.get("receipt")
    if f and f.filename:
        doc_id = store_document(f, data.get("car_id"), "Receipt",
                                data.get("receipt_title") or f"{data.get('supplier') or 'Purchase'} {data.get('purchased_date') or today()}")
    common = {k: data.get(k) for k in ("car_id", "supplier", "purchased_date", "order_ref", "status", "location") if data.get(k)}
    common.setdefault("status", "On hand")
    common.setdefault("purchased_date", today())
    ids = []
    for it in items:
        try:
            ids.append(insert_part({**common, **{k: v for k, v in it.items() if v not in (None, "")}, "document_id": doc_id}))
        except ValueError:
            continue
    return jsonify(ids=ids, document_id=doc_id)


# ---------- documents (manuals, receipts) ----------

def store_document(f, car_id, kind, title, notes=""):
    orig = os.path.basename(f.filename or "file")
    ext = os.path.splitext(orig)[1].lower()[:10]
    name = f"{uuid.uuid4().hex[:12]}{ext}"
    path = os.path.join(db.DOC_DIR, name)
    f.save(path)
    return db.execute("""INSERT INTO documents (car_id, kind, title, filename, original_name, size, notes)
                         VALUES (?,?,?,?,?,?,?)""",
                      (to_int(car_id), kind if kind in db.DOC_KINDS else "Other",
                       title or os.path.splitext(orig)[0], name, orig, os.path.getsize(path), notes)).lastrowid


@app.route("/api/documents", methods=["GET", "POST"])
def api_documents():
    if request.method == "POST":
        ids = []
        files = request.files.getlist("file")
        if not files:
            return err("Choose a file")
        for f in files:
            title = request.form.get("title") if len(files) == 1 else None
            ids.append(store_document(f, request.form.get("car_id"), request.form.get("kind") or "Manual",
                                      title, request.form.get("notes") or ""))
        return jsonify(ids=ids)
    where, args = "", []
    if request.args.get("car_id"):
        where, args = "WHERE d.car_id=?", [int(request.args["car_id"])]
    return jsonify(db.rows(f"""SELECT d.*, c.name AS car,
                               (SELECT COUNT(*) FROM parts p WHERE p.document_id=d.id) AS parts
                               FROM documents d LEFT JOIN cars c ON c.id=d.car_id {where}
                               ORDER BY d.kind, d.title COLLATE NOCASE""", args))


@app.route("/api/documents/<int:did>", methods=["PUT", "DELETE"])
def api_document(did):
    d = db.row("SELECT * FROM documents WHERE id=?", (did,))
    if not d:
        return err("No such document", 404)
    if request.method == "DELETE":
        try:
            os.remove(os.path.join(db.DOC_DIR, d["filename"]))
        except OSError:
            pass
        db.execute("DELETE FROM documents WHERE id=?", (did,))
        return jsonify(ok=True)
    b = body()
    vals = {k: b[k] for k in ("title", "kind", "notes", "car_id") if k in b}
    if "car_id" in vals:
        vals["car_id"] = to_int(vals["car_id"])
    if vals:
        db.execute(f"UPDATE documents SET {','.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [did])
    return jsonify(ok=True)


@app.route("/files/<int:did>/<path:name>")
def file_view(did, name):
    d = db.row("SELECT * FROM documents WHERE id=?", (did,))
    if not d:
        return err("No such document", 404)
    return send_from_directory(db.DOC_DIR, d["filename"], download_name=d["original_name"] or d["filename"],
                               as_attachment=request.args.get("download") == "1", max_age=3600)


# ---------- dashboard / Home Assistant ----------

def ha_summary():
    """Compact per-car summary for Home Assistant (REST sensors, and the add-on's own sensors)."""
    out = []
    for c in db.rows("SELECT * FROM cars ORDER BY id"):
        up = upcoming(c)
        nxt = next((u for u in up if u["state"] != "unknown"), None)
        out.append({
            "id": c["id"], "name": c["name"], "mileage": c["mileage"], "mileage_date": c["mileage_date"],
            "overdue": sum(1 for u in up if u["state"] == "overdue"),
            "due_soon": sum(1 for u in up if u["state"] == "soon"),
            "next_service": nxt and {"title": nxt["title"], "due_mileage": nxt["due_mileage"], "due_date": nxt["due_date"],
                                     "miles_left": nxt["miles_left"], "days_left": nxt["days_left"], "state": nxt["state"]},
            "parts_needed": db.row("SELECT COUNT(*) AS n FROM parts WHERE car_id=? AND status IN ('Needed','Check','Ordered')",
                                   (c["id"],))["n"],
        })
    return {"cars": out}


@app.route("/api/ha/summary")
def api_ha_summary():
    return jsonify(ha_summary())


@app.route("/api/ha/dashboard")
def api_ha_dashboard():
    """YAML for a Garage dashboard view using the add-on's sensors."""
    from . import ha_sensors
    # The add-on's sidebar panel lives at /<slug>; its container hostname is the slug with dashes
    # (local-parts-catalog for a local install, 1a2b3c4d-parts-catalog from a repository).
    host = os.environ.get("HOSTNAME", "")
    path = "/" + host.replace("-", "_") if ADDON and host.endswith("parts-catalog") else "/local_parts_catalog"
    return app.response_class(ha_sensors.dashboard_yaml(ha_summary(), path), mimetype="text/plain")


# ---------- settings, backup ----------

def lan_addresses():
    addrs = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


@app.route("/api/settings", methods=["GET", "PUT"])
def api_settings():
    s = load_settings()
    if request.method == "PUT":
        b = body()
        restart = False
        if ADDON and "lan" in b:
            return err("Network access for the add-on is set in Home Assistant (add-on Configuration).")
        if "lan" in b and bool(b["lan"]) != s["lan"]:
            s["lan"] = bool(b["lan"])
            restart = True
        save_settings(s)
        if restart:
            # run.pyw (the supervisor) relaunches us with the new bind address.
            threading.Timer(0.5, lambda: os._exit(RESTART_CODE)).start()
        return jsonify(ok=True, restarting=restart, settings=s)
    return jsonify(settings=s, lan_addresses=lan_addresses(), data_dir=db.DATA_DIR, addon=ADDON,
                   lan_active=app.config.get("BIND_HOST") == "0.0.0.0" and not ADDON)


def write_backup(out):
    """Zip garage.db + the catalog db (consistent snapshots), settings and all stored files into `out`."""
    from . import epc
    tmp = tempfile.mkdtemp()
    try:
        snap = os.path.join(tmp, "garage.db")
        dst = sqlite3.connect(snap)
        db.connect().backup(dst)
        dst.close()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "garage.db")
            if os.path.exists(SETTINGS_PATH):
                z.write(SETTINGS_PATH, "settings.json")
            if os.path.exists(epc.EPC_DB):
                esnap = os.path.join(tmp, "epc.db")
                edst = sqlite3.connect(esnap)
                epc.connect().backup(edst)
                edst.close()
                z.write(esnap, "epc/epc.db")
            for folder in (db.DOC_DIR, epc.FILES_DIR):
                for dirpath, _, files in os.walk(folder):
                    for fn in files:
                        full = os.path.join(dirpath, fn)
                        z.write(full, os.path.relpath(full, db.DATA_DIR).replace(os.sep, "/"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.route("/api/backup")
def api_backup():
    tmp = tempfile.mkdtemp()
    try:
        out = os.path.join(tmp, "backup.zip")
        write_backup(out)
        with open(out, "rb") as f:
            data = io.BytesIO(f.read())
        return send_file(data, mimetype="application/zip", as_attachment=True,
                         download_name=time.strftime("parts-catalog-backup-%Y-%m-%d-%H%M.zip"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


RESTORE_PATHS = ("garage.db", "epc/epc.db", "documents/", "epc/files/")


@app.route("/api/restore", methods=["POST"])
def api_restore():
    """Replace all data with a backup zip (e.g. moving from the PC to the Home Assistant add-on).
    The current data is saved first as data/before-restore-<time>.zip. The zip is unpacked to
    data/restore-pending/, swapped in at the next start (db.apply_pending_restore), and we restart."""
    f = request.files.get("backup")
    if not f:
        return err("Choose a backup zip")
    tmp = tempfile.mkdtemp()
    pending = os.path.join(db.DATA_DIR, "restore-pending")
    try:
        zpath = os.path.join(tmp, "upload.zip")
        f.save(zpath)
        try:
            z = zipfile.ZipFile(zpath)
        except zipfile.BadZipFile:
            return err("That isn't a zip file")
        with z:
            names = z.namelist()
            if "garage.db" not in names:
                return err("Not a Parts Catalog backup (no garage.db inside)")
            for n in names:
                parts = n.replace("\\", "/").split("/")
                if n.startswith(("/", "\\")) or ".." in parts or ":" in n:
                    return err("Backup contains unsafe paths")
            shutil.rmtree(pending, ignore_errors=True)
            os.makedirs(pending)
            for n in names:
                if n.endswith("/") or not n.startswith(RESTORE_PATHS):
                    continue  # settings.json is left alone: it belongs to this install
                target = os.path.join(pending, *n.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(n) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        c = sqlite3.connect(os.path.join(pending, "garage.db"))
        try:
            ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            c.execute("SELECT COUNT(*) FROM cars").fetchone()
        except sqlite3.Error:
            ok = False
        finally:
            c.close()
        if not ok:
            shutil.rmtree(pending, ignore_errors=True)
            return err("The database in that backup is damaged")
        write_backup(os.path.join(db.DATA_DIR, time.strftime("before-restore-%Y%m%d-%H%M%S.zip")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not app.config.get("TESTING"):
        threading.Timer(0.5, lambda: os._exit(RESTART_CODE)).start()
    return jsonify(ok=True, restarting=True)


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return err("Not found", 404)
    return e
