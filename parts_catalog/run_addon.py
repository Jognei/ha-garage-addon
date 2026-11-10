"""Add-on entry point (the container's CMD). Plays the role run.pyw has on the PC:
runs `python -m app` with data in /data and restarts it on exit code 3 (restore / settings) or a crash."""
import json
import os
import subprocess
import sys
import time

RESTART_CODE = 3
# HA's ingress proxy (Supervisor) and Home Assistant Core on the internal hassio network.
HA_CLIENTS = "172.30.32.2,172.30.32.1"

try:
    with open("/data/options.json", encoding="utf-8") as f:
        options = json.load(f)
except (OSError, ValueError):
    options = {}

env = dict(os.environ,
           PARTS_CATALOG_DATA="/data",
           PARTS_CATALOG_PORT="8765",
           PARTS_CATALOG_HOST="0.0.0.0",
           PARTS_CATALOG_ADDON="1")
if options.get("publish_sensors") is False:
    env["PARTS_CATALOG_SENSORS"] = "0"
if not options.get("direct_access"):
    env["PARTS_CATALOG_ALLOW"] = HA_CLIENTS
print("Parts Catalog add-on starting; direct network access:", bool(options.get("direct_access")), flush=True)

while True:
    code = subprocess.call([sys.executable, "-m", "app"], env=env)
    if code == RESTART_CODE:
        print("Restarting (requested by the app)", flush=True)
        continue
    print(f"App exited with code {code}; restarting in 5 seconds", flush=True)
    time.sleep(5)
