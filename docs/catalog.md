# Offline parts catalog

The **Catalog** tab (`#/catalog`) is an offline BMW-style parts catalog: vehicles, main groups, diagrams with
clickable callouts, parts lists with OEM part numbers, quantities, fitment and supersessions, plus search.
After import it runs entirely offline. Nothing in it fetches from the web: it only reads files you import.

## Where things live
- `data/epc/epc.db`: the catalog database, separate from `garage.db` so it can be copied or shared on its own.
- `data/epc/files/<source>/`: original imported files (HTML pages, CSV, JSON, diagram images), kept so they
  can be re-processed later. They're also listed in the `source_files` table with a SHA-256.
- Included in Settings › Download backup.
- Code: `app/epc.py` (schema, queries, validation), `app/epc_import.py` (importers, export), `app/epc_api.py`
  (`/api/epc/*`, `/epc-files/*`), `static/catalog.js` (UI), `tools/epc_import.py` (command line).

## Schema
| table | what |
|---|---|
| `sources` | each import source; `is_sample=1` marks sample data. Removing a source removes everything it imported |
| `source_files` | stored original files |
| `vehicles` | catalog variant: `code` (unique, e.g. `E34-535i-USA`), make, series, model, body, market, engine, transmission, steering, `prod_from`/`prod_to` (YYYYMM) |
| `categories` | main groups (`11`) and optional subgroups (`11.12`, `parent_id`) |
| `diagrams` | `code` unique (e.g. `11_0050`), category, title, stored image + pixel size |
| `diagram_vehicles` | which vehicles use a diagram |
| `diagram_items` | one parts-list row: `callout`, `part_number` (normalized) + `part_number_raw` (as in the source), `description`, `supplement`, `qty` (original text, NULL = not given), `from_date`/`to_date`, `fitment_note` |
| `item_vehicles` | optional: limits a row to some of the diagram's vehicles |
| `parts` | one record per part number |
| `hotspots` | clickable callout boxes, as fractions (0–1) of the image size |
| `supersessions` | `old_pn` → `new_pn` (+ date, note) |
| `expected_diagrams` | diagram codes known to exist for a vehicle, so missing ones get reported |

Another model (E39, E30, a non-BMW) is just more vehicles, diagrams and items. The main-group taxonomy is shared.
Diagram codes are unique, so one diagram can serve several vehicles via `diagram_vehicles`.

Missing values stay empty/NULL and show in the UI as dashed **missing** tags. Nothing is filled in by guessing.

## Importing
Use the app (Catalog › Data & import) or the command line:
```
.venv\Scripts\python tools\epc_import.py PATH... [--vehicle E34-535i-USA] [--category 11] [--diagram 11_0050] [--source NAME]
.venv\Scripts\python tools\epc_import.py --validate
.venv\Scripts\python tools\epc_import.py --export out_dir        # portable catalog.json + images/
.venv\Scripts\python tools\epc_import.py --remove-source "SAMPLE DATA (not real)"
```
If you import the same diagram from the same source again, that source's rows get replaced, not duplicated.

### JSON bundle (portable format, also what `--export` writes)
```json
{"format": "garage-epc", "version": 1,
 "source": {"name": "My data", "kind": "json", "is_sample": false, "note": ""},
 "vehicles": [{"code": "E34-535i-USA", "series": "E34", "model": "535i", "engine": "M30B35", "prod_from": "1988-01"}],
 "categories": [{"code": "11", "name": "Engine"}, {"code": "11.12", "name": "Cylinder head", "parent": "11"}],
 "parts": [{"part_number": "11 53 1 740 996", "description": "Thermostat", "notes": ""}],
 "diagrams": [{"code": "11_0050", "category": "11", "title": "...", "image": "images/11_0050.png",
               "vehicles": ["E34-535i-USA"],
               "items": [{"callout": "1", "part_number": "...", "description": "...", "supplement": "",
                          "qty": "1", "from": "1990-09", "to": null, "fitment_note": "", "vehicles": []}],
               "hotspot_units": "px",
               "hotspots": [{"callout": "1", "x": 120, "y": 80, "w": 24, "h": 24}]}],
 "supersessions": [{"old": "...", "new": "...", "date": "1992-01", "note": ""}],
 "expected_diagrams": [{"vehicle": "E34-535i-USA", "code": "11_0050", "title": "..."}]}
```
Image paths are relative to the JSON file. `hotspot_units` is `px` (converted using the image size) or `fraction`.
Dates can be written as `1990-09`, `09/1990` or `199009`.

### CSV
Each CSV holds one kind of record, and the importer detects which from its headers (header names are flexible, e.g. `Part Number` / `part no` / `P/N`).
Delimiter: comma, semicolon or tab.
- items: `diagram, callout, part_number, description, supplement, qty, from, to, fitment_note[, vehicles, category, title]`
- hotspots: `diagram, callout, x, y, w, h` (pixels or fractions)
- supersessions: `old, new[, date, note]`
- parts: `part_number, description[, notes]`
- vehicles: `code, series, model, …`
- categories: `code, name[, parent]`
- diagrams: `diagram, title, category`

### Saved HTML pages
For pages you saved yourself in a browser (File › Save page as › "Web page, complete"), or copies from another
permitted source. One page = one diagram:
- the parts table is found by its header (`No.`, `Description`, `Qty`, `Part Number`, …) or by a column of 11-digit numbers
- the diagram image is the largest **local** `<img>` on the page (remote URLs are never fetched), or pass `--image`
- `<map>`/`<area>` regions on that image become clickable hotspots
- the diagram code is the file name unless you pass `--diagram`; the title comes from `<title>`
- the HTML itself is stored, so you can re-run a better parser later without the original files

### Images
`11_0050.png` attaches to diagram `11_0050` (matching ignores case and punctuation). With `--create`, images
that match no diagram become new diagrams that don't have a parts list yet.

## Validation report
Catalog › Data & import, or `--validate`. Checks:
- **missing diagrams**: expected but not imported; vehicles with no diagrams; diagrams without image /
  image file gone / no parts list / no category / no vehicle
- **missing part records**: item part numbers with no `parts` row, rows without a part number, malformed (non-11-digit)
  BMW numbers, rows with no quantity or no description
- **duplicates**: the same row (diagram, callout, part number, supplement, dates) more than once, including across sources;
  the same file imported twice
- **broken relationships**: hotspots with no matching row, callouts with no hotspot, hotspots outside the image,
  supersession targets with no record, supersession loops, stored files missing on disk, missing parent categories
