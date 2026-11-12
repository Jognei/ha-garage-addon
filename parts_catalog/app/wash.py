"""Car wash weather: a nudge when it's nice out and no rain is forecast for the next couple of days.

Reads a Home Assistant weather entity (current conditions + hourly forecast via weather.get_forecasts) and, when the
day looks good, sends a notification through HA (notify.<service>, e.g. a phone's mobile_app_*, or a persistent
notification). It also keeps `binary_sensor.garage_wash_weather` up to date for dashboards and automations.

Settings (settings.json "wash", Settings › Car wash weather): enabled, weather_entity, notify (list of notify services),
min_temp (in HA's temperature unit), hours (how far ahead must be dry), max_rain_chance (%, when the forecast has
one), notify_hour (earliest local hour), every_days (minimum days between nudges; a logged wash/detail also counts).

Talks to HA through the Supervisor (add-on: SUPERVISOR_TOKEN) or, for testing elsewhere, PARTS_CATALOG_HA_URL
(e.g. http://<home-assistant-host>:8123/api) + PARTS_CATALOG_HA_TOKEN.
"""
import datetime as dt
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("wash")

DEFAULTS = {"enabled": False, "weather_entity": "", "notify": [], "min_temp": 50, "hours": 48, "max_rain_chance": 20,
            "notify_hour": 9, "every_days": 5}
WET = {"rainy", "pouring", "lightning-rainy", "lightning", "snowy", "snowy-rainy", "hail", "exceptional"}
CHECK_EVERY = 1800      # seconds between checks
CACHE_FOR = 600         # seconds a verdict is reused by the UI
SENSOR = "binary_sensor.garage_wash_weather"
WASH_WORDS = ("wash", "detail")


def config(settings):
    c = {**DEFAULTS, **(settings.get("wash") or {})}
    for k in ("min_temp", "hours", "max_rain_chance", "notify_hour", "every_days"):
        try:
            c[k] = float(c[k]) if k == "min_temp" else int(c[k])
        except (TypeError, ValueError):
            c[k] = DEFAULTS[k]
    c["hours"] = max(6, min(c["hours"], 120))
    c["notify_hour"] = max(0, min(c["notify_hour"], 23))
    c["every_days"] = max(1, min(c["every_days"], 60))
    c["notify"] = [str(n).removeprefix("notify.") for n in (c["notify"] or []) if str(n).strip()]
    c["enabled"] = bool(c["enabled"])
    return c


def clean_config(b):
    """Only known keys from a settings PUT."""
    return config({"wash": {k: v for k, v in (b or {}).items() if k in DEFAULTS}})


def _parse(t):
    try:
        d = dt.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _wet(f, cfg):
    """Why a forecast entry (hour or day) would spoil a wash, or None."""
    cond = f.get("condition")
    if cond in WET:
        return cond.replace("-", " ")
    p = f.get("precipitation_probability")
    if p is not None and p > cfg["max_rain_chance"]:
        return f"{p:.0f}% chance of rain"
    amt = f.get("precipitation")
    if p is None and amt and amt > 0:
        return "rain"
    return None


