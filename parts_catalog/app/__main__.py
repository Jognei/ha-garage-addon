"""Start the server: python -m app  (run.pyw supervises this in the background)."""
import logging
import os
import socket
import sys

from waitress import serve

from . import db, epc_api, ha_sensors
from .server import app, load_settings, ha_summary

PORT_BUSY = 4


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if db.apply_pending_restore():
        logging.info("Restored data from a backup")
    db.init()
    epc_api.init()
    s = load_settings()
    port = int(os.environ.get("PARTS_CATALOG_PORT") or s["port"])
    host = os.environ.get("PARTS_CATALOG_HOST") or ("0.0.0.0" if s["lan"] else "127.0.0.1")
    app.config["BIND_HOST"] = host
    # Windows lets two servers share a port (SO_REUSEADDR), so check first rather than
    # silently fighting another program. run.pyw retries until the port is free.
    try:
        socket.create_connection(("127.0.0.1", port), 1).close()
        logging.error("Port %s is already in use by another program; will retry.", port)
        return PORT_BUSY
    except OSError:
        pass
    logging.info("Parts Catalog on http://%s:%s (data: %s)", host, port, db.DATA_DIR)
    def summary_in_app_context():
        with app.app_context():
            return ha_summary()
    ha_sensors.start(summary_in_app_context)
    serve(app, host=host, port=port, threads=8, ident="parts-catalog")


if __name__ == "__main__":
    sys.exit(main())
