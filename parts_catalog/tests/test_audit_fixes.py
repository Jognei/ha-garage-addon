"""Fixes from the 2026-11-11 QoL audit: bad dates, mileage-less services, restore staging, urgency over drag order,
project finish dates, and agent argument checks."""
import datetime as dt
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile

os.environ.setdefault("PARTS_CATALOG_DATA", tempfile.mkdtemp())

from app import db, epc, epc_api, server  # noqa: E402
from app.server import app  # noqa: E402


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
        r = self.c.post("/api/restore", data={"backup": (io.BytesIO(bad.getvalue()), "b.zip")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "restore-pending")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "restore-staging")))
        self.assertTrue(any(f.startswith("before-restore-") for f in os.listdir(self.tmp)))  # safety copy came first

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

    def test_editing_a_done_project_keeps_its_finish_date(self):
        cid = self.car("DEMO-PROJ")
        pid = self.c.post(f"/api/cars/{cid}/projects", json={"title": "Control arms", "status": "Done",
                                                             "done_date": "2026-10-01"}).get_json()["id"]
        self.c.put(f"/api/projects/{pid}", json={"title": "Control arms", "status": "Done", "notes": "Torqued at ride height"})
        p = next(x for x in self.c.get(f"/api/cars/{cid}/projects").get_json() if x["id"] == pid)
        self.assertEqual(p["done_date"], "2026-10-01")

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


if __name__ == "__main__":
    unittest.main()
