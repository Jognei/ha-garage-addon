"""MCP (Model Context Protocol) endpoint so AI agents can work with the garage: POST /mcp (Streamable HTTP, JSON replies).

Off until an agent key is created under Settings › AI agents. Clients send it as `Authorization: Bearer <key>`.
Tools reuse the app's own JSON API (an internal test client), so validation and side effects match the UI.
Reaching it from another device needs the add-on's direct network access (or phone access on a PC install).
"""
import hmac
import json

from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("mcp", __name__)
PROTOCOL = "2025-06-18"
INTERNAL = "parts_catalog.internal"   # WSGI environ flag for the tools' internal API calls (not settable over HTTP)

VEHICLE = {"type": "string", "description": "Vehicle id, or part of its name (case-insensitive), e.g. 'wagon'"}
STATUSES = ["Needed", "Check", "Ordered", "On hand", "Installed"]
PART_FIELDS = {
    "name": {"type": "string"}, "part_number": {"type": "string"}, "brand": {"type": "string"},
    "category": {"type": "string"}, "qty": {"type": "integer"}, "price": {"type": "number", "description": "Price each"},
    "status": {"type": "string", "enum": STATUSES}, "supplier": {"type": "string"}, "order_ref": {"type": "string"},
    "purchased_date": {"type": "string", "description": "YYYY-MM-DD"}, "location": {"type": "string"},
    "installed_mileage": {"type": "integer"}, "installed_date": {"type": "string"}, "url": {"type": "string"},
    "notes": {"type": "string"},
}


def _tool(name, description, props=None, required=()):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": props or {}, "required": list(required)}}


TOOLS = [
    _tool("list_vehicles", "All vehicles with odometer, overdue / due-soon counts, next services and open parts; hidden = sold or stored."),
    _tool("get_vehicle", "One vehicle in detail: every service schedule with its due mileage/date and state, recent odometer readings.",
          {"vehicle": VEHICLE}, ["vehicle"]),
    _tool("whats_due", "Services that are overdue or due soon (and schedules never done), for one vehicle or all that aren't hidden.",
          {"vehicle": VEHICLE}),
    _tool("list_services", "Service history of a vehicle, newest first.",
          {"vehicle": VEHICLE, "limit": {"type": "integer", "default": 20}}, ["vehicle"]),
    _tool("update_mileage", "Record an odometer reading. The odometer only moves forward unless correct=true.",
          {"vehicle": VEHICLE, "mileage": {"type": "integer"}, "date": {"type": "string", "description": "YYYY-MM-DD, default today"},
           "correct": {"type": "boolean", "description": "Allow a lower reading (fixing a typo)"}}, ["vehicle", "mileage"]),
    _tool("log_service", "Log a completed service. Matching schedules reset; listed parts become Installed at that mileage.",
          {"vehicle": VEHICLE, "title": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD, default today"},
           "mileage": {"type": "integer"}, "cost": {"type": "number"}, "notes": {"type": "string"},
           "done_by": {"type": "string", "description": "DIY or shop name"},
           "schedules": {"type": "array", "items": {"type": "string"}, "description": "Schedule titles this service satisfied, e.g. ['Engine oil & filter']"},
           "part_ids": {"type": "array", "items": {"type": "integer"}, "description": "Parts used (ids from list_parts)"}},
          ["vehicle", "title"]),
    _tool("add_schedule", "Add a service schedule (interval by miles and/or months) with when it was last done.",
          {"vehicle": VEHICLE, "title": {"type": "string"}, "interval_miles": {"type": "integer"}, "interval_months": {"type": "integer"},
           "last_date": {"type": "string"}, "last_mileage": {"type": "integer"}, "notes": {"type": "string"}},
          ["vehicle", "title"]),
    _tool("set_vehicle_notes", "Overall notes for a vehicle (history, quirks, mods). Replaces them, or adds a line with append=true.",
          {"vehicle": VEHICLE, "notes": {"type": "string"}, "append": {"type": "boolean"}}, ["vehicle", "notes"]),
    _tool("list_parts", "Parts, filtered by vehicle, status and/or a search over name, part number and brand.",
          {"vehicle": VEHICLE, "status": {"type": "string", "enum": STATUSES}, "search": {"type": "string"}}),
    _tool("add_parts", "Add one or more parts (e.g. from an order). Status defaults to Needed.",
          {"vehicle": {**VEHICLE, "description": VEHICLE["description"] + "; omit for unassigned"},
           "items": {"type": "array", "items": {"type": "object", "properties": PART_FIELDS, "required": ["name"]}}},
          ["items"]),
    _tool("update_part", "Change a part, e.g. its status (Ordered → On hand → Installed) or installed mileage.",
          {"part_id": {"type": "integer"}, **{k: v for k, v in PART_FIELDS.items()}}, ["part_id"]),
]


