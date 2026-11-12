# Garage for Home Assistant

A Home Assistant add-on for keeping track of your vehicles:

- **Service tracking:** schedules by miles and/or months, what's overdue or due soon, full service history
- **Parts:** to get / ordered / on hand / installed, with the mileage each part went in at and its receipt
- **Manuals & receipts:** PDFs and photos per vehicle, opened right in Home Assistant
- **Handy extras:** drag to reorder vehicles, hide sold ones, vehicle photos, estimated due dates from your
  driving, one-tap "done" from the garage page, CSV export of service history and parts
- **Projects:** bigger jobs with steps to tick off and parts set aside; the next one shows on the vehicle's card
- **Offline parts catalog** (optional, off by default): main groups, diagrams with clickable callouts, OEM part numbers, quantities,
  fitment and supersessions, imported from your own files (JSON, CSV, saved HTML pages, images)
- **3D workshop** (optional): import a 3D model of a vehicle built from simple shapes, pull it apart section by
  section, click a part for its number, fastener thread, torque and tightening order, and its repair steps
- **Home Assistant sensors** for odometer, next service and overdue counts, plus a ready-made dashboard tab
- **AI agent access (MCP):** let Claude or another MCP-capable assistant check what's due, log services and add parts

It runs as a sidebar panel through Home Assistant Ingress, so it works in the Home Assistant app at home or
away, and it needs no separate login.

## Install
1. In Home Assistant: **Settings › Apps** (Add-ons) **› Install app › ⋮ › Repositories**, and add
   `https://github.com/Jognei/ha-garage-addon`
2. Find **Parts Catalog** in the store, then **Install** and **Start**, and turn on **Show in sidebar**.
3. Open **Garage** in the sidebar.

Home Assistant OS or Supervised is required (add-ons aren't available on Container/Core installs).
Supported: amd64 and aarch64 (e.g. Home Assistant Green/Yellow, Raspberry Pi 4/5 64-bit).

More: [add-on documentation](parts_catalog/DOCS.md), [catalog import formats](docs/catalog.md).

## Notes
- All data stays in your Home Assistant (included in HA backups). Nothing is sent anywhere.
- The parts catalog never downloads from websites. It only reads files you import.
- Not affiliated with BMW or any parts catalog. BMW main group numbers are included as reference data.

## Development
The app is Flask + SQLite with a no-build single-page UI. Run the tests with
`cd parts_catalog && python -m unittest discover tests`, and run it locally with
`cd parts_catalog && python -m app` (data goes in `parts_catalog/data/`, or set `PARTS_CATALOG_DATA`).

## License
MIT
