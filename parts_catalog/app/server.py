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
from .mcp_server import bp as mcp_bp, INTERNAL

STATIC = os.path.join(db.ROOT, "static")
SETTINGS_PATH = os.path.join(db.DATA_DIR, "settings.json")
DEFAULT_SETTINGS = {"lan": False, "port": 8765, "catalog": False, "agent_key": None}
RESTART_CODE = 3
# Set by the Home Assistant add-on (run_addon.py): no phone-access toggle, and optionally only
# accept requests from HA's ingress proxy.
ADDON = os.environ.get("PARTS_CATALOG_ADDON") == "1"
ALLOWED_CLIENTS = {a.strip() for a in os.environ.get("PARTS_CATALOG_ALLOW", "").split(",") if a.strip()}
# The add-on's name on Home Assistant's network: its slug with dashes (local-parts-catalog for a local install,
# 1a2b3c4d-parts-catalog from a repository).
ADDON_HOST = os.environ.get("HOSTNAME", "")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # service manuals are big
app.register_blueprint(epc_bp)
app.register_blueprint(mcp_bp)

# Common service intervals offered as one-click presets: (title, miles, months, group).
PRESETS = [
    ("Engine oil & filter", 5000, 12, "Engine"),
    ("Engine air filter", 15000, 24, "Engine"),
    ("Spark plugs", 30000, 48, "Engine"),
    ("Valve adjustment", 15000, None, "Engine"),
    ("Drive belts (inspect)", 30000, 24, "Engine"),
    ("Timing belt", 60000, 72, "Engine"),
    ("Fuel filter", 30000, 48, "Fuel"),
    ("Diesel fuel filter", 15000, 12, "Fuel"),
    ("Coolant flush", 30000, 24, "Cooling"),
    ("Coolant hoses (inspect)", 30000, 24, "Cooling"),
    ("Manual transmission fluid", 60000, None, "Drivetrain"),
    ("Automatic transmission fluid & filter", 60000, None, "Drivetrain"),
    ("Differential fluid", 60000, None, "Drivetrain"),
    ("Transfer case fluid", 60000, None, "Drivetrain"),
    ("Clutch fluid", None, 24, "Drivetrain"),
    ("Brake fluid flush", None, 24, "Brakes"),
    ("Brake pads (inspect)", 15000, 12, "Brakes"),
    ("Parking brake (adjust)", 30000, 24, "Brakes"),
    ("Power steering fluid", 60000, 48, "Steering & suspension"),
    ("Suspension & steering (inspect)", 15000, 12, "Steering & suspension"),
    ("Wheel alignment", 15000, 24, "Steering & suspension"),
    ("Tire rotation", 6000, 6, "Tires"),
    ("Tire pressure & tread check", None, 3, "Tires"),
    ("Cabin / microfilter", 15000, 24, "Body & interior"),
    ("Wiper blades", None, 12, "Body & interior"),
    ("A/C service", None, 36, "Body & interior"),
    ("Battery (test)", None, 12, "Electrical"),
    ("Inspection / registration", None, 12, "Paperwork"),
]

# One-tap titles when logging a service that isn't on a schedule. Settings › Quick services picks which ones show
# (QUICK_DEFAULT until then) and can add your own.
QUICK_SERVICES = [
    "Wash", "Fuel fill-up", "Oil top-up", "Tire pressure check", "Detail", "Washer fluid", "Bulb replaced",
    "Battery replaced", "New tires", "Tire repair", "Tire rotation", "Wheel alignment",
    "Brake job (pads & rotors, front)", "Brake job (pads & rotors, rear)", "Brake pads (front)", "Brake pads (rear)",
    "Wheel bearing", "Shocks / struts", "Control arms / bushings", "Tie rods", "Drive belt & tensioner",
    "Water pump & thermostat", "Radiator & hoses", "Valve cover gasket", "Oil pan gasket", "Oil leak repair", "Clutch",
    "Starter", "Alternator", "Fuel pump", "Exhaust repair", "A/C recharge", "Diagnosis / inspection",
]
QUICK_DEFAULT = ["Wash", "Fuel fill-up", "Oil top-up", "Tire pressure check"]
CARD_DEFAULTS = {"photo": True, "mileage": True, "upcoming_rows": 3, "hide_ok": False, "project": True, "buttons": True}


def card_settings(s=None):
    """How garage cards look (Garage > Customize cards)."""
    c = {**CARD_DEFAULTS, **((load_settings() if s is None else s).get("card") or {})}
    rows = to_int(c["upcoming_rows"])
    c["upcoming_rows"] = max(0, min(12, 3 if rows is None else rows))
    return {k: (c[k] if k == "upcoming_rows" else bool(c[k])) for k in CARD_DEFAULTS}


def clean_quick(items):
    out, seen = [], set()
    for x in items or []:
        t = str(x).strip()[:80]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:80]


def quick_services(s=None):
    """The one-tap jobs shown in Log a service. `quick_list` (set once they're picked in Settings) is exactly what
    shows; without it, the older `quick_services` (own jobs) followed by QUICK_DEFAULT."""
    s = load_settings() if s is None else s
    if s.get("quick_list") is not None:
        return clean_quick(s["quick_list"])
    return clean_quick(list(s.get("quick_services") or []) + QUICK_DEFAULT)


def quick_own(s=None):
    """Your own jobs (not built in), shown or not, so Settings can offer them again after unticking."""
    s = load_settings() if s is None else s
    built = {q.lower() for q in QUICK_SERVICES}
    return [q for q in clean_quick(list(s.get("quick_own") or []) + quick_services(s)) if q.lower() not in built]


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