class ToolError(Exception):
    pass


def call(method, path, payload=None):
    client = current_app.test_client()
    kw = {"environ_base": {INTERNAL: True, "REMOTE_ADDR": "127.0.0.1"}}
    if payload is not None:
        kw["json"] = payload
    r = client.open("/api" + path, method=method, **kw)
    data = r.get_json(silent=True)
    if r.status_code >= 400:
        raise ToolError((data or {}).get("error") or f"HTTP {r.status_code}")
    return data


def vehicle(ref):
    if ref in (None, ""):
        return None
    cars = call("GET", "/cars")
    s = str(ref).strip()
    if s.isdigit():
        hit = [c for c in cars if c["id"] == int(s)]
    else:
        hit = [c for c in cars if s.lower() in c["name"].lower()]
        exact = [c for c in hit if c["name"].lower() == s.lower()]
        hit = exact or hit
    if len(hit) > 1:
        hit = [c for c in hit if not c.get("archived")] or hit  # a hidden (sold, stored) vehicle gives way
    if not hit:
        raise ToolError(f"No vehicle matches {ref!r}. Vehicles: " + ", ".join(f"{c['id']}={c['name']}" for c in cars))
    if len(hit) > 1:
        raise ToolError(f"{ref!r} matches several vehicles: " + ", ".join(f"{c['id']}={c['name']}" for c in hit))
    return hit[0]


def due_row(u):
    return {k: u.get(k) for k in ("id", "title", "state", "due_mileage", "due_date", "miles_left", "days_left",
                                  "interval_miles", "interval_months", "last_date", "last_mileage")}


