"""Home Assistant add-on support: backup/restore, client allowlist, add-on settings, relative UI URLs."""
import io
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
import zipfile

os.environ.setdefault("PARTS_CATALOG_DATA", tempfile.mkdtemp())

from app import db, epc, epc_api, server  # noqa: E402
from app.server import app  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AddonTest(unittest.TestCase):
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

    def test_document_kinds(self):
        cid = self.c.post("/api/cars", json={"name": "Kinds car"}).get_json()["id"]
        ref = self.c.get("/api/refdata").get_json()["doc_kinds"]
        self.assertIn("Part photo", ref)
        self.assertNotIn("Photo", ref)  # vehicle photos aren't picked by hand
        did = self.c.post("/api/documents", data={"car_id": str(cid), "kind": "Part photo", "title": "Filter",
                                                   "file": (io.BytesIO(b"x"), "f.jpg")}, content_type="multipart/form-data").get_json()["ids"][0]
        docs = self.c.get(f"/api/documents?car_id={cid}").get_json()
        self.assertEqual(docs[0]["kind"], "Part photo")
        self.assertEqual(self.c.put(f"/api/documents/{did}", json={"kind": "Bogus"}).status_code, 400)
        self.assertEqual(self.c.put(f"/api/documents/{did}", json={"kind": "Receipt"}).status_code, 200)
        self.c.put(f"/api/cars/{cid}", json={"notes": "Overall note"})
        car = self.c.get(f"/api/cars/{cid}").get_json()
        self.assertEqual((car["name"], car["notes"]), ("Kinds car", "Overall note"))  # partial update keeps the rest

    def test_backup_restore_roundtrip(self):
        cid = self.c.post("/api/cars", json={"name": "Backup car"}).get_json()["id"]
        r = self.c.post("/api/documents", data={"car_id": str(cid), "kind": "Other", "title": "Note",
                                                 "file": (io.BytesIO(b"hello"), "note.txt")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        backup = self.c.get("/api/backup").data
        names = zipfile.ZipFile(io.BytesIO(backup)).namelist()
        self.assertIn("garage.db", names)
        self.assertIn("epc/epc.db", names)
        self.assertTrue(any(n.startswith("documents/") for n in names))
        # change something, then restore the backup
        self.c.post("/api/cars", json={"name": "Temporary car"})
        r = self.c.post("/api/restore", data={"backup": (io.BytesIO(backup), "b.zip")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "restore-pending", "garage.db")))
        self.assertTrue(any(f.startswith("before-restore-") for f in os.listdir(self.tmp)))
        # what the next start does
        db.connect().close()
        db._local.conn = None
        epc.close()
        self.assertTrue(db.apply_pending_restore())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "restore-pending")))
        db.init()
        epc_api.init()
        names = [c["name"] for c in self.c.get("/api/cars").get_json()]
        self.assertNotIn("Temporary car", names)
        docs = self.c.get("/api/documents").get_json()
        self.assertTrue(any(d["title"] == "Note" for d in docs))
        self.assertFalse(db.apply_pending_restore())

    def test_restore_rejects_bad_zips(self):
        post = lambda data: self.c.post("/api/restore", data={"backup": (io.BytesIO(data), "x.zip")},  # noqa: E731
                                        content_type="multipart/form-data")
        self.assertEqual(post(b"not a zip").status_code, 400)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("other.txt", "x")
        self.assertEqual(post(buf.getvalue()).status_code, 400)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("garage.db", "x")
            z.writestr("../evil.txt", "x")
        self.assertEqual(post(buf.getvalue()).status_code, 400)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("garage.db", "definitely not sqlite")
        self.assertEqual(post(buf.getvalue()).status_code, 400)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "restore-pending")))

    def test_allowlist_and_addon_settings(self):
        try:
            server.ALLOWED_CLIENTS = {"172.30.32.2"}
            self.assertEqual(self.c.get("/api/ping").status_code, 403)
            self.assertEqual(self.c.get("/api/ping", environ_base={"REMOTE_ADDR": "172.30.32.2"}).status_code, 200)
        finally:
            server.ALLOWED_CLIENTS = set()
        try:
            server.ADDON = True
            s = self.c.get("/api/settings").get_json()
            self.assertTrue(s["addon"])
            self.assertFalse(s["lan_active"])
            self.assertEqual(self.c.put("/api/settings", json={"lan": True}).status_code, 400)
        finally:
            server.ADDON = False

    def test_ui_uses_relative_urls(self):
        """Under HA ingress the app lives at /api/hassio_ingress/<token>/, so the UI must not use root-absolute paths."""
        bad = re.compile(r"""(?:fetch\(|href=|src=)\s*['"`]/(?!/)""")
        for fn in ("index.html", "app.js", "catalog.js"):
            with open(os.path.join(ROOT, "static", fn), encoding="utf-8") as f:
                self.assertIsNone(bad.search(f.read()), fn)


if __name__ == "__main__":
    unittest.main()
