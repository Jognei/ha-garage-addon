"""Fixes from the 2026-11-11 QoL audit: bad dates, mileage-less services, restore staging, urgency over drag order,
project finish dates, and agent argument checks."""
import datetime as dt
import io
import json
import os
import re
import shutil
import tempfile
import time
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("PARTS_CATALOG_DATA", tempfile.mkdtemp())

from app import db, epc, epc_api, ha_sensors, server  # noqa: E402
from app.server import app  # noqa: E402


def ago(days):
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


class AuditFixesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.saved = (db.DATA_DIR, db.DB_PATH, db.DOC_DIR, epc.EPC_DIR, epc.EPC_DB, epc.FILES_DIR, server.SETTINGS_PATH)
        db._local.conn = None
        epc.close()
        db.DATA_DIR = cls.tmp
        db.DB_PATH = os.path.join(cls.tmp, "garage.db")
        db.DOC_DIR = os.path.join(cls.tmp, "documents")
        epc.EPC_DIR = os.path.join(cls.tmp, "epc")
        epc.EPC_DB = os.path.join(epc.EPC_DIR, "epc.db")
        epc.FILES_DIR = os.path.join(epc.EPC_DIR, "files")
        server.SETTINGS_PATH = os.path.join(cls.tmp, "settings.json")
        db.init()
        epc_api.init()
        app.config["TESTING"] = True
        cls.c = app.test_client()

    @classmethod
    def tearDownClass(cls):
        db.connect().close()
        db._local.conn = None
        epc.close()
        (db.DATA_DIR, db.DB_PATH, db.DOC_DIR, epc.EPC_DIR, epc.EPC_DB, epc.FILES_DIR, server.SETTINGS_PATH) = cls.saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def car(self, name, mileage=None):
        return self.c.post("/api/cars", json={"name": name, "mileage": mileage}).get_json()["id"]

    def backup(self):
        with self.c.get("/api/backup") as r:
            return r.data

    def upload(self, data, purpose, name="b.zip"):
        uid = self.c.post("/api/documents/chunked", json={"filename": name, "size": len(data), "purpose": purpose}).get_json()["upload_id"]
        for off in range(0, len(data), 4096):
            self.c.put(f"/api/documents/chunked/{uid}?offset={off}", data=data[off:off + 4096], content_type="application/octet-stream")
        return uid

    def restore(self, data):
        return self.c.post("/api/restore", data={"upload": self.upload(data, "restore")})

    def safety_zips(self):
        return {f: os.stat(os.path.join(self.tmp, f)).st_mtime_ns for f in os.listdir(self.tmp) if f.startswith("before-restore-")}

    def assert_restore_changed_nothing(self, r, before):
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.get_json()["error"].startswith("Restore failed, nothing was changed: "), r.get_json())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "restore-pending")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "restore-staging")))
        self.assertEqual(self.safety_zips(), before)

    def test_bad_dates_are_refused_and_old_ones_tolerated(self):
        cid = self.car("DEMO-DATES", 1000)
        for path, payload in ((f"/api/cars/{cid}/mileage", {"mileage": 1200, "date": "11/11/2026"}),
                              (f"/api/cars/{cid}/services", {"title": "Wash", "date": "yesterday"}),
                              ("/api/parts", {"name": "Filter", "purchased_date": "2026/11/11"})):
            r = self.c.post(path, json=payload)
            self.assertEqual(r.status_code, 400, path)
            self.assertIn("like 2026-11-12", r.get_json()["error"])
        # a bad date already in the database must not take the garage down
        self.c.post(f"/api/cars/{cid}/schedules", json={"items": [{"title": "Oil", "interval_months": 12, "last_date": "2026-01-01"}]})
        db.execute("UPDATE schedules SET last_date='yesterday' WHERE car_id=?", (cid,))
        db.execute("INSERT INTO mileage_log (car_id, date, mileage) VALUES (?, '2026/11/11', 1100)", (cid,))
        self.assertEqual(self.c.get("/api/cars").status_code, 200)
        self.assertEqual(self.c.get(f"/api/cars/{cid}").status_code, 200)

    def test_dates_and_intervals_out_of_range_are_refused(self):
        cid = self.car("DEMO-RANGE")
        for item, field in (({"title": "Oil", "interval_months": 12, "last_date": "9999-12-31"}, "Last done"),
                            ({"title": "Oil", "interval_months": 100000}, "Every (months)"),
                            ({"title": "Oil", "interval_miles": -5000}, "Every (miles)")):
            r = self.c.post(f"/api/cars/{cid}/schedules", json={"items": [item]})
            self.assertEqual(r.status_code, 400, item)
            self.assertIn(field, r.get_json()["error"])

    def test_a_schedule_that_cannot_be_counted_reads_unknown(self):
        cid = self.car("DEMO-UNCOUNTABLE", 1000)
        for title, months, last in (("Year 9999", 12, "9999-12-31"), ("Huge interval", 100000, "2026-01-01")):
            db.execute("INSERT INTO schedules (car_id, title, interval_months, last_date) VALUES (?,?,?,?)", (cid, title, months, last))
        self.assertEqual(self.c.get("/api/cars").status_code, 200)
        for u in self.c.get(f"/api/cars/{cid}").get_json()["upcoming"]:
            self.assertEqual((u["state"], u["due_date"], u["days_left"]), ("unknown", None, None), u["title"])

    def test_slow_driving_gives_no_estimate_instead_of_a_crash(self):
        cid = self.car("DEMO-GARAGE-QUEEN")
        server.record_mileage(cid, 1000, (dt.date.today() - dt.timedelta(days=100)).isoformat())
        server.record_mileage(cid, 1001)  # one mile in 100 days
        self.c.post(f"/api/cars/{cid}/schedules", json={"items": [{"title": "Timing belt", "interval_miles": 60000, "last_mileage": 1000}]})
        self.assertEqual(self.c.get("/api/cars").status_code, 200)
        u = self.c.get(f"/api/cars/{cid}").get_json()["upcoming"][0]
        self.assertEqual((u["miles_left"], u["est_date"], u["state"]), (59999, None, "ok"))

    def test_purchase_with_a_bad_item_stores_nothing(self):
        cid = self.car("DEMO-PURCHASE")
        data = {"car_id": cid, "supplier": "Parts Store", "items": [{"name": "Pads"}, {"name": "Rotor", "purchased_date": "yesterday"}]}
        r = self.c.post("/api/purchases", data={"data": json.dumps(data), "receipt": (io.BytesIO(b"\x89PNG fake"), "order.png")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertIn("like 2026-11-12", r.get_json()["error"])
        self.assertIsNone(db.row("SELECT id FROM documents WHERE car_id=?", (cid,)))
        self.assertIsNone(db.row("SELECT id FROM parts WHERE car_id=?", (cid,)))

    def test_adding_schedules_with_a_bad_one_stores_none(self):
        cid = self.car("DEMO-SCHED-BATCH")
        r = self.c.post(f"/api/cars/{cid}/schedules", json={"items": [{"title": "Oil", "interval_months": 12},
                                                                      {"title": "Coolant", "interval_months": 24, "last_date": "yesterday"}]})
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(db.row("SELECT id FROM schedules WHERE car_id=?", (cid,)))

    def test_a_schedule_without_a_due_point_reads_unknown(self):
        cid = self.car("DEMO-NO-DUE", 200000)
        cases = [({"title": "Valve adjustment", "interval_miles": 15000, "last_date": "2026-01-01"}, "unknown"),
                 ({"title": "Wiper blades", "interval_months": 12, "last_mileage": 190000}, "unknown"),
                 ({"title": "Differential fluid", "interval_miles": 60000}, "unknown"),
                 ({"title": "Detail", "last_date": "2026-01-01"}, "ok")]  # no interval: nothing to count, nothing missing
        ids = self.c.post(f"/api/cars/{cid}/schedules", json={"items": [i for i, _ in cases]}).get_json()["ids"]
        self.c.post(f"/api/cars/{cid}/services", json={"title": "Differential fluid", "date": "2026-01-01", "schedule_ids": [ids[2]]})
        up = {u["id"]: u for u in self.c.get(f"/api/cars/{cid}").get_json()["upcoming"]}
        for sid, (item, state) in zip(ids, cases):
            self.assertEqual(up[sid]["state"], state, item["title"])

    def test_agents_and_home_assistant_tell_never_done_from_done_without_a_due_point(self):
        cid = self.car("DEMO-HALF-DONE", 12000)
        self.c.post(f"/api/cars/{cid}/schedules", json={"items": [{"title": "Chain", "interval_miles": 500},
                                                                  {"title": "Valve adjustment", "interval_miles": 15000, "last_date": "2026-03-01"},
                                                                  {"title": "Brake fluid", "interval_months": 24, "last_mileage": 11000}]})
        self.c.put("/api/settings", json={"agent_key": "new"})
        key = self.c.get("/api/settings").get_json()["settings"]["agent_key"]
        r = self.c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "whats_due", "arguments": {"vehicle": "DEMO-HALF-DONE"}}},
                        headers={"Authorization": f"Bearer {key}"}).get_json()["result"]
        due = json.loads(r["content"][0]["text"])[0]
        self.assertEqual({k: due.get(k) for k in ("never_done", "missing_last_mileage", "missing_last_date")},
                         {"never_done": ["Chain"], "missing_last_mileage": ["Valve adjustment"], "missing_last_date": ["Brake fluid"]})
        states = ha_sensors.build_states(self.c.get("/api/ha/summary").get_json())
        self.assertEqual(states["sensor.garage_demo_half_done_status"]["state"], "OK")

    def test_a_newer_baseline_beats_an_older_service_mileage(self):
        cid = self.car("DEMO-BASELINE", 103000)
        sid = self.c.post(f"/api/cars/{cid}/schedules", json={"items": [
            {"title": "Engine oil & filter", "interval_miles": 5000, "last_date": "2026-06-01", "last_mileage": 100000}]}).get_json()["ids"][0]
        self.c.post(f"/api/cars/{cid}/services", json={"title": "Oil change", "date": "2026-01-01", "mileage": 90000, "schedule_ids": [sid]})
        self.c.post(f"/api/cars/{cid}/services", json={"title": "Oil top-up", "schedule_ids": [sid]})
        up = next(u for u in self.c.get(f"/api/cars/{cid}").get_json()["upcoming"] if u["id"] == sid)
        self.assertEqual((up["last_mileage"], up["state"]), (100000, "ok"))

    def test_values_that_are_not_clean_are_refused(self):
        cid = self.car("DEMO-NUMBERS", 1000)
        pid = self.c.post("/api/parts", json={"name": "Pads", "status": "On hand"}).get_json()["id"]
        for method, path, payload, field in (
                ("POST", f"/api/cars/{cid}/schedules", {"title": "Oil", "interval_miles": "5k"}, "Every (miles)"),
                ("POST", f"/api/cars/{cid}/services", {"title": "Oil", "mileage": "150.000"}, "Mileage"),
                ("POST", f"/api/cars/{cid}/services", {"title": "Oil", "cost": "1.234,56"}, "Cost"),
                ("PUT", f"/api/parts/{pid}", {"qty": "7.500"}, "Qty"),
                ("PUT", f"/api/parts/{pid}", {"price": "abc"}, "Price"),
                ("PUT", f"/api/parts/{pid}", {"status": "installed"}, "Status"),
                ("PUT", f"/api/cars/{cid}", {"name": "  "}, "Name is required"),
                ("POST", "/api/cars", {"name": "DEMO-BAD-MILES", "mileage": "5k"}, "Current mileage")):
            r = self.c.open(path, method=method, json=payload)
            self.assertEqual(r.status_code, 400, payload)
            self.assertIn(field, r.get_json()["error"])
        p = self.c.get(f"/api/parts/{pid}").get_json()
        self.assertEqual((p["qty"], p["price"], p["status"]), (1, None, "On hand"))
        self.assertEqual(self.c.get(f"/api/cars/{cid}/services").get_json(), [])
        self.assertNotIn("DEMO-BAD-MILES", [c["name"] for c in self.c.get("/api/cars").get_json()])

    def test_numbers_that_are_not_finite_are_refused(self):
        cid = self.car("DEMO-INFINITE", 1000)
        try:
            app.config["PROPAGATE_EXCEPTIONS"] = False  # as in production; tests otherwise re-raise
            for raw in ("Infinity", "NaN", "1e400"):
                r = self.c.post(f"/api/cars/{cid}/mileage", data=f'{{"mileage": {raw}}}', content_type="application/json")
                self.assertEqual(r.status_code, 400, raw)
                self.assertIn("Mileage", r.get_json()["error"])
        finally:
            app.config["PROPAGATE_EXCEPTIONS"] = None

    def test_a_hand_edited_card_setting_does_not_take_the_garage_down(self):
        saved = server.load_settings()
        try:
            server.save_settings({**saved, "card": {"upcoming_rows": "lots"}})
            self.assertEqual(self.c.get("/api/cars").status_code, 200)
        finally:
            server.save_settings(saved)

    def test_numbers_as_people_type_them(self):
        cid = self.car("DEMO-TYPED", " 150,000 mi ")
        self.assertEqual(self.c.get(f"/api/cars/{cid}").get_json()["mileage"], 150000)
        self.c.post(f"/api/cars/{cid}/services", json={"title": "Brake pads", "cost": "24,99"})
        self.assertEqual(self.c.get(f"/api/cars/{cid}/services").get_json()[0]["cost"], 24.99)
        pid = self.c.post("/api/parts", json={"name": "Rotor", "price": " $1,234.50 ", "qty": "2"}).get_json()["id"]
        p = self.c.get(f"/api/parts/{pid}").get_json()
        self.assertEqual((p["price"], p["qty"]), (1234.5, 2))

    def test_api_errors_answer_json(self):
        limit = app.config["MAX_CONTENT_LENGTH"]
        uid = self.c.post("/api/documents/chunked", json={"filename": "manual.pdf", "size": 3 * 1024 * 1024}).get_json()["upload_id"]
        try:
            app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
            r = self.c.put(f"/api/documents/chunked/{uid}?offset=0", data=b"x" * 2 * 1024 * 1024, content_type="application/octet-stream")
        finally:
            app.config["MAX_CONTENT_LENGTH"] = limit
        self.assertEqual(r.status_code, 413)
        self.assertIn("1 MB", r.get_json()["error"])
        try:
            app.config["PROPAGATE_EXCEPTIONS"] = False  # as in production; tests otherwise re-raise
            with mock.patch.object(server, "ha_summary", side_effect=RuntimeError("boom")), self.assertLogs(app.logger, "ERROR") as logs:
                r = self.c.get("/api/ha/summary")
        finally:
            app.config["PROPAGATE_EXCEPTIONS"] = None
        self.assertEqual(r.status_code, 500)
        self.assertIn("add-on log", r.get_json()["error"])
        self.assertIsNotNone(logs.records[0].exc_info)  # the traceback still reaches the log

    def test_unreadable_photo_is_refused_and_removed(self):
        cid = self.car("DEMO-HEIC")
        files = set(os.listdir(db.DOC_DIR))
        r = self.c.post(f"/api/cars/{cid}/photo", data={"photo": (io.BytesIO(b"ftypheic, not for Pillow"), "IMG_0001.HEIC")},
                        content_type="multipart/form-data")
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "Couldn't read that photo. Save it as JPEG or PNG."))
        self.assertEqual(set(os.listdir(db.DOC_DIR)), files)
        self.assertIsNone(db.row("SELECT id FROM documents WHERE car_id=? AND kind='Photo'", (cid,)))
        self.assertIsNone(self.c.get(f"/api/cars/{cid}").get_json()["photo_id"])

    def test_a_file_for_a_vehicle_id_that_is_not_a_number_is_refused_before_it_is_kept(self):
        files = set(os.listdir(db.DOC_DIR))
        r = self.c.post("/api/documents", data={"car_id": "Wagon", "kind": "Manual", "file": (io.BytesIO(b"%PDF-1.4"), "m.pdf")},
                        content_type="multipart/form-data")
        self.assertEqual((r.status_code, set(os.listdir(db.DOC_DIR))), (400, files))
        r = self.c.post("/api/documents/chunked", json={"filename": "big.pdf", "size": 8, "car_id": "Wagon"})  # before it is sent
        self.assertEqual((r.status_code, r.get_json().get("error")), (400, "Vehicle must be a number, not 'Wagon'"))

    def test_a_photo_sent_in_pieces_is_the_vehicle_photo(self):
        from PIL import Image
        cid = self.car("DEMO-BIG-PHOTO")
        png = io.BytesIO()
        Image.new("RGB", (2400, 1800), "red").save(png, "PNG")
        r = self.c.post(f"/api/cars/{cid}/photo", data={"upload": self.upload(png.getvalue(), "photo", "car.png")})
        self.assertEqual(r.status_code, 200, r.get_json())
        photo = db.row("SELECT d.* FROM documents d JOIN cars c ON c.photo_id=d.id WHERE c.id=?", (cid,))
        self.assertEqual((photo["kind"], photo["original_name"]), ("Photo", "car.jpg"))
        with Image.open(os.path.join(db.DOC_DIR, photo["filename"])) as im:
            self.assertEqual((im.format, im.size), ("JPEG", (1600, 1200)))  # shrunk, as a photo sent whole is

    def test_a_receipt_sent_in_pieces_is_the_purchases_receipt(self):
        cid = self.car("DEMO-BIG-RECEIPT")
        scan = b"%PDF-1.4 " + bytes(range(256)) * 40
        data = {"car_id": cid, "supplier": "Parts Store", "items": [{"name": "Pads"}, {"name": "Rotor"}]}
        r = self.c.post("/api/purchases", data={"data": json.dumps(data), "upload": self.upload(scan, "receipt", "order.pdf")}).get_json()
        self.assertIsNotNone(r["document_id"])
        self.assertEqual({self.c.get(f"/api/parts/{pid}").get_json()["document_id"] for pid in r["ids"]}, {r["document_id"]})
        doc = db.row("SELECT * FROM documents WHERE id=?", (r["document_id"],))
        self.assertEqual((doc["kind"], doc["car_id"], doc["original_name"]), ("Receipt", cid, "order.pdf"))
        with self.c.get(f"/files/{doc['id']}/order.pdf") as f:
            self.assertEqual(f.data, scan)

    def test_an_upload_is_only_taken_for_what_it_was_sent_for(self):
        cid = self.car("DEMO-PURPOSE")
        backup, manual = self.upload(self.backup(), "restore"), self.upload(b"%PDF-1.4 manual", "document", "manual.pdf")
        docs = db.row("SELECT COUNT(*) AS n FROM documents")["n"]
        purchase = json.dumps({"car_id": cid, "items": [{"name": "Pads"}]})
        for path, form, error in ((f"/api/documents/chunked/{backup}/finish", {}, "That upload is for restore, not document"),
                                  (f"/api/cars/{cid}/photo", {"upload": backup}, "That upload is for restore, not photo"),
                                  ("/api/purchases", {"data": purchase, "upload": backup}, "That upload is for restore, not receipt"),
                                  ("/api/epc/import", {"upload": backup}, "That upload is for restore, not catalog"),
                                  ("/api/restore", {"upload": manual}, "That upload is for document, not restore")):
            with self.subTest(path):
                r = self.c.post(path, data=form)
                self.assertEqual((r.status_code, r.get_json()["error"]), (400, error))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "restore-pending")))
        self.assertEqual(db.row("SELECT COUNT(*) AS n FROM documents")["n"], docs)
        self.assertIsNone(db.row("SELECT id FROM parts WHERE car_id=?", (cid,)))
        # refused, so still there for what it was sent for, and used up once used
        self.assertEqual(self.c.post(f"/api/documents/chunked/{manual}/finish").status_code, 200)
        self.assertEqual(self.c.post(f"/api/documents/chunked/{manual}/finish").status_code, 404)

    def test_abandoned_uploads_of_every_purpose_are_swept(self):
        old = [self.upload(b"x", purpose, "x.bin") for purpose in server.UPLOAD_PURPOSES]
        long_ago = time.time() - 2 * 86400 - 60
        for uid in old:
            for p in server.upload_paths(uid):
                os.utime(p, (long_ago, long_ago))
        self.upload(b"x", "document", "x.bin")  # starting one sweeps
        self.assertEqual([uid for uid in old if any(os.path.exists(p) for p in server.upload_paths(uid))], [])

    def test_backup_is_sent_from_disk_and_removed_once_sent(self):
        made, real = [], tempfile.mkdtemp

        def mkdtemp(*a, **k):
            made.append(real(*a, **k))
            return made[-1]
        with mock.patch("tempfile.mkdtemp", mkdtemp):
            r = self.c.get("/api/backup")
        with open(os.path.join(made[0], "backup.zip"), "ab") as f:
            f.write(b"read from disk")  # only a response reading the file as it sends sees this
        self.assertTrue(r.data.endswith(b"read from disk"))
        r.close()
        self.assertFalse(os.path.exists(made[0]))

    def test_restore_keeps_only_the_newest_safety_copy(self):
        for old in ("before-restore-20200101-000000.zip", "before-restore-20200102-000000.zip.part"):
            with open(os.path.join(self.tmp, old), "wb") as f:
                f.write(b"older")
        self.assertEqual(self.restore(self.backup()).status_code, 200)
        shutil.rmtree(os.path.join(self.tmp, "restore-pending"))
        kept = list(self.safety_zips())
        self.assertEqual(len(kept), 1, kept)
        self.assertIn("garage.db", zipfile.ZipFile(os.path.join(self.tmp, kept[0])).namelist())

    def test_restore_takes_the_backups_layout_and_keeps_this_installs_access(self):
        shown = ("card", "quick_list", "quick_own", "quick_services", "catalog")
        theirs = {"card": {**server.CARD_DEFAULTS, "upcoming_rows": 7}, "quick_list": ["Wash", "Chain lube"], "quick_own": ["Chain lube"],
                  "quick_services": ["Chain lube"], "catalog": True,
                  "lan": True, "port": 9999, "agent_key": "theirs", "wash": {"weather_entity": "weather.theirs"}}
        mine = {"card": {**server.CARD_DEFAULTS, "upcoming_rows": 2}, "quick_list": ["Wash"], "quick_own": [], "quick_services": [],
                "catalog": False, "lan": False, "port": 8765, "agent_key": "mine", "wash": {"weather_entity": "weather.mine"}}
        saved = server.load_settings()
        try:
            server.save_settings({**saved, **theirs})
            backup = self.backup()
            server.save_settings({**saved, **mine})
            self.assertEqual(self.restore(backup).status_code, 200)
            self.assertEqual(server.load_settings(), {**saved, **mine})  # nothing yet: it comes with the data, at the next start
            db.connect().close()
            db._local.conn = None
            epc.close()
            self.assertTrue(db.apply_pending_restore())
            db.init()
            epc_api.init()
            s = server.load_settings()
            self.assertEqual({k: s[k] for k in shown}, {k: theirs[k] for k in shown})
            self.assertEqual({k: s[k] for k in ("lan", "port", "agent_key", "wash")}, {k: mine[k] for k in ("lan", "port", "agent_key", "wash")})
        finally:
            server.save_settings(saved)

    def test_restore_takes_the_layout_a_backup_left_at_its_defaults(self):
        saved = server.load_settings()
        try:
            for theirs, quick in ((["Chain lube"], ["Chain lube"] + server.QUICK_DEFAULT),  # older quick services, cards never changed
                                  (None, server.QUICK_DEFAULT)):  # settings never changed: no settings.json in the backup
                with self.subTest(theirs=theirs):
                    if os.path.exists(server.SETTINGS_PATH):
                        os.remove(server.SETTINGS_PATH)
                    if theirs:
                        self.c.put("/api/settings", json={"quick_services": theirs})
                    backup = self.backup()
                    server.save_settings({**saved, "card": {**server.CARD_DEFAULTS, "hide_ok": True}, "quick_list": ["Fuel fill-up"],
                                          "catalog": True})
                    self.assertEqual(self.restore(backup).status_code, 200)
                    db.connect().close()
                    db._local.conn = None
                    epc.close()
                    self.assertTrue(db.apply_pending_restore())
                    db.init()
                    epc_api.init()
                    ref = self.c.get("/api/refdata").get_json()
                    self.assertEqual((ref["card"], ref["quick_services"], ref["features"]["catalog"]), (server.CARD_DEFAULTS, quick, False))
        finally:
            server.save_settings(saved)

    def test_service_without_mileage_keeps_the_last_known_mileage(self):
        cid = self.car("DEMO-MILES", 200000)
        sid = self.c.post(f"/api/cars/{cid}/schedules", json={"items": [
            {"title": "Valve adjustment", "interval_miles": 15000, "last_date": "2025-01-01", "last_mileage": 150000}]}).get_json()["ids"][0]
        self.c.post(f"/api/cars/{cid}/services", json={"title": "Valve adjustment", "date": "2026-01-01", "schedule_ids": [sid]})
        up = next(u for u in self.c.get(f"/api/cars/{cid}").get_json()["upcoming"] if u["id"] == sid)
        self.assertEqual((up["last_mileage"], up["state"]), (150000, "overdue"))

    def test_failed_restore_changes_nothing(self):
        self.car("DEMO-KEEP")
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("garage.db", b"not a database")
        before = self.safety_zips()
        r = self.restore(bad.getvalue())
        self.assertEqual(r.status_code, 400)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "restore-pending")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "restore-staging")))
        self.assertEqual(self.safety_zips(), before)

    def test_restore_that_fails_to_unpack_changes_nothing(self):
        garage = zipfile.ZipFile(io.BytesIO(self.backup())).read("garage.db")
        for why, method, where, value in (("bad CRC", zipfile.ZIP_STORED, "data", ord("X")),
                                          ("corrupt deflate stream", zipfile.ZIP_DEFLATED, "data", 0xFF),
                                          ("encrypted", zipfile.ZIP_STORED, "flags", 1),
                                          ("unknown compression", zipfile.ZIP_STORED, "method", 99)):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("garage.db", garage, zipfile.ZIP_DEFLATED)
                z.writestr("documents/manual.pdf", b"%PDF-1.4 " * 200, method)
                info = z.getinfo("documents/manual.pdf")
            raw = bytearray(buf.getvalue())
            central = raw.rindex(b"PK\x01\x02")
            raw[{"data": info.header_offset + 30 + len(info.filename), "flags": central + 8, "method": central + 10}[where]] = value
            with self.subTest(why):
                before = self.safety_zips()
                self.assert_restore_changed_nothing(self.restore(bytes(raw)), before)

    def test_restore_that_cannot_write_its_safety_copy_changes_nothing(self):
        def disk_full(out):
            with open(out, "wb") as f:
                f.write(b"PK partial")
            raise OSError(28, "No space left on device")
        before = self.safety_zips()
        backup = self.backup()
        with mock.patch.object(server, "write_backup", disk_full):
            self.assert_restore_changed_nothing(self.restore(backup), before)

    def test_restore_refuses_a_damaged_catalog(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.backup())) as src, zipfile.ZipFile(buf, "w") as z:
            z.writestr("garage.db", src.read("garage.db"))
            z.writestr("epc/epc.db", b"not a database")
        r = self.restore(buf.getvalue())
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "The catalog database in that backup is damaged"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "restore-pending")))

    def test_interrupted_restore_finishes_at_the_next_start(self):
        cid = self.car("DEMO-RESTORED")
        did = self.c.post("/api/documents", data={"car_id": str(cid), "kind": "Other", "title": "Receipt",
                                                   "file": (io.BytesIO(b"kept"), "r.txt")}, content_type="multipart/form-data").get_json()["ids"][0]
        with open(os.path.join(epc.FILES_DIR, "diagram.svg"), "w", encoding="utf-8") as f:
            f.write("<svg/>")
        backup = self.backup()
        real_move = shutil.move
        for cut in range(3):  # documents/, epc/, garage.db
            with self.subTest(cut_at_move=cut):
                self.car("DEMO-AFTER-BACKUP")
                self.assertEqual(self.restore(backup).status_code, 200)
                db.connect().close()
                db._local.conn = None
                epc.close()
                moves = []

                def move(src, dst):
                    moves.append(src)
                    if len(moves) == cut + 1:
                        raise OSError("power cut")
                    return real_move(src, dst)
                os.makedirs(os.path.join(self.tmp, "restore-staging"))  # an upload that was cut short
                with mock.patch("shutil.move", move):
                    self.assertRaises(OSError, db.apply_pending_restore)
                self.assertTrue(db.apply_pending_restore())
                self.assertFalse(os.path.exists(os.path.join(self.tmp, "restore-staging")))
                db.init()
                epc_api.init()
                names = [c["name"] for c in self.c.get("/api/cars").get_json()]
                self.assertIn("DEMO-RESTORED", names)
                self.assertNotIn("DEMO-AFTER-BACKUP", names)
                f = self.c.get(f"/files/{did}/r.txt")
                self.assertEqual(f.data, b"kept")
                f.close()
                self.assertTrue(os.path.exists(os.path.join(epc.FILES_DIR, "diagram.svg")))

    def test_restore_without_files_leaves_none_of_the_current_ones(self):
        cid = self.car("DEMO-NO-FILES")
        self.c.post("/api/documents", data={"car_id": str(cid), "kind": "Other", "title": "Old",
                                            "file": (io.BytesIO(b"old"), "old.txt")}, content_type="multipart/form-data")
        with open(os.path.join(epc.FILES_DIR, "old.svg"), "w", encoding="utf-8") as f:
            f.write("<svg/>")
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.backup())) as src, zipfile.ZipFile(buf, "w") as z:
            z.writestr("garage.db", src.read("garage.db"))
        self.assertEqual(self.restore(buf.getvalue()).status_code, 200)
        db.connect().close()
        db._local.conn = None
        epc.close()
        self.assertTrue(db.apply_pending_restore())
        db.init()
        epc_api.init()
        self.assertEqual(os.listdir(db.DOC_DIR), [])
        self.assertFalse(os.path.exists(os.path.join(epc.FILES_DIR, "old.svg")))

    def test_overdue_leads_even_after_a_drag(self):
        cid = self.car("DEMO-ORDER", 50000)
        today = dt.date.today()
        ids = self.c.post(f"/api/cars/{cid}/schedules", json={"items": [
            {"title": "Coolant", "interval_months": 48, "last_date": today.isoformat()},
            {"title": "Tires", "interval_months": 24, "last_date": today.isoformat()},
            {"title": "Wipers", "interval_months": 12, "last_date": (today - dt.timedelta(days=500)).isoformat()}]}).get_json()["ids"]
        self.c.post(f"/api/cars/{cid}/schedules/order", json={"ids": [ids[1], ids[0], ids[2]]})
        titles = [u["title"] for u in self.c.get(f"/api/cars/{cid}").get_json()["upcoming"]]
        self.assertEqual(titles, ["Wipers", "Tires", "Coolant"])  # overdue first, then the dragged order

    def test_drag_order_applies_within_each_state(self):
        cid = self.car("DEMO-GROUPS", 50000)
        ids = self.c.post(f"/api/cars/{cid}/schedules", json={"items": [
            {"title": "Oil", "interval_miles": 5000, "last_mileage": 40000},              # overdue
            {"title": "Tires", "interval_miles": 10000, "last_mileage": 48000},           # ok, 8,000 of 10,000 left
            {"title": "Coolant", "interval_miles": 30000, "last_mileage": 45000},         # ok, 25,000 of 30,000 left
            {"title": "Spark plugs", "interval_miles": 60000, "last_mileage": 45000},     # ok, not dragged
            {"title": "Brakes", "interval_miles": 10000, "last_mileage": 30000,
             "snoozed_until": (dt.date.today() + dt.timedelta(days=30)).isoformat()},
            {"title": "Wipers", "interval_miles": 10000}]}).get_json()["ids"]             # never done
        _, tires, coolant, _, brakes, wipers = ids
        self.c.post(f"/api/cars/{cid}/schedules/order", json={"ids": [wipers, brakes, coolant, tires]})
        titles = [u["title"] for u in self.c.get(f"/api/cars/{cid}").get_json()["upcoming"]]
        self.assertEqual(titles, ["Oil", "Coolant", "Tires", "Spark plugs", "Brakes", "Wipers"])

    def test_editing_a_done_project_keeps_its_finish_date(self):
        cid = self.car("DEMO-PROJ")
        pid = self.c.post(f"/api/cars/{cid}/projects", json={"title": "Control arms", "status": "Done",
                                                             "done_date": "2026-10-01"}).get_json()["id"]
        self.c.put(f"/api/projects/{pid}", json={"title": "Control arms", "status": "Done", "notes": "Torqued at ride height"})
        p = next(x for x in self.c.get(f"/api/cars/{cid}/projects").get_json() if x["id"] == pid)
        self.assertEqual(p["done_date"], "2026-10-01")

    def test_estimated_due_date_reaches_home_assistant_and_agents(self):
        cid = self.car("DEMO-EST", 10000)
        self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 9000, "date": (dt.date.today() - dt.timedelta(days=100)).isoformat()})
        self.c.post(f"/api/cars/{cid}/schedules", json={"items": [{"title": "Oil", "interval_miles": 5000, "last_mileage": 9500}]})
        est = self.c.get(f"/api/cars/{cid}").get_json()["upcoming"][0]["est_date"]
        self.assertIsNotNone(est)  # what the UI shows as "≈ <month>"
        summary = next(c for c in self.c.get("/api/ha/summary").get_json()["cars"] if c["id"] == cid)
        self.assertEqual(summary["next_service"]["est_date"], est)
        self.c.put("/api/settings", json={"agent_key": "new"})
        key = self.c.get("/api/settings").get_json()["settings"]["agent_key"]
        r = self.c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "get_vehicle", "arguments": {"vehicle": "DEMO-EST"}}},
                        headers={"Authorization": f"Bearer {key}"}).get_json()["result"]
        self.assertEqual(json.loads(r["content"][0]["text"])["upcoming"][0]["est_date"], est)

    def test_a_service_estimated_within_30_days_is_due_soon(self):
        states = {}
        for name, per_day in (("DEMO-EST-FAST", 100), ("DEMO-EST-SLOW", 5)):
            cid = self.car(name)
            self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 50000 - 100 * per_day, "date": ago(100)})
            self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 50000})
            self.c.post(f"/api/cars/{cid}/schedules", json={"items": [
                {"title": "Oil", "interval_miles": 5000, "last_mileage": 45900},  # 900 miles left
                {"title": "Oil, 3,000 left", "interval_miles": 5000, "last_mileage": 48000},
                {"title": "Oil, 3,100 left", "interval_miles": 5000, "last_mileage": 48100},
                {"title": "Oil, dismissed", "interval_miles": 5000, "last_mileage": 45900, "snoozed_until": ago(-14)}]})
            states[name] = {u["title"]: u["state"] for u in self.c.get(f"/api/cars/{cid}").get_json()["upcoming"]}
        # at 100 miles a day: 9, 30 and 31 days away
        self.assertEqual(states["DEMO-EST-FAST"], {"Oil": "soon", "Oil, 3,000 left": "soon", "Oil, 3,100 left": "ok",
                                                   "Oil, dismissed": "snoozed"})
        self.assertEqual(set(states["DEMO-EST-SLOW"].values()), {"ok"})

    def odometers(self, *cids):
        states = ha_sensors.build_states(self.c.get("/api/ha/summary").get_json())
        return {s["attributes"]["car_id"]: (e, s["attributes"]["friendly_name"]) for e, s in states.items()
                if e.endswith("_odometer") and s["attributes"]["car_id"] in cids}

    def test_renaming_reordering_or_hiding_a_vehicle_keeps_its_sensor_ids(self):
        first, second = self.car("Work Van"), self.car("Work Van")
        self.assertEqual(self.odometers(first, second), {first: ("sensor.garage_work_van_odometer", "Work Van odometer"),
                                                         second: ("sensor.garage_work_van_2_odometer", "Work Van odometer")})
        self.c.put(f"/api/cars/{first}", json={"archived": True})
        third = self.car("Work Van")  # added while the first is hidden: the id the first holds stays taken
        self.assertEqual(self.odometers(first, second, third), {second: ("sensor.garage_work_van_2_odometer", "Work Van odometer"),
                                                                third: ("sensor.garage_work_van_3_odometer", "Work Van odometer")})
        self.c.put(f"/api/cars/{first}", json={"archived": False})
        self.c.post("/api/cars/order", json={"ids": [third, second, first]})
        self.c.put(f"/api/cars/{first}", json={"name": "Red Van"})
        self.assertEqual(self.odometers(first, second), {first: ("sensor.garage_work_van_odometer", "Red Van odometer"),
                                                         second: ("sensor.garage_work_van_2_odometer", "Work Van odometer")})
        y = self.c.get("/api/ha/dashboard").get_data(as_text=True)
        self.assertEqual(re.search(r'heading: "Red Van".*?entity: (\S+)_status', y, re.S).group(1), "sensor.garage_work_van")

    def test_a_vehicle_sensor_id_can_be_changed_under_details(self):
        cid = self.car("DEMO-SLUG")
        self.car("DEMO-SLUG-OTHER")
        self.assertEqual(self.odometers(cid)[cid][0], "sensor.garage_demo_slug_odometer")
        put = lambda s: self.c.put(f"/api/cars/{cid}", json={"name": "DEMO-SLUG", "ha_slug": s})  # noqa: E731
        for bad in ("Demo Slug", "demo-slug", "demo_slug_other", "_demo_slug", "demo_slug_", "demo__slug"):  # the last 3: HA refuses
            self.assertEqual(put(bad).status_code, 400, bad)
        self.assertIn("lower-case letters and digits, with single _ between them", put("demo_slug_").get_json()["error"])
        self.assertIn("DEMO-SLUG-OTHER", put("demo_slug_other").get_json()["error"])
        self.assertEqual(put("demo_slug").status_code, 200)  # Details sends it back unchanged on every save
        self.assertEqual(put("my_car").status_code, 200)
        self.assertEqual(self.odometers(cid)[cid][0], "sensor.garage_my_car_odometer")
        self.assertEqual(self.c.get(f"/api/cars/{cid}").get_json()["ha_slug"], "my_car")

    def test_an_existing_garage_keeps_the_sensor_ids_it_published(self):
        # before ids were stored: from the name, in garage order, hidden vehicles left out
        hidden, a, b, c = (self.car(n) for n in ("Old Wagon", "Old Wagon", "Old Wagon 2", "Old Wagon"))
        self.c.put(f"/api/cars/{hidden}", json={"archived": True})
        self.c.post("/api/cars/order", json={"ids": [hidden, c, b, a]})
        self.c.get("/api/ha/summary")
        slugs = {cid: self.c.get(f"/api/cars/{cid}").get_json()["ha_slug"] for cid in (hidden, a, b, c)}
        self.assertEqual(slugs, {hidden: None, c: "old_wagon", b: "old_wagon_2", a: "old_wagon_3"})

    def test_agent_list_arguments(self):
        cid = self.car("DEMO-AGENT", 10000)
        self.c.post(f"/api/cars/{cid}/schedules", json={"items": [{"title": "Spark plugs", "interval_miles": 60000},
                                                                  {"title": "Fuel filter", "interval_miles": 30000}]})
        self.c.put("/api/settings", json={"agent_key": "new"})
        key = self.c.get("/api/settings").get_json()["settings"]["agent_key"]

        def tool(name, **a):
            r = self.c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": a}},
                            headers={"Authorization": f"Bearer {key}"}).get_json()["result"]
            return r["content"][0]["text"], r["isError"]

        text, bad = tool("log_service", vehicle="DEMO-AGENT", title="Plugs", schedules="Spark plugs")
        self.assertFalse(bad, text)
        r = json.loads(text)
        self.assertEqual(r["schedules_reset"], 1)  # the string is one name, not 11 letters
        self.assertEqual(r["logged"]["id"] and self.c.get(f"/api/cars/{cid}/services").get_json()[0]["mileage"], 10000)
        text, bad = tool("log_service", vehicle="DEMO-AGENT", title="Plugs", part_ids="12")
        self.assertTrue(bad)

    def log(self, cid):
        return [(m["date"], m["mileage"]) for m in self.c.get(f"/api/cars/{cid}").get_json()["mileage_log"]]

    def test_a_reading_that_does_not_fit_the_log_is_not_logged(self):
        cid = self.car("DEMO-ODO-FIT")
        self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 10000, "date": ago(60)})
        self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 12000, "date": ago(30)})
        self.assertEqual(self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 11000}).get_json()["mileage"], 12000)
        self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 13000, "date": ago(45)})  # higher than a later reading
        self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 11000, "date": ago(45)})  # fits between two
        self.assertEqual(self.log(cid), [(ago(30), 12000), (ago(45), 11000), (ago(60), 10000)])
        self.assertEqual(self.c.get(f"/api/cars/{cid}").get_json()["mileage"], 12000)
        # a correction is logged, and the readings after it count from it
        self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 11500, "correct": True})
        self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 11600})
        self.assertEqual(self.log(cid)[:2], [(ago(0), 11600), (ago(0), 11500)])

    def test_saving_a_service_again_logs_its_reading_once(self):
        cid = self.car("DEMO-ODO-DUP", 20000)
        sid = self.c.post(f"/api/cars/{cid}/services", json={"title": "Wash", "mileage": 20100}).get_json()["id"]
        self.c.put(f"/api/services/{sid}", json={"title": "Wash", "mileage": 20100, "notes": "and wax"})
        self.assertEqual([m for _, m in self.log(cid)], [20100, 20000])

    def test_an_odometer_typo_can_be_corrected_and_deleted(self):
        cid = self.car("DEMO-ODO-TYPO")
        self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 148000, "date": ago(171)})
        self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 1502500})
        self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 150300, "correct": True})
        self.assertAlmostEqual(server.miles_per_day(cid), 13.45, places=2)
        log = self.c.get(f"/api/cars/{cid}").get_json()["mileage_log"]
        typo = next(m["id"] for m in log if m["mileage"] == 1502500)
        self.assertEqual(self.c.delete(f"/api/cars/{self.car('DEMO-ODO-OTHER')}/mileage/{typo}").status_code, 404)
        self.assertEqual(self.c.delete(f"/api/cars/{cid}/mileage/{typo}").get_json()["mileage"], 150300)
        self.assertEqual([m for _, m in self.log(cid)], [150300, 148000])
        self.assertAlmostEqual(server.miles_per_day(cid), 13.45, places=2)
        # deleting the newest reading takes the odometer back to the one before it
        self.c.delete(f"/api/cars/{cid}/mileage/{log[0]['id']}")
        car = self.c.get(f"/api/cars/{cid}").get_json()
        self.assertEqual((car["mileage"], car["mileage_date"]), (148000, ago(171)))

    def test_deleting_the_typo_on_the_odometer_lowers_it_when_the_log_is_out_of_order(self):
        cid = self.car("DEMO-ODO-OLD-TYPO")
        for date, mileage in (("2026-09-01", 50120), ("2026-10-01", 501200), ("2026-11-01", 50900)):  # as older versions logged them
            db.execute("INSERT INTO mileage_log (car_id, date, mileage) VALUES (?,?,?)", (cid, date, mileage))
        db.execute("UPDATE cars SET mileage=501200, mileage_date='2026-10-01' WHERE id=?", (cid,))
        typo = db.row("SELECT id FROM mileage_log WHERE car_id=? AND mileage=501200", (cid,))["id"]
        self.c.delete(f"/api/cars/{cid}/mileage/{typo}")
        car = self.c.get(f"/api/cars/{cid}").get_json()
        self.assertEqual((car["mileage"], car["mileage_date"]), (50900, "2026-11-01"))

    def test_deleting_a_service_takes_its_reading_off_the_odometer(self):
        cid = self.car("DEMO-ODO-WRONG-CAR")
        self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 30000, "date": "2026-09-01"})
        svc = self.c.post(f"/api/cars/{cid}/services", json={"title": "Oil change", "date": "2026-10-01", "mileage": 120500}).get_json()["id"]
        db.execute("INSERT INTO mileage_log (car_id, date, mileage, source) VALUES (?, '2026-10-01', 120500, 'service')", (cid,))  # saved again by an older version
        self.c.delete(f"/api/services/{svc}")
        car = self.c.get(f"/api/cars/{cid}").get_json()
        self.assertEqual((car["mileage"], car["mileage_date"], self.log(cid)), (30000, "2026-09-01", [("2026-09-01", 30000)]))
        # two services logged together share one reading: it stays while either does
        _, tires = [self.c.post(f"/api/cars/{cid}/services", json={"title": t, "date": "2026-10-02", "mileage": 30200}).get_json()["id"]
                    for t in ("Oil", "Tire rotation")]
        self.c.delete(f"/api/services/{tires}")
        self.assertEqual(self.c.get(f"/api/cars/{cid}").get_json()["mileage"], 30200)

    def last_done(self, cid, sid):
        u = next(u for u in self.c.get(f"/api/cars/{cid}").get_json()["upcoming"] if u["id"] == sid)
        return u["last_date"], u["last_mileage"]

    def test_editing_a_schedule_leaves_last_done_to_its_services(self):
        cid = self.car("DEMO-SCHED-EDIT")
        sid = self.c.post(f"/api/cars/{cid}/schedules", json={"title": "Oil", "interval_miles": 5000}).get_json()["ids"][0]
        log = lambda: self.c.post(f"/api/cars/{cid}/services", json={  # noqa: E731
            "title": "Oil", "date": "2026-10-01", "mileage": 30000, "schedule_ids": [sid]}).get_json()["id"]
        edit = lambda **kw: self.c.put(f"/api/schedules/{sid}", json={  # noqa: E731 - every field, as the Edit dialog sends
            "title": "Oil", "interval_miles": "5000", "interval_months": "", "last_date": "2026-10-01", "last_mileage": "30000",
            "doc_id": "", "doc_page": "", "notes": "", **kw})
        svc = log()
        edit(interval_miles="7500")
        self.c.delete(f"/api/services/{svc}")
        self.assertEqual(self.last_done(cid, sid), (None, None))
        # a last done typed by hand counts only while it is newer than the logged services
        svc = log()
        edit(last_date="2026-06-01", last_mileage="25000")
        self.assertEqual(self.last_done(cid, sid), ("2026-10-01", 30000))
        self.c.delete(f"/api/services/{svc}")
        self.assertEqual(self.last_done(cid, sid), ("2026-06-01", 25000))

    def test_half_of_last_done_sent_alone_keeps_the_other_half(self):
        cid = self.car("DEMO-SCHED-HALF", 60000)
        item = {"title": "Oil", "interval_miles": 5000, "interval_months": 12, "last_date": ago(700), "last_mileage": 40000}
        by_date, by_mileage = self.c.post(f"/api/cars/{cid}/schedules", json={"items": [item, {**item, "title": "Coolant"}]}).get_json()["ids"]
        svc = self.c.post(f"/api/cars/{cid}/services", json={"title": "Oil and coolant", "date": ago(500), "mileage": 45000,
                                                             "schedule_ids": [by_date, by_mileage]}).get_json()["id"]
        self.c.put(f"/api/schedules/{by_date}", json={"last_date": ago(10)})  # as update_schedule sends it
        self.assertEqual(self.last_done(cid, by_date), (ago(10), 45000))
        self.c.put(f"/api/schedules/{by_mileage}", json={"last_mileage": 44000})
        self.c.delete(f"/api/services/{svc}")  # a service on the same day outranks it until then
        self.assertEqual(self.last_done(cid, by_mileage), (ago(500), 44000))

    def test_a_different_last_done_ends_a_dismissal(self):
        cid = self.car("DEMO-SNOOZE")
        sid = self.c.post(f"/api/cars/{cid}/schedules", json={"title": "Wipers", "interval_months": 12,
                                                               "last_date": ago(400)}).get_json()["ids"][0]
        until = lambda: next(u for u in self.c.get(f"/api/cars/{cid}").get_json()["upcoming"] if u["id"] == sid)["snoozed_until"]  # noqa: E731
        self.c.put(f"/api/schedules/{sid}", json={"snoozed_until": ago(-14)})
        self.c.put(f"/api/schedules/{sid}", json={"title": "Wipers", "last_date": ago(400), "notes": "Bosch"})
        self.assertEqual(until(), ago(-14))  # last done sent back unchanged
        self.c.put(f"/api/schedules/{sid}", json={"title": "Wipers", "last_date": ago(340), "snoozed_until": ago(-7)})
        self.assertEqual(until(), ago(-7))
        self.c.put(f"/api/schedules/{sid}", json={"title": "Wipers", "last_date": ago(300)})
        self.assertIsNone(until())

    def test_editing_a_service_keeps_its_schedules_dismissed(self):
        cid = self.car("DEMO-SNOOZE-EDIT")
        sid = self.c.post(f"/api/cars/{cid}/schedules", json={"title": "Oil", "interval_months": 12}).get_json()["ids"][0]
        svc = {"title": "Oil change", "date": ago(400), "schedule_ids": [sid]}
        svc_id = self.c.post(f"/api/cars/{cid}/services", json=svc).get_json()["id"]
        self.c.put(f"/api/schedules/{sid}", json={"snoozed_until": ago(-30)})
        self.c.put(f"/api/services/{svc_id}", json={**svc, "cost": 45.5})  # sent back whole, as update_service does
        u = next(u for u in self.c.get(f"/api/cars/{cid}").get_json()["upcoming"] if u["id"] == sid)
        self.assertEqual((u["state"], u["snoozed_until"]), ("snoozed", ago(-30)))

    def test_a_part_unticked_from_a_service_goes_back_on_hand(self):
        cid = self.car("DEMO-UNTICK")
        filt = self.c.post("/api/parts", json={"car_id": cid, "name": "Oil filter", "status": "On hand"}).get_json()["id"]
        washer = self.c.post("/api/parts", json={"car_id": cid, "name": "Drain plug washer", "status": "On hand"}).get_json()["id"]
        svc = {"title": "Oil", "date": "2026-10-01", "mileage": 40000}
        sid = self.c.post(f"/api/cars/{cid}/services", json={**svc, "parts": [{"id": filt}, {"id": washer}]}).get_json()["id"]
        self.c.put(f"/api/services/{sid}", json={**svc, "parts": [{"id": washer}]})
        p = self.c.get(f"/api/parts/{filt}").get_json()
        self.assertEqual((p["status"], p["installed_date"], p["installed_mileage"]), ("On hand", None, None))
        self.assertEqual(self.c.get(f"/api/parts/{washer}").get_json()["status"], "Installed")

    def use_four_of_six(self, cid, name, **row):
        pid = self.c.post("/api/parts", json={"name": name, "qty": 6, "status": "On hand", "price": 4.25, **row}).get_json()["id"]
        svc = self.c.post(f"/api/cars/{cid}/services", json={"title": "Plugs", "date": "2026-10-01", "mileage": 40000,
                                                             "parts": [{"id": pid, "qty": 4}]}).get_json()["id"]
        return pid, svc

    def stock(self, name):
        return sorted((p["qty"], p["status"], p["installed_date"], p["installed_mileage"])
                      for p in self.c.get("/api/parts").get_json() if p["name"] == name)

    def test_deleting_a_service_puts_a_partly_used_row_back_together(self):
        cid = self.car("DEMO-MERGE-DELETE")
        for name, owner in (("Plug for the car", cid), ("Plug not assigned", None)):
            pid, svc = self.use_four_of_six(cid, name, car_id=owner)
            self.c.delete(f"/api/services/{svc}")
            self.assertEqual(self.stock(name), [(6, "On hand", None, None)], name)
            self.assertEqual(self.c.get(f"/api/parts/{pid}").get_json()["car_id"], owner)

    def test_unticking_a_partly_used_part_puts_its_row_back_together(self):
        cid = self.car("DEMO-MERGE-UNTICK")
        _, svc = self.use_four_of_six(cid, "Plug unticked", car_id=cid)
        self.c.put(f"/api/services/{svc}", json={"title": "Plugs", "date": "2026-10-01", "mileage": 40000, "parts": []})
        self.assertEqual(self.stock("Plug unticked"), [(6, "On hand", None, None)])

    def test_a_partly_used_row_edited_or_used_up_since_stays_apart(self):
        cid = self.car("DEMO-MERGE-APART")
        pid, svc = self.use_four_of_six(cid, "Plug repriced", car_id=cid)
        self.c.put(f"/api/parts/{pid}", json={"price": 5.10})
        self.c.delete(f"/api/services/{svc}")
        self.assertEqual(self.stock("Plug repriced"), [(2, "On hand", None, None), (4, "On hand", None, None)])
        pid, svc = self.use_four_of_six(cid, "Plug used up", car_id=cid)
        self.c.post(f"/api/cars/{cid}/services", json={"title": "Plugs", "date": "2026-10-08", "mileage": 40100, "parts": [{"id": pid}]})
        self.c.delete(f"/api/services/{svc}")
        self.assertEqual(self.stock("Plug used up"), [(2, "Installed", "2026-10-08", 40100), (4, "On hand", None, None)])

    def test_deleting_a_service_puts_its_parts_back_on_hand(self):
        cid = self.car("DEMO-DELETE-PARTS")
        filt = self.c.post("/api/parts", json={"car_id": cid, "name": "Oil filter", "status": "On hand"}).get_json()["id"]
        svc = self.c.post(f"/api/cars/{cid}/services", json={"title": "Oil", "date": "2026-10-01", "mileage": 40000,
                                                             "parts": [{"id": filt}]}).get_json()["id"]
        self.c.delete(f"/api/services/{svc}")
        p = self.c.get(f"/api/parts/{filt}").get_json()
        self.assertEqual((p["status"], p["installed_date"], p["installed_mileage"], p["service_id"]), ("On hand", None, None, None))

    def test_using_none_of_a_part_is_refused(self):
        cid = self.car("DEMO-QTY")
        plugs = self.c.post("/api/parts", json={"car_id": cid, "name": "Spark plug", "qty": 6, "status": "On hand"}).get_json()["id"]
        r = self.c.post(f"/api/cars/{cid}/services", json={"title": "Plugs", "parts": [{"id": plugs, "qty": 0}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.c.get(f"/api/cars/{cid}/services").get_json(), [])
        self.assertEqual(self.c.get(f"/api/parts/{plugs}").get_json()["status"], "On hand")
        self.c.post(f"/api/cars/{cid}/services", json={"title": "Plugs", "parts": [{"id": plugs, "qty": 8}]})
        p = self.c.get(f"/api/parts/{plugs}").get_json()
        self.assertEqual((p["qty"], p["status"]), (6, "Installed"))  # more than the row has: the whole row

    def test_parts_carry_the_project_they_are_set_aside_for(self):
        cid = self.car("DEMO-SET-ASIDE")
        part = self.c.post("/api/parts", json={"car_id": cid, "name": "Control arm", "status": "On hand"}).get_json()["id"]
        self.c.post(f"/api/cars/{cid}/projects", json={"title": "Suspension refresh", "part_ids": [part]})
        p = next(p for p in self.c.get(f"/api/parts?car_id={cid}").get_json() if p["id"] == part)
        self.assertEqual(p["project_title"], "Suspension refresh")

    def test_every_file_but_a_vehicle_photo_is_listed(self):
        from PIL import Image
        cid = self.car("DEMO-FILES")
        upload = lambda kind, car="": self.c.post("/api/documents", data={  # noqa: E731
            "file": (io.BytesIO(b"x"), "a.jpg"), "kind": kind, "car_id": car}, content_type="multipart/form-data").get_json()["ids"][0]
        saved_as_photo = upload("Photo", cid)  # as older versions let you
        loose = upload("Receipt")
        png = io.BytesIO()
        Image.new("RGB", (40, 30), "red").save(png, "PNG")
        r = self.c.post(f"/api/cars/{cid}/photo", data={"photo": (io.BytesIO(png.getvalue()), "car.png")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual([d["id"] for d in self.c.get(f"/api/documents?car_id={cid}").get_json()], [saved_as_photo])
        self.assertEqual(next(c for c in self.c.get("/api/cars").get_json() if c["id"] == cid)["documents"], 1)
        r = self.c.get("/api/documents?car_id=none")
        self.assertIn(loose, [d["id"] for d in r.get_json()])
        self.assertEqual({d["car_id"] for d in r.get_json()}, {None})


if __name__ == "__main__":
    unittest.main()
