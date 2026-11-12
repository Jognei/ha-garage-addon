# Changelog

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