class BadInput(ValueError):
    """A value the API can't accept; answered as 400 with this message."""


@app.errorhandler(BadInput)
def bad_input(e):
    return err(str(e), 400)


def to_date(v, field="Date"):
    """'YYYY-MM-DD' (anything after the 10th character is dropped), None for blank; else 400."""
    if v in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(v).strip()[:10]).isoformat()
    except ValueError:
        raise BadInput(f"{field} must be a date like 2026-11-12, not {str(v)[:30]!r}") from None


def parse_date(v):
    """A stored date as a date, or None if it's missing or unreadable (older data may hold anything)."""
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def add_months(date_str, months):
    d = parse_date(date_str)
    if d is None:
        return None
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1])).isoformat()


@app.before_request
def only_allowed_clients():
    if ALLOWED_CLIENTS and request.remote_addr not in ALLOWED_CLIENTS and not request.environ.get(INTERNAL):
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
    return jsonify(statuses=db.STATUSES, kinds=db.KINDS, doc_kinds=db.UPLOAD_DOC_KINDS,
                   features={"catalog": bool(load_settings().get("catalog"))},
                   project_statuses=db.PROJECT_STATUSES, card=card_settings(), quick_services=quick_services(),
                   quick_builtin=QUICK_SERVICES, quick_own=quick_own(),
                   presets=[{"title": t, "interval_miles": m, "interval_months": mo, "group": g} for t, m, mo, g in PRESETS])


# ---------- cars & mileage ----------

CAR_FIELDS = ["name", "kind", "year", "make", "model", "engine", "transmission", "vin", "plate", "notes"]


CAR_ORDER = "COALESCE(c.sort_order, c.id), c.id"
# a vehicle's own picture shows on its card, not among its files
NOT_CAR_PHOTO = "NOT EXISTS (SELECT 1 FROM cars pc WHERE pc.photo_id=d.id)"


def car_values(b):
    vals = {k: (str(b[k]).strip() or None) if b.get(k) is not None else None for k in CAR_FIELDS if k in b}
    if "year" in vals:
        vals["year"] = to_int(vals["year"])
    if "archived" in b:  # hidden from the garage and HA sensors (sold, stored...) but kept with its history
        vals["archived"] = 1 if b["archived"] in (True, 1, "1", "true", "on") else 0
    return vals


def miles_per_day(car_id):
    """Average daily driving between the first and the last odometer reading of the past year, or None without enough data."""
    since = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    r = db.row("""SELECT f.date AS d0, l.date AS d1, f.mileage AS m0, l.mileage AS m1 FROM
                  (SELECT date, mileage FROM mileage_log WHERE car_id=? AND date >= ? ORDER BY date, id LIMIT 1) f,
                  (SELECT date, mileage FROM mileage_log WHERE car_id=? AND date >= ? ORDER BY date DESC, id DESC LIMIT 1) l""",
               (car_id, since, car_id, since))
    if not r or not r["d0"] or r["d0"] == r["d1"]:
        return None
    d0, d1 = parse_date(r["d0"]), parse_date(r["d1"])
    if not d0 or not d1:
        return None
    days = (d1 - d0).days
    if days < 14 or r["m1"] <= r["m0"]:
        return None
    return (r["m1"] - r["m0"]) / days


def newest_reading(car_id, until=None):
    """The car's newest odometer reading, or its newest dated on or before `until`."""
    return db.row("SELECT * FROM mileage_log WHERE car_id=?" + (" AND date<=?" if until else "")
                  + " ORDER BY date DESC, id DESC LIMIT 1", (car_id, until) if until else (car_id,))


