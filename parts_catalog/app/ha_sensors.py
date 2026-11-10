"""Home Assistant sensors published by the add-on itself (no YAML needed).

When running as a Home Assistant add-on (SUPERVISOR_TOKEN is set and `homeassistant_api: true` in config.yaml),
a background thread writes entity states through the Supervisor's Core API proxy:

  sensor.garage_overdue / sensor.garage_due_soon / sensor.garage_parts_to_get        totals
  sensor.garage_<car>_odometer / _status / _next_service / _next_service_due / _parts_to_get

<car> is the vehicle name slugified ("Blue Wagon" -> blue_wagon). States set this way disappear when
HA restarts, so everything is re-sent every few minutes, and changes are sent within PUSH_EVERY seconds.
Disable with the add-on option publish_sensors: false (PARTS_CATALOG_SENSORS=0).
"""
import json
import logging
import os
import re
import threading
import time
import urllib.request

log = logging.getLogger("ha_sensors")
API = "http://supervisor/core/api"
PUSH_EVERY = 30        # seconds between change checks
REFRESH_EVERY = 300    # resend everything (HA may have restarted)


def slug(name, used=None):
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "car"
    if used is not None:
        base, n = s, 2
        while s in used:
            s = f"{base}_{n}"
            n += 1
        used.add(s)
    return s


def due_text(s):
    if not s:
        return "—"
    bits = []
    if s.get("miles_left") is not None:
        m = s["miles_left"]
        bits.append(f"{abs(int(m)):,} mi {'over' if m < 0 else 'left'}")
    if s.get("days_left") is not None:
        d = s["days_left"]
        bits.append(f"{abs(int(d))} days {'over' if d < 0 else 'left'}")
    return " · ".join(bits) or "—"


def status_text(c):
    if c["overdue"]:
        return "Overdue"
    if c["due_soon"]:
        return "Due soon"
    return "OK" if c["next_service"] else "No schedule"


STATUS_ICON = {"Overdue": "mdi:alert-circle", "Due soon": "mdi:clock-alert-outline", "OK": "mdi:check-circle",
               "No schedule": "mdi:calendar-blank"}


def build_states(summary):
    """summary = server.ha_summary() -> {entity_id: {"state": ..., "attributes": {...}}}"""
    cars = summary["cars"]
    out = {
        "sensor.garage_overdue": {"state": sum(c["overdue"] for c in cars),
                                  "attributes": {"friendly_name": "Garage overdue", "icon": "mdi:car-wrench", "state_class": "measurement"}},
        "sensor.garage_due_soon": {"state": sum(c["due_soon"] for c in cars),
                                   "attributes": {"friendly_name": "Garage due soon", "icon": "mdi:calendar-clock", "state_class": "measurement"}},
        "sensor.garage_parts_to_get": {"state": sum(c["parts_needed"] for c in cars),
                                       "attributes": {"friendly_name": "Garage parts to get", "icon": "mdi:package-variant", "state_class": "measurement"}},
    }
    used = set()
    for c in cars:
        s = slug(c["name"], used)
        base = f"sensor.garage_{s}"
        nm = c["name"]
        common = {"car_id": c["id"], "car": nm}
        status = status_text(c)
        ns = c["next_service"]
        out[f"{base}_odometer"] = {"state": c["mileage"] if c["mileage"] is not None else "unknown", "attributes": {
            **common, "friendly_name": f"{nm} odometer", "icon": "mdi:counter", "unit_of_measurement": "mi",
            "device_class": "distance", "state_class": "total_increasing", "reading_date": c["mileage_date"]}}
        out[f"{base}_status"] = {"state": status, "attributes": {
            **common, "friendly_name": f"{nm} service status", "icon": STATUS_ICON[status],
            "overdue": c["overdue"], "due_soon": c["due_soon"]}}
        out[f"{base}_next_service"] = {"state": ns["title"] if ns else "None scheduled", "attributes": {
            **common, "friendly_name": f"{nm} next service", "icon": "mdi:wrench-clock",
            **({"due_mileage": ns["due_mileage"], "due_date": ns["due_date"], "miles_left": ns["miles_left"],
                "days_left": ns["days_left"], "state": ns["state"]} if ns else {})}}
        out[f"{base}_next_service_due"] = {"state": due_text(ns), "attributes": {
            **common, "friendly_name": f"{nm} next service due", "icon": "mdi:calendar-arrow-right"}}
        out[f"{base}_parts_to_get"] = {"state": c["parts_needed"], "attributes": {
            **common, "friendly_name": f"{nm} parts to get", "icon": "mdi:package-variant", "state_class": "measurement"}}
    return out


