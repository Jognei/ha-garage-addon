# Changelog

## 1.0.52

- Restore: a backup that fails partway changes nothing, a damaged catalog database is refused, and a restore brings
  the backup's card layout, quick services and catalog switch. Restores, photos, receipts and catalog imports of any
  size work through the sidebar.
- Home Assistant: sensor ids no longer change when a vehicle is renamed, reordered or hidden; sensors a vehicle no
  longer has are removed; the estimated due date is an attribute; the dashboard YAML pastes under `views:`; the
  add-on options have names and descriptions; Settings shows how Home Assistant can send an odometer reading.
- Services: a service whose estimated due date is within 30 days reads due soon; a dragged order stays inside each
  state; deleting a service returns its parts to On hand and removes its odometer reading; using part of a
  quantity can be undone.
- Odometer: a reading that doesn't fit the log isn't logged, a reading can be deleted, and the driving rate uses the
  first and last reading.
- Parts and files: filters stay in the address, the CSV exports what the list shows, parts set aside for a project
  say so, and files without a vehicle are listed under All parts > Not assigned.
- Phones: the Parts and Upcoming tables fit the screen, touch targets are finger-sized, number fields open a number pad.
- Dialogs and errors: messages raised in a dialog are seen, a late answer no longer closes the wrong thing, and
  failed requests say what went wrong.
- Catalog: better search (words with digits, diagram codes, spaced part numbers), CSV columns map by name, more page
  formats import, unreadable dates are reported, export from the app.
- AI agents: tools to correct or delete a service, change a schedule and move a part; log_service refuses an
  ambiguous schedule name.
- Add-on: stops at once, Home Assistant backups stop it while they run, images are built only after the tests pass,
  and a published version is never overwritten.
- Stricter input: values that aren't cleanly numbers or real dates are refused with a message naming the field.

## 1.0.50

- Garage cards keep the same layout on every vehicle: the reorder arrows stay in the top corner, long names wrap
  under them, due tags sit under the name, and the "New reading" box is a full-width row under the mileage.

## 1.0.48

- Quick services: Log a service shows only the jobs you tick (by default wash, fuel fill-up, oil top-up, tire
  pressure). Pick them, or add your own, under Settings > Quick services.
- Car wash weather (Settings): a notification when it's warm and no rain is forecast for the next 48 hours, using a
  Home Assistant weather entity. Also adds binary_sensor.garage_wash_weather.
- Files: a "Part photo" type, filter buttons by type, and upload behind a button. Phones show a shorter list.
- Vehicle notes at the top of each vehicle page (and an agent tool to set them).
- Safer restore: a failed restore changes nothing, and the safety copy is made first.
- Dates are checked when saved, so a badly written date can no longer break the garage page.
- A service logged without mileage keeps the last known mileage for mileage-based schedules.
- Dialogs show errors inside the dialog, Save can't be tapped twice, and default dates use your time zone.
- Overdue and due-soon services stay on top after reordering. "Finish & log" only marks a project done once the
  service is saved.
- The add-on stops cleanly and has a health check.

## Earlier

Projects, dismissable and reorderable upcoming services, quick jobs, part quantities, a new look, Home Assistant
sensors, an MCP endpoint for AI agents, chunked uploads for big manuals, and the optional parts catalog.
