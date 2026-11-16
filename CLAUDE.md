# CLAUDE.md

Context for Claude Code working in this repository.

## What this is

Deye Menubar: a SwiftBar plugin for macOS that shows Deye hybrid inverter data from the
Deye Cloud OpenAPI in the menu bar (PV, battery SoC, home load), with a detailed dropdown.
Public repo, MIT license. The maintainer communicates in Romanian; code, docs, commits
and UI default language are English.

## Layout

- `plugin/deye.py`: the plugin (single file, Python standard library only, 3.8+).
  `VERSION` constant at the top is the source of truth for the version.
- `install.sh` / `uninstall.sh`: macOS installer/uninstaller. Must stay compatible with
  the macOS system bash 3.2 (no `mapfile`, no associative arrays, no `${var,,}`).
- `locales/*.json`: translations. `en.json` is the reference and fallback.
- `scripts/check.sh`: all checks (bash -n, shellcheck, py_compile, locale validation,
  smoke test). Run it before every commit.
- `scripts/build.sh`: `git archive` → `dist/deye-menubar-<version>.zip`
  (export-ignore rules in `.gitattributes`).
- `tests/smoke_test.py`: end-to-end test against a local mock of the Deye API, using
  `tests/fixtures/device_dump.tsv` (real data point names from the maintainer's inverter).
- `.github/workflows/`: `ci.yml` (Linux py3.9 + py3.13, macOS) and `release.yml`
  (on tag `v*`: checks, tag == VERSION, build zip, GitHub release with CHANGELOG notes).

## Deye Cloud API facts (verified against the maintainer's account)

- Base: `https://eu1-developer.deyecloud.com/v1.0` (EU) or `us1-…` (other regions).
- Login: `POST /account/token?appId=…` with `{appSecret, email, password: sha256 hex}`,
  then `Authorization: bearer <token>`. Token cached in `~/.config/deye/token.json`.
- Main data: `POST /device/latest {"deviceList": [sn]}` → `deviceDataList[0].dataList`
  of `{key, value, unit}`. One call per refresh provides the title and all details.
- Fallback: `POST /station/latest {"stationId"}`. Serial lookup: `POST /station/listWithDevice`.
- Quota: 100,000 calls/month per developer account. Never add extra calls per refresh.
- Sign conventions: `BatteryPower` negative = charging. `TotalGridPower` and station
  `wirePower` positive = **import** (confirmed via `DailyGridFeedIn = 0`).
  `GridRelayStatus` `Pull-in` = connected; anything else = grid outage.
- Key names used: `TotalSolarPower`, `DCPowerPV1..4`, `DCVoltagePV*`, `DCCurrentPV*`,
  `TotalConsumptionPower`, `UPSLoadPower`, `LoadVoltageL*`, `LoadPowerL*`, `SOC`,
  `BMSSOC`, `BatteryVoltage`, `BatteryTotalCurrent`, `Temperature- Battery` (sic),
  `BatteryRatedCapacity` (Ah), `GridVoltageL*`, `GridCurrentL*`, `GridPowerL*`,
  `Daily*` / `Total*` energy counters, `AC Temperature`, `MAIN`, `HMI`,
  `LithiumBatteryVersionNumber`. Data point names can differ on other models: always
  degrade gracefully (show nothing rather than crash) when a key is missing.

## SwiftBar specifics

- Refresh interval is in the file name (`deye.5m.py`); `--set-interval` renames the file.
- Item params used: `trim=false` (indented detail lines), `symbolize=true` with
  `:sf.symbol.name:` in the title, `checked=true`, `bash=… param1=… terminal=false`,
  `refresh=true`, `sfimage=` / `sfcolor=` on dropdown section headers. Escape `|` and
  newlines in text (`clean()`).
- Title uses monochrome SF Symbols by default (readable on tinted / inactive-display
  menu bars); `barIcons: "color"` (emoji) is optional. Dropdown section icons: `menuIcons`
  (`mono`/`color`/`none`). Both are set from the menu via `--set-icons`.
- Don't use red for alerts in the title (the maintainer's menu bar is red); the default
  alert color is yellow `#FFD60A`.

## Runtime files (never commit)

`~/.config/deye/`: `config.json` (no secrets), `token.json`, `last.json`, `device.json`,
`usage.json`, `locales/`, `backup/`. Secrets live in the Keychain, service `deye-menubar`,
accounts `appSecret` and `passwordHash`.

## Rules

- No third-party Python dependencies.
- Every user-visible string goes through `t("key")`, with the key added to `en.json` and
  `ro.json`. Placeholders must match across locales (`scripts/check_locales.py` enforces this).
- Any new feature gets a check in `tests/smoke_test.py`.
- Never commit secrets, the inverter serial number, the station id, the account email,
  or the maintainer's name or location (screenshots included).
- Releases: bump `VERSION`, add a `CHANGELOG.md` section, commit, then `git tag vX.Y.Z`.

## Open items

- `docs/menubar.png` and `docs/menu.png` are real SwiftBar screenshots (v1.2.0). Retake
  them if the look changes; keep location and personal data out of them.
- LICENSE file comes from GitHub (MIT) with the maintainer's name.
