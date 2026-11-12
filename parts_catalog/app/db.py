"""SQLite storage: data/garage.db, with uploaded files in data/documents/."""
import json
import os
import sqlite3
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("PARTS_CATALOG_DATA") or os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "garage.db")
DOC_DIR = os.path.join(DATA_DIR, "documents")

STATUSES = ["Needed", "Check", "Ordered", "On hand", "Installed"]
KINDS = ["Car", "Truck", "Motorcycle", "Other"]
DOC_KINDS = ["Manual", "Receipt", "Photo", "Other"]
PROJECT_STATUSES = ["Planned", "In progress", "Done"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS cars (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT DEFAULT 'Car',
  year INTEGER, make TEXT, model TEXT, engine TEXT, transmission TEXT,
  vin TEXT, plate TEXT,
  mileage INTEGER, mileage_date TEXT,
  sort_order INTEGER, archived INTEGER DEFAULT 0, photo_id INTEGER,
  notes TEXT DEFAULT '',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS mileage_log (
  id INTEGER PRIMARY KEY,
  car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  mileage INTEGER NOT NULL,
  source TEXT DEFAULT 'manual'
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  car_id INTEGER REFERENCES cars(id) ON DELETE SET NULL,
  kind TEXT DEFAULT 'Other',
  title TEXT NOT NULL,
  filename TEXT NOT NULL,
  original_name TEXT,
  size INTEGER,
  notes TEXT DEFAULT '',
  uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY,
  car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  interval_miles INTEGER,
  interval_months INTEGER,
  last_date TEXT,
  last_mileage INTEGER,
  base_date TEXT,      -- "last done" entered by hand; logged services override it
  base_mileage INTEGER,
  doc_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  doc_page INTEGER,
  notes TEXT DEFAULT '',
  active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS services (
  id INTEGER PRIMARY KEY,
  car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  mileage INTEGER,
  title TEXT NOT NULL,
  notes TEXT DEFAULT '',
  cost REAL,
  done_by TEXT DEFAULT 'DIY',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS service_schedules (
  service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
  PRIMARY KEY (service_id, schedule_id)
);
CREATE TABLE IF NOT EXISTS parts (
  id INTEGER PRIMARY KEY,
  car_id INTEGER REFERENCES cars(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  category TEXT DEFAULT '',
  part_number TEXT DEFAULT '',
  brand TEXT DEFAULT '',
  qty INTEGER DEFAULT 1,
  status TEXT DEFAULT 'Needed',
  supplier TEXT DEFAULT '',
  price REAL,
  url TEXT DEFAULT '',
  order_ref TEXT DEFAULT '',
  purchased_date TEXT,
  location TEXT DEFAULT '',
  installed_mileage INTEGER,
  installed_date TEXT,
  service_id INTEGER REFERENCES services(id) ON DELETE SET NULL,
  document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  notes TEXT DEFAULT '',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  notes TEXT DEFAULT '',
  status TEXT DEFAULT 'Planned',
  target_date TEXT,
  done_date TEXT,
  sort_order INTEGER,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS project_tasks (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  done INTEGER DEFAULT 0,
  sort_order INTEGER
);
CREATE INDEX IF NOT EXISTS ix_parts_car ON parts(car_id);
CREATE INDEX IF NOT EXISTS ix_services_car ON services(car_id);
"""

_local = threading.local()


def connect():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        _local.conn = conn
    return conn


def apply_pending_restore():
    """Swap in data/restore-pending/ (written by /api/restore) before any database is opened."""
    import shutil
    pending = os.path.join(DATA_DIR, "restore-pending")
    if not os.path.exists(os.path.join(pending, "garage.db")):
        return False
    epc = os.path.join(DATA_DIR, "epc")
    for p in (DB_PATH, DB_PATH + "-wal", DB_PATH + "-shm", DOC_DIR, os.path.join(epc, "epc.db"),
              os.path.join(epc, "epc.db-wal"), os.path.join(epc, "epc.db-shm"), os.path.join(epc, "files"),
              os.path.join(DATA_DIR, "workshop")):
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)
    for name in ("garage.db", "documents", os.path.join("epc", "epc.db"), os.path.join("epc", "files"), "workshop"):
        src = os.path.join(pending, name)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(os.path.join(DATA_DIR, name)), exist_ok=True)
            shutil.move(src, os.path.join(DATA_DIR, name))
    shutil.rmtree(pending, ignore_errors=True)
    return True


def init():
    os.makedirs(DOC_DIR, exist_ok=True)
    conn = connect()
    conn.executescript(SCHEMA)
    add_missing_columns(conn)
    conn.commit()


def add_missing_columns(conn):
    """Columns added after a database was created (CREATE TABLE IF NOT EXISTS won't add them)."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(parts)")}
    if "category" not in have:
        conn.execute("ALTER TABLE parts ADD COLUMN category TEXT DEFAULT ''")
    if "project_id" not in have:
        conn.execute("ALTER TABLE parts ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
    have = {r[1] for r in conn.execute("PRAGMA table_info(schedules)")}
    for col, ddl in (("sort_order", "INTEGER"), ("snoozed_until", "TEXT")):  # manual order; "dismissed until" date
        if col not in have:
            conn.execute(f"ALTER TABLE schedules ADD COLUMN {col} {ddl}")
    have = {r[1] for r in conn.execute("PRAGMA table_info(cars)")}
    for col, ddl in (("sort_order", "INTEGER"), ("archived", "INTEGER DEFAULT 0"),
                     ("photo_id", "INTEGER REFERENCES documents(id) ON DELETE SET NULL")):
        if col not in have:
            conn.execute(f"ALTER TABLE cars ADD COLUMN {col} {ddl}")


def insert(conn, table, values):
    cols = ",".join(values)
    return conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({','.join('?' * len(values))})",
                        list(values.values())).lastrowid


def rows(sql, args=()):
    return [dict(r) for r in connect().execute(sql, args).fetchall()]


def row(sql, args=()):
    r = connect().execute(sql, args).fetchone()
    return dict(r) if r else None


def execute(sql, args=()):
    conn = connect()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur
