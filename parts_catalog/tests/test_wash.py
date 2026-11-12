"""Car wash weather (app/wash.py): the verdict, when to notify, and the /api/wash endpoints with a fake HA."""
import datetime as dt
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("PARTS_CATALOG_DATA", tempfile.mkdtemp())

from app import db, epc, epc_api, server, wash  # noqa: E402
from app.server import app  # noqa: E402

NOW = dt.datetime(2026, 6, 1, 13, 0, tzinfo=dt.timezone.utc)
CFG = wash.config({"wash": {"enabled": True, "weather_entity": "weather.home"}})


def state(cond="sunny", temp=68):
    return {"state": cond, "attributes": {"temperature": temp, "temperature_unit": "°F", "friendly_name": "Home"}}


def hourly(n=48, wet_at=None, start=NOW, **extra):
    out = []
    for h in range(n):
        f = {"datetime": (start + dt.timedelta(hours=h)).isoformat(), "condition": "sunny", "temperature": 66,
             "precipitation": 0.0, **extra}
        if wet_at is not None and h == wet_at:
            f.update(condition="rainy", precipitation=0.12)
        out.append(f)
    return out


class EvaluateTest(unittest.TestCase):
    def test_good_day(self):
        v = wash.evaluate(state(), hourly(), [], CFG, NOW)
        self.assertTrue(v["good"], v["reasons"])
        self.assertIn("no rain in the next 48 hours", v["summary"])
        self.assertEqual(len(v["hours"]), 48)

    def test_rain_later(self):
        v = wash.evaluate(state(), hourly(wet_at=30), [], CFG, NOW)
        self.assertFalse(v["good"])
        self.assertIn("rainy expected in about 30 hours", v["reasons"])

    def test_rain_beyond_the_window_is_fine(self):
        self.assertTrue(wash.evaluate(state(), hourly(n=60, wet_at=50), [], CFG, NOW)["good"])

    def test_cold_or_raining_now(self):
        self.assertIn("only 41°F (below 50°F)", wash.evaluate(state(temp=41), hourly(), [], CFG, NOW)["reasons"])
        self.assertIn("it's pouring now", wash.evaluate(state("pouring"), hourly(), [], CFG, NOW)["reasons"])

    def test_rain_chance(self):
        v = wash.evaluate(state(), hourly(precipitation_probability=40), [], CFG, NOW)
        self.assertIn("40% chance of rain", v["reasons"][0])
        self.assertTrue(wash.evaluate(state(), hourly(precipitation_probability=10, precipitation=0.2), [], CFG, NOW)["good"])

    def test_daily_fills_a_short_hourly_forecast(self):
        days = [{"datetime": (NOW + dt.timedelta(days=d)).replace(hour=0).isoformat(), "condition": c, "precipitation": 0}
                for d, c in ((0, "sunny"), (1, "sunny"), (2, "rainy"))]
        self.assertFalse(wash.evaluate(state(), hourly(n=12), days, CFG, NOW)["good"])
        self.assertTrue(wash.evaluate(state(), hourly(n=12), days[:2] + [{**days[2], "condition": "cloudy"}], CFG, NOW)["good"])
        v = wash.evaluate(state(), hourly(n=12), [], CFG, NOW)
        self.assertIn("forecast only covers the next 12 hours", v["reasons"][0])

    def test_unavailable(self):
        self.assertFalse(wash.evaluate({"state": "unavailable", "attributes": {}}, [], [], CFG, NOW)["good"])

    def test_config_is_clamped(self):
        c = wash.clean_config({"min_temp": "x", "hours": 999, "notify_hour": -3, "every_days": 0,
                               "notify": ["notify.mobile_app_phone", " "], "bogus": 1})
        self.assertEqual((c["min_temp"], c["hours"], c["notify_hour"], c["every_days"], c["notify"]),
                         (50, 120, 0, 1, ["mobile_app_phone"]))
        self.assertNotIn("bogus", c)


class FakeHA:
    def __init__(self):
        self.sent, self.sensor = [], None
        self.cond, self.wet_at = "sunny", None

    def state(self, entity):
        return state(self.cond)

    def forecast(self, entity, kind):
        return hourly(wet_at=self.wet_at, start=dt.datetime.now(dt.timezone.utc)) if kind == "hourly" else []

    def weather_entities(self):
        return [{"entity_id": "weather.home", "name": "Home"}]

    def notify_services(self):
        return ["mobile_app_phone"]

    def time_zone(self):
        return None

    def notify(self, services, title, message, url=None):
        self.sent.append((tuple(services), title, message))
        return list(services) or ["persistent notification"]

    def set_sensor(self, v):
        self.sensor = v["good"]


class WasherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.saved = (db.DATA_DIR, db.DB_PATH, db.DOC_DIR, epc.EPC_DIR, epc.EPC_DB, epc.FILES_DIR, server.SETTINGS_PATH, wash.washer)
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
        cls.car = cls.c.post("/api/cars", json={"name": "DEMO-SEDAN"}).get_json()["id"]

    @classmethod
    def tearDownClass(cls):
        db.connect().close()
        db._local.conn = None
        epc.close()
        (db.DATA_DIR, db.DB_PATH, db.DOC_DIR, epc.EPC_DIR, epc.EPC_DB, epc.FILES_DIR, server.SETTINGS_PATH, wash.washer) = cls.saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.ha = FakeHA()
        wash.washer = wash.Washer(server.load_settings, server.last_wash, os.path.join(self.tmp, "wash.json"), ha=self.ha)
        if os.path.exists(os.path.join(self.tmp, "wash.json")):
            os.remove(os.path.join(self.tmp, "wash.json"))

    def test_not_available_without_ha(self):
        wash.washer = wash.Washer(server.load_settings, server.last_wash, os.path.join(self.tmp, "wash.json"), ha=None)
        wash.washer.ha = None
        r = self.c.get("/api/wash").get_json()
        self.assertFalse(r["available"])
        self.assertEqual(self.c.post("/api/wash/test").status_code, 400)

    def test_api_and_check(self):
        r = self.c.get("/api/wash").get_json()
        self.assertTrue(r["available"])
        self.assertEqual(r["suggested_entity"], "weather.home")
        self.assertIsNone(wash.washer.check())  # off: nothing happens
        r = self.c.put("/api/wash", json={"enabled": True, "weather_entity": "weather.home", "notify": ["mobile_app_phone"],
                                          "notify_hour": 0, "every_days": 3}).get_json()
        self.assertTrue(r["verdict"]["good"])
        self.assertTrue(wash.washer.check()["good"])
        self.assertEqual(len(self.ha.sent), 1)
        self.assertTrue(self.ha.sensor)
        wash.washer.check()  # already told today
        self.assertEqual(len(self.ha.sent), 1)
        self.assertTrue(self.c.get("/api/wash").get_json()["last_sent"])
        # a test always sends, and doesn't count as the reminder
        self.assertEqual(self.c.post("/api/wash/test").get_json()["sent"], ["mobile_app_phone"])
        self.assertEqual(len(self.ha.sent), 2)

    def test_skips_after_a_logged_wash_and_bad_weather(self):
        self.c.put("/api/wash", json={"enabled": True, "weather_entity": "weather.home", "notify_hour": 0, "every_days": 5})
        self.c.post(f"/api/cars/{self.car}/services", json={"title": "Wash", "date": dt.date.today().isoformat()})
        self.assertTrue(wash.washer.check()["good"])
        self.assertEqual(self.ha.sent, [])
        self.assertEqual(self.c.get("/api/wash").get_json()["last_wash"], dt.date.today().isoformat())

    def test_rain_coming_means_no_nudge(self):
        self.c.put("/api/wash", json={"enabled": True, "weather_entity": "weather.home", "notify_hour": 0})
        self.ha.wet_at = 20
        self.assertFalse(wash.washer.check()["good"])
        self.assertFalse(self.ha.sensor)
        self.assertEqual(self.ha.sent, [])

    def test_due_to_notify(self):
        w = wash.washer
        cfg = wash.config({"wash": {"notify_hour": 9, "every_days": 3}})
        morning = dt.datetime(2026, 6, 10, 8, 0)
        self.assertFalse(w.due_to_notify(cfg, morning))
        w.last_wash = lambda: None
        self.assertTrue(w.due_to_notify(cfg, morning.replace(hour=10)))
        w._save_state({"last_sent": "2026-06-08T10:00"})
        self.assertFalse(w.due_to_notify(cfg, morning.replace(hour=10)))   # 2 days ago
        self.assertTrue(w.due_to_notify(cfg, dt.datetime(2026, 6, 11, 10)))  # 3 days later


if __name__ == "__main__":
    unittest.main()