def record_mileage(car_id, mileage, date=None, source="manual"):
    """Log an odometer reading if the log stays in mileage order by date and it isn't a repeat; the car's mileage only moves forward."""
    mileage = to_int(mileage)
    if mileage is None:
        return
    date = to_date(date) or today()
    before = newest_reading(car_id, date)
    after = db.row("SELECT mileage FROM mileage_log WHERE car_id=? AND date>? ORDER BY date, id LIMIT 1", (car_id, date))
    if before and (mileage < before["mileage"] or (mileage, date) == (before["mileage"], before["date"])):
        return
    if after and mileage > after["mileage"]:
        return
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
        (SELECT COUNT(*) FROM documents d WHERE d.car_id=c.id AND """ + NOT_CAR_PHOTO + """) AS documents,
        (SELECT MAX(date) FROM services s WHERE s.car_id=c.id) AS last_service,
        ph.filename AS photo_file, ph.original_name AS photo_name
        FROM cars c LEFT JOIN documents ph ON ph.id=c.photo_id
        ORDER BY c.archived, """ + CAR_ORDER)
    card = card_settings()
    for c in cars:
        up = upcoming(c, manual=True)
        shown = [u for u in up if u["state"] != "snoozed" and not (card["hide_ok"] and u["state"] == "ok")]
        c["upcoming"] = shown[:card["upcoming_rows"]]
        c["overdue"] = sum(1 for u in up if u["state"] == "overdue")
        c["due_soon"] = sum(1 for u in up if u["state"] == "soon")
        c["snoozed"] = sum(1 for u in up if u["state"] == "snoozed")
        c["schedules"] = len(up)
        c["projects"] = [p for p in projects_for(c["id"]) if p["status"] != "Done"]
    return jsonify(cars)


@app.route("/api/cars/order", methods=["POST"])
def api_cars_order():
    """{"ids": [3, 1, 2]}: the garage order (also used for HA sensors and the dashboard YAML)."""
    ids = [to_int(i) for i in body().get("ids") or []]
    for n, cid in enumerate(i for i in ids if i):
        db.execute("UPDATE cars SET sort_order=? WHERE id=?", (n + 1, cid))
    return jsonify(ok=True)


@app.route("/api/cars/<int:car_id>/photo", methods=["POST", "DELETE"])
def api_car_photo(car_id):
    car = db.row("SELECT * FROM cars WHERE id=?", (car_id,))
    if not car:
        return err("No such car", 404)
    old = car.get("photo_id")
    if request.method == "POST":
        f = request.files.get("photo")
        if not f:
            return err("Choose a photo")
        did = store_document(f, car_id, "Photo", f"{car['name']} photo")
        shrink_photo(did)
        db.execute("UPDATE cars SET photo_id=? WHERE id=?", (did, car_id))
    else:
        db.execute("UPDATE cars SET photo_id=NULL WHERE id=?", (car_id,))
    if old:
        delete_document(old)
    return jsonify(ok=True)


def shrink_photo(did, max_side=1600):
    """Phone photos are large; keep vehicle photos to a sensible size (and the right way up)."""
    d = db.row("SELECT * FROM documents WHERE id=?", (did,))
    path = os.path.join(db.DOC_DIR, d["filename"])
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((max_side, max_side))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            new = os.path.splitext(d["filename"])[0] + ".jpg"
            im.save(os.path.join(db.DOC_DIR, new), "JPEG", quality=85)
        if new != d["filename"]:
            os.remove(path)
        db.execute("UPDATE documents SET filename=?, original_name=?, size=? WHERE id=?",
                   (new, os.path.splitext(d["original_name"] or "photo")[0] + ".jpg",
                    os.path.getsize(os.path.join(db.DOC_DIR, new)), did))
    except Exception:  # noqa: BLE001 - not an image Pillow can read: keep the original file
        pass


def delete_document(did):
    d = db.row("SELECT * FROM documents WHERE id=?", (did,))
    if d:
        db.execute("DELETE FROM documents WHERE id=?", (did,))
        try:
            os.remove(os.path.join(db.DOC_DIR, d["filename"]))
        except OSError:
            pass


def csv_response(rows, cols, filename):
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label in cols])
    for r in rows:
        w.writerow(["" if r.get(k) is None else r[k] for k, _ in cols])
    return app.response_class("\ufeff" + buf.getvalue(), mimetype="text/csv",
                              headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s or "export").strip("-") or "export"


@app.route("/api/cars/<int:car_id>/export/services.csv")
def api_export_services(car_id):
    car = db.row("SELECT * FROM cars WHERE id=?", (car_id,))
    if not car:
        return err("No such car", 404)
    rows = db.rows("""SELECT s.*, (SELECT GROUP_CONCAT(p.name || CASE WHEN p.part_number<>'' THEN ' (' || p.part_number || ')' ELSE '' END, '; ')
                                  FROM parts p WHERE p.service_id=s.id) AS parts_used
                      FROM services s WHERE s.car_id=? ORDER BY s.date, s.id""", (car_id,))
    cols = [("date", "Date"), ("mileage", "Mileage"), ("title", "Work"), ("parts_used", "Parts used"),
            ("cost", "Cost"), ("done_by", "Done by"), ("notes", "Notes")]
    return csv_response(rows, cols, f"{safe_name(car['name'])}-service-history.csv")


@app.route("/api/export/parts.csv")
def api_export_parts():
    where, args, name = "", [], "all"
    if request.args.get("car_id"):
        where, args = "WHERE p.car_id=?", [int(request.args["car_id"])]
        car = db.row("SELECT name FROM cars WHERE id=?", args)
        name = safe_name(car["name"]) if car else "parts"
    rows = db.rows(f"""SELECT p.*, c.name AS car FROM parts p LEFT JOIN cars c ON c.id=p.car_id {where}
                       ORDER BY c.name, p.status, p.name COLLATE NOCASE""", args)
    cols = [("car", "Vehicle"), ("name", "Part"), ("part_number", "Part number"), ("brand", "Brand"), ("category", "Category"),
            ("qty", "Qty"), ("status", "Status"), ("supplier", "Supplier"), ("price", "Price"), ("order_ref", "Order"),
            ("purchased_date", "Purchased"), ("installed_mileage", "Installed at (mi)"), ("installed_date", "Installed on"),
            ("location", "Location"), ("notes", "Notes")]
    return csv_response(rows, cols, f"{name}-parts.csv")


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
        if car.get("photo_id"):
            delete_document(car["photo_id"])
        return jsonify(ok=True)
    car["upcoming"] = upcoming(car, manual=request.args.get("manual") != "0")
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


@app.route("/api/cars/<int:car_id>/mileage/<int:log_id>", methods=["DELETE"])
def api_mileage_reading(car_id, log_id):
    newest = newest_reading(car_id)
    if not db.execute("DELETE FROM mileage_log WHERE id=? AND car_id=?", (log_id, car_id)).rowcount:
        return err("No such reading", 404)
    if newest["id"] == log_id:  # the odometer showed this reading: back to the newest one left
        r = newest_reading(car_id) or {}
        db.execute("UPDATE cars SET mileage=?, mileage_date=? WHERE id=?", (r.get("mileage"), r.get("date"), car_id))
    return jsonify(ok=True, mileage=db.row("SELECT mileage FROM cars WHERE id=?", (car_id,))["mileage"])


# ---------- service schedules & upcoming ----------

def upcoming(car, manual=False):
    """Every active schedule for a car with its due point and state, most urgent first.
    A schedule dismissed until a later date is "snoozed" instead of soon/overdue. manual=True puts
    schedules the owner dragged into an order first, in that order."""
    out = []
    today_d = dt.date.today()
    rate = miles_per_day(car["id"])
    for s in db.rows("""SELECT s.*, d.title AS doc_title FROM schedules s LEFT JOIN documents d ON d.id=s.doc_id
                        WHERE s.car_id=? AND s.active=1""", (car["id"],)):
        due_m = (s["last_mileage"] + s["interval_miles"]) if (s["interval_miles"] and s["last_mileage"] is not None) else None
        due_d = add_months(s["last_date"], s["interval_months"]) if (s["interval_months"] and s["last_date"]) else None
        miles_left = (due_m - car["mileage"]) if (due_m is not None and car.get("mileage") is not None) else None
        days_left = (parse_date(due_d) - today_d).days if due_d else None
        if s["last_date"] is None and s["last_mileage"] is None:
            state = "unknown"
        elif (miles_left is not None and miles_left < 0) or (days_left is not None and days_left < 0):
            state = "overdue"
        elif ((miles_left is not None and miles_left <= max(500, (s["interval_miles"] or 0) // 10))
              or (days_left is not None and days_left <= 30)):
            state = "soon"
        else:
            state = "ok"
        if state in ("overdue", "soon") and s.get("snoozed_until") and s["snoozed_until"] > today_d.isoformat():
            state = "snoozed"
        # rank: fraction of interval remaining (smallest first)
        fracs = []
        if miles_left is not None and s["interval_miles"]:
            fracs.append(miles_left / s["interval_miles"])
        if days_left is not None and s["interval_months"]:
            fracs.append(days_left / (s["interval_months"] * 30.4))
        # when the mileage limit comes first (or is the only one), estimate when from average driving
        est = None
        if rate and miles_left is not None and miles_left > 0:
            est_d = today_d + dt.timedelta(days=round(miles_left / rate))
            if not due_d or est_d.isoformat() < due_d:
                est = est_d.isoformat()
        out.append({**s, "due_mileage": due_m, "due_date": due_d, "miles_left": miles_left,
                    "days_left": days_left, "est_date": est, "state": state, "rank": min(fracs) if fracs else 9e9})
    order = {"overdue": 0, "soon": 1, "ok": 2, "snoozed": 3, "unknown": 4}
    out.sort(key=lambda u: (order[u["state"]], u["rank"], u["title"]))
    if manual:
        # a dragged order applies within each state, so overdue and due-soon always come first
        out.sort(key=lambda u: (order[u["state"]], u.get("sort_order") is None, u.get("sort_order") or 0))  # stable
    return out


SCHEDULE_FIELDS = ["title", "interval_miles", "interval_months", "last_date", "last_mileage", "doc_id", "doc_page",
                   "notes", "active", "snoozed_until"]


def schedule_values(b, cur=None):
    vals = {k: b[k] for k in SCHEDULE_FIELDS if k in b}
    for k in ("interval_miles", "interval_months", "last_mileage", "doc_id", "doc_page", "active"):
        if k in vals:
            vals[k] = to_int(vals[k])
    for k in ("last_date", "snoozed_until"):
        if k in vals:
            vals[k] = to_date(vals[k], "Last done" if k == "last_date" else "Dismiss until")
    # what the user types as "last done" is the baseline that logged services build on; the Edit dialog sends
    # "last done" back even when it wasn't touched, and that must not freeze what the logged services gave it
    if cur is None or any(k in vals and vals[k] != cur[k] for k in ("last_date", "last_mileage")):
        if "last_date" in vals:
            vals["base_date"] = vals["last_date"]
        if "last_mileage" in vals:
            vals["base_mileage"] = vals["last_mileage"]
        if cur:  # a different last done also ends a dismissal, unless this request sets one
            vals.setdefault("snoozed_until", None)
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


@app.route("/api/cars/<int:car_id>/schedules/order", methods=["POST"])
def api_schedules_order(car_id):
    """{"ids": [5, 2, 9]}: show these schedules first, in this order. {"ids": []} goes back to most-urgent-first."""
    ids = [i for i in (to_int(x) for x in body().get("ids") or []) if i]
    if not ids:
        db.execute("UPDATE schedules SET sort_order=NULL WHERE car_id=?", (car_id,))
        return jsonify(ok=True)
    rest = [r["id"] for r in db.rows("SELECT id FROM schedules WHERE car_id=? AND sort_order IS NOT NULL ORDER BY sort_order",
                                     (car_id,)) if r["id"] not in ids]
    for n, sid in enumerate(ids + rest):
        db.execute("UPDATE schedules SET sort_order=? WHERE id=? AND car_id=?", (n + 1, sid, car_id))
    return jsonify(ok=True)


@app.route("/api/schedules/<int:sid>", methods=["PUT", "DELETE"])
def api_schedule(sid):
    if request.method == "DELETE":
        db.execute("DELETE FROM schedules WHERE id=?", (sid,))
        return jsonify(ok=True)
    vals = schedule_values(body(), db.row("SELECT last_date, last_mileage FROM schedules WHERE id=?", (sid,)))
    if vals:
        db.execute(f"UPDATE schedules SET {','.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [sid])
        refresh_schedule(sid)
    return jsonify(ok=True)


# ---------- services ----------

@app.route("/api/cars/<int:car_id>/services", methods=["GET", "POST"])
def api_services(car_id):
    if request.method == "POST":
        return save_service(car_id, None, body())
    services = db.rows("SELECT * FROM services WHERE car_id=? ORDER BY date DESC, mileage DESC, id DESC", (car_id,))
    for s in services:
        s["parts"] = db.rows("SELECT id, name, part_number, qty, brand, installed_mileage FROM parts WHERE service_id=?", (s["id"],))
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
        if mileage is None:  # logged without mileage: keep the newest known one, or mileage intervals stop counting
            known = db.row("""SELECT s.mileage FROM services s JOIN service_schedules ss ON ss.service_id=s.id
                              WHERE ss.schedule_id=? AND s.mileage IS NOT NULL ORDER BY s.date DESC, s.mileage DESC LIMIT 1""", (sid,))
            mileage = known["mileage"] if known else sched["base_mileage"]
    else:
        date, mileage = sched["base_date"], sched["base_mileage"]
    db.execute("UPDATE schedules SET last_date=?, last_mileage=? WHERE id=?", (date, mileage, sid))


def save_service(car_id, service_id, b):
    if not (b.get("title") or "").strip():
        return err("What was done? (title required)")
    # parts used: [{"id": 7, "qty": 1}] (or the older part_ids). Using fewer than the part's quantity
    # splits it: the rest stays as it was, the used ones become their own Installed entry.
    uses = [{"id": to_int(u.get("id")), "qty": to_int(u.get("qty"))} for u in b.get("parts") or [] if isinstance(u, dict)]
    uses += [{"id": int(pid), "qty": None} for pid in b.get("part_ids") or []]
    if any(u["qty"] is not None and u["qty"] < 1 for u in uses):
        return err("Use at least 1 of each ticked part.")
    date = to_date(b.get("date")) or today()
    mileage = to_int(b.get("mileage"))
    vals = dict(date=date, mileage=mileage, title=b["title"].strip(), notes=b.get("notes") or "",
                cost=to_money(b.get("cost")), done_by=b.get("done_by") or "DIY")
    if service_id:
        db.execute(f"UPDATE services SET {','.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [service_id])
        old_scheds = [r["schedule_id"] for r in db.rows("SELECT schedule_id FROM service_schedules WHERE service_id=?", (service_id,))]
        old_parts = {r["id"] for r in db.rows("SELECT id FROM parts WHERE service_id=?", (service_id,))}
        db.execute("DELETE FROM service_schedules WHERE service_id=?", (service_id,))
        db.execute("UPDATE parts SET service_id=NULL WHERE service_id=?", (service_id,))
    else:
        vals["car_id"] = car_id
        service_id = db.execute(f"INSERT INTO services ({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
                                list(vals.values())).lastrowid
        old_scheds, old_parts = [], set()
    for sid in b.get("schedule_ids") or []:
        db.execute("INSERT OR IGNORE INTO service_schedules (service_id, schedule_id) VALUES (?,?)", (service_id, int(sid)))
    for sid in set(old_scheds) | {int(x) for x in b.get("schedule_ids") or []}:
        refresh_schedule(sid)
    for sid in b.get("schedule_ids") or []:  # done now, so a "dismissed until" date no longer applies
        db.execute("UPDATE schedules SET snoozed_until=NULL WHERE id=?", (int(sid),))
    for u in uses:
        p = db.row("SELECT * FROM parts WHERE id=?", (u["id"],))
        if not p:
            continue
        pid, have = p["id"], p["qty"] or 1
        if pid not in old_parts and u["qty"] and 0 < u["qty"] < have:
            db.execute("UPDATE parts SET qty=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (have - u["qty"], pid))
            copy = {k: p[k] for k in PART_FIELDS if k in p}
            copy["qty"] = u["qty"]
            pid = db.execute(f"INSERT INTO parts ({','.join(copy)}) VALUES ({','.join('?' * len(copy))})",
                             list(copy.values())).lastrowid
        db.execute("""UPDATE parts SET service_id=?, car_id=COALESCE(car_id, ?), status='Installed', installed_date=?,
                      installed_mileage=COALESCE(?, installed_mileage), updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                   (service_id, car_id, date, mileage, pid))
    for pid in old_parts - {u["id"] for u in uses}:  # unticked while editing: not installed after all
        db.execute("""UPDATE parts SET status='On hand', installed_date=NULL, installed_mileage=NULL,
                      updated_at=CURRENT_TIMESTAMP WHERE id=?""", (pid,))
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


