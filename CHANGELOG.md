# Changelog

## 1.4.3 — 2026-11-24

- Fixed: a PV string was hidden at night when it reported 0 W and less than 20 V. A string is now listed unless its power, voltage and current are all zero, so unused strings (0 V) stay hidden while a connected one still shows (for example `PV1: 0W · 14.0V · 0.00A`).

## 1.4.2 — 2026-11-17

- Fixed: after changing the schedule hours, the *From* / *Until* submenus (and sometimes the refresh item itself) could turn gray and could no longer be opened. Submenu parents now carry a no-op action so SwiftBar keeps them enabled.
- The default schedule window is now 08:00–17:00 (was 07:00–21:00). The schedule is still off by default and existing configs keep their hours.

## 1.4.1 — 2026-11-17

- Dropdown text is easier to read: rows without an action are disabled menu items, which macOS draws in faded gray, so they now get an explicit high-contrast color (`menuTextColor`: `auto`, `default` for the old look, or a fixed color). With colored dropdown icons, section titles take their icon's color.
- The *Updated* line is now *Data from Deye* (*Date de la Deye*), so it is not confused with SwiftBar's own *Updated … ago* line at the bottom of the menu.

## 1.4.0 — 2026-11-17

- Optional refresh schedule by time of day (menu: **Refresh interval ▸ Schedule by time of day**, config key `schedule`, CLI `--set-schedule`): for example 1 min from 07:00 to 21:00 and 5 min otherwise. Hours and both intervals are configurable; off by default. Outside the window the plugin skips the API call until the slower interval has passed.
- **Refresh now** always fetches, also when a schedule is active. Picking an interval from the plain list turns the schedule off.
- New runtime file `~/.config/deye/state.json` (time and result of the last fetch attempt).

## 1.3.1 — 2026-11-16

- Documentation only: Romanian README (`README.ro.md`) with a language switch at the top of both READMEs.
- README: the low battery color is `lowBatteryColor` (yellow by default), not red.

## 1.3.0 — 2026-11-16

- Fewer API calls: a manual refresh or a menu setting change less than `minFetchSeconds` (default 60) after the last fetch now shows the cached data instead of calling the API. Scheduled runs are never skipped.
- On HTTP 429, 5xx or network errors the plugin no longer retries with the station endpoint; it shows the last data marked as stale and waits for the next scheduled refresh.

## 1.2.1 — 2026-11-16

- CI (Linux with Python 3.9 and 3.13, macOS) and an automated release workflow: pushing a `v*` tag runs the checks, builds `deye-menubar-<version>.zip` and publishes a GitHub release with the changelog section as notes.
- The release zip leaves out tests, docs, scripts and CI files (`.gitattributes`).

## 1.2.0 — 2026-11-16

- Menu bar: grid power next to PV, battery and home (positive = import, minus = export). During a grid outage the bolt-slash icon replaces the value.
- New menu bar icon style **Color (symbols)**: SF Symbols with an individual color each, picked to stay readable on tinted menu bars (`barIcons: "symbols"`).

## 1.1.1 — 2026-11-16

- Grid power is now shown exactly (e.g. "importing 17W") instead of "~0" below 20 W. New setting `thresholds.gridIdleWatts` (default 0); `idleWatts` now only applies to the battery.
- "Self-sufficiency" renamed to "Grid independence" (Romanian: "Independență față de rețea"): it is the share of today's consumption not bought from the grid, not a battery value.

## 1.1.0 — 2026-11-16

- Default refresh interval is now 5 minutes (the update interval of the Deye inverter itself), down from 1 minute: fewer API calls (~8,640 per month) with no loss of fresh data. Existing installs keep their current interval; change it any time from the **Refresh interval** menu.

## 1.0.1 — 2026-11-16

- Fixed: dropdown section icons stayed monochrome when set to **Color**. SwiftBar only tints
  `sfimage` icons through an `sfconfig` palette, which the plugin now sends.
- Tests: the palette configuration is decoded and checked in the smoke test.

## 1.0.0 — 2026-11-16

First public release.

- Menu bar: PV production, battery SoC with charge/discharge arrow, home consumption (SF Symbols or emoji).
- Grid outage indicator based on the inverter's grid relay status.
- Details per PV string and per phase (voltage, current, power) for load and grid.
- Battery submenu: voltage, current, temperature, BMS values, capacity, stored energy, today/lifetime charge and discharge, estimated cycles, time to full / to reserve.
- Energy today and lifetime totals submenus, with daily self-sufficiency.
- Inverter submenu: output per phase, temperature, grid relay, rated power, firmware.
- Icon style selectable from the menu (monochrome or color for the menu bar; monochrome, color or none for the dropdown).
- Refresh interval and language selectable from the menu; API quota projection and local call counter.
- One API call per refresh via `/device/latest`, with fallback to `/station/latest` and to cached data.
- Installer (SwiftBar setup, Keychain credentials, station selection, login item) and uninstaller.
- English and Romanian translations.
