"""Add-on entry point (the container's CMD). Plays the role run.pyw has on the PC:
runs `python -m app` with data in /data and restarts it on exit code 3 (restore / settings) or a crash."""
import json
import os
import signal
import subprocess
import sys
import threading
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

child = None
stopping = False


def stop(signum, _frame):
    """As process 1 we get no default SIGTERM handling: pass the stop on to the app and exit,
    instead of Home Assistant waiting out its timeout and killing us."""
    global stopping
    stopping = True
    if child is None or child.poll() is not None:  # between runs: nothing to wait for
        sys.exit(0)
    # The loop below exits once its child.wait() sees the app stop. Waiting here would never see it:
    # that wait() holds the lock this one needs.
    child.terminate()
    killer = threading.Timer(8, child.kill)  # for an app that ignores the stop
    killer.daemon = True
    killer.start()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

fast_fails = 0
while True:
    started = time.time()
    child = subprocess.Popen([sys.executable, "-m", "app"], env=env)
    code = child.wait()
    if stopping:
        sys.exit(0)
    if code == RESTART_CODE:
        print("Restarting (requested by the app)", flush=True)
        continue
    fast_fails = fast_fails + 1 if time.time() - started < 30 else 0
    if fast_fails >= 5:  # crashing on start: stop, so Home Assistant shows the add-on as stopped with its log
        print(f"App keeps failing at start (last exit code {code}); giving up", flush=True)
        sys.exit(1)
    print(f"App exited with code {code}; restarting in 5 seconds", flush=True)
    time.sleep(5)