# ---------- projects (bigger jobs: steps to tick off, parts set aside) ----------

def projects_for(car_id):
    out = db.rows("""SELECT * FROM projects WHERE car_id=?
                     ORDER BY status='Done', COALESCE(sort_order, id), id""", (car_id,))
    for p in out:
        p["tasks"] = db.rows("SELECT id, title, done FROM project_tasks WHERE project_id=? ORDER BY COALESCE(sort_order, id), id",
                             (p["id"],))
        p["parts"] = db.rows("""SELECT id, name, part_number, brand, qty, status, location FROM parts WHERE project_id=?
                                ORDER BY name COLLATE NOCASE""", (p["id"],))
    return out


def save_project(car_id, pid, b):
    vals = {k: (b[k] or "").strip() for k in ("title", "notes") if k in b}
    if "status" in b:
        vals["status"] = b["status"] if b["status"] in db.PROJECT_STATUSES else "Planned"
        prev = db.row("SELECT status, done_date FROM projects WHERE id=?", (pid,)) if pid else None
        if vals["status"] != "Done":
            vals["done_date"] = None
        elif b.get("done_date"):
            vals["done_date"] = to_date(b["done_date"], "Finished on")
        elif not (prev and prev["status"] == "Done" and prev["done_date"]):  # keep the date of a project already done
            vals["done_date"] = today()
    if "target_date" in b:
        vals["target_date"] = to_date(b["target_date"], "Target date")
    if "title" in vals and not vals["title"]:
        return err("Give the project a name.")
    if pid is None:
        if not vals.get("title"):
            return err("Give the project a name.")
        vals["car_id"] = car_id
        pid = db.execute(f"INSERT INTO projects ({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
                         list(vals.values())).lastrowid
    elif vals:
        db.execute(f"UPDATE projects SET {','.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [pid])
    if "tasks" in b:  # the whole list, in order; {id, title} keeps the tick of that step (no id: a new step),
        # plain text keeps the tick of a step with the same text
        old = db.rows("SELECT * FROM project_tasks WHERE project_id=?", (pid,))
        by_id, by_title = {t["id"]: t for t in old}, {t["title"].lower(): t for t in old}
        db.execute("DELETE FROM project_tasks WHERE project_id=?", (pid,))
        n = 0
        for t in b["tasks"] or []:
            text = isinstance(t, str)
            t = {"title": t} if text else t
            title = (t.get("title") or "").strip()
            if not title:
                continue
            prev = (by_title.get(title.lower()) if text else by_id.get(to_int(t.get("id")))) or {}
            done = t["done"] if "done" in t else prev.get("done", 0)
            n += 1
            db.execute("INSERT INTO project_tasks (project_id, title, done, sort_order) VALUES (?,?,?,?)",
                       (pid, title, 1 if done else 0, n))
    if "part_ids" in b:
        db.execute("UPDATE parts SET project_id=NULL WHERE project_id=?", (pid,))
        for part_id in b["part_ids"] or []:
            db.execute("UPDATE parts SET project_id=?, car_id=COALESCE(car_id, ?) WHERE id=?", (pid, car_id, int(part_id)))
    return jsonify(id=pid)


@app.route("/api/cars/<int:car_id>/projects", methods=["GET", "POST"])
def api_projects(car_id):
    if not db.row("SELECT id FROM cars WHERE id=?", (car_id,)):
        return err("No such car", 404)
    if request.method == "POST":
        return save_project(car_id, None, body())
    return jsonify(projects_for(car_id))


@app.route("/api/cars/<int:car_id>/projects/order", methods=["POST"])
def api_projects_order(car_id):
    for n, pid in enumerate(i for i in (to_int(x) for x in body().get("ids") or []) if i):
        db.execute("UPDATE projects SET sort_order=? WHERE id=? AND car_id=?", (n + 1, pid, car_id))
    return jsonify(ok=True)


@app.route("/api/projects/<int:pid>", methods=["PUT", "DELETE"])
def api_project(pid):
    p = db.row("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        return err("No such project", 404)
    if request.method == "DELETE":
        db.execute("DELETE FROM projects WHERE id=?", (pid,))
        return jsonify(ok=True)
    return save_project(p["car_id"], pid, body())


@app.route("/api/project-tasks/<int:tid>", methods=["PUT"])
def api_project_task(tid):
    db.execute("UPDATE project_tasks SET done=? WHERE id=?", (1 if body().get("done") else 0, tid))
    return jsonify(ok=True)


# ---------- parts ----------

PART_FIELDS = ["car_id", "name", "category", "part_number", "brand", "qty", "status", "supplier", "price", "url", "order_ref",
               "purchased_date", "location", "installed_mileage", "installed_date", "service_id", "document_id", "notes",
               "project_id"]


def part_values(b):
    vals = {k: b[k] for k in PART_FIELDS if k in b}
    for k in ("car_id", "qty", "installed_mileage", "service_id", "document_id", "project_id"):
        if k in vals:
            vals[k] = to_int(vals[k])
    if "price" in vals:
        vals["price"] = to_money(vals["price"])
    for k in ("purchased_date", "installed_date"):
        if k in vals:
            vals[k] = to_date(vals[k], "Purchased" if k == "purchased_date" else "Installed on")
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
                 s.title AS service_title, pr.title AS project_title FROM parts p LEFT JOIN cars c ON c.id=p.car_id
                 LEFT JOIN documents d ON d.id=p.document_id LEFT JOIN services s ON s.id=p.service_id
                 LEFT JOIN projects pr ON pr.id=p.project_id"""


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
    name = new_doc_name(orig)
    f.save(os.path.join(db.DOC_DIR, name))
    return register_document(name, orig, car_id, kind, title, notes)


def new_doc_name(orig):
    return f"{uuid.uuid4().hex[:12]}{os.path.splitext(orig)[1].lower()[:10]}"


def register_document(name, orig, car_id, kind, title, notes=""):
    """Add a row for a file already saved in DOC_DIR under `name`."""
    return db.execute("""INSERT INTO documents (car_id, kind, title, filename, original_name, size, notes)
                         VALUES (?,?,?,?,?,?,?)""",
                      (to_int(car_id), kind if kind in db.DOC_KINDS else "Other",
                       title or os.path.splitext(orig)[0], name, orig,
                       os.path.getsize(os.path.join(db.DOC_DIR, name)), notes or "")).lastrowid


# ---- chunked uploads: Home Assistant Ingress rejects big request bodies (HTTP 413), so large files
# (service manuals) go up in pieces: start -> PUT each piece at its offset -> finish.

UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")


def upload_paths(uid):
    if not UPLOAD_ID.match(uid or ""):
        return None
    d = os.path.join(db.DATA_DIR, "uploads")
    return os.path.join(d, uid + ".json"), os.path.join(d, uid + ".part")


@app.route("/api/documents/chunked", methods=["POST"])
def api_chunked_start():
    b = body()
    orig = os.path.basename(str(b.get("filename") or "")).strip()
    size = to_int(b.get("size"))
    if not orig or size is None or size < 0:
        return err("filename and size are required")
    d = os.path.join(db.DATA_DIR, "uploads")
    os.makedirs(d, exist_ok=True)
    for fn in os.listdir(d):  # forget uploads abandoned more than two days ago
        fp = os.path.join(d, fn)
        if time.time() - os.path.getmtime(fp) > 2 * 86400:
            try:
                os.remove(fp)
            except OSError:
                pass
    uid = uuid.uuid4().hex
    meta_p, part_p = upload_paths(uid)
    with open(meta_p, "w", encoding="utf-8") as f:
        json.dump({"filename": orig, "size": size, "car_id": b.get("car_id"), "kind": b.get("kind") or "Manual",
                   "title": b.get("title") or "", "notes": b.get("notes") or ""}, f)
    open(part_p, "wb").close()
    return jsonify(upload_id=uid, received=0)


@app.route("/api/documents/chunked/<uid>", methods=["PUT"])
def api_chunked_piece(uid):
    paths = upload_paths(uid)
    if not paths or not os.path.exists(paths[0]):
        return err("No such upload", 404)
    meta_p, part_p = paths
    with open(meta_p, encoding="utf-8") as f:
        meta = json.load(f)
    have = os.path.getsize(part_p)
    offset = to_int(request.args.get("offset"))
    if offset != have:  # resend from where the server actually is
        return jsonify(error="offset mismatch", received=have), 409
    data = request.get_data(cache=False)
    if have + len(data) > meta["size"]:
        return err("More data than the announced size")
    with open(part_p, "ab") as f:
        f.write(data)
    return jsonify(received=have + len(data))


@app.route("/api/documents/chunked/<uid>/finish", methods=["POST"])
def api_chunked_finish(uid):
    paths = upload_paths(uid)
    if not paths or not os.path.exists(paths[0]):
        return err("No such upload", 404)
    meta_p, part_p = paths
    with open(meta_p, encoding="utf-8") as f:
        meta = json.load(f)
    have = os.path.getsize(part_p)
    if have != meta["size"]:
        return jsonify(error=f"Upload incomplete ({have} of {meta['size']} bytes)", received=have), 409
    name = new_doc_name(meta["filename"])
    os.replace(part_p, os.path.join(db.DOC_DIR, name))
    os.remove(meta_p)
    did = register_document(name, meta["filename"], meta["car_id"], meta["kind"], meta["title"], meta["notes"])
    return jsonify(id=did)


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
    where, args = [NOT_CAR_PHOTO], []
    if request.args.get("car_id") == "none":
        where.append("d.car_id IS NULL")
    elif request.args.get("car_id"):
        where.append("d.car_id=?")
        args.append(int(request.args["car_id"]))
    return jsonify(db.rows(f"""SELECT d.*, c.name AS car,
                               (SELECT COUNT(*) FROM parts p WHERE p.document_id=d.id) AS parts
                               FROM documents d LEFT JOIN cars c ON c.id=d.car_id WHERE {' AND '.join(where)}
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
    if "kind" in vals and vals["kind"] not in db.DOC_KINDS:
        return err("Unknown file type")
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
    for c in db.rows("SELECT * FROM cars c WHERE IFNULL(c.archived, 0)=0 ORDER BY " + CAR_ORDER):
        up = upcoming(c)
        nxt = next((u for u in up if u["state"] not in ("unknown", "snoozed")), None)
        out.append({
            "id": c["id"], "name": c["name"], "mileage": c["mileage"], "mileage_date": c["mileage_date"],
            "overdue": sum(1 for u in up if u["state"] == "overdue"),
            "due_soon": sum(1 for u in up if u["state"] == "soon"),
            "next_service": nxt and {"title": nxt["title"], "due_mileage": nxt["due_mileage"], "due_date": nxt["due_date"],
                                     "miles_left": nxt["miles_left"], "days_left": nxt["days_left"], "est_date": nxt["est_date"],
                                     "state": nxt["state"]},
            "parts_needed": db.row("SELECT COUNT(*) AS n FROM parts WHERE car_id=? AND status IN ('Needed','Check','Ordered')",
                                   (c["id"],))["n"],
            "snoozed": sum(1 for u in up if u["state"] == "snoozed"),
        })
    return {"cars": out}


@app.route("/api/ha/summary")
def api_ha_summary():
    return jsonify(ha_summary())


@app.route("/api/ha/dashboard")
def api_ha_dashboard():
    """YAML for a Garage dashboard view using the add-on's sensors."""
    from . import ha_sensors
    # The add-on's sidebar panel lives at /<slug>, its hostname with underscores.
    path = "/" + ADDON_HOST.replace("-", "_") if ADDON and ADDON_HOST.endswith("parts-catalog") else "/local_parts_catalog"
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
        if "catalog" in b:
            s["catalog"] = bool(b["catalog"])
        if isinstance(b.get("card"), dict):
            s["card"] = card_settings({"card": {**(s.get("card") or {}), **{k: v for k, v in b["card"].items() if k in CARD_DEFAULTS}}})
        if "quick_services" in b:  # older form: own jobs, shown ahead of the built-in ones
            s["quick_services"] = clean_quick(b["quick_services"])[:60]
            s.pop("quick_list", None)
        if "quick_own" in b:  # your own jobs (Settings › Quick services), shown or not
            s["quick_own"] = clean_quick(b["quick_own"])
        if "quick_list" in b:  # exactly which jobs show, in order; null = back to the defaults
            s["quick_list"] = None if b["quick_list"] is None else clean_quick(b["quick_list"])
            if b["quick_list"] is None:
                s.pop("quick_services", None)
        if "agent_key" in b:  # "new" creates (or replaces) the key, null turns agent access off
            import secrets
            s["agent_key"] = secrets.token_urlsafe(32) if b["agent_key"] == "new" else None
        if "lan" in b and bool(b["lan"]) != s["lan"]:
            s["lan"] = bool(b["lan"])
            restart = True
        save_settings(s)
        if restart:
            # The PC launcher (not part of this repository) relaunches us with the new bind address.
            threading.Timer(0.5, lambda: os._exit(RESTART_CODE)).start()
        return jsonify(ok=True, restarting=restart, settings=s)
    from . import ha_sensors
    return jsonify(settings=s, lan_addresses=lan_addresses(), data_dir=db.DATA_DIR, addon=ADDON, hostname=ADDON_HOST,
                   lan_active=app.config.get("BIND_HOST") == "0.0.0.0" and not ADDON, publish_sensors=ha_sensors.enabled())


# ---------- car wash weather (app/wash.py) ----------

def last_wash():
    """Date of the newest logged wash / detail on any vehicle, or None."""
    from . import wash
    where = " OR ".join("LOWER(title) LIKE ?" for _ in wash.WASH_WORDS)
    r = db.row(f"SELECT MAX(date) AS d FROM services WHERE {where}", tuple(f"%{w}%" for w in wash.WASH_WORDS))
    return r["d"] if r else None


def washer():
    from . import wash
    return wash.get(load_settings, last_wash, os.path.join(db.DATA_DIR, "wash.json"))


@app.route("/api/wash", methods=["GET", "PUT"])
def api_wash():
    from . import wash
    w = washer()
    if request.method == "PUT":
        s = load_settings()
        s["wash"] = wash.clean_config({**wash.config(s), **body()})
        save_settings(s)
    cfg = wash.config(load_settings())
    out = {"available": w.available, "config": cfg, **w.status()}
    if not w.available:
        return jsonify(out)
    try:
        out["weather_entities"] = w.ha.weather_entities()
        out["notify_services"] = w.ha.notify_services()
        if not cfg["weather_entity"] and out["weather_entities"]:
            out["suggested_entity"] = out["weather_entities"][0]["entity_id"]
        if cfg["weather_entity"]:
            out["verdict"] = w.verdict(cfg, fresh=request.method == "PUT")
    except Exception as e:  # noqa: BLE001 - HA unreachable etc.
        out["error"] = f"Couldn't reach Home Assistant: {e}"
    return jsonify(out)


@app.route("/api/wash/test", methods=["POST"])
def api_wash_test():
    w = washer()
    if not w.available:
        return err("Needs Home Assistant (runs as an add-on).")
    try:
        v = w.verdict(fresh=True)
        return jsonify(sent=w.send(v, test=True), verdict=v)
    except ValueError as e:
        return err(str(e))
    except Exception as e:  # noqa: BLE001
        return err(f"Couldn't send: {e}", 502)


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
    # Unpack into a staging folder and only rename it to restore-pending/ once everything is in and checked:
    # the next start swaps restore-pending/ in, so a half-written one must never exist.
    staging = os.path.join(db.DATA_DIR, "restore-staging")
    shutil.rmtree(staging, ignore_errors=True)
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
            # safety copy of the current data first, so a failure below never leaves you without one
            write_backup(os.path.join(db.DATA_DIR, time.strftime("before-restore-%Y%m%d-%H%M%S.zip")))
            os.makedirs(staging)
            for n in names:
                if n.endswith("/") or not n.startswith(RESTORE_PATHS):
                    continue  # settings.json is left alone: it belongs to this install
                target = os.path.join(staging, *n.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(n) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        c = sqlite3.connect(os.path.join(staging, "garage.db"))
        try:
            ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            c.execute("SELECT COUNT(*) FROM cars").fetchone()
        except sqlite3.Error:
            ok = False
        finally:
            c.close()
        if not ok:
            return err("The database in that backup is damaged")
        shutil.rmtree(pending, ignore_errors=True)
        os.replace(staging, pending)
    except (zipfile.BadZipFile, OSError, EOFError) as e:
        return err(f"Restore failed, nothing was changed: {e}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
    if not app.config.get("TESTING"):
        threading.Timer(0.5, lambda: os._exit(RESTART_CODE)).start()
    return jsonify(ok=True, restarting=True)


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return err("Not found", 404)
    return e