def dashboard_yaml(summary, app_path="/local_parts_catalog"):
    """A ready-to-paste sections view (Edit dashboard > ⋮ > Raw configuration editor, under views:)."""
    cars = summary["cars"]
    used = set()
    lines = [
        "- title: Garage", "  path: garage", "  icon: mdi:car-wrench", "  type: sections", "  max_columns: 3",
        "  badges:",
        "    - {type: entity, entity: sensor.garage_overdue, name: Overdue, show_name: true}",
        "    - {type: entity, entity: sensor.garage_due_soon, name: Due soon, show_name: true}",
        "    - {type: entity, entity: sensor.garage_parts_to_get, name: Parts to get, show_name: true}",
        "  sections:",
    ]
    for c in cars:
        s = slug(c["name"], used)
        b = f"sensor.garage_{s}"
        lines += [
            "    - type: grid", "      cards:",
            f"        - type: heading",
            f"          heading: {json.dumps(c['name'])}",
            "          icon: mdi:car",
            f"          tap_action: {{action: navigate, navigation_path: {app_path}}}",
            "          badges:",
            f"            - {{type: entity, entity: {b}_status, show_state: true, show_icon: true}}",
            "        - type: entities", "          state_color: false", "          entities:",
            f"            - {{entity: {b}_odometer, name: Odometer}}",
            f"            - {{entity: {b}_next_service, name: Next service}}",
            f"            - {{entity: {b}_next_service_due, name: Due, icon: mdi:calendar-arrow-right}}",
            f"            - {{entity: {b}_parts_to_get, name: Parts to get}}",
        ]
    lines += [
        "    - type: grid", "      cards:",
        "        - {type: heading, heading: Garage app, icon: mdi:car-wrench}",
        f"        - {{type: button, name: Open the Garage app, icon: mdi:open-in-app, show_state: false, tap_action: {{action: navigate, navigation_path: {app_path}}}}}",
    ]
    return "\n".join(lines) + "\n"


class Publisher:
    def __init__(self, get_summary, token, api=API):
        self.get_summary, self.token, self.api = get_summary, token, api
        self.sent = {}
        self.last_full = 0

    def _req(self, method, entity_id, body=None):
        req = urllib.request.Request(f"{self.api}/states/{entity_id}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()

    def push(self, force=False):
        states = build_states(self.get_summary())
        full = force or time.time() - self.last_full > REFRESH_EVERY
        for eid, st in states.items():
            if full or self.sent.get(eid) != st:
                self._req("POST", eid, st)
                self.sent[eid] = st
        for eid in [e for e in self.sent if e not in states]:  # deleted or renamed car
            try:
                self._req("DELETE", eid)
            except Exception:  # noqa: BLE001 - already gone is fine
                pass
            self.sent.pop(eid, None)
        if full:
            self.last_full = time.time()
        return len(states)

    def run(self):
        while True:
            try:
                self.push()
            except Exception as e:  # noqa: BLE001 - HA restarting etc.; try again next round
                log.warning("Couldn't update Home Assistant sensors: %s", e)
                self.last_full = 0
            time.sleep(PUSH_EVERY)


def start(get_summary):
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token or os.environ.get("PARTS_CATALOG_SENSORS", "1") == "0":
        return None
    p = Publisher(get_summary, token)
    threading.Thread(target=p.run, name="ha-sensors", daemon=True).start()
    log.info("Publishing Home Assistant sensors")
    return p
