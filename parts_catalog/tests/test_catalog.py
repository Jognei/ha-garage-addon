"""Offline catalog: importers, queries, validation, API. Run with the other tests:
cd parts_catalog && python -m unittest discover tests"""
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("PARTS_CATALOG_DATA", tempfile.mkdtemp())

from app import db, epc, epc_api, epc_import  # noqa: E402
from app.server import app  # noqa: E402

PAGE = """<html><head><title>Test page: water pump</title></head><body>
<img src="page_files/logo.png"><img src="page_files/diagram.png" usemap="#m">
<map name="m"><area shape="rect" coords="10,20,50,60" alt="1"><area shape="circle" coords="200,150,20" alt="2">
<area shape="rect" coords="300,10,340,40" alt="9"></map>
<table><tr><td>nav</td></tr></table>
<table>
<tr><th>No.</th><th>Description</th><th>Supplement</th><th>Qty</th><th>From</th><th>Up to</th><th>Part Number</th></tr>
<tr><td>1</td><td>Water pump</td><td></td><td>1</td><td></td><td>08/1990</td><td>11 51 1 234 567</td></tr>
<tr><td>2</td><td>Gasket &amp; seal</td><td>ENGINE-A</td><td>1</td><td>09/1990</td><td></td><td>11-51-1-234-568</td></tr>
<tr><td>3</td><td>Bolt</td><td>M8x25</td><td></td><td></td><td></td><td>07 11 9 900 001</td></tr>
<tr><td>4</td><td>Not available</td><td></td><td></td><td></td><td></td><td>--</td></tr>
</table></body></html>"""


def make_png(path, size=(400, 300)):
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, "white").save(path)


class CatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        # test_app may have run first and removed its data dir: use our own for both databases
        db._local.conn = None
        db.DB_PATH = os.path.join(cls.tmp, "garage.db")
        db.DOC_DIR = os.path.join(cls.tmp, "documents")
        epc.close()
        epc.EPC_DIR = os.path.join(cls.tmp, "epc")
        epc.EPC_DB = os.path.join(epc.EPC_DIR, "epc.db")
        epc.FILES_DIR = os.path.join(epc.EPC_DIR, "files")
        db.init()
        epc_api.init()
        epc_import.load_sample()
        epc_import.import_bundle({"source": {"name": "Test vehicles"}, "vehicles": [
            {"code": "DEMO-SEDAN", "series": "DEMO", "model": "Sedan", "engine": "ENGINE-A"}]})
        cls.c = app.test_client()

    @classmethod
    def tearDownClass(cls):
        epc.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def diag(self, code):
        return epc.row("SELECT * FROM diagrams WHERE code=?", (code,))

    def checks(self):
        return {r["check"] for r in epc.validate()}

    def test_reference_data(self):
        self.assertEqual(epc.row("SELECT name FROM categories WHERE code='11'")["name"], "Engine")
        self.assertIsNone(epc.row("SELECT 1 FROM vehicles v JOIN sources s ON s.id=v.source_id WHERE s.kind='reference'"))

    def test_part_numbers(self):
        self.assertEqual(epc.norm_pn("11 53 1-740.996"), "11531740996")
        self.assertEqual(epc.fmt_pn("11531740996"), "11 53 1 740 996")
        self.assertEqual(epc_import.yyyymm("08/1990"), "199008")
        self.assertEqual(epc_import.yyyymm("1990-09"), "199009")
        self.assertIsNone(epc_import.yyyymm("soon"))

    def test_sample_diagram_detail(self):
        d = epc.diagram_detail(self.diag("SAMPLE-11-01")["id"])
        self.assertTrue(d["is_sample"])
        self.assertTrue(d["image_exists"])
        self.assertEqual((d["image_w"], d["image_h"]), (800, 600))
        h1 = next(h for h in d["hotspots"] if h["callout"] == "1")
        self.assertAlmostEqual(h1["x"], 382 / 800)
        old = next(i for i in d["items"] if i["part_number"] == "99999999002")
        self.assertEqual(old["superseded_by"], "99999999012")
        hose = next(i for i in d["items"] if i["callout"] == "4")
        self.assertIsNone(hose["qty"])  # missing stays missing

    def test_fitment_by_production_date(self):
        old = {"from_date": None, "to_date": "199008", "_vehicles": None}
        new = {"from_date": "199009", "to_date": None, "_vehicles": None}
        car = {"id": 1, "prod_from": "199101", "prod_to": "199312"}
        self.assertFalse(epc.item_fits(old, car))
        self.assertTrue(epc.item_fits(new, car))
        self.assertTrue(epc.item_fits(old, {"id": 1, "prod_from": None, "prod_to": None}))  # unknown dates never exclude
        self.assertFalse(epc.item_fits({**new, "_vehicles": {2}}, car))

    def test_search(self):
        self.assertTrue(any(r["part_number"] == "99999999003" for r in epc.search("99 99 9 999 003")))
        self.assertTrue(any(r["part_number"] == "99999999003" for r in epc.search("9999999")))
        self.assertTrue(any(r["part_number"] == "99999999002" for r in epc.search("99999999012")))  # superseded
        self.assertTrue(any(r["description"] == "SAMPLE bolt" for r in epc.search("sample BOLT")))
        self.assertEqual(epc.search(""), [])

    def test_validation_sample(self):
        c = self.checks()
        for k in ("missing_diagram", "missing_image", "missing_part_record", "missing_quantity"):
            self.assertIn(k, c)

    def test_html_import(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        make_png(os.path.join(d, "page_files", "diagram.png"))
        make_png(os.path.join(d, "page_files", "logo.png"), (20, 20))
        with open(os.path.join(d, "page.html"), "w", encoding="utf-8") as f:
            f.write(PAGE)
        rep = epc_import.import_html(os.path.join(d, "page.html"), "Test HTML", "11_0099", "11", "DEMO-SEDAN")
        self.assertEqual(rep.counts["items_added"], 4)
        self.assertEqual(rep.counts["hotspots_added"], 3)
        self.assertTrue(any("9" in p for p in rep.problems))  # hotspot label not in table
        dg = epc.diagram_detail(self.diag("11_0099")["id"])
        self.assertEqual(dg["title"], "Test page: water pump")
        self.assertEqual(dg["image_name"], "diagram.png")
        items = {i["callout"]: i for i in dg["items"]}
        self.assertEqual(items["1"]["part_number"], "11511234567")
        self.assertEqual(items["1"]["to_date"], "199008")
        self.assertEqual(items["2"]["description"], "Gasket & seal")
        self.assertEqual(items["2"]["supplement"], "ENGINE-A")
        self.assertEqual(items["2"]["from_date"], "199009")
        self.assertIsNone(items["3"]["qty"])
        self.assertEqual((items["4"]["part_number"], items["4"]["part_number_raw"]), ("", "--"))
        h2 = next(h for h in dg["hotspots"] if h["callout"] == "2")
        self.assertAlmostEqual(h2["x"], 180 / 400)
        self.assertAlmostEqual(h2["h"], 40 / 300)
        stored = epc.rows("SELECT * FROM source_files WHERE source_id=(SELECT id FROM sources WHERE name='Test HTML')")
        self.assertEqual(sorted(s["kind"] for s in stored), ["html", "image"])
        for s in stored:
            self.assertTrue(os.path.exists(epc.file_path(s["stored_path"])))
        # re-import replaces instead of duplicating
        epc_import.import_html(os.path.join(d, "page.html"), "Test HTML", "11_0099", "11", "DEMO-SEDAN")
        self.assertEqual(len(epc.diagram_detail(dg["id"])["items"]), 4)
        self.assertEqual(epc.row("SELECT COUNT(*) AS n FROM source_files WHERE source_id=(SELECT id FROM sources WHERE name='Test HTML')")["n"], 2)

    def html_items(self, code, tables):
        p = os.path.join(tempfile.mkdtemp(dir=self.tmp), code + ".html")
        with open(p, "w", encoding="utf-8") as f:
            f.write("<html><body>" + "".join("<table>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in t) + "</table>"
                                             for t in tables) + "</body></html>")
        epc_import.import_html(p, "Test HTML tables", code)
        return [(i["callout"], i["part_number"], i["description"]) for i in epc.diagram_detail(self.diag(code)["id"])["items"]]

    def test_html_tables_without_spaced_numbers(self):
        recent = [["Part number", "Description"], ["11 51 1 234 999", "Thermostat"]]
        dashed = [["No.", "Description", "Qty", "Part Number"], ["1", "Water pump", "1", "11-51-1-234-567"], ["2", "Gasket", "1", "11.51.1.234.568"]]
        self.assertEqual(self.html_items("97_0001", [recent, dashed]), [("1", "11511234567", "Water pump"), ("2", "11511234568", "Gasket")])
        other_make = [["Ref", "Part No.", "Description"], ["1", "16100-39466", "Water pump"], ["2", "90915-YZZD1", "Oil filter"]]
        self.assertEqual(self.html_items("97_0002", [other_make]), [("1", "1610039466", "Water pump"), ("2", "90915YZZD1", "Oil filter")])
        no_numbers = [["No.", "Description", "Part Number"], ["1", "Water pump", ""], ["2", "Gasket", ""]]
        self.assertEqual(self.html_items("97_0003", [no_numbers]), [("1", "", "Water pump"), ("2", "", "Gasket")])

    def test_csv_import_and_duplicates(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        p = os.path.join(d, "items.csv")
        with open(p, "w", encoding="utf-8") as f:
            f.write("diagram,callout,part number,description,qty,from,to\n"
                    "17_0001,1,17111234567,Radiator,1,,\n17_0001,2,17111234568,Hose,2,1990-09,\n"
                    "17_0001,2,17111234568,Hose,2,1990-09,\n")
        rep = epc_import.import_csv(p, "Test CSV", vehicle="DEMO-SEDAN", category="17")
        self.assertEqual((rep.counts["items_added"], rep.counts["duplicates_skipped"]), (2, 1))
        hp = os.path.join(d, "hot.csv")
        with open(hp, "w", encoding="utf-8") as f:
            f.write("diagram;callout;x;y;w;h\n17_0001;1;0.1;0.1;0.05;0.05\n17_0001;7;0.5;0.5;0.05;0.05\n")
        epc_import.import_csv(hp, "Test CSV")
        sp = os.path.join(d, "sup.csv")
        with open(sp, "w", encoding="utf-8") as f:
            f.write("old,new,date\n17111234568,17111234569,1992-01\n17111234569,17111234568,\n")
        epc_import.import_csv(sp, "Test CSV")
        c = self.checks()
        self.assertIn("hotspot_without_item", c)
        self.assertIn("item_without_hotspot", c)
        self.assertIn("supersession_cycle", c)
        # duplicate across sources shows in validation
        epc_import.import_csv(p, "Another CSV")
        self.assertIn("duplicate_item", self.checks())
        epc.delete_source(epc.row("SELECT id FROM sources WHERE name='Another CSV'")["id"])
        self.assertNotIn("duplicate_item", self.checks())

    def write_csv(self, name, text, **kw):
        p = os.path.join(tempfile.mkdtemp(dir=self.tmp), name)
        with open(p, "wb") as f:
            f.write(text if isinstance(text, bytes) else text.encode("utf-8"))
        return epc_import.import_csv(p, "Test headers", **kw)

    def test_csv_header_names(self):
        self.write_csv("cats.csv", "code,name,parent\n95,Test group,\n95.1,Test subgroup,95\n")
        self.assertEqual(epc.row("SELECT name FROM categories WHERE code='95.1'")["name"], "Test subgroup")
        self.write_csv("diagrams.csv", "diagram,title,category\n95_0001,Test diagram,95\n")
        self.assertEqual(self.diag("95_0001")["title"], "Test diagram")
        rep = self.write_csv("items.csv", "diagram,title,callout,part number,description,qty,colour\n95_0002,Pump group,1,95111234567,Pump,1,red\n")
        self.assertEqual(self.diag("95_0002")["title"], "Pump group")
        self.assertEqual(epc.row("SELECT description FROM diagram_items WHERE part_number='95111234567'")["description"], "Pump")
        self.assertIn("Columns used: diagram, title, callout, part number (part_number), description, qty; ignored: colour", rep.problems)
        # "Name" still means the description in an items file
        rep = self.write_csv("spaced.csv", "Diagram code,Ref,Part number,Name\n95_0003,1,95111234568,Hose\n")
        self.assertEqual(rep.counts.get("kind_items"), 1)
        self.assertEqual(epc.row("SELECT description FROM diagram_items WHERE part_number='95111234568'")["description"], "Hose")

    def test_dates_and_encodings(self):
        for v in ("1990-08-01", "1990/08/01", "13/8/1990", "8/13/1990", "8/8/1990", "Aug 1990", "1-Aug-90", "1990-8"):
            self.assertEqual(epc_import.yyyymm(v), "199008", v)
        for v in ("8/1/1990", "1990-13", "13/1990", "Augx 1990"):
            self.assertIsNone(epc_import.yyyymm(v), v)
        rep = self.write_csv("dates.csv", "diagram,callout,part number,description,to\n96_0001,1,96111234567,Sch\xf8n pump,8/1/1990\n".encode("cp1252"))
        self.assertIn("Diagram 96_0001 callout 1, to: can't read date '8/1/1990' (day and month could be either way round), left empty", rep.problems)
        self.assertIn("Not valid UTF-8: read as Windows-1252", rep.problems)
        self.assertEqual(epc.row("SELECT description FROM diagram_items WHERE part_number='96111234567'")["description"], "Schøn pump")
        d = tempfile.mkdtemp(dir=self.tmp)
        with open(os.path.join(d, "page.html"), "wb") as f:
            f.write('<html><head><meta charset="windows-1252"></head><body><table><tr><th>No.</th><th>Description</th><th>Part Number</th></tr>'
                    '<tr><td>1</td><td>Sch\xf8n hose</td><td>96 11 1 234 568</td></tr></table></body></html>'.encode("cp1252"))
        epc_import.import_html(os.path.join(d, "page.html"), "Test HTML charset", "96_0002")
        self.assertEqual(epc.row("SELECT description FROM diagram_items WHERE part_number='96111234568'")["description"], "Schøn hose")
        # the newest replacement wins however its date was written
        self.write_csv("sup.csv", "old,new,date\n96111230001,96111230002,1992-01\n96111230001,96111230003,02/1992\n")
        self.assertEqual(epc.supersession_chain("96111230001")[0][0]["new_pn"], "96111230003")

    def test_images_import(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        make_png(os.path.join(d, "SAMPLE_17_01.png"))
        make_png(os.path.join(d, "unknown_99.png"))
        rep = epc_import.import_images([d], "Test images", is_sample=True)
        self.assertEqual((rep.counts["images_attached"], rep.counts["images_unmatched"]), (1, 1))
        self.assertIsNotNone(self.diag("SAMPLE-17-01")["image_file_id"])
        rep = epc_import.import_images([os.path.join(d, "unknown_99.png")], "Test images", create=True, category="99")
        self.assertIsNotNone(self.diag("unknown_99"))

    def test_export_roundtrip(self):
        out = tempfile.mkdtemp(dir=self.tmp)
        path = epc_import.export_bundle(out, include_samples=True)
        with open(path, encoding="utf-8") as f:
            b = json.load(f)
        s = next(x for x in b["diagrams"] if x["code"] == "SAMPLE-11-01")
        self.assertTrue(os.path.exists(os.path.join(out, s["image"])))
        self.assertEqual(len(s["hotspots"]), 4)
        with open(epc_import.export_bundle(tempfile.mkdtemp(dir=self.tmp)), encoding="utf-8") as f:
            real_only = json.load(f)
        self.assertNotIn("SAMPLE-11-01", [x["code"] for x in real_only["diagrams"]])

    def test_api(self):
        ov = self.c.get("/api/epc/overview").get_json()
        self.assertGreaterEqual(ov["stats"]["diagrams"], 2)
        did = self.diag("SAMPLE-11-01")["id"]
        d = self.c.get(f"/api/epc/diagrams/{did}").get_json()
        self.assertEqual(len(d["hotspots"]), 4)
        img = self.c.get("/epc-files/" + d["image_path"])
        self.assertEqual(img.status_code, 200)
        img.close()
        self.assertEqual(self.c.get("/epc-files/../epc.db").status_code, 404)
        self.assertEqual(self.c.get("/epc-files/files/../epc.db").status_code, 404)
        self.assertTrue(self.c.get("/api/epc/search?q=999999990").get_json())
        self.assertIn("issues", self.c.get("/api/epc/validation").get_json())
        tree = self.c.get("/api/epc/categories").get_json()
        self.assertTrue(any(c["code"] == "11" and c["total"] >= 1 for c in tree))
        # garage link: a part in the list shows up on the diagram
        self.c.post("/api/parts", json={"name": "Sample cover", "part_number": "99 99 9 999 001", "status": "On hand"})
        d = self.c.get(f"/api/epc/diagrams/{did}").get_json()
        self.assertEqual(next(i for i in d["items"] if i["callout"] == "1")["garage"][0]["status"], "On hand")
        # upload
        csv_bytes = b"diagram,callout,part number,description,qty\n12_0001,1,12311234567,Alternator,1\n"
        r = self.c.post("/api/epc/import", data={"file": (io.BytesIO(csv_bytes), "alt.csv"), "vehicle": "DEMO-SEDAN", "category": "12"},
                        content_type="multipart/form-data")
        self.assertEqual(r.get_json()["reports"][0]["counts"]["items_added"], 1)
        self.assertEqual(self.c.get(f"/api/epc/diagrams?vehicle={epc.row('SELECT id FROM vehicles WHERE code=?', ('DEMO-SEDAN',))['id']}&category={epc.row('SELECT id FROM categories WHERE code=?', ('12',))['id']}").get_json()[0]["code"], "12_0001")

    def post_import(self, *files):
        r = self.c.post("/api/epc/import", data={"file": [(io.BytesIO(b), n) for n, b in files]}, content_type="multipart/form-data")
        return r.get_json()["reports"]

    def assert_not_locked(self):
        other = sqlite3.connect(epc.EPC_DB, timeout=0.2)
        try:
            other.execute("BEGIN IMMEDIATE")  # "database is locked" while another connection holds a write transaction
            other.rollback()
        finally:
            other.close()

    def test_failed_import_leaves_catalog_unlocked(self):
        broken = {"source": {"name": "Broken bundle"}, "vehicles": [{"code": "BROKEN-1"}], "diagrams": [{"code": "98_0001", "image": 5}]}
        good = b"diagram,callout,part number,description\n98_0003,1,98111234567,Pump\n"
        reports = self.post_import(("broken.json", json.dumps(broken).encode()), ("good.csv", good))
        self.assertIn("error", reports[0])
        self.assertIsNone(epc.row("SELECT 1 FROM sources WHERE name='Broken bundle'"))  # the next file's commit didn't keep it

        def half_written(*a, **kw):
            epc.connect().execute("INSERT INTO sources (name) VALUES ('Half-written images')")
            raise RuntimeError("disk full")
        with mock.patch.object(epc_import, "import_images", half_written), mock.patch.dict(app.config, PROPAGATE_EXCEPTIONS=True):
            with self.assertRaises(RuntimeError):
                self.post_import(("98_0001.png", b"not really a png"))
        self.assert_not_locked()

    def test_bundle_shape_errors(self):
        self.assertEqual(self.post_import(("strings.json", b'{"vehicles": ["E34-535i"]}'))[0]["error"], "vehicles[0] must be an object")
        nested = {"diagrams": [{"code": "98_0002", "items": ["1 pump"]}]}
        self.assertEqual(self.post_import(("nested.json", json.dumps(nested).encode()))[0]["error"], "diagrams[0].items[0] must be an object")

    def test_same_code_on_two_vehicles(self):
        def bundle(source, vehicle, title, pn):
            return {"source": {"name": source}, "vehicles": [{"code": vehicle, "series": vehicle[:3], "model": "x"}],
                    "diagrams": [{"code": "83_0647", "category": "83", "title": title, "vehicles": [vehicle],
                                  "items": [{"callout": "1", "part_number": pn, "description": title}]}]}
        epc_import.import_bundle(bundle("Shared code A", "VEH-A", "Coolant A", "83 19 0 000 001"))
        epc_import.import_bundle(bundle("Shared code B", "VEH-B", "Coolant B", "83 19 0 000 002"))
        ds = epc.rows("SELECT id, title FROM diagrams WHERE code='83_0647' ORDER BY id")
        self.assertEqual([d["title"] for d in ds], ["Coolant A", "Coolant B"])
        for d, pn in zip(ds, ("83190000001", "83190000002")):
            self.assertEqual([i["part_number"] for i in epc.rows("SELECT part_number FROM diagram_items WHERE diagram_id=?", (d["id"],))], [pn])
        # re-importing for the same vehicle updates that vehicle's diagram, doesn't add a third
        rep = epc_import.import_bundle(bundle("Shared code B", "VEH-B", "Coolant B2", "83 19 0 000 003"))
        self.assertEqual(rep.counts.get("diagrams_updated"), 1)
        self.assertEqual([d["title"] for d in epc.rows("SELECT title FROM diagrams WHERE code='83_0647' ORDER BY id")], ["Coolant A", "Coolant B2"])

    def test_delete_vehicle_keeps_other_diagrams(self):
        epc_import.import_bundle({"source": {"name": "Del A"}, "vehicles": [{"code": "DEL-A"}, {"code": "DEL-B"}],
                                  "diagrams": [{"code": "DEL_1", "vehicles": ["DEL-A", "DEL-B"], "items": [{"callout": "1", "part_number": "1"}]},
                                               {"code": "DEL_2", "vehicles": ["DEL-B"]}]})
        vid = epc.row("SELECT id FROM vehicles WHERE code='DEL-B'")["id"]
        self.assertEqual(self.c.delete(f"/api/epc/vehicles/{vid}").status_code, 200)
        self.assertIsNone(epc.row("SELECT 1 FROM vehicles WHERE code='DEL-B'"))
        self.assertIsNone(self.diag("DEL_2"))  # empty and unlinked: removed
        kept = self.diag("DEL_1")
        self.assertEqual([r["code"] for r in epc.rows("SELECT v.code FROM vehicles v JOIN diagram_vehicles dv ON dv.vehicle_id=v.id WHERE dv.diagram_id=?", (kept["id"],))], ["DEL-A"])

    def test_migration_drops_unique_code(self):
        import sqlite3
        path = os.path.join(self.tmp, "v1.db")
        old = sqlite3.connect(path)
        old.executescript(epc.SCHEMA.replace("code TEXT NOT NULL,\n  category_id", "code TEXT NOT NULL UNIQUE,\n  category_id", 1))
        old.execute("INSERT INTO sources (id, name) VALUES (1, 's')")
        old.execute("INSERT INTO diagrams (id, code, title, source_id) VALUES (7, '11_0001', 'Head', 1)")
        old.execute("INSERT INTO diagram_items (diagram_id, callout, source_id) VALUES (7, '1', 1)")
        old.commit()
        self.assertIn("UNIQUE", old.execute("SELECT sql FROM sqlite_master WHERE name='diagrams'").fetchone()[0])
        old.row_factory = sqlite3.Row
        old.execute("PRAGMA foreign_keys = ON")
        epc.drop_unique_diagram_code(old)
        self.assertNotIn("UNIQUE", old.execute("SELECT sql FROM sqlite_master WHERE name='diagrams'").fetchone()[0])
        self.assertEqual(tuple(old.execute("SELECT id, code, title FROM diagrams").fetchone()), (7, "11_0001", "Head"))
        old.execute("INSERT INTO diagrams (code, source_id) VALUES ('11_0001', 1)")  # same code allowed now
        self.assertEqual(old.execute("SELECT COUNT(*) FROM diagram_items WHERE diagram_id=7").fetchone()[0], 1)
        self.assertIn("diagram_items", [r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")])
        old.close()

    def test_zz_remove_sample(self):
        sid = epc.row("SELECT id FROM sources WHERE is_sample=1 AND name='SAMPLE DATA (not real)'")["id"]
        epc.delete_source(sid)
        self.assertIsNone(self.diag("SAMPLE-11-01"))
        self.assertIsNone(epc.row("SELECT 1 FROM vehicles WHERE code='SAMPLE-CAR'"))
        self.assertIsNone(epc.row("SELECT 1 FROM parts WHERE part_number='99999999001'"))
        self.assertIsNotNone(epc.row("SELECT 1 FROM vehicles WHERE code='DEMO-SEDAN'"))


if __name__ == "__main__":
    unittest.main()