def evaluate(state, hourly, daily, cfg, now=None):
    """-> {"good": bool, "reasons": [...], "summary": str, "temp", "unit", "condition", "hours": [{t, wet}], "checked_until"}

    state = the weather entity's state object; hourly / daily = weather.get_forecasts lists (either may be empty)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    end = now + dt.timedelta(hours=cfg["hours"])
    attrs = (state or {}).get("attributes") or {}
    cond = (state or {}).get("state")
    temp = attrs.get("temperature")
    unit = attrs.get("temperature_unit") or ""
    reasons = []
    if cond in (None, "unavailable", "unknown"):
        reasons.append("the weather entity has no data right now")
    elif cond in WET:
        reasons.append(f"it's {cond.replace('-', ' ')} now")
    if temp is not None and temp < cfg["min_temp"]:
        reasons.append(f"only {temp:g}{unit} (below {cfg['min_temp']:g}{unit})")

    hours, first_wet, covered = [], None, now
    for f in hourly or []:
        t = _parse(f.get("datetime"))
        if not t or t + dt.timedelta(hours=1) <= now or t >= end:
            continue
        why = _wet(f, cfg)
        hours.append({"t": t.isoformat(), "wet": bool(why), "temp": f.get("temperature")})
        covered = max(covered, t + dt.timedelta(hours=1))
        if why and not first_wet:
            first_wet = (t, why)
    if covered < end:  # hourly forecast too short (or missing): fill the rest from the daily one
        for f in daily or []:
            t = _parse(f.get("datetime"))
            if not t or t + dt.timedelta(days=1) <= covered or t >= end:
                continue
            why = _wet(f, cfg)
            covered = max(covered, t + dt.timedelta(days=1))
            if why and (not first_wet or t < first_wet[0]):
                first_wet = (max(t, now), why)
    if first_wet:
        h = max(0, int((first_wet[0] - now).total_seconds() // 3600))
        reasons.append(f"{first_wet[1]} expected {'within the hour' if h < 1 else f'in about {h} hours'}")
    if covered < end - dt.timedelta(hours=2) and not reasons:
        reasons.append(f"the forecast only covers the next {max(0, int((covered - now).total_seconds() // 3600))} hours")

    good = not reasons
    now_txt = ", ".join(x for x in [f"{temp:g}{unit}" if temp is not None else "", (cond or "").replace("-", " ")] if x)
    summary = (f"{now_txt} and no rain in the next {cfg['hours']} hours." if good
               else "Not a great wash day: " + "; ".join(reasons) + ".")
    return {"good": good, "reasons": reasons, "summary": summary, "temp": temp, "unit": unit, "condition": cond,
            "hours": hours, "checked_until": covered.isoformat()}


# ---------- Home Assistant ----------

class HA:
    def __init__(self, base, token):
        self.base, self.token = base.rstrip("/"), token

    @classmethod
    def from_env(cls):
        if os.environ.get("SUPERVISOR_TOKEN"):
            return cls("http://supervisor/core/api", os.environ["SUPERVISOR_TOKEN"])
        if os.environ.get("PARTS_CATALOG_HA_URL") and os.environ.get("PARTS_CATALOG_HA_TOKEN"):
            return cls(os.environ["PARTS_CATALOG_HA_URL"], os.environ["PARTS_CATALOG_HA_TOKEN"])
        return None

    def req(self, method, path, body=None):
        r = urllib.request.Request(f"{self.base}/{path.lstrip('/')}", method=method,
                                   data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None

    def state(self, entity):
        try:
            return self.req("GET", f"states/{entity}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def forecast(self, entity, kind):
        try:
            r = self.req("POST", "services/weather/get_forecasts?return_response", {"entity_id": entity, "type": kind})
            return ((r or {}).get("service_response") or {}).get(entity, {}).get("forecast") or []
        except urllib.error.HTTPError:
            return []  # the entity doesn't offer that kind of forecast

    def weather_entities(self):
        return sorted(({"entity_id": s["entity_id"], "name": s["attributes"].get("friendly_name") or s["entity_id"]}
                       for s in self.req("GET", "states") if s["entity_id"].startswith("weather.")), key=lambda x: x["name"])

    def notify_services(self):
        for d in self.req("GET", "services"):
            if d.get("domain") == "notify":
                return sorted(k for k in d.get("services", {}) if k not in ("notify", "send_message", "persistent_notification"))
        return []

    def time_zone(self):
        try:
            return self.req("GET", "config").get("time_zone")
        except Exception:  # noqa: BLE001
            return None

    def notify(self, services, title, message, url=None):
        if not services:
            self.req("POST", "services/persistent_notification/create",
                     {"title": title, "message": message, "notification_id": "garage_wash"})
            return ["persistent notification"]
        sent = []
        for s in services:
            data = {"title": title, "message": message}
            if url:
                data["data"] = {"url": url, "clickAction": url, "tag": "garage_wash"}
            self.req("POST", f"services/notify/{s}", data)
            sent.append(s)
        return sent

    def set_sensor(self, verdict):
        self.req("POST", f"states/{SENSOR}", {"state": "on" if verdict["good"] else "off", "attributes": {
            "friendly_name": "Good car wash weather", "icon": "mdi:car-wash" if verdict["good"] else "mdi:weather-rainy",
            "summary": verdict["summary"], "reasons": verdict["reasons"], "temperature": verdict["temp"]}})


def panel_url():
    """The add-on's sidebar path (e.g. /abcd1234_parts_catalog), so tapping the notification opens the app."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        r = urllib.request.Request("http://supervisor/addons/self/info", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(r, timeout=10) as resp:
            return "/" + json.loads(resp.read())["data"]["slug"]
    except Exception:  # noqa: BLE001
        return None


