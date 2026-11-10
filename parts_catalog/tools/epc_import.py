"""Import local files into the offline parts catalog (data/epc/epc.db). Never downloads anything.

  .venv\\Scripts\\python tools\\epc_import.py FILE_OR_DIR... [options]
      .json   portable catalog bundle (see docs/catalog.md)
      .csv    items / hotspots / supersessions / parts / vehicles / categories / diagrams (detected from headers)
      .html   a saved web page with one diagram's parts table (+ its local image, + <map> hotspots if present)
      images  attach to diagrams by file name (11_0050.png -> diagram 11_0050); a folder works too
  --vehicle CODE           link imported diagrams to this catalog vehicle
  --category 11            put new diagrams in this main group
  --diagram 11_0050        diagram code for a single HTML page (default: the file name)
  --image PATH             diagram image for a single HTML page (default: the largest local <img> on it)
  --source NAME            group the import under this source name (re-importing replaces that source's rows)
  --create                 images with no matching diagram become new diagrams
  --sample                 load the built-in SAMPLE data
  --remove-source NAME     delete a source and everything it imported
  --export DIR             write the whole catalog (no samples) as catalog.json + images/
  --validate               print the validation report (also runs after every import)
The server doesn't need to be stopped; SQLite handles both.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import epc, epc_api, epc_import  # noqa: E402


def print_validation():
    rep = epc.validate()
    summ = epc.validation_summary(rep)
    print("\nValidation:", "no issues" if not rep else "")
    for check, v in sorted(summ.items(), key=lambda kv: ("error", "warning", "info").index(kv[1]["severity"])):
        print(f"  {v['severity']:7} {check:28} {v['count']}")
    for r in rep[:40]:
        print(f"    - {r['message']}")
    if len(rep) > 40:
        print(f"    … {len(rep) - 40} more (see Catalog > Data & import in the app)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--vehicle")
    ap.add_argument("--category")
    ap.add_argument("--diagram")
    ap.add_argument("--image")
    ap.add_argument("--source")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--remove-source")
    ap.add_argument("--export")
    ap.add_argument("--validate", action="store_true")
    a = ap.parse_args()
    epc_api.init()

    if a.remove_source:
        s = epc.row("SELECT id FROM sources WHERE name=?", (a.remove_source,))
        if not s:
            sys.exit(f"No source named {a.remove_source!r}. Sources: " + ", ".join(r["name"] for r in epc.sources()))
        epc.delete_source(s["id"])
        print("Removed source", a.remove_source)
    if a.sample:
        print(json.dumps(epc_import.load_sample().as_dict(), indent=1))
    for p in a.paths:
        kw = dict(source_name=a.source, vehicle=a.vehicle, category=a.category, diagram=a.diagram, image=a.image, create=a.create)
        if os.path.isdir(p):
            # a folder: JSON/CSV/HTML files one by one, then all images together
            files = sorted(os.path.join(p, f) for f in os.listdir(p))
            for f in files:
                if f.lower().endswith((".json", ".csv", ".html", ".htm")):
                    print(json.dumps(epc_import.import_path(f, **{**kw, "diagram": None}).as_dict(), indent=1))
            imgs = [f for f in files if os.path.splitext(f)[1].lower() in epc_import.IMAGE_EXT]
            if imgs:
                print(json.dumps(epc_import.import_images(imgs, a.source, a.create, a.category, a.vehicle).as_dict(), indent=1))
        else:
            print(json.dumps(epc_import.import_path(p, **kw).as_dict(), indent=1))
    if a.export:
        print("Exported to", epc_import.export_bundle(a.export))
    if a.paths or a.sample or a.validate or a.remove_source:
        print_validation()
    print("\nCatalog:", json.dumps(epc.stats()))


if __name__ == "__main__":
    main()