def run_tool(name, a):
    if name == "list_vehicles":
        return [{"id": c["id"], "name": c["name"], "hidden": bool(c.get("archived")), "kind": c.get("kind"), "year": c.get("year"),
                 "odometer": c.get("mileage"), "odometer_date": c.get("mileage_date"), "overdue": c["overdue"], "due_soon": c["due_soon"],
                 "next": [due_row(u) for u in c["upcoming"]], "parts_to_get": c["parts_open"],
                 "parts_on_hand": c["parts_on_hand"], "last_service": c["last_service"]} for c in call("GET", "/cars")]
    if name == "get_vehicle":
        c = call("GET", f"/cars/{vehicle(a['vehicle'])['id']}")
        c["upcoming"] = [due_row(u) for u in c["upcoming"]]
        return c
    if name == "whats_due":
        cars = [vehicle(a["vehicle"])] if a.get("vehicle") else [c for c in call("GET", "/cars") if not c.get("archived")]
        out = []
        for c in cars:
            up = call("GET", f"/cars/{c['id']}")["upcoming"]
            out.append({"vehicle": c["name"], "odometer": c.get("mileage"),
                        "due": [due_row(u) for u in up if u["state"] in ("overdue", "soon")],
                        "never_done": [u["title"] for u in up if u["state"] == "unknown"]})
        return out
    if name == "list_services":
        rows = call("GET", f"/cars/{vehicle(a['vehicle'])['id']}/services")
        return rows[: int(a.get("limit") or 20)]
    if name == "update_mileage":
        c = vehicle(a["vehicle"])
        call("POST", f"/cars/{c['id']}/mileage", {"mileage": a["mileage"], "date": a.get("date"), "correct": bool(a.get("correct"))})
        now = call("GET", f"/cars/{c['id']}")
        return {"vehicle": c["name"], "odometer": now["mileage"], "odometer_date": now["mileage_date"],
                "note": None if now["mileage"] == a["mileage"] else "Reading is lower than the current odometer; pass correct=true to lower it."}
    if name == "log_service":
        c = vehicle(a["vehicle"])
        if isinstance(a.get("schedules"), str):
            a["schedules"] = [a["schedules"]]
        if a.get("part_ids") is not None and not isinstance(a["part_ids"], list):
            raise ToolError("part_ids must be a list of part ids, e.g. [12]")
        for pid in a.get("part_ids") or []:  # checked before logging: a failure after it would make a retry log it twice
            try:
                call("GET", f"/parts/{int(str(pid))}")
            except (ToolError, ValueError):
                raise ToolError(f"No part with id {pid!r}. Part ids come from list_parts.") from None
        hits, unmatched = [], []
        if a.get("schedules"):
            up = call("GET", f"/cars/{c['id']}")["upcoming"]
            for t in a["schedules"]:
                exact = [u for u in up if u["title"].lower() == t.lower()]
                m = exact or [u for u in up if t.lower() in u["title"].lower()]
                if len(m) > 1 and not exact:
                    raise ToolError(f"{t!r} matches several schedules: " + ", ".join(u["title"] for u in m))
                (hits.append(m[0]) if m else unmatched.append(t))
        sched_ids = [u["id"] for u in hits]
        payload = {k: a.get(k) for k in ("title", "date", "mileage", "cost", "notes", "done_by") if a.get(k) is not None}
        if payload.get("mileage") is None and c.get("mileage") is not None:
            payload["mileage"] = c["mileage"]  # as the UI does: default to the current odometer
        payload.update(schedule_ids=sched_ids, part_ids=a.get("part_ids") or [])
        r = call("POST", f"/cars/{c['id']}/services", payload)
        return {"logged": r, "vehicle": c["name"], "schedules_reset": len(hits),
                "schedules_reset_titles": [u["title"] for u in hits], "schedules_not_found": unmatched}
    if name == "add_schedule":
        c = vehicle(a["vehicle"])
        item = {k: a.get(k) for k in ("title", "interval_miles", "interval_months", "last_date", "last_mileage", "notes") if a.get(k) is not None}
        return call("POST", f"/cars/{c['id']}/schedules", {"items": [item]})
    if name == "set_vehicle_notes":
        c = call("GET", f"/cars/{vehicle(a['vehicle'])['id']}")
        notes = str(a["notes"]).strip()
        if a.get("append") and c.get("notes"):
            notes = c["notes"].rstrip() + "\n" + notes
        call("PUT", f"/cars/{c['id']}", {"notes": notes})
        return {"vehicle": c["name"], "notes": notes}
    if name == "list_parts":
        q = []
        if a.get("vehicle"):
            q.append(f"car_id={vehicle(a['vehicle'])['id']}")
        if a.get("status"):
            q.append("status=" + a["status"].replace(" ", "%20"))
        if a.get("search"):
            from urllib.parse import quote
            q.append("q=" + quote(a["search"]))
        keep = ("id", "name", "part_number", "brand", "category", "qty", "status", "supplier", "price", "car",
                "installed_mileage", "installed_date", "notes")
        return [{k: p.get(k) for k in keep} for p in call("GET", "/parts" + ("?" + "&".join(q) if q else ""))]
    if name == "add_parts":
        car_id = vehicle(a["vehicle"])["id"] if a.get("vehicle") else None
        ids = []
        for it in a.get("items") or []:
            ids.append(call("POST", "/parts", {**{k: v for k, v in it.items() if k in PART_FIELDS}, "car_id": car_id})["id"])
        return {"added": len(ids), "part_ids": ids}
    if name == "update_part":
        pid = a["part_id"]
        call("PUT", f"/parts/{pid}", {k: v for k, v in a.items() if k in PART_FIELDS})
        return call("GET", f"/parts/{pid}")
    raise ToolError(f"Unknown tool {name}")


def handle(msg):
    """One JSON-RPC message -> response dict, or None for notifications."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None
    ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}  # noqa: E731
    if method == "initialize":
        return ok({"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "garage", "title": "Garage (Parts Catalog)", "version": "1"},
                   "instructions": "Vehicles, service schedules and history, odometer readings and parts for the user's garage. "
                                   "Start with list_vehicles or whats_due. Vehicles can be named by id or part of their name."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        try:
            result = run_tool(name, args)
            return ok({"content": [{"type": "text", "text": json.dumps(result, indent=1, default=str)}], "isError": False})
        except (ToolError, KeyError, ValueError, TypeError) as e:
            msg_ = str(e) if isinstance(e, ToolError) else f"Bad arguments: {e}"
            return ok({"content": [{"type": "text", "text": msg_}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def agent_key():
    from .server import load_settings
    return load_settings().get("agent_key")


@bp.route("/mcp", methods=["POST", "GET", "DELETE"])
def mcp():
    key = agent_key()
    if not key:
        return jsonify(error="Agent access is off. Create an agent key in Settings › AI agents."), 403
    auth = request.headers.get("Authorization", "")
    if not hmac.compare_digest(auth.encode(), f"Bearer {key}".encode()):
        return jsonify(error="Missing or wrong agent key"), 401
    if request.method != "POST":
        return "", 405  # no server-initiated stream
    msg = request.get_json(silent=True)
    if msg is None:
        return jsonify({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}), 400
    if isinstance(msg, list):
        out = [r for r in (handle(m) for m in msg) if r is not None]
        return (jsonify(out), 200) if out else ("", 202)
    r = handle(msg)
    return (jsonify(r), 200) if r is not None else ("", 202)
