"""The add-on's own Home Assistant sensors, against a fake Supervisor Core API."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from app import ha_sensors


def summary(cars):
    return {"cars": cars}


CAR = {"id": 3, "name": "Blue Wagon", "mileage": 119800, "mileage_date": "2026-11-09", "overdue": 1, "due_soon": 1,
       "parts_needed": 2, "next_service": {"title": "Engine oil & filter", "due_mileage": 118734, "due_date": "2026-12-28",
                                            "miles_left": -1066, "days_left": 49, "est_date": None, "state": "overdue"}}
TRUCK = {"id": 4, "name": "Work Truck", "mileage": None, "mileage_date": None, "overdue": 0, "due_soon": 0,
         "parts_needed": 0, "next_service": None}
# What HA has before the add-on starts: a total, Blue Wagon's odometer, a vehicle no longer in the garage,
# and two that aren't the add-on's: the user's own sensor.garage_door, and a light that happens to have a car_id.
HA_STATES = [{"entity_id": "sensor.garage_overdue", "state": "1", "attributes": {"friendly_name": "Garage overdue"}},
             {"entity_id": "sensor.garage_blue_wagon_odometer", "state": "119800", "attributes": {"car_id": 3}},
             {"entity_id": "sensor.garage_old_van_status", "state": "OK", "attributes": {"car_id": 9}},
             {"entity_id": "sensor.garage_door", "state": "closed", "attributes": {"friendly_name": "Garage door"}},
             {"entity_id": "light.garage", "state": "on", "attributes": {"car_id": 1}}]


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
    states = []   # what GET /states lists

    def do_GET(self):
        FakeHA.calls.append(("GET", self.path, self.headers.get("Authorization"), None))
        listing = self.path.endswith("/states")
        self.send_response(200 if FakeHA.known or listing else 404)
        self.end_headers()
        self.wfile.write(json.dumps(FakeHA.states if listing else {}).encode())

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

    def test_next_service_due_carries_the_estimated_date(self):
        ns = dict(CAR["next_service"], miles_left=900, days_left=200, est_date="2027-03-01", state="ok")
        st = ha_sensors.build_states(summary([dict(CAR, next_service=ns)]))
        self.assertEqual(st["sensor.garage_blue_wagon_next_service_due"]["attributes"]["estimated_date"], "2027-03-01")

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
            self.assertEqual([c[0] for c in FakeHA.calls], ["GET"] + ["POST"] * n)  # the GET lists what an earlier start left
            self.assertTrue(all(c[2] == "Bearer tok" for c in FakeHA.calls))
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

    def deleted_by(self, run):
        """Entity ids `run(api)` deletes from a fake HA holding HA_STATES."""
        srv = HTTPServer(("127.0.0.1", 0), FakeHA)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            FakeHA.states = HA_STATES
            FakeHA.calls.clear()
            run(f"http://127.0.0.1:{srv.server_port}/core/api")
            return sorted(c[1].rsplit("/", 1)[1] for c in FakeHA.calls if c[0] == "DELETE")
        finally:
            FakeHA.states = []
            srv.shutdown()

    def test_first_push_removes_what_an_earlier_start_left(self):
        deleted = self.deleted_by(lambda api: ha_sensors.Publisher(lambda: summary([CAR]), "tok", api=api).push())
        self.assertEqual(deleted, ["sensor.garage_old_van_status"])

    def test_publishing_off_removes_the_addon_states_once(self):
        def start(api):
            with mock.patch.dict(os.environ, {"SUPERVISOR_TOKEN": "tok", "PARTS_CATALOG_SENSORS": "0"}), \
                    mock.patch.object(ha_sensors, "API", api):
                self.assertIsNone(ha_sensors.start(lambda: summary([CAR])))
        self.assertEqual(self.deleted_by(start),
                         ["sensor.garage_blue_wagon_odometer", "sensor.garage_old_van_status", "sensor.garage_overdue"])

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
