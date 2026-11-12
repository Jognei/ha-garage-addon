"""3D workshop packs: import, validation, spec entries, backup. Run with the other tests:
.venv\Scripts\python -m unittest discover tests"""
import io
import os
import tempfile
import unittest
import zipfile

os.environ.setdefault("PARTS_CATALOG_DATA", tempfile.mkdtemp())

from app import workshop  # noqa: E402
from app.server import app  # noqa: E402


def pack():
    return {
        "id": "test-car", "title": "Test car",
        "sections": [{"id": "engine", "title": "Engine", "explode": [1, 0, 0]}],
        "frames": {"bank": {"pos": [0, 0.3, 0], "rot": [45, 0, 0]}},
        "parts": [
            {"id": "cover", "name": "Valve cover", "section": "engine", "frame": "bank", "shape": {"type": "box", "size": [0.4, 0.05, 0.2]},
             "procedures": ["gasket"], "held_by": ["nuts"]},
            {"id": "nuts", "name": "Cap nut", "section": "engine", "frame": "bank", "shape": {"type": "capnut", "d": 0.006},
             "instances": [{"pos": [0.1, 0, 0], "seq": 1}, {"pos": [-0.1, 0, 0], "seq": 2}], "fastener": {"thread": "M6"}},
        ],
        "procedures": [{"id": "gasket", "title": "Replace the gasket", "steps": [{"text": "Undo the nuts", "parts": ["nuts"], "torque_of": "nuts"}]}],
    }


class WorkshopTests(unittest.TestCase):
    def setUp(self):
        self.c = app.test_client()
        for fn in os.listdir(workshop.DIR) if os.path.isdir(workshop.DIR) else []:
            os.remove(os.path.join(workshop.DIR, fn))

    def test_import_list_get_delete(self):
        self.assertFalse(self.c.get("/api/refdata").get_json()["features"]["workshop"])
        self.assertEqual(self.c.post("/api/workshop/packs", json=pack()).status_code, 200)
        self.assertTrue(self.c.get("/api/refdata").get_json()["features"]["workshop"])
        lst = self.c.get("/api/workshop/packs").get_json()
        self.assertEqual([(p["id"], p["sections"], p["parts"], p["procedures"]) for p in lst], [("test-car", 1, 2, 1)])
        self.assertEqual(self.c.get("/api/workshop/packs/test-car").get_json()["parts"][1]["fastener"], {"thread": "M6"})
        self.assertEqual(self.c.get("/api/workshop/packs/nope").status_code, 404)
        self.assertEqual(self.c.get("/api/workshop/packs/..%2Fsettings").status_code, 404)
        self.assertEqual(self.c.delete("/api/workshop/packs/test-car").status_code, 200)
        self.assertEqual(self.c.get("/api/workshop/packs").get_json(), [])

    def test_validation(self):
        for change, word in ((lambda p: p.update(id="Bad Id"), "id"),
                             (lambda p: p["parts"][0].update(section="nope"), "section"),
                             (lambda p: p["parts"][0].update(frame="nope"), "frame"),
                             (lambda p: p["parts"][0]["shape"].update(type="teapot"), "shape"),
                             (lambda p: p["parts"][0].update(procedures=["nope"]), "procedure"),
                             (lambda p: p["procedures"][0]["steps"][0].update(parts=["nope"]), "unknown part"),
                             (lambda p: p["parts"].append(dict(p["parts"][0])), "repeated")):
            p = pack()
            change(p)
            r = self.c.post("/api/workshop/packs", json=p)
            self.assertEqual(r.status_code, 400, word)
            self.assertIn(word, r.get_json()["error"])
        self.assertEqual(self.c.post("/api/workshop/packs", json=[1]).status_code, 400)

    def test_spec_entries_survive_reimport(self):
        self.c.post("/api/workshop/packs", json=pack())
        r = self.c.put("/api/workshop/packs/test-car/specs/nuts", json={"torque_nm": "10", "source": "Manual p. 12"})
        f = r.get_json()["parts"][1]["fastener"]
        self.assertEqual((f["thread"], f["torque_nm"], f["source"], f["owner_entered"]), ("M6", 10.0, "Manual p. 12", ["source", "torque_nm"]))
        self.assertEqual(self.c.put("/api/workshop/packs/test-car/specs/nuts", json={"torque_nm": "tight"}).status_code, 400)
        self.assertEqual(self.c.put("/api/workshop/packs/test-car/specs/nope", json={"torque_nm": 5}).status_code, 404)
        self.c.post("/api/workshop/packs", json=pack())  # importing the model again keeps what was entered
        self.assertEqual(self.c.get("/api/workshop/packs/test-car").get_json()["parts"][1]["fastener"]["torque_nm"], 10.0)
        r = self.c.put("/api/workshop/packs/test-car/specs/nuts", json={"torque_nm": "", "source": None})
        self.assertEqual(r.get_json()["parts"][1]["fastener"], {"thread": "M6"})

    def test_backup_includes_packs(self):
        self.c.post("/api/workshop/packs", json=pack())
        r = self.c.get("/api/backup")
        with zipfile.ZipFile(io.BytesIO(r.data)) as z:
            self.assertIn("workshop/test-car.json", z.namelist())


if __name__ == "__main__":
    unittest.main()
