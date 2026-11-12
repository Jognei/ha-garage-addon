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


if __name__ == "__main__":
    unittest.main()
