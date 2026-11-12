"""3D workshop: a "pack" is one vehicle as a 3D model made of simple shapes: sections (engine, front axle, ...) that
explode apart, the parts in them (fasteners carry thread / torque / tightening order) and repair procedures the
parts point at. Packs are JSON files you import; they live in
data/workshop/<id>.json. Values you fill in later (torque, notes) go in <id>.specs.json so that
re-importing a pack never loses them. Missing values stay missing: nothing here invents a spec.
"""
import json
import os
import re

from flask import Blueprint, jsonify, request

from . import db

bp = Blueprint("workshop", __name__)
DIR = os.path.join(db.DATA_DIR, "workshop")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SPEC_FIELDS = ("torque_nm", "torque_note", "sequence_note", "source")
SHAPES = {"box", "cyl", "sphere", "bolt", "capnut", "stud", "ring", "frame", "extrude", "tube"}


def err(msg, code=400):
    return jsonify(error=msg), code


def _path(pid, suffix=".json"):
    return os.path.join(DIR, pid + suffix)


def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def pack_ids():
    if not os.path.isdir(DIR):
        return []
    return sorted(fn[:-5] for fn in os.listdir(DIR) if fn.endswith(".json") and not fn.endswith(".specs.json"))


def has_packs():
    return bool(pack_ids())


def validate(pack):
    """Return a list of problems (empty when the pack is usable)."""
    if not isinstance(pack, dict):
        return ["The pack must be a JSON object"]
    bad = []
    if not ID_RE.match(str(pack.get("id", ""))):
        bad.append("id: lowercase letters, digits and dashes only")
    if not pack.get("title"):
        bad.append("title is missing")
    sections = {s.get("id") for s in pack.get("sections") or []}
    frames = pack.get("frames") or {}
    procs = {p.get("id") for p in pack.get("procedures") or []}
    part_ids = set()
    for p in pack.get("parts") or []:
        pid = p.get("id")
        if not pid or pid in part_ids:
            bad.append(f"part id missing or repeated: {pid!r}")
        part_ids.add(pid)
        if (p.get("shape") or {}).get("type") not in SHAPES:
            bad.append(f"{pid}: unknown shape")
        if p.get("section") not in sections:
            bad.append(f"{pid}: unknown section {p.get('section')!r}")
        if p.get("frame") and p["frame"] not in frames:
            bad.append(f"{pid}: unknown frame {p['frame']!r}")
        for ref in p.get("procedures") or []:
            if ref not in procs:
                bad.append(f"{pid}: unknown procedure {ref!r}")
    for pr in pack.get("procedures") or []:
        for st in pr.get("steps") or []:
            for ref in st.get("parts") or []:
                if ref not in part_ids:
                    bad.append(f"procedure {pr.get('id')}: step points at unknown part {ref!r}")
    return bad


def load(pid):
    """The pack with your own spec entries merged in (marked `owner_entered`)."""
    pack = _read(_path(pid))
    if not pack:
        return None
    specs = _read(_path(pid, ".specs.json"), {})
    for p in pack.get("parts") or []:
        mine = specs.get(p["id"])
        if mine:
            p["fastener"] = {**(p.get("fastener") or {}), **mine, "owner_entered": sorted(mine)}
    return pack


@bp.route("/api/workshop/packs")
def packs():
    out = []
    for pid in pack_ids():
        p = _read(_path(pid)) or {}
        out.append({"id": pid, "title": p.get("title") or pid, "subtitle": p.get("subtitle") or "",
                    "sections": len(p.get("sections") or []), "parts": len(p.get("parts") or []),
                    "procedures": len(p.get("procedures") or [])})
    return jsonify(out)


@bp.route("/api/workshop/packs", methods=["POST"])
def import_pack():
    pack = request.get_json(silent=True)
    bad = validate(pack)
    if bad:
        return jsonify(error="This pack has problems: " + "; ".join(bad[:8]), problems=bad), 400
    _write(_path(pack["id"]), pack)
    return jsonify(ok=True, id=pack["id"])


@bp.route("/api/workshop/packs/<pid>")
def pack(pid):
    p = load(pid) if ID_RE.match(pid) else None
    return jsonify(p) if p else err("Not found", 404)


@bp.route("/api/workshop/packs/<pid>", methods=["DELETE"])
def delete_pack(pid):
    if not ID_RE.match(pid) or not os.path.exists(_path(pid)):
        return err("Not found", 404)
    os.remove(_path(pid))  # your spec entries stay, in case the pack is imported again
    return jsonify(ok=True)


@bp.route("/api/workshop/packs/<pid>/specs/<part>", methods=["PUT"])
def set_spec(pid, part):
    """Fill in (or clears, with null/"") a fastener's torque, notes and where the value came from."""
    p = _read(_path(pid)) if ID_RE.match(pid) else None
    if not p or not any(x.get("id") == part for x in p.get("parts") or []):
        return err("Not found", 404)
    b = request.get_json(silent=True) or {}
    specs = _read(_path(pid, ".specs.json"), {})
    cur = dict(specs.get(part) or {})
    for k in SPEC_FIELDS:
        if k not in b:
            continue
        v = b[k]
        if v in (None, ""):
            cur.pop(k, None)
        elif k == "torque_nm":
            try:
                v = float(v)
            except (TypeError, ValueError):
                return err("Torque must be a number (Nm)")
            if not 0 < v < 2000:
                return err("Torque must be between 0 and 2000 Nm")
            cur[k] = v
        else:
            cur[k] = str(v).strip()[:500]
    if cur:
        specs[part] = cur
    else:
        specs.pop(part, None)
    _write(_path(pid, ".specs.json"), specs)
    return jsonify(load(pid))