# ---------- the checker ----------

class Washer:
    def __init__(self, load_settings, last_wash, state_path, ha=None):
        self.load_settings, self.last_wash, self.state_path = load_settings, last_wash, state_path
        self.ha = ha if ha is not None else HA.from_env()
        self.cache = (0, None, None)  # (time, config, verdict)
        self.lock = threading.Lock()
        self.tz = None

    @property
    def available(self):
        return self.ha is not None

    def _state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self, st):
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(st, f)

    def local_now(self):
        if self.tz is None:
            name = self.ha.time_zone() if self.ha else None
            try:
                from zoneinfo import ZoneInfo
                self.tz = ZoneInfo(name) if name else False
            except Exception:  # noqa: BLE001 - no tz database: use the container's clock
                self.tz = False
        return dt.datetime.now(self.tz) if self.tz else dt.datetime.now().astimezone()

    def verdict(self, cfg=None, fresh=False):
        cfg = cfg or config(self.load_settings())
        with self.lock:
            at, ccfg, v = self.cache
            if not fresh and v and ccfg == cfg and time.time() - at < CACHE_FOR:
                return v
            ent = cfg["weather_entity"]
            if not ent:
                raise ValueError("Pick a weather entity first")
            hourly = self.ha.forecast(ent, "hourly")
            daily = self.ha.forecast(ent, "daily") if len(hourly) < cfg["hours"] else []
            v = evaluate(self.ha.state(ent), hourly, daily, cfg)
            self.cache = (time.time(), cfg, v)
            return v

    def status(self):
        st = self._state()
        last = self.last_wash()
        return {"last_sent": st.get("last_sent"), "last_wash": last}

    def due_to_notify(self, cfg, now):
        """Not before notify_hour, at most once per every_days, and not if a wash was logged in that time."""
        if now.hour < cfg["notify_hour"]:
            return False
        since = (now.date() - dt.timedelta(days=cfg["every_days"] - 1)).isoformat()
        last_sent = self._state().get("last_sent")
        if last_sent and last_sent[:10] >= since:
            return False
        last = self.last_wash()
        return not (last and last >= since)

    def send(self, v, test=False):
        title = "Good day to wash the car" if v["good"] else "Car wash weather (test)"
        msg = v["summary"] if v["good"] else v["summary"] + " (Test: you'll only get this when the weather is good.)"
        sent = self.ha.notify(config(self.load_settings())["notify"], title, msg, panel_url())
        if not test:
            self._save_state({**self._state(), "last_sent": self.local_now().isoformat(timespec="minutes")})
        return sent

    def check(self):
        cfg = config(self.load_settings())
        if not cfg["enabled"] or not cfg["weather_entity"]:
            return None
        v = self.verdict(cfg, fresh=True)
        try:
            self.ha.set_sensor(v)
        except Exception as e:  # noqa: BLE001
            log.warning("Couldn't update %s: %s", SENSOR, e)
        if v["good"] and self.due_to_notify(cfg, self.local_now()):
            log.info("Wash weather is good; notifying %s", self.send(v))
        return v

    def run(self):
        while True:
            try:
                self.check()
            except Exception as e:  # noqa: BLE001 - HA restarting, network...; try again next round
                log.warning("Car wash weather check failed: %s", e)
            time.sleep(CHECK_EVERY)


washer = None


def start(load_settings, last_wash, state_path):
    global washer
    washer = Washer(load_settings, last_wash, state_path)
    if washer.available:
        threading.Thread(target=washer.run, name="wash-weather", daemon=True).start()
    return washer


def get(load_settings, last_wash, state_path):
    """The running checker, or one made on demand (tests, a server started without __main__)."""
    global washer
    if washer is None:
        washer = Washer(load_settings, last_wash, state_path)
    return washer
