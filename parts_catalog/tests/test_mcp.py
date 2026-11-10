"""The MCP agent endpoint (/mcp) and the catalog feature switch."""
import datetime as dt
import json
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("PARTS_CATALOG_DATA", tempfile.mkdtemp())

from app import db, epc, epc_api, server  # noqa: E402
from app.server import app  # noqa: E402


class McpTest(unittest.TestCase):
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
        cls.car = cls.c.post("/api/cars", json={"name": "Blue Wagon", "mileage": "100,000"}).get_json()["id"]
        cls.c.post(f"/api/cars/{cls.car}/schedules", json={"items": [
            {"title": "Engine oil & filter", "interval_miles": 5000, "interval_months": 12,
             "last_date": (dt.date.today() - dt.timedelta(days=30)).isoformat(), "last_mileage": 95400}]})

    @classmethod
    def tearDownClass(cls):
        db.connect().close()
        db._local.conn = None
        epc.close()
        (db.DATA_DIR, db.DB_PATH, db.DOC_DIR, epc.EPC_DIR, epc.EPC_DB, epc.FILES_DIR, server.SETTINGS_PATH) = cls.saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def rpc(self, method, params=None, key=None, mid=1):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if mid is not None:
            msg["id"] = mid
        return self.c.post("/mcp", json=msg, headers={"Authorization": f"Bearer {key or self.key}"})

    def tool(self, name, **args):
        r = self.rpc("tools/call", {"name": name, "arguments": args}).get_json()["result"]
        text = r["content"][0]["text"]
        return (text if r["isError"] else json.loads(text)), r["isError"]

    def setUp(self):
        self.key = self.c.get("/api/settings").get_json()["settings"]["agent_key"]
        if not self.key:
            self.c.put("/api/settings", json={"agent_key": "new"})
            self.key = self.c.get("/api/settings").get_json()["settings"]["agent_key"]

    def test_auth(self):
        self.assertEqual(self.c.post("/mcp", json={}).status_code, 401)
        self.assertEqual(self.rpc("ping", key="wrong").status_code, 401)
        self.c.put("/api/settings", json={"agent_key": None})
        self.assertEqual(self.c.post("/mcp", json={}, headers={"Authorization": "Bearer x"}).status_code, 403)
        self.c.put("/api/settings", json={"agent_key": "new"})
        new = self.c.get("/api/settings").get_json()["settings"]["agent_key"]
        self.assertEqual(self.rpc("ping", key=self.key).status_code, 401)  # old key no longer works
        self.assertEqual(self.rpc("ping", key=new).status_code, 200)

    def test_protocol(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18"}).get_json()["result"]
        self.assertEqual(init["serverInfo"]["name"], "garage")
        self.assertIn("tools", init["capabilities"])
        self.assertEqual(self.rpc("notifications/initialized", mid=None).status_code, 202)
        names = [t["name"] for t in self.rpc("tools/list").get_json()["result"]["tools"]]
        for n in ("list_vehicles", "whats_due", "log_service", "update_mileage", "add_parts", "update_part"):
            self.assertIn(n, names)
        self.assertIn("error", self.rpc("nope").get_json())

    def test_tools_flow(self):
        vs, err = self.tool("list_vehicles")
        self.assertFalse(err)
        self.assertEqual(vs[0]["name"], "Blue Wagon")
        due, _ = self.tool("whats_due", vehicle="wagon")
        self.assertEqual(due[0]["due"][0]["title"], "Engine oil & filter")  # 400 mi left: due soon
        r, _ = self.tool("update_mileage", vehicle="Blue", mileage=100200)
        self.assertEqual(r["odometer"], 100200)
        r, _ = self.tool("update_mileage", vehicle="Blue", mileage=90000)
        self.assertEqual(r["odometer"], 100200)
        self.assertIn("correct=true", r["note"])
        added, _ = self.tool("add_parts", vehicle=str(self.car), items=[
            {"name": "Oil filter", "part_number": "11427953129", "status": "On hand", "price": 9.5}])
        pid = added["part_ids"][0]
        parts, _ = self.tool("list_parts", search="11427953129")
        self.assertEqual(parts[0]["id"], pid)
        logged, _ = self.tool("log_service", vehicle="wagon", title="Oil change", mileage=100300,
                              schedules=["oil"], part_ids=[pid])
        self.assertEqual(logged["schedules_reset"], 1)
        part, _ = self.tool("update_part", part_id=pid, notes="Mann")
        self.assertEqual((part["status"], part["installed_mileage"], part["notes"]), ("Installed", 100300, "Mann"))
        hist, _ = self.tool("list_services", vehicle="wagon")
        self.assertEqual(hist[0]["title"], "Oil change")
        sched, _ = self.tool("add_schedule", vehicle="wagon", title="Spark plugs", interval_miles=60000)
        self.assertTrue(sched["ids"])

    def test_tool_errors(self):
        msg, err = self.tool("get_vehicle", vehicle="nonexistent")
        self.assertTrue(err)
        self.assertIn("No vehicle matches", msg)
        msg, err = self.tool("get_vehicle")
        self.assertTrue(err)

    def test_internal_calls_pass_allowlist(self):
        try:
            server.ALLOWED_CLIENTS = {"172.30.32.2"}
            r = self.c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                          "params": {"name": "list_vehicles", "arguments": {}}},
                            headers={"Authorization": f"Bearer {self.key}"}, environ_base={"REMOTE_ADDR": "172.30.32.2"})
            self.assertFalse(r.get_json()["result"]["isError"])
            self.assertEqual(self.c.get("/api/cars").status_code, 403)  # outsiders still blocked
        finally:
            server.ALLOWED_CLIENTS = set()

    def test_catalog_switch(self):
        self.assertFalse(self.c.get("/api/refdata").get_json()["features"]["catalog"])
        self.c.put("/api/settings", json={"catalog": True})
        self.assertTrue(self.c.get("/api/refdata").get_json()["features"]["catalog"])
        self.c.put("/api/settings", json={"catalog": False})


if __name__ == "__main__":
    unittest.main()
