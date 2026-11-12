"""Projects, dismissing and ordering upcoming services, using part of a part's quantity, card settings."""
import datetime as dt
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("PARTS_CATALOG_DATA", tempfile.mkdtemp())

from app import db, epc, epc_api, server  # noqa: E402
from app.server import app  # noqa: E402


def day(offset):
    return (dt.date.today() + dt.timedelta(days=offset)).isoformat()


class ProjectsTest(unittest.TestCase):
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
        cls.car = cls.c.post("/api/cars", json={"name": "Test car", "mileage": 100000}).get_json()["id"]

    @classmethod
    def tearDownClass(cls):
        db.connect().close()
        db._local.conn = None
        epc.close()
        (db.DATA_DIR, db.DB_PATH, db.DOC_DIR, epc.EPC_DIR, epc.EPC_DB, epc.FILES_DIR, server.SETTINGS_PATH) = cls.saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def card(self):
        return next(c for c in self.c.get("/api/cars").get_json() if c["id"] == self.car)

    def test_project_steps_and_parts(self):
        part = self.c.post("/api/parts", json={"name": "Headers", "status": "On hand"}).get_json()["id"]  # unassigned
        pid = self.c.post(f"/api/cars/{self.car}/projects", json={
            "title": "Engine reseal", "tasks": ["Pull drivetrain", "Reseal engine", "Install headers"],
            "part_ids": [part]}).get_json()["id"]
        p = self.c.get(f"/api/cars/{self.car}/projects").get_json()[0]
        self.assertEqual([t["title"] for t in p["tasks"]], ["Pull drivetrain", "Reseal engine", "Install headers"])
        self.assertEqual(p["parts"][0]["name"], "Headers")
        self.assertEqual(self.c.get(f"/api/parts/{part}").get_json()["car_id"], self.car)  # joined the car
        self.c.put(f"/api/project-tasks/{p['tasks'][0]['id']}", json={"done": True})
        # rewriting the list keeps the tick on a step whose text is unchanged
        self.c.put(f"/api/projects/{pid}", json={"tasks": ["Order gaskets", "Pull drivetrain", "Reseal engine"]})
        tasks = self.c.get(f"/api/cars/{self.car}/projects").get_json()[0]["tasks"]
        self.assertEqual([(t["title"], t["done"]) for t in tasks],
                         [("Order gaskets", 0), ("Pull drivetrain", 1), ("Reseal engine", 0)])
        self.assertEqual(self.card()["projects"][0]["title"], "Engine reseal")
        self.c.put(f"/api/projects/{pid}", json={"status": "Done"})
        self.assertEqual(self.card()["projects"], [])  # finished projects leave the card
        self.assertEqual(self.c.get(f"/api/cars/{self.car}/projects").get_json()[0]["done_date"], day(0))
        self.assertEqual(self.c.post(f"/api/cars/{self.car}/projects", json={"title": " "}).status_code, 400)
        self.c.delete(f"/api/projects/{pid}")
        self.assertIsNone(self.c.get(f"/api/parts/{part}").get_json()["project_id"])

    def test_dismiss_and_order(self):
        ids = self.c.post(f"/api/cars/{self.car}/schedules", json={"items": [
            {"title": "Wipers", "interval_months": 12, "last_date": day(-400)},      # overdue
            {"title": "Oil", "interval_miles": 5000, "last_mileage": 99000},         # ok
            {"title": "Coolant", "interval_months": 24, "last_date": day(-30)},      # ok, further out
        ]}).get_json()["ids"]
        wipers, oil, coolant = ids
        self.assertEqual(self.card()["overdue"], 1)
        self.c.put(f"/api/schedules/{wipers}", json={"snoozed_until": day(14)})
        card = self.card()
        self.assertEqual((card["overdue"], card["snoozed"]), (0, 1))
        self.assertNotIn("Wipers", [u["title"] for u in card["upcoming"]])
        up = self.c.get(f"/api/cars/{self.car}").get_json()["upcoming"]
        self.assertEqual(next(u for u in up if u["id"] == wipers)["state"], "snoozed")
        self.assertEqual(server.ha_summary()["cars"][0]["overdue"], 0)
        self.c.put(f"/api/schedules/{wipers}", json={"snoozed_until": day(-1)})    # the date passed: due again
        self.assertEqual(self.card()["overdue"], 1)
        self.c.put(f"/api/schedules/{wipers}", json={"snoozed_until": day(14)})
        self.c.post(f"/api/cars/{self.car}/services", json={"title": "Wipers", "schedule_ids": [wipers]})
        self.assertIsNone(db.row("SELECT snoozed_until FROM schedules WHERE id=?", (wipers,))["snoozed_until"])
        # manual order wins over urgency until it's reset
        self.c.post(f"/api/cars/{self.car}/schedules/order", json={"ids": [coolant, oil]})
        self.assertEqual([u["title"] for u in self.card()["upcoming"]][:2], ["Coolant", "Oil"])
        self.c.post(f"/api/cars/{self.car}/schedules/order", json={"ids": []})
        self.assertEqual(self.card()["upcoming"][0]["title"], "Oil")
        for sid in ids:
            self.c.delete(f"/api/schedules/{sid}")

    def test_use_some_of_a_part(self):
        plugs = self.c.post("/api/parts", json={"car_id": self.car, "name": "Spark plug", "part_number": "308", "qty": 6,
                                                "status": "On hand", "location": "Box 2"}).get_json()["id"]
        filt = self.c.post("/api/parts", json={"car_id": self.car, "name": "Oil filter", "status": "On hand"}).get_json()["id"]
        sid = self.c.post(f"/api/cars/{self.car}/services", json={
            "title": "Tune-up", "mileage": 100500, "parts": [{"id": plugs, "qty": 4}, {"id": filt, "qty": 1}]}).get_json()["id"]
        left = self.c.get(f"/api/parts/{plugs}").get_json()
        self.assertEqual((left["qty"], left["status"], left["service_id"]), (2, "On hand", None))
        svc = next(s for s in self.c.get(f"/api/cars/{self.car}/services").get_json() if s["id"] == sid)
        used = {p["name"]: p for p in svc["parts"]}
        self.assertEqual((used["Spark plug"]["qty"], used["Spark plug"]["installed_mileage"]), (4, 100500))
        self.assertEqual(used["Oil filter"]["id"], filt)  # whole part used: no copy
        # saving the service again must not split the already-used entry
        again = [{"id": p["id"], "qty": 1} for p in svc["parts"]]
        self.c.put(f"/api/services/{sid}", json={"title": "Tune-up", "mileage": 100500, "parts": again})
        svc = next(s for s in self.c.get(f"/api/cars/{self.car}/services").get_json() if s["id"] == sid)
        self.assertEqual(sorted(p["qty"] for p in svc["parts"]), [1, 4])
        self.assertEqual(self.c.get(f"/api/parts/{plugs}").get_json()["qty"], 2)

    def test_card_settings_and_quick_services(self):
        ref = self.c.get("/api/refdata").get_json()
        self.assertEqual(ref["card"]["upcoming_rows"], 3)
        self.assertIn("Brake job (pads & rotors, front)", ref["quick_services"])
        self.assertTrue(all(p["group"] for p in ref["presets"]))
        self.c.put("/api/settings", json={"card": {"upcoming_rows": 99, "photo": False, "bogus": 1},
                                          "quick_services": ["Headers install", " "]})
        ref = self.c.get("/api/refdata").get_json()
        self.assertEqual(ref["card"], {**server.CARD_DEFAULTS, "upcoming_rows": 12, "photo": False})
        self.assertEqual(ref["quick_services"][0], "Headers install")
        self.c.put("/api/settings", json={"card": dict(server.CARD_DEFAULTS), "quick_services": []})


if __name__ == "__main__":
    unittest.main()
