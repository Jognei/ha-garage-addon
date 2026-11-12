"""Catalog (EPC) JSON API under /api/epc and stored files under /epc-files/."""
import functools
import os
import shutil
import tempfile
import zipfile
from collections import Counter

from flask import Blueprint, jsonify, request, send_file, send_from_directory, abort

from . import db, epc, epc_import

bp = Blueprint("epc", __name__)


def init():
    """Create the catalog DB; on first run load the reference data (BMW main groups)."""
    epc.init()
    if not epc.row("SELECT 1 FROM sources WHERE kind IN ('reference', 'manual')"):
        epc_import.load_reference()


def err(msg, code=400):
    return jsonify(error=msg), code


def to_id(v):
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


@bp.route("/api/epc/overview")
def overview():
    return jsonify(stats=epc.stats(), vehicles=epc.vehicles(), sources=epc.sources())


@bp.route("/api/epc/vehicles")
def vehicles():
    return jsonify(epc.vehicles())


@bp.route("/api/epc/categories")
def categories():
    return jsonify(epc.category_tree(to_id(request.args.get("vehicle"))))


@bp.route("/api/epc/diagrams")
def diagrams():
    return jsonify(epc.diagrams_in(to_id(request.args.get("category")), to_id(request.args.get("vehicle"))))


def garage_status(pns):
    """Which of these part numbers are already in the garage parts list, under it or a number replacing it, and their status."""
    pns = {p for p in pns if p}
    if not pns:
        return {}
    # garage number -> catalog numbers it shows on: its own, and those it replaces one step back (Add saves the replacing number)
    marks = {p: {p} for p in pns}
    for s in epc.rows(f"SELECT old_pn, new_pn FROM supersessions WHERE old_pn IN ({','.join('?' * len(pns))})", list(pns)):
        marks.setdefault(s["new_pn"], set()).add(s["old_pn"])
    out = {}
    for r in db.rows("""SELECT p.id, p.part_number, p.status, p.name, c.name AS car FROM parts p LEFT JOIN cars c ON c.id=p.car_id
                        WHERE p.part_number<>''"""):
        for pn in marks.get(epc.norm_pn(r["part_number"]), ()):
            out.setdefault(pn, []).append(r)
    return out


@bp.route("/api/epc/diagrams/<int:did>")
def diagram(did):
    d = epc.diagram_detail(did, to_id(request.args.get("vehicle")))
    if not d:
        return err("Not found", 404)
    gs = garage_status([i["part_number"] for i in d["items"]])
    for i in d["items"]:
        i["garage"] = gs.get(i["part_number"], [])
    return jsonify(d)


@bp.route("/api/epc/search")
def search():
    res = epc.search(request.args.get("q", ""), to_id(request.args.get("vehicle")))
    gs = garage_status([r["part_number"] for r in res])
    for r in res:
        r["garage"] = gs.get(r["part_number"], [])
    return jsonify(res)


@bp.route("/api/epc/parts/<pn>")
def part(pn):
    p = epc.part_detail(pn)
    p["garage"] = garage_status([p["part_number"]]).get(p["part_number"], [])
    return jsonify(p)


@bp.route("/api/epc/validation")
def validation():
    rep = epc.validate()
    return jsonify(summary=epc.validation_summary(rep), issues=rep)


@bp.route("/api/epc/sources/<int:sid>", methods=["DELETE"])
def delete_source(sid):
    epc.delete_source(sid)
    return jsonify(ok=True)


@bp.route("/api/epc/vehicles/<int:vid>", methods=["DELETE"])
def delete_vehicle(vid):
    epc.delete_vehicle(vid)
    return jsonify(ok=True)


@bp.route("/api/epc/sample", methods=["POST"])
def load_sample():
    return jsonify(epc_import.load_sample().as_dict())


def upload_dest(tmp, up):
    """Where an upload goes in the import's folder: at the path it was sent with, if that stays inside, else at its name."""
    dst = os.path.normpath(os.path.join(tmp, (up.meta.get("path") or "").replace("\\", "/")))
    return dst if dst.startswith(tmp + os.sep) else os.path.join(tmp, os.path.basename(up.filename or "upload"))


