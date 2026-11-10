# Parts Catalog (Garage)

A garage tracker for your vehicles that runs inside Home Assistant: service history, upcoming maintenance,
parts to get / on hand / installed, manuals and receipts, plus an offline parts catalog with clickable diagrams.
Open **Garage** in the sidebar, or the same panel in the Home Assistant app. It works anywhere Home Assistant
works, and Home Assistant handles sign-in.

## Getting started
1. Open **Garage** in the sidebar and add your vehicles (**＋ Add a vehicle**).
2. Set up service schedules with the presets (oil, filters, plugs, fluids…) and enter when each was last done.
3. Log services as you do them. The odometer and the next due dates update, and parts you used get marked installed.

## Home Assistant sensors
The add-on keeps these up to date (turn off with **publish_sensors**):
- `sensor.garage_overdue`, `sensor.garage_due_soon`, `sensor.garage_parts_to_get`: totals
- per vehicle, `sensor.garage_<vehicle>_odometer`, `_status`, `_next_service`, `_next_service_due`, `_parts_to_get`,
  where `<vehicle>` is the vehicle's name in lower case with `_` for spaces (renaming a vehicle renames its sensors)

Use them in automations, e.g. a notification when `sensor.garage_overdue` goes above 0.

## A Garage dashboard tab
In the add-on, go to **Settings › Home Assistant dashboard** and copy the YAML it shows for your vehicles. Then in
your dashboard choose **Edit dashboard › ⋮ › Raw configuration editor**, and paste it under `views:`.

## AI agents (MCP)
Let an AI assistant such as Claude, or any app that supports MCP, work with your garage. It can list vehicles,
check what's due, read service history, record odometer readings, log services, and add or update parts.
1. In the add-on, go to **Settings › AI agents** and click **Turn on: create an agent key**.
2. Turn on **direct_access** and set a host port under **Network**, so the agent can reach the add-on.
3. In the agent app, add an MCP server of type **HTTP**: `http://<home-assistant-ip>:<port>/mcp`, with the header
   `Authorization: Bearer <agent key>`.

Anyone with the key can change your garage data. **New key** locks out the old one, and **Turn off agent access**
disables it.

## Parts catalog
Turn it on under **Settings › Features** (it's off by default, because it starts empty). The **Catalog** tab is an offline parts catalog. Import your own data (JSON, CSV, saved HTML pages, diagram
images) under **Catalog › Data & import**. The catalog ships with BMW main group numbers and optional sample data
(marked SAMPLE). It doesn't download anything from the internet.

## Backups
Home Assistant backups include the add-on's data. You can also download everything as one zip under
**Settings › Download backup**, and restore it under **Settings › Restore from a backup**. That's also how you move
data between installs.

## Options
- **direct_access** (off): only Home Assistant can reach the add-on. Turn it on, and set a host port under
  **Network**, to reach it directly at `http://<home-assistant-ip>:<port>`. There's no password on that port, so
  only use it on a trusted network.
- **publish_sensors** (on): create the Home Assistant sensors above.
