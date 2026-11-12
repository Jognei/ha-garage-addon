"""Catalog (EPC) JSON API under /api/epc and stored files under /epc-files/."""
import io
import os
import shutil
import tempfile
import zipfile

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
    """Which of these part numbers are already in the garage parts list, and their status."""
    pns = [p for p in set(pns) if p]
    if not pns:
        return {}
    out = {}
    for r in db.rows(f"""SELECT p.id, p.part_number, p.status, p.name, c.name AS car FROM parts p LEFT JOIN cars c ON c.id=p.car_id
                         WHERE REPLACE(REPLACE(p.part_number,' ',''),'-','') IN ({','.join('?' * len(pns))})""", pns):
        out.setdefault(epc.norm_pn(r["part_number"]), []).append(r)
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


@bp.route("/api/epc/import", methods=["POST"])
def import_files():
    """Multipart upload: files (JSON / CSV / HTML / images) + optional source, vehicle, category, diagram, create."""
    files = request.files.getlist("file")
    if not files:
        return err("No files")
    f = request.form
    opts = dict(source_name=(f.get("source") or "").strip() or None, vehicle=f.get("vehicle") or None,
                category=f.get("category") or None, diagram=(f.get("diagram") or "").strip() or None,
                create=f.get("create") == "1")
    tmp = tempfile.mkdtemp()
    reports = []
    try:
        saved = []
        for up in files:
            name = os.path.basename(up.filename or "upload")
            p = os.path.join(tmp, name)
            up.save(p)
            saved.append(p)
        images = [p for p in saved if os.path.splitext(p)[1].lower() in epc_import.IMAGE_EXT]
        others = [p for p in saved if p not in images]
        htmls = [p for p in others if p.lower().endswith((".html", ".htm"))]
        # uploads arrive flat, so "page_files/diagram.png" or "images/11_0050.png" is found by its base name
        by_name = {os.path.basename(p): p for p in images}
        referred = set()

        def find_upload(ref):
            p = by_name.get(os.path.basename(ref.replace("\\", "/")))
            if p:
                referred.add(p)
            return p
        for p in others:
            try:
                kw = dict(opts, find_upload=find_upload)
                if p in htmls and len(htmls) > 1:
                    kw["diagram"] = None  # several pages: each uses its own file name as diagram code
                if p in htmls and len(htmls) == 1 and len(images) == 1:
                    kw["image"] = images[0]
                    referred.add(images[0])
                reports.append(epc_import.import_path(p, **kw).as_dict())
            except Exception as e:  # noqa: BLE001 - report per file
                epc.connect().rollback()
                reports.append({"source": os.path.basename(p), "error": str(e)})
        loose = [p for p in images if p not in referred]
        if loose:
            reports.append(epc_import.import_images(loose, opts["source_name"], opts["create"], opts["category"], opts["vehicle"]).as_dict())
    finally:
        epc.connect().rollback()
        shutil.rmtree(tmp, ignore_errors=True)
    return jsonify(reports=reports)


@bp.route("/api/epc/export")
def export():
    """The catalog in the portable format as a zip (catalog.json + images/); sample data only with samples=1."""
    tmp = tempfile.mkdtemp()
    try:
        epc_import.export_bundle(tmp, include_samples=request.args.get("samples") == "1")
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as z:
            for dirpath, _, fns in os.walk(tmp):
                for fn in fns:
                    full = os.path.join(dirpath, fn)
                    z.write(full, os.path.relpath(full, tmp).replace(os.sep, "/"))
        data.seek(0)
        return send_file(data, mimetype="application/zip", as_attachment=True, download_name="catalog-export.zip")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@bp.route("/epc-files/<path:p>")
def files(p):
    full = os.path.normpath(os.path.join(epc.EPC_DIR, p))
    if not full.startswith(os.path.normpath(epc.FILES_DIR) + os.sep) or not os.path.isfile(full):
        abort(404)
    # stored HTML pages are served as text so saved pages never run scripts here
    if full.lower().endswith((".html", ".htm")):
        return send_from_directory(epc.EPC_DIR, p, mimetype="text/plain")
    return send_from_directory(epc.EPC_DIR, p)
