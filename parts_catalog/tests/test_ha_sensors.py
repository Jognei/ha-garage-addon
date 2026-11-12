"""The add-on's own Home Assistant sensors, against a fake Supervisor Core API."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import ha_sensors


def summary(cars):
    return {"cars": cars}


CAR = {"id": 3, "name": "Blue Wagon", "mileage": 119800, "mileage_date": "2026-11-09", "overdue": 1, "due_soon": 1,
       "parts_needed": 2, "next_service": {"title": "Engine oil & filter", "due_mileage": 118734, "due_date": "2026-12-28",
                                            "miles_left": -1066, "days_left": 49, "state": "overdue"}}
TRUCK = {"id": 4, "name": "Work Truck", "mileage": None, "mileage_date": None, "overdue": 0, "due_soon": 0,
         "parts_needed": 0, "next_service": None}


class FakeHA(BaseHTTPRequestHandler):
    calls = []

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        FakeHA.calls.append((self.command, self.path, self.headers.get("Authorization"), body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    do_POST = do_DELETE = _handle
    known = True  # whether HA "has" our states (False = it restarted)

    def do_GET(self):
        FakeHA.calls.append(("GET", self.path, self.headers.get("Authorization"), None))
        self.send_response(200 if FakeHA.known else 404)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


class SensorTest(unittest.TestCase):
    def test_states(self):
        st = ha_sensors.build_states(summary([CAR, TRUCK]))
        self.assertEqual(st["sensor.garage_overdue"]["state"], 1)
        self.assertEqual(st["sensor.garage_parts_to_get"]["state"], 2)
        self.assertEqual(st["sensor.garage_blue_wagon_odometer"]["state"], 119800)
        self.assertEqual(st["sensor.garage_blue_wagon_odometer"]["attributes"]["unit_of_measurement"], "mi")
        self.assertEqual(st["sensor.garage_blue_wagon_status"]["state"], "Overdue")
        self.assertEqual(st["sensor.garage_blue_wagon_next_service_due"]["state"], "1,066 mi over · 49 days left")
        self.assertEqual(st["sensor.garage_work_truck_odometer"]["state"], "unknown")
        self.assertEqual(st["sensor.garage_work_truck_status"]["state"], "No schedule")
        self.assertEqual(st["sensor.garage_work_truck_next_service_due"]["state"], "—")

    def test_duplicate_names_get_unique_ids(self):
        st = ha_sensors.build_states(summary([dict(TRUCK, id=1), dict(TRUCK, id=2)]))
        self.assertIn("sensor.garage_work_truck_status", st)
        self.assertIn("sensor.garage_work_truck_2_status", st)

    def test_publisher_pushes_changes_and_removes_deleted(self):
        srv = HTTPServer(("127.0.0.1", 0), FakeHA)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            data = {"cars": [CAR, TRUCK]}
            p = ha_sensors.Publisher(lambda: data, "tok", api=f"http://127.0.0.1:{srv.server_port}/core/api")
            FakeHA.calls.clear()
            n = p.push()
            self.assertEqual(len(FakeHA.calls), n)
            self.assertTrue(all(c[2] == "Bearer tok" and c[0] == "POST" for c in FakeHA.calls))
            self.assertIn("/core/api/states/sensor.garage_blue_wagon_odometer", [c[1] for c in FakeHA.calls])
            FakeHA.calls.clear()
            p.push()
            self.assertEqual([c[0] for c in FakeHA.calls], ["GET"])  # nothing changed: only the restart check
            FakeHA.known = False  # HA restarted and forgot our states
            FakeHA.calls.clear()
            p.push()
            self.assertEqual(len([c for c in FakeHA.calls if c[0] == "POST"]), n)
            FakeHA.known = True
            data["cars"] = [dict(CAR, mileage=120000)]
            p.push()
            posted = [c[1] for c in FakeHA.calls if c[0] == "POST"]
            deleted = [c[1] for c in FakeHA.calls if c[0] == "DELETE"]
            self.assertIn("/core/api/states/sensor.garage_blue_wagon_odometer", posted)
            self.assertEqual(len(deleted), 5)  # the truck's sensors
            FakeHA.calls.clear()
            p.push(force=True)
            self.assertEqual(len([c for c in FakeHA.calls if c[0] == "POST"]), 3 + 5)
        finally:
            srv.shutdown()

    def test_dashboard_yaml(self):
        y = ha_sensors.dashboard_yaml(summary([CAR, TRUCK]))
        self.assertTrue(y.startswith("  - title: Garage\n"))
        self.assertIn("entity: sensor.garage_blue_wagon_odometer", y)
        self.assertIn('heading: "Work Truck"', y)

    def test_dashboard_yaml_pastes_under_views(self):
        """Home Assistant's raw configuration editor shows the views indented two spaces."""
        y = ha_sensors.dashboard_yaml(summary([CAR, TRUCK]))
        try:
            import yaml
        except ImportError:
            yaml = None
        if yaml:
            views = yaml.safe_load("views:\n  - title: Home\n    cards: []\n" + y)["views"]
            self.assertEqual([v["title"] for v in views], ["Home", "Garage"])
        else:
            self.assertEqual([ln for ln in y.splitlines() if not ln.startswith("  ")], [])

    def test_disabled_without_token(self):
        self.assertIsNone(ha_sensors.start(lambda: {"cars": []}))


if __name__ == "__main__":
    unittest.main()
