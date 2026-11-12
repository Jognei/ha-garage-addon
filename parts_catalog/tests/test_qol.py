"""Vehicle order, hiding vehicles, photos, estimated due dates, CSV export."""
import csv
import datetime as dt
import io
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("PARTS_CATALOG_DATA", tempfile.mkdtemp())

from app import db, epc, epc_api, server  # noqa: E402
from app.server import app  # noqa: E402


def ago(days):
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


class QolTest(unittest.TestCase):
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
        cls.ids = [cls.c.post("/api/cars", json={"name": n}).get_json()["id"] for n in ("Alpha", "Bravo", "Charlie")]

    @classmethod
    def tearDownClass(cls):
        db.connect().close()
        db._local.conn = None
        epc.close()
        (db.DATA_DIR, db.DB_PATH, db.DOC_DIR, epc.EPC_DIR, epc.EPC_DB, epc.FILES_DIR, server.SETTINGS_PATH) = cls.saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def names(self):
        return [c["name"] for c in self.c.get("/api/cars").get_json() if not c["archived"]]

    def test_order_and_hide(self):
        a, b, c = self.ids
        self.c.post("/api/cars/order", json={"ids": [c, a, b]})
        self.assertEqual(self.names(), ["Charlie", "Alpha", "Bravo"])
        self.assertEqual([x["name"] for x in server.ha_summary()["cars"]], ["Charlie", "Alpha", "Bravo"])
        self.c.put(f"/api/cars/{a}", json={"archived": True})
        self.assertEqual(self.names(), ["Charlie", "Bravo"])
        self.assertEqual(self.c.get("/api/cars").get_json()[-1]["name"], "Alpha")  # hidden ones come last
        self.assertNotIn("Alpha", [x["name"] for x in server.ha_summary()["cars"]])
        self.c.put(f"/api/cars/{a}", json={"archived": False})
        self.assertEqual(self.names(), ["Charlie", "Alpha", "Bravo"])
        self.c.post("/api/cars/order", json={"ids": [a, b, c]})

    def test_photo(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (3000, 2000), "red").save(buf, "PNG")
        a = self.ids[0]
        r = self.c.post(f"/api/cars/{a}/photo", data={"photo": (io.BytesIO(buf.getvalue()), "car.png")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)
        car = next(x for x in self.c.get("/api/cars").get_json() if x["id"] == a)
        self.assertTrue(car["photo_id"])
        self.assertTrue(car["photo_name"].endswith(".jpg"))
        img = self.c.get(f"/files/{car['photo_id']}/{car['photo_name']}")
        with Image.open(io.BytesIO(img.data)) as im:
            self.assertEqual(max(im.size), 1600)  # shrunk
        img.close()
        self.assertEqual(self.c.get(f"/api/documents?car_id={a}").get_json(), [])  # not listed with manuals
        first = car["photo_id"]
        self.c.post(f"/api/cars/{a}/photo", data={"photo": (io.BytesIO(buf.getvalue()), "car2.png")},
                    content_type="multipart/form-data")
        self.assertIsNone(db.row("SELECT 1 FROM documents WHERE id=?", (first,)))  # old photo replaced
        self.c.delete(f"/api/cars/{a}/photo")
        self.assertIsNone(next(x for x in self.c.get("/api/cars").get_json() if x["id"] == a)["photo_id"])

    def test_estimated_due_date(self):
        b = self.ids[1]
        server.record_mileage(b, 10000, ago(100))
        server.record_mileage(b, 13000, ago(0))  # 30 mi/day
        self.c.post(f"/api/cars/{b}/schedules", json={"items": [
            {"title": "Oil", "interval_miles": 5000, "last_mileage": 12000, "last_date": ago(10)}]})
        u = next(x for x in self.c.get(f"/api/cars/{b}").get_json()["upcoming"] if x["title"] == "Oil")
        self.assertEqual(u["miles_left"], 4000)
        expected = dt.date.today() + dt.timedelta(days=round(4000 / 30))
        self.assertEqual(u["est_date"], expected.isoformat())

    def test_no_estimate_without_history(self):
        c = self.ids[2]
        self.c.post(f"/api/cars/{c}/schedules", json={"items": [{"title": "Plugs", "interval_miles": 30000, "last_mileage": 0}]})
        u = self.c.get(f"/api/cars/{c}").get_json()["upcoming"][0]
        self.assertIsNone(u["est_date"])

    def test_csv_exports(self):
        a = self.ids[0]
        self.c.post(f"/api/cars/{a}/services", json={"date": "2026-01-02", "mileage": "50,000", "title": "Brakes, front", "cost": "120"})
        self.c.post("/api/parts", json={"car_id": a, "name": "Pads", "part_number": "34116761244", "status": "On hand"})
        r = self.c.get(f"/api/cars/{a}/export/services.csv")
        self.assertEqual(r.mimetype, "text/csv")
        self.assertIn("Alpha-service-history.csv", r.headers["Content-Disposition"])
        text = r.data.decode("utf-8-sig")
        self.assertTrue(text.startswith("Date,Mileage,Work"))
        self.assertIn('2026-01-02,50000,"Brakes, front"', text)
        text = self.c.get(f"/api/export/parts.csv?car_id={a}").data.decode("utf-8-sig")
        self.assertIn("Alpha,Pads,34116761244", text)
        self.assertIn("Pads", self.c.get("/api/export/parts.csv").data.decode("utf-8-sig"))

    def test_parts_csv_follows_the_list_filters(self):
        a = self.ids[0]
        self.c.post("/api/parts", json={"car_id": a, "name": "Caliper", "status": "Needed"})
        self.c.post("/api/parts", json={"car_id": a, "name": "Brake hose", "status": "On hand"})
        self.c.post("/api/parts", json={"name": "Wiper blade", "status": "Needed", "url": "https://example.com/wiper"})
        pid = self.c.post("/api/parts", json={"car_id": a, "name": "Brake pads front", "status": "On hand"}).get_json()["id"]
        self.c.post(f"/api/cars/{a}/services", json={"date": "2026-02-03", "title": "Brake job", "part_ids": [pid]})

        def export(query):
            text = self.c.get("/api/export/parts.csv?" + query).data.decode("utf-8-sig")
            return {r["Part"]: r for r in csv.DictReader(io.StringIO(text))}
        to_get = export(f"car_id={a}&status=Needed,Check,Ordered")
        self.assertIn("Caliper", to_get)
        self.assertNotIn("Brake hose", to_get)
        loose = export("car_id=none")
        self.assertEqual((list(loose), loose["Wiper blade"]["Link"]), (["Wiper blade"], "https://example.com/wiper"))
        self.assertEqual(list(export("q=hose")), ["Brake hose"])
        used = export(f"car_id={a}")["Brake pads front"]
        self.assertEqual((used["Service"], used["Service date"]), ("Brake job", "2026-02-03"))

    def test_service_csv_shows_quantities(self):
        c = self.ids[2]
        plugs = self.c.post("/api/parts", json={"car_id": c, "name": "Spark plug", "part_number": "12120037244", "qty": 4}).get_json()["id"]
        oil = self.c.post("/api/parts", json={"car_id": c, "name": "Oil filter", "qty": 1}).get_json()["id"]
        self.c.post(f"/api/cars/{c}/services", json={"date": "2026-03-04", "title": "Tune-up", "part_ids": [plugs, oil]})
        text = self.c.get(f"/api/cars/{c}/export/services.csv").data.decode("utf-8-sig")
        used = next(r for r in csv.DictReader(io.StringIO(text)) if r["Work"] == "Tune-up")["Parts used"]
        self.assertEqual(sorted(used.split("; ")), ["4× Spark plug (12120037244)", "Oil filter"])


if __name__ == "__main__":
    unittest.main()
