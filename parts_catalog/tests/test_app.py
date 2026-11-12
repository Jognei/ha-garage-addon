"""Run: cd parts_catalog && python -m unittest discover tests"""
import datetime as dt
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest

TMP = tempfile.mkdtemp()
os.environ["PARTS_CATALOG_DATA"] = TMP

from app import db  # noqa: E402
from app.server import app  # noqa: E402


def ago(days):
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


class GarageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        cls.c = app.test_client()

    @classmethod
    def tearDownClass(cls):
        db.connect().close()
        shutil.rmtree(TMP, ignore_errors=True)

    def test_fresh_install_is_empty(self):
        self.assertEqual(self.c.get("/api/parts").get_json(), [])

    def test_truck_services_and_upcoming(self):
        cid = self.c.post("/api/cars", json={"name": "My truck", "kind": "Truck", "year": "2004", "mileage": "150,000"}).get_json()["id"]
        car = self.c.get(f"/api/cars/{cid}").get_json()
        self.assertEqual(car["mileage"], 150000)
        r = self.c.post(f"/api/cars/{cid}/schedules", json={"items": [
            {"title": "Oil", "interval_miles": 5000, "interval_months": 12, "last_date": ago(30), "last_mileage": 145400},
            {"title": "Coolant", "interval_months": 24, "last_date": ago(800)},
            {"title": "Plugs", "interval_miles": 30000},
        ]}).get_json()
        oil, coolant, plugs = r["ids"]
        up = {u["title"]: u for u in self.c.get(f"/api/cars/{cid}").get_json()["upcoming"]}
        self.assertEqual(up["Oil"]["miles_left"], 400)
        self.assertEqual(up["Oil"]["state"], "soon")
        self.assertEqual(up["Coolant"]["state"], "overdue")
        self.assertEqual(up["Plugs"]["state"], "unknown")

        # a part on hand gets used in a service: installed at the service mileage; schedule resets
        pid = self.c.post("/api/parts", json={"car_id": cid, "name": "Oil filter", "status": "On hand"}).get_json()["id"]
        self.c.post(f"/api/cars/{cid}/services", json={"date": dt.date.today().isoformat(), "mileage": "150,250",
                                                       "title": "Oil change", "schedule_ids": [oil], "part_ids": [pid]})
        p = self.c.get(f"/api/parts/{pid}").get_json()
        self.assertEqual((p["status"], p["installed_mileage"]), ("Installed", 150250))
        car = self.c.get(f"/api/cars/{cid}").get_json()
        self.assertEqual(car["mileage"], 150250)
        o = next(u for u in car["upcoming"] if u["title"] == "Oil")
        self.assertEqual((o["last_mileage"], o["due_mileage"], o["state"]), (150250, 155250, "ok"))
        # editable installed mileage
        self.c.put(f"/api/parts/{pid}", json={"installed_mileage": "150,200"})
        self.assertEqual(self.c.get(f"/api/parts/{pid}").get_json()["installed_mileage"], 150200)
        # mileage never goes backwards unless corrected
        self.c.post(f"/api/cars/{cid}/mileage", json={"mileage": 100})
        self.assertEqual(self.c.get(f"/api/cars/{cid}").get_json()["mileage"], 150250)
        # deleting the service rolls the schedule back
        sid = self.c.get(f"/api/cars/{cid}/services").get_json()[0]["id"]
        self.c.delete(f"/api/services/{sid}")
        o = next(u for u in self.c.get(f"/api/cars/{cid}").get_json()["upcoming"] if u["title"] == "Oil")
        self.assertEqual(o["last_mileage"], 145400)  # unchanged: no other linked service
        ha = self.c.get("/api/ha/summary").get_json()
        truck = next(x for x in ha["cars"] if x["id"] == cid)
        self.assertEqual(truck["overdue"], 1)

    def test_purchase_with_receipt_and_documents(self):
        car1 = self.c.post("/api/cars", json={"name": "Doc car"}).get_json()["id"]
        data = {"car_id": car1, "supplier": "Parts Store", "items": [
            {"name": "Thermostat", "part_number": "11531740996", "qty": 1, "price": "$24.99"},
            {"name": "", "part_number": ""}]}
        r = self.c.post("/api/purchases", data={"data": json.dumps(data),
                                                "receipt": (io.BytesIO(b"\x89PNG fake"), "order.png")},
                        content_type="multipart/form-data").get_json()
        self.assertEqual(len(r["ids"]), 1)
        p = self.c.get(f"/api/parts/{r['ids'][0]}").get_json()
        self.assertEqual((p["status"], p["price"], p["document_id"]), ("On hand", 24.99, r["document_id"]))
        up = self.c.post("/api/documents", data={"car_id": str(car1), "kind": "Manual", "title": "Bentley",
                                                 "file": (io.BytesIO(b"%PDF-1.4 test"), "bentley.pdf")},
                         content_type="multipart/form-data").get_json()
        docs = self.c.get(f"/api/documents?car_id={car1}").get_json()
        self.assertIn("Bentley", [d["title"] for d in docs])
        f = self.c.get(f"/files/{up['ids'][0]}/bentley.pdf")
        self.assertEqual(f.data, b"%PDF-1.4 test")
        f.close()
        b = self.c.get("/api/backup")
        self.assertEqual(b.mimetype, "application/zip")
        b.close()

    def test_chunked_document_upload(self):
        car = self.c.post("/api/cars", json={"name": "Chunk car"}).get_json()["id"]
        blob = bytes(range(256)) * 40  # 10,240 bytes
        uid = self.c.post("/api/documents/chunked", json={"filename": "manual.pdf", "size": len(blob), "car_id": car,
                                                          "kind": "Manual", "title": "Big manual"}).get_json()["upload_id"]
        put = lambda off, data: self.c.put(f"/api/documents/chunked/{uid}?offset={off}", data=data,  # noqa: E731
                                           content_type="application/octet-stream")
        self.assertEqual(put(0, blob[:4000]).get_json()["received"], 4000)
        r = put(8000, blob[8000:])  # a piece got lost: the server says where to resume
        self.assertEqual((r.status_code, r.get_json()["received"]), (409, 4000))
        self.assertEqual(self.c.post(f"/api/documents/chunked/{uid}/finish").status_code, 409)  # not complete yet
        put(4000, blob[4000:8000])
        put(8000, blob[8000:])
        self.assertEqual(put(len(blob), b"x").status_code, 400)  # more than announced
        did = self.c.post(f"/api/documents/chunked/{uid}/finish").get_json()["id"]
        doc = next(d for d in self.c.get(f"/api/documents?car_id={car}").get_json() if d["id"] == did)
        self.assertEqual((doc["title"], doc["original_name"], doc["size"], doc["kind"]), ("Big manual", "manual.pdf", len(blob), "Manual"))
        f = self.c.get(f"/files/{did}/manual.pdf")
        self.assertEqual(f.data, blob)
        f.close()
        self.assertEqual(self.c.post(f"/api/documents/chunked/{uid}/finish").status_code, 404)  # used up
        self.assertEqual(self.c.put("/api/documents/chunked/../etc?offset=0", data=b"x").status_code, 404)


if __name__ == "__main__":
    unittest.main()