@bp.route("/api/epc/import", methods=["POST"])
def import_files():
    """Form: "upload" once per file (JSON / CSV / HTML / images), each sent on its own first, with its path in a chosen
    folder if any, + optional source, vehicle, category, diagram, create."""
    from .server import finished_upload
    uids = list(dict.fromkeys(uid for uid in request.form.getlist("upload") if uid))  # an id named twice is one upload
    if not uids:  # files posted directly come from a page loaded before uploads went up on their own
        return err("Reload the page and try again." if request.files else "No files")
    files = [finished_upload(uid, "catalog") for uid in uids]
    f = request.form
    opts = dict(source_name=(f.get("source") or "").strip() or None, vehicle=f.get("vehicle") or None,
                category=f.get("category") or None, diagram=(f.get("diagram") or "").strip() or None,
                create=f.get("create") == "1")
    tmp = tempfile.mkdtemp()
    reports = []
    try:
        sent_as = {}
        for up in files:
            dst = upload_dest(tmp, up)
            # a second file sent as the same name gets a folder of its own, so neither replaces the other
            p = os.path.join(tempfile.mkdtemp(dir=tmp), os.path.basename(dst)) if os.path.exists(dst) else dst
            os.makedirs(os.path.dirname(p), exist_ok=True)
            up.save(p)
            sent_as[p] = dst
        saved = list(sent_as)
        images = [p for p in saved if os.path.splitext(p)[1].lower() in epc_import.IMAGE_EXT]
        others = [p for p in saved if p not in images]
        htmls = [p for p in others if p.lower().endswith((".html", ".htm"))]
        # a reference finds the image at its path from the referring file, else the one image with its name
        by_path, by_name = {}, {}
        for p in images:
            by_path.setdefault(sent_as[p], []).append(p)
            by_name.setdefault(os.path.basename(p), []).append(p)
        referred = set()

        def find_upload(ref, near, problems):
            ref = ref.replace("\\", "/")
            hits = by_path.get(os.path.normpath(os.path.join(near, ref))) or by_name.get(os.path.basename(ref), [])
            referred.update(hits)
            if len(hits) > 1:
                problems.append(f"{ref}: {len(hits)} uploaded files have that name; none used")
                return None
            return hits[0] if hits else None
        for p in others:
            try:
                problems = []
                kw = dict(opts, find_upload=functools.partial(find_upload, near=os.path.dirname(sent_as[p]), problems=problems))
                if p in htmls and len(htmls) > 1:
                    kw["diagram"] = None  # several pages: each uses its own file name as diagram code
                if p in htmls and len(htmls) == 1 and len(images) == 1:
                    kw["image"] = images[0]
                    referred.add(images[0])
                with epc_import.undo_on_failure():
                    rep = epc_import.import_path(p, **kw).as_dict()
                rep["problems"] += problems
                reports.append(rep)
            except Exception as e:  # noqa: BLE001 - report per file
                reports.append({"source": os.path.basename(p), "error": str(e)})
        loose = [p for p in images if p not in referred]
        if loose:
            with epc_import.undo_on_failure():
                reports.append(epc_import.import_images(loose, opts["source_name"], opts["create"], opts["category"], opts["vehicle"]).as_dict())
        clashes = epc_import.Report("Uploaded files")
        for dst, n in Counter(sent_as.values()).items():
            if n > 1:
                clashes.problem(f"{n} files named {os.path.relpath(dst, tmp).replace(os.sep, '/')}, each kept")
        if clashes.problems:
            reports.append(clashes.as_dict())
    finally:
        epc.connect().rollback()
        shutil.rmtree(tmp, ignore_errors=True)
    return jsonify(reports=reports)


@bp.route("/api/epc/export")
def export():
    """The catalog in the portable format as a zip (catalog.json + images/); sample data only with samples=1."""
    tmp = tempfile.mkdtemp()
    try:
        src, out = os.path.join(tmp, "export"), os.path.join(tmp, "catalog-export.zip")
        epc_import.export_bundle(src, include_samples=request.args.get("samples") == "1")
        with zipfile.ZipFile(out, "w") as z:
            z.write(os.path.join(src, "catalog.json"), "catalog.json", zipfile.ZIP_DEFLATED)
            for fn in sorted(os.listdir(os.path.join(src, "images"))):
                z.write(os.path.join(src, "images", fn), "images/" + fn)  # stored: images are compressed already
        resp = send_file(out, mimetype="application/zip", as_attachment=True, download_name="catalog-export.zip")
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    # sent from disk, so removed once it's sent; a passed-through file would skip Response.close and this with it
    resp.direct_passthrough = False
    resp.call_on_close(lambda: shutil.rmtree(tmp, ignore_errors=True))
    return resp


@bp.route("/epc-files/<path:p>")
def files(p):
    full = os.path.normpath(os.path.join(epc.EPC_DIR, p))
    if not full.startswith(os.path.normpath(epc.FILES_DIR) + os.sep) or not os.path.isfile(full):
        abort(404)
    # stored HTML pages are served as text so saved pages never run scripts here
    if full.lower().endswith((".html", ".htm")):
        return send_from_directory(epc.EPC_DIR, p, mimetype="text/plain")
    return send_from_directory(epc.EPC_DIR, p)
