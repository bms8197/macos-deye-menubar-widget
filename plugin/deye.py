#!/usr/bin/env python3
# <xbar.title>Deye Menubar</xbar.title>
# <xbar.version>v1.4.0</xbar.version>
# <xbar.desc>Deye inverter PV production, battery and consumption in the macOS menu bar.</xbar.desc>
# <xbar.dependencies>python3</xbar.dependencies>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
#
# Developed by Bogdan Stoica (bogdan at 898 dot ro), November 2026.
# Co-developed by Claude.
# Released under the MIT License, see LICENSE.
"""
Deye Menubar - SwiftBar plugin for Deye Cloud OpenAPI.

Usage:
  deye.<interval>.py                    normal SwiftBar output
  deye.<interval>.py --check            test login, print stations as JSON (used by installer)
  deye.<interval>.py --set-language XX  set UI language (code of a file in locales/, or "auto")
  deye.<interval>.py --dump-device      print all inverter data points (key, value, unit)
  deye.<interval>.py --set-interval 2m  change the refresh interval (renames this plugin file)
  deye.<interval>.py --set-icons bar|menu mono|color|none   icon style (bar also: symbols; none: menu only)

Only the Python standard library is used.
"""
import base64
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.4.0"
CFG_DIR = os.path.expanduser("~/.config/deye")
CFG_FILE = os.path.join(CFG_DIR, "config.json")
TOKEN_FILE = os.path.join(CFG_DIR, "token.json")
LAST_FILE = os.path.join(CFG_DIR, "last.json")
DEVICE_FILE = os.path.join(CFG_DIR, "device.json")
USAGE_FILE = os.path.join(CFG_DIR, "usage.json")
STATE_FILE = os.path.join(CFG_DIR, "state.json")
INTERVALS = ["30s", "1m", "2m", "5m", "10m", "15m"]
# Optional time-of-day schedule: a fast interval inside a daily window, a slow one outside.
SCHEDULE_DEFAULTS = {"enabled": False, "from": 7, "to": 21, "inside": "1m", "outside": "5m"}
SCHEDULE_INSIDE_OPTIONS = ["30s", "1m", "2m", "5m"]
SCHEDULE_OUTSIDE_OPTIONS = ["1m", "2m", "5m", "10m", "15m"]
DEFAULT_QUOTA = 100000
MIN_FETCH_SECONDS = 60       # config "minFetchSeconds": reuse cached data younger than this
LOCALE_DIR = os.path.join(CFG_DIR, "locales")
KC_SERVICE = "deye-menubar"
DEFAULT_LANG = "en"
DEFAULT_BASE = "https://eu1-developer.deyecloud.com/v1.0"
DEYE_CLOUD_URL = "https://www.deyecloud.com"
SELF = os.path.abspath(__file__)


class ConfigError(Exception):
    pass


class ApiError(Exception):
    """transient=True: rate limit, server or network problem. Retrying right away would
    only burn quota, so callers wait for the next scheduled refresh instead."""

    def __init__(self, msg, transient=False):
        super().__init__(msg)
        self.transient = transient


# ---------------------------------------------------------------- files

def write_private(path, data):
    """Atomically write JSON readable only by the current user."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_config():
    try:
        return read_json(CFG_FILE)
    except FileNotFoundError:
        raise ConfigError("config.json not found")
    except ValueError as e:
        raise ConfigError(f"config.json is invalid: {e}")


# ---------------------------------------------------------------- secrets

def keychain(account):
    try:
        r = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", KC_SERVICE, "-a", account, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def credentials(cfg):
    """Secrets live in the macOS Keychain; plaintext config values are a legacy fallback."""
    secret = keychain("appSecret") or cfg.get("appSecret")
    pw_hash = keychain("passwordHash")
    if not pw_hash and cfg.get("password"):
        pw_hash = hashlib.sha256(cfg["password"].encode()).hexdigest()
    if not (cfg.get("appId") and cfg.get("email") and secret and pw_hash):
        raise ConfigError("missing credentials")
    return secret, pw_hash


# ---------------------------------------------------------------- i18n

class I18n:
    """English is always loaded first, so any key missing from a translation falls back to English."""

    def __init__(self, lang):
        self.strings = self._load(DEFAULT_LANG)
        if lang != DEFAULT_LANG:
            self.strings.update(self._load(lang))

    @staticmethod
    def _load(code):
        try:
            return read_json(os.path.join(LOCALE_DIR, f"{code}.json"))
        except Exception:
            return {}

    def __call__(self, key, **kwargs):
        text = self.strings.get(key, key)
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text


def available_languages():
    langs = []
    for path in sorted(glob.glob(os.path.join(LOCALE_DIR, "*.json"))):
        code = os.path.splitext(os.path.basename(path))[0]
        try:
            name = read_json(path).get("_language_name", code)
        except Exception:
            continue
        langs.append((code, name))
    return langs


def system_language():
    try:
        out = subprocess.run(
            ["/usr/bin/defaults", "read", "-g", "AppleLanguages"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for line in out.splitlines():
            token = line.strip().strip('(),"')
            if token:
                return token.split("-")[0].split("_")[0].lower()
    except Exception:
        pass
    return DEFAULT_LANG


def resolve_language(cfg):
    lang = str(cfg.get("language") or "auto").lower()
    if lang == "auto":
        lang = system_language()
    if os.path.exists(os.path.join(LOCALE_DIR, f"{lang}.json")):
        return lang
    return DEFAULT_LANG


def set_language(code):
    code = code.lower()
    valid = {"auto"} | {c for c, _ in available_languages()}
    if code not in valid:
        print(f"Unknown language '{code}'. Available: {', '.join(sorted(valid))}", file=sys.stderr)
        return 1
    cfg = load_config()
    cfg["language"] = code
    write_private(CFG_FILE, cfg)
    return 0


# ---------------------------------------------------------------- API

def count_api_call():
    """Local counter of Deye API calls per calendar month (this Mac only)."""
    month = time.strftime("%Y-%m")
    try:
        usage = read_json(USAGE_FILE)
    except Exception:
        usage = {}
    if usage.get("month") != month:
        usage = {"month": month, "count": 0, "since": time.time()}
    usage["count"] = usage.get("count", 0) + 1
    try:
        write_private(USAGE_FILE, usage)
    except Exception:
        pass


def api_post(cfg, path, body, token=None, query=""):
    base = str(cfg.get("base") or DEFAULT_BASE).rstrip("/")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"bearer {token}"
    req = urllib.request.Request(base + path + query, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    try:
        count_api_call()
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            payload = json.load(e)
        except Exception:
            payload = {}
        payload.setdefault("success", False)
        payload.setdefault("code", str(e.code))
        payload.setdefault("msg", e.reason)
        payload["_http"] = e.code
        return payload
    except urllib.error.URLError as e:
        raise ApiError(f"network: {e.reason}", transient=True)


def get_token(cfg, force=False):
    if not force:
        try:
            t = read_json(TOKEN_FILE)
            same_account = t.get("appId") == cfg.get("appId") and t.get("email") == cfg.get("email")
            if same_account and t.get("exp", 0) > time.time() + 3600:
                return t["token"]
        except Exception:
            pass
    secret, pw_hash = credentials(cfg)
    r = api_post(cfg, "/account/token",
                 {"appSecret": secret, "email": cfg["email"], "password": pw_hash},
                 query="?appId=" + urllib.parse.quote(str(cfg["appId"])))
    token = r.get("accessToken")
    if not token:
        raise ApiError(f"login failed: {r.get('code')} {r.get('msg')}")
    exp = time.time() + int(float(r.get("expiresIn") or 86400))
    write_private(TOKEN_FILE, {"token": token, "exp": exp,
                               "appId": cfg["appId"], "email": cfg["email"]})
    return token


def api_call(cfg, path, body):
    """One request; logs in again only if the cached token was rejected."""
    d = {}
    for attempt in range(2):
        token = get_token(cfg, force=attempt == 1)
        d = api_post(cfg, path, body, token)
        if d.get("success"):
            return d
        auth_problem = d.get("_http") == 401 or "token" in str(d.get("msg", "")).lower()
        if not auth_problem:
            break
    http = d.get("_http") or 0
    transient = http == 429 or http >= 500 or str(d.get("code")) == "429"
    raise ApiError(f"{d.get('code')}: {d.get('msg')}", transient=transient)


def list_stations(cfg):
    d = api_call(cfg, "/station/list", {"page": 1, "size": 50})
    stations = [{"id": s.get("id"), "name": s.get("name", "")} for s in d.get("stationList") or []]
    if not stations:
        raise ApiError("no stations found on this account")
    return stations


# ---------------------------------------------------------------- helpers

def num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def fmt_power(w):
    if w is None:
        return "–"
    return f"{w / 1000:.1f}kW" if abs(w) >= 1000 else f"{w:.0f}W"


def fmt_energy(kwh):
    if kwh is None:
        return "–"
    return f"{kwh:.1f}kWh" if kwh < 1000 else f"{kwh / 1000:.2f}MWh"


def fmt_duration(hours):
    if hours > 24:
        return "24h+"
    minutes = int(round(hours * 60))
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def clean(text):
    """SwiftBar uses '|' as a separator and one item per line."""
    return str(text).replace("|", "/").replace("\n", " ")


def pretty_key(key):
    key = key.replace("- ", " ").replace("-", " ")
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", key).strip()


def join(*parts):
    return " · ".join(p for p in parts if p)


def detail(text):
    print(f"    {clean(text)} | trim=false size=12")


# ---------------------------------------------------------------- data sources

def find_inverters(cfg):
    d = api_call(cfg, "/station/listWithDevice", {"page": 1, "size": 20})
    serials = []
    for station in d.get("stationList") or []:
        if cfg.get("stationId") and str(station.get("id")) != str(cfg["stationId"]):
            continue
        for dev in station.get("deviceListItems") or []:
            if str(dev.get("deviceType", "")).upper() == "INVERTER" and dev.get("deviceSn"):
                serials.append(dev["deviceSn"])
    return serials, d


def inverter_serial(cfg):
    serial = cfg.get("deviceSn")
    if not serial:
        serials, _ = find_inverters(cfg)
        if not serials:
            raise ApiError("no inverter found on this station")
        serial = serials[0]
        cfg["deviceSn"] = serial
        write_private(CFG_FILE, cfg)
    return serial


def fetch_device(cfg):
    serial = inverter_serial(cfg)
    r = api_call(cfg, "/device/latest", {"deviceList": [serial]})
    devices = r.get("deviceDataList") or []
    if not devices:
        raise ApiError("no device data returned")
    dev = devices[0]
    points = {}
    for item in dev.get("dataList") or []:
        if item.get("key"):
            points[item["key"]] = {"v": item.get("value"), "u": item.get("unit") or ""}
    data = {"_fetched": time.time(), "sn": dev.get("deviceSn", serial),
            "collectionTime": dev.get("collectionTime"), "points": points}
    write_private(DEVICE_FILE, data)
    return data


def cached_device():
    try:
        return read_json(DEVICE_FILE)
    except Exception:
        return None


def fetch_station(cfg):
    station_id = cfg.get("stationId")
    if not station_id:
        station_id = list_stations(cfg)[0]["id"]
        cfg["stationId"] = station_id
        write_private(CFG_FILE, cfg)
    return api_call(cfg, "/station/latest", {"stationId": station_id})


class Points:
    def __init__(self, dev):
        self.p = (dev or {}).get("points") or {}

    def get(self, *keys):
        """First existing numeric key -> (value, unit)."""
        for k in keys:
            if k in self.p:
                v = num(self.p[k]["v"])
                if v is not None:
                    return v, self.p[k]["u"]
        return None

    def val(self, *keys):
        pair = self.get(*keys)
        return pair[0] if pair else None

    def text(self, key):
        item = self.p.get(key)
        return str(item["v"]) if item and item.get("v") not in (None, "") else None

    def matching(self, pattern):
        rx = re.compile(pattern, re.I)
        return [k for k in sorted(self.p) if rx.search(k)]


# Normalized summary: grid > 0 = import, battery < 0 = charging.

def summary_from_device(dev):
    pt = Points(dev)
    pv = pt.val("TotalSolarPower")
    if pv is None:
        strings = [pt.val(f"DCPowerPV{i}") for i in range(1, 9)]
        pv = sum(s for s in strings if s) if any(s is not None for s in strings) else None
    soc = pt.val("SOC", "BMSSOC")
    if pv is None or soc is None:
        raise ApiError("device data lacks PV power or SOC")
    return {
        "pv": pv,
        "load": pt.val("TotalConsumptionPower", "UPSLoadPower"),
        "soc": soc,
        "battery": pt.val("BatteryPower"),
        "grid": pt.val("TotalGridPower"),
        "gridRelay": pt.text("GridRelayStatus"),
        "updated": num(dev.get("collectionTime")) or dev.get("_fetched"),
        "source": "device",
    }


def summary_from_station(d):
    return {
        "pv": num(d.get("generationPower")),
        "load": num(d.get("consumptionPower")),
        "soc": num(d.get("batterySOC")),
        "battery": num(d.get("batteryPower")),
        "grid": num(d.get("wirePower")),
        "gridRelay": None,
        "updated": num(d.get("lastUpdateTime")),
        "source": "station",
    }


def grid_is_down(relay):
    """GridRelayStatus 'Pull-in' = relay closed = connected to the grid."""
    if not relay:
        return False
    return relay.strip().lower().replace(" ", "").replace("-", "") not in ("pullin", "closed", "on")


# ---------------------------------------------------------------- title

def battery_symbol(soc):
    if soc is None:
        return "battery.0percent"
    for limit, name in ((88, "100"), (63, "75"), (38, "50"), (13, "25")):
        if soc >= limit:
            return f"battery.{name}percent"
    return "battery.0percent"


ICON_SCOPES = {"bar": ("mono", "color", "symbols"), "menu": ("mono", "color", "none")}
# Per-symbol colors for barIcons "symbols": "light menu bar,dark menu bar", picked to stay
# readable on tinted menu bars (including red) without using red or orange.
BAR_SYMBOL_COLORS = {"pv": "#D99A00,#FFD60A", "battery": "#1E9E52,#5EF2A0",
                     "home": "#0A84FF,#64D2FF", "grid": "#A04BD6,#D6A2FF"}
SECTION_COLORS = {"pv": "#FF9F0A", "home": "#0A84FF", "battery": "#30D158", "grid": "#BF5AF2"}


def bar_icons(cfg):
    """'mono' (SF Symbols that follow the menu bar text color), 'symbols' (SF Symbols with
    individual colors) or 'color' (emoji)."""
    value = str(cfg.get("barIcons") or "").lower()
    if value not in ICON_SCOPES["bar"]:
        value = "color" if str(cfg.get("titleStyle") or "").lower() == "emoji" else "mono"
    return value


def menu_icons(cfg):
    """'mono', 'color' or 'none' for the section icons in the dropdown."""
    value = str(cfg.get("menuIcons") or "mono").lower()
    return value if value in ICON_SCOPES["menu"] else "mono"


def set_icons(scope, value):
    value = value.lower()
    if scope not in ICON_SCOPES or value not in ICON_SCOPES[scope]:
        print("Usage: --set-icons bar mono|symbols|color  or  --set-icons menu mono|color|none",
              file=sys.stderr)
        return 1
    cfg = load_config()
    cfg["barIcons" if scope == "bar" else "menuIcons"] = value
    write_private(CFG_FILE, cfg)
    return 0


def section(text, cfg, symbol, color_key, color=None):
    """Print a dropdown section header with an SF Symbol, if menu icons are enabled."""
    mode = menu_icons(cfg)
    params = ""
    if mode != "none":
        params = f" | sfimage={symbol}"
        if mode == "color":
            # sfcolor alone does not tint the item image in dropdown menus; SwiftBar needs a
            # Palette rendering configuration (base64 JSON) for that.
            c = color or SECTION_COLORS[color_key]
            cfg_json = json.dumps({"renderingMode": "Palette", "colors": [c, c]},
                                  separators=(",", ":"))
            params += f" sfcolor={c} sfconfig={base64.b64encode(cfg_json.encode()).decode()}"
    return clean(text) + params


def build_title(cfg, s, soc_txt, arrow, is_low, stale, grid_down):
    """
    barIcons "mono" (default): monochrome SF Symbols that follow the menu bar text
    color, so they stay readable on tinted / inactive-display menu bars.
    barIcons "symbols": the same SF Symbols, each with its own color (sfcolor, sfcolor2, ...).
    barIcons "color": colored emoji. (The legacy titleStyle "emoji" still works.)
    """
    icons = bar_icons(cfg)
    style = "emoji" if icons == "color" else "sf"
    grid = s.get("grid")
    # Grid power: positive = import, negative (shown with a minus) = export.
    grid_txt = "–" if grid is None else ("-" if grid < 0 else "") + fmt_power(abs(grid))
    if style == "emoji":
        title = (f"☀️{fmt_power(s['pv'])} 🔋{soc_txt}{arrow} 🏠{fmt_power(s['load'])} "
                 + ("🔌✖" if grid_down else f"⚡{grid_txt}"))
        if stale:
            title += " ⏸"
    else:
        title = (f":sun.max.fill: {fmt_power(s['pv'])}  :{battery_symbol(s['soc'])}: {soc_txt}{arrow}"
                 f"  :house.fill: {fmt_power(s['load'])}  "
                 + (":bolt.slash.fill:" if grid_down else f":bolt.fill: {grid_txt}"))
        if stale:
            title += "  :pause.circle:"

    params = []
    alert = (is_low or grid_down) and not stale
    color = cfg.get("lowBatteryColor", "#FFD60A") if alert else cfg.get("titleColor")
    if color:
        params.append(f"color={color}")
        if style != "emoji":
            params.append(f"sfcolor={color}")
    if cfg.get("titleFont"):
        params.append(f"font={cfg['titleFont']}")
    if cfg.get("titleSize"):
        params.append(f"size={cfg['titleSize']}")
    if icons == "symbols" and not params:
        colors = [BAR_SYMBOL_COLORS[k] for k in ("pv", "battery", "home", "grid")]
        if stale:
            colors.append("gray")
        params += [f"sfcolor={colors[0]}"] + [f"sfcolor{i}={c}" for i, c in enumerate(colors[1:], 2)]
    params.append("symbolize=" + ("false" if style == "emoji" else "true"))
    return title + " | " + " ".join(params)


# ---------------------------------------------------------------- details

def details_pv(pt, t):
    for i in range(1, 9):
        pw, v, a = pt.get(f"DCPowerPV{i}"), pt.get(f"DCVoltagePV{i}"), pt.get(f"DCCurrentPV{i}")
        if not ((pw and pw[0] > 0) or (v and v[0] >= 20)):
            continue
        detail(f"PV{i}: " + join(fmt_power(pw[0]) if pw else None,
                                 f"{v[0]:.1f}V" if v else None,
                                 f"{a[0]:.2f}A" if a else None))
    today = pt.val("DailyActiveProduction", "PVDailyPowerGenerationActive")
    if today is not None:
        detail(t("today_energy", energy=fmt_energy(today)))


def phase_line(n, v, a, pw):
    return f"L{n}: " + join(f"{v:.1f}V" if v is not None else None,
                            f"{a:.2f}A" if a is not None else None,
                            fmt_power(pw) if pw is not None else None)


def details_load(pt, t):
    ups = pt.val("UPSLoadPower")
    if ups is not None:
        detail(t("ups_load", power=fmt_power(ups)))
    for n in (1, 2, 3):
        v, pw = pt.val(f"LoadVoltageL{n}"), pt.val(f"LoadPowerL{n}")
        if v is not None or pw is not None:
            detail(phase_line(n, v, None, pw))
    today = pt.val("DailyConsumption")
    if today is not None:
        detail(t("today_energy", energy=fmt_energy(today)))


def details_battery(pt, cfg, s, t):
    v = pt.val("BatteryVoltage", "BMSVoltage")
    a = pt.val("BatteryTotalCurrent", "BatteryCurrent1", "BMSCurrent")
    temp = pt.val("Temperature- Battery", "BatteryTemperature", "BMSTemperature")
    line = join(f"{v:.2f}V" if v is not None else None,
                f"{a:.1f}A" if a is not None else None,
                f"{temp:.0f}°C" if temp is not None else None)
    if line:
        detail(line)

    # Capacity: config override, else rated Ah × nominal voltage (51.2V for 16s LiFePO4)
    cap_kwh = battery_capacity_kwh(pt, cfg)
    soc, batt = s.get("soc"), s.get("battery")
    if cap_kwh and soc is not None:
        stored = cap_kwh * soc / 100
        detail(t("battery_energy", stored=fmt_energy(stored), capacity=fmt_energy(cap_kwh)))
        idle = float((cfg.get("thresholds") or {}).get("idleWatts", 20))
        reserve = float(cfg.get("batteryReservePercent", 10))
        if batt is not None and batt < -idle and soc < 100:
            detail(t("battery_full_in", duration=fmt_duration(cap_kwh * (100 - soc) / 100 / (-batt / 1000))))
        elif batt is not None and batt > idle and soc > reserve:
            detail(t("battery_reserve_in", percent=f"{reserve:.0f}%",
                     duration=fmt_duration(cap_kwh * (soc - reserve) / 100 / (batt / 1000))))


def details_grid(pt, t):
    for n in (1, 2, 3):
        v, a, pw = pt.val(f"GridVoltageL{n}"), pt.val(f"GridCurrentL{n}"), pt.val(f"GridPowerL{n}")
        if v is not None or pw is not None:
            detail(phase_line(n, v, a, pw))
    bought, sold = pt.val("DailyEnergyPurchased", "DailyEnergyBuy"), pt.val("DailyGridFeedIn", "DailyEnergySell")
    if bought is not None or sold is not None:
        detail(t("grid_today", bought=fmt_energy(bought), sold=fmt_energy(sold)))


ENERGY_TODAY = [("DailyActiveProduction", "e_solar"), ("DailyConsumption", "e_consumption"),
                ("DailyEnergyPurchased", "e_bought"), ("DailyGridFeedIn", "e_sold"),
                ("DailyChargingEnergy", "e_charged"), ("DailyDischargingEnergy", "e_discharged")]
ENERGY_TOTAL = [("TotalActiveProduction", "e_solar"), ("TotalConsumption", "e_consumption"),
                ("TotalEnergyBuy", "e_bought"), ("TotalEnergySell", "e_sold"),
                ("TotalChargeEnergy", "e_charged"), ("TotalDischargeEnergy", "e_discharged")]


def submenu_energy(pt, t, header, keys, ratio=False):
    rows = [(t(label), pt.val(k)) for k, label in keys if pt.val(k) is not None]
    if not rows:
        return
    print(clean(header))
    for label, value in rows:
        print(f"--{clean(label)}: {fmt_energy(value)}")
    if ratio:
        cons, bought = pt.val("DailyConsumption"), pt.val("DailyEnergyPurchased")
        if cons and bought is not None:
            pct = max(0.0, min(100.0, (1 - bought / cons) * 100))
            print(f"--{clean(t('self_sufficiency', percent=f'{pct:.0f}%'))}")


def submenu_inverter(pt, t):
    rows = []
    total = pt.val("TotalInverterOutputPower")
    if total is not None:
        rows.append(t("inverter_output", power=fmt_power(total)))
    for n in (1, 2, 3):
        pw = pt.val(f"InverterOutputPowerL{n}")
        if pw is not None:
            rows.append(f"L{n}: {fmt_power(pw)}")
    for k in pt.matching(r"temp"):
        if "battery" in k.lower() or "bms" in k.lower():
            continue
        v = pt.val(k)
        if v is not None:
            rows.append(f"{pretty_key(k)}: {v:.1f}°C")
    relay = pt.text("GridRelayStatus")
    if relay:
        rows.append(t("grid_relay", status=relay))
    rated = pt.val("RatedPower")
    if rated:
        rows.append(t("rated_power", power=fmt_power(rated)))
    fw = join(*(f"{k} {pt.text(k)}" for k in ("MAIN", "HMI") if pt.text(k)))
    if fw:
        rows.append(t("firmware", version=fw))
    if rows:
        print(clean(t("inverter")))
        for row in rows:
            print(f"--{clean(row)}")


def battery_capacity_kwh(pt, cfg):
    cap = num(cfg.get("batteryCapacityKWh"))
    ah = pt.val("BatteryRatedCapacity")
    if cap is None and ah:
        cap = ah * float(cfg.get("batteryNominalVoltage", 51.2)) / 1000
    return cap


def submenu_battery(pt, cfg, t):
    rows = []
    soc, bms_soc = pt.val("SOC"), pt.val("BMSSOC")
    if soc is not None:
        rows.append(t("b_soc", value=f"{soc:.0f}%"
                      + (f" (BMS {bms_soc:.0f}%)" if bms_soc is not None and bms_soc != soc else "")))
    v, a, pw = pt.val("BatteryVoltage"), pt.val("BatteryTotalCurrent", "BatteryCurrent1"), pt.val("BatteryPower")
    if v is not None:
        rows.append(t("b_voltage", value=f"{v:.2f}V"))
    if a is not None:
        rows.append(t("b_current", value=f"{a:.2f}A"))
    if pw is not None:
        rows.append(t("b_power", value=fmt_power(pw)))
    temp = pt.val("Temperature- Battery", "BatteryTemperature", "BMSTemperature")
    if temp is not None:
        rows.append(t("b_temperature", value=f"{temp:.1f}°C"))
    bms_v, bms_a = pt.val("BMSVoltage"), pt.val("BMSCurrent")
    if bms_v is not None or bms_a is not None:
        rows.append("BMS: " + join(f"{bms_v:.2f}V" if bms_v is not None else None,
                                   f"{bms_a:.0f}A" if bms_a is not None else None))
    chg_v = pt.val("BMSChargeVoltage")
    if chg_v:
        rows.append(t("b_charge_voltage", value=f"{chg_v:.1f}V"))
    ah, cap = pt.val("BatteryRatedCapacity"), battery_capacity_kwh(pt, cfg)
    if cap:
        rows.append(t("b_capacity", value=join(f"{ah:.0f}Ah" if ah else None, fmt_energy(cap))))
        if soc is not None:
            rows.append(t("b_stored", value=fmt_energy(cap * soc / 100)))
    today_c, today_d = pt.val("DailyChargingEnergy"), pt.val("DailyDischargingEnergy")
    if today_c is not None or today_d is not None:
        rows.append(t("b_today", charged=fmt_energy(today_c), discharged=fmt_energy(today_d)))
    tot_c, tot_d = pt.val("TotalChargeEnergy"), pt.val("TotalDischargeEnergy")
    if tot_c is not None or tot_d is not None:
        rows.append(t("b_total", charged=fmt_energy(tot_c), discharged=fmt_energy(tot_d)))
    if cap and tot_d:
        rows.append(t("b_cycles", value=f"~{tot_d / cap:.0f}"))
    fw = pt.text("LithiumBatteryVersionNumber")
    if fw:
        rows.append(t("firmware", version=fw))
    if rows:
        print(clean(t("battery_menu")))
        for row in rows:
            print(f"--{clean(row)}")


# ---------------------------------------------------------------- rendering

def render(s, cfg, t, dev=None, stale_error=None):
    th = cfg.get("thresholds") or {}
    low_battery = float(th.get("lowBatteryPercent", 20))
    idle = float(th.get("idleWatts", 20))               # battery flows below this are "idle"
    grid_idle = float(th.get("gridIdleWatts", 0))       # grid: show the exact value by default

    batt, grid, soc = s.get("battery"), s.get("grid"), s.get("soc")
    if batt is not None and cfg.get("invertBatterySign"):
        batt = -batt
    if grid is not None and cfg.get("invertGridSign"):
        grid = -grid
    s = dict(s, battery=batt, grid=grid)
    grid_down = grid_is_down(s.get("gridRelay"))

    if batt is None or abs(batt) < idle:
        arrow, batt_state = "", t("battery_idle")
    elif batt < 0:
        arrow, batt_state = "↑", t("battery_charging", power=fmt_power(-batt))
    else:
        arrow, batt_state = "↓", t("battery_discharging", power=fmt_power(batt))

    if grid_down:
        grid_state = t("grid_down", status=s.get("gridRelay"))
    elif grid is None or abs(grid) <= grid_idle:
        grid_state = t("grid_idle")
    elif grid > 0:
        grid_state = t("grid_import", power=fmt_power(grid))
    else:
        grid_state = t("grid_export", power=fmt_power(-grid))

    soc_txt = f"{soc:.0f}%" if soc is not None else "–"
    is_low = soc is not None and soc < low_battery
    print(build_title(cfg, s, soc_txt, arrow, is_low, bool(stale_error), grid_down))

    print("---")
    if stale_error:
        print(clean(t("stale_data", error=stale_error)) + " | color=orange")
        print("---")
    pt = Points(dev)
    print(section(t("pv_production", power=fmt_power(s.get("pv"))), cfg, "sun.max.fill", "pv"))
    details_pv(pt, t)
    print(section(t("home_consumption", power=fmt_power(s.get("load"))), cfg, "house.fill", "home"))
    details_load(pt, t)
    print(section(t("battery", soc=soc_txt, state=batt_state), cfg, battery_symbol(soc), "battery",
                  "#FFD60A" if is_low else None))
    details_battery(pt, cfg, s, t)
    grid_line = section(t("grid", state=grid_state), cfg,
                        "bolt.slash.fill" if grid_down else "bolt.fill", "grid",
                        "#FF453A" if grid_down else None)
    if grid_down:
        grid_line += (" " if "|" in grid_line else " | ") + "color=red"
    print(grid_line)
    details_grid(pt, t)
    updated = s.get("updated")
    if updated:
        age = max(0, int(time.time() - updated) // 60)
        print(clean(t("last_update", time=time.strftime("%H:%M", time.localtime(updated)),
                      minutes=age)))
    if pt.p:
        print("---")
        submenu_energy(pt, t, t("energy_today"), ENERGY_TODAY, ratio=True)
        submenu_energy(pt, t, t("energy_total"), ENERGY_TOTAL)
        submenu_battery(pt, cfg, t)
        submenu_inverter(pt, t)
    menu_footer(cfg, t)


def current_interval():
    m = re.match(r"^deye\.([0-9]+[smhd])\.py$", os.path.basename(SELF))
    return m.group(1) if m else None


def interval_seconds(iv):
    n, unit = int(iv[:-1]), iv[-1]
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def calls_per_month(iv):
    return 30 * 86400 / interval_seconds(iv)


def fmt_count(n):
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else f"{n:.0f}"


def interval_label(iv):
    n, unit = iv[:-1], iv[-1]
    return f"{n} {'sec' if unit == 's' else 'min' if unit == 'm' else unit}"


def valid_interval(iv):
    return bool(re.match(r"^[0-9]+[smhd]$", str(iv))) and interval_seconds(iv) >= 10


def rename_interval(iv):
    """The interval is part of the plugin's file name; SwiftBar picks the rename up."""
    if current_interval() == iv:
        return
    os.rename(SELF, os.path.join(os.path.dirname(SELF), f"deye.{iv}.py"))
    try:
        subprocess.run(["/usr/bin/open", "-g", "swiftbar://refreshallplugins"],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def set_interval(iv):
    """Same interval all day: also turns the time-of-day schedule off."""
    if not valid_interval(iv):
        print(f"Invalid interval '{iv}' (examples: 30s, 1m, 5m)", file=sys.stderr)
        return 1
    try:
        cfg = load_config()
        if schedule_cfg(cfg)["enabled"]:
            cfg["schedule"] = dict(schedule_cfg(cfg), enabled=False)
            write_private(CFG_FILE, cfg)
    except ConfigError:
        pass
    rename_interval(iv)
    return 0


# ---------------------------------------------------------------- schedule

def schedule_cfg(cfg):
    """The schedule settings with defaults filled in and invalid values replaced."""
    s = dict(SCHEDULE_DEFAULTS)
    raw = cfg.get("schedule")
    if isinstance(raw, dict):
        for key in ("from", "to"):
            if isinstance(raw.get(key), int) and not isinstance(raw.get(key), bool) \
                    and 0 <= raw[key] <= 23:
                s[key] = raw[key]
        for key in ("inside", "outside"):
            if valid_interval(raw.get(key)):
                s[key] = raw[key]
        s["enabled"] = raw.get("enabled") is True
    return s


def window_hours(s):
    return (s["to"] - s["from"]) % 24 or 24      # from == to: the whole day


def schedule_interval(s, now=None):
    """Interval that applies right now: 'inside' during the daily window, else 'outside'."""
    now = now or time.localtime()
    hour = now.tm_hour + now.tm_min / 60
    return s["inside"] if (hour - s["from"]) % 24 < window_hours(s) else s["outside"]


def schedule_calls_per_month(s):
    width = window_hours(s)
    per_day = (width * 3600 / interval_seconds(s["inside"])
               + (24 - width) * 3600 / interval_seconds(s["outside"]))
    return 30 * per_day


def schedule_file_interval(s):
    """SwiftBar runs the plugin at the faster of the two intervals; the slower one is
    enforced by skipping the API call (see fetch_due)."""
    return min((s["inside"], s["outside"]), key=interval_seconds)


def read_state():
    try:
        return read_json(STATE_FILE)
    except Exception:
        return {}


def write_state(error=""):
    try:
        write_private(STATE_FILE, {"attempt": time.time(), "error": error})
    except Exception:
        pass


def fetch_due(cfg):
    """Without a schedule every run fetches. With one, a run fetches only if the interval
    of the current period has passed since the last attempt (half a run of tolerance, so
    a 5 min period with a 1 min plugin interval fetches on the 5th run, not the 4th)."""
    s = schedule_cfg(cfg)
    if not s["enabled"]:
        return True
    iv = current_interval()
    run_every = interval_seconds(iv) if iv else 60
    age = time.time() - float(read_state().get("attempt", 0) or 0)
    return age < 0 or age >= interval_seconds(schedule_interval(s)) - run_every / 2


def refresh_now():
    """'Refresh now' from the menu: the next run fetches even outside the schedule."""
    try:
        write_private(STATE_FILE, dict(read_state(), attempt=0))
    except Exception:
        pass
    return 0


def set_schedule(key, value):
    usage = ("Usage: --set-schedule enabled on|off | from|to 0-23 | "
             "inside|outside <interval, e.g. 1m>")
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    s = schedule_cfg(cfg)
    if key == "enabled" and value in ("on", "off"):
        s["enabled"] = value == "on"
    elif key in ("from", "to") and value.isdigit() and 0 <= int(value) <= 23:
        s[key] = int(value)
    elif key in ("inside", "outside") and valid_interval(value):
        s[key] = value
    else:
        print(usage, file=sys.stderr)
        return 1
    cfg["schedule"] = s
    write_private(CFG_FILE, cfg)
    if s["enabled"]:
        rename_interval(schedule_file_interval(s))
    return 0


def sched_title(s, t):
    """Short summary, e.g. 'Refresh: 07:00–21:00 (1m), else 5m'."""
    return t("sched_title", start=f"{s['from']:02d}:00", end=f"{s['to']:02d}:00",
             inside=s["inside"], outside=s["outside"])


def menu_schedule(cfg, t, quota):
    s = schedule_cfg(cfg)
    hours = lambda h: f"{h:02d}:00"
    on = s["enabled"]
    print(f"--{clean(t('sched_menu'))}" + (" | checked=true" if on else ""))
    print(f'----{clean(t("sched_enabled"))} | bash="{SELF}" param1=--set-schedule param2=enabled '
          f'param3={"off" if on else "on"} terminal=false refresh=true' + (" checked=true" if on else ""))
    calls = schedule_calls_per_month(s)
    print(f"----{clean(sched_title(s, t))} | size=12")
    print(f"----{clean(t('api_projection', calls=fmt_count(calls), quota=fmt_count(quota)))} | size=12"
          + (" color=red" if calls > quota else ""))
    print("-------")
    for key, label_key, options in (("inside", "sched_inside", SCHEDULE_INSIDE_OPTIONS),
                                    ("outside", "sched_outside", SCHEDULE_OUTSIDE_OPTIONS)):
        print(f"----{clean(t(label_key, label=interval_label(s[key])))}")
        for iv in options:
            mark = " checked=true" if iv == s[key] else ""
            print(f'------{clean(interval_label(iv))} | bash="{SELF}" param1=--set-schedule '
                  f'param2={key} param3={iv} terminal=false refresh=true{mark}')
    for key, label_key in (("from", "sched_from"), ("to", "sched_to")):
        print(f"----{clean(t(label_key, hour=hours(s[key])))}")
        for h in range(24):
            mark = " checked=true" if h == s[key] else ""
            print(f'------{hours(h)} | bash="{SELF}" param1=--set-schedule param2={key} '
                  f'param3={h} terminal=false refresh=true{mark}')


def menu_interval(cfg, t):
    quota = int(cfg.get("monthlyQuota", DEFAULT_QUOTA))
    current = current_interval()
    s = schedule_cfg(cfg)
    if s["enabled"]:
        print(clean(sched_title(s, t)))
    else:
        print(clean(t("refresh_interval")) + (f": {interval_label(current)}" if current else ""))
    options = INTERVALS if current in INTERVALS or not current else INTERVALS + [current]
    for iv in options:
        calls = calls_per_month(iv)
        params = ["terminal=false", "refresh=false"]
        if iv == current and not s["enabled"]:
            params.append("checked=true")
        if calls > quota:
            params.append("color=red")
        print(f'--{clean(t("interval_option", label=interval_label(iv), calls=fmt_count(calls)))} | '
              f'bash="{SELF}" param1=--set-interval param2={iv} ' + " ".join(params))
    print("-----")
    menu_schedule(cfg, t, quota)
    print("-----")
    try:
        usage = read_json(USAGE_FILE)
        if usage.get("month") == time.strftime("%Y-%m"):
            print(f'--{clean(t("api_usage", count=usage.get("count", 0)))} | size=12')
    except Exception:
        pass
    calls = schedule_calls_per_month(s) if s["enabled"] else (calls_per_month(current) if current else None)
    if calls is not None:
        print(f'--{clean(t("api_projection", calls=fmt_count(calls), quota=fmt_count(quota)))} | size=12')
    print(f'--{clean(t("api_note"))} | size=11 color=gray')


def menu_icons_options(cfg, t):
    print(clean(t("icons")))
    names = {"mono": t("icons_mono"), "color": t("icons_color"), "none": t("icons_none"),
             "symbols": t("icons_symbols")}
    for scope, label, current in (("bar", t("icons_bar"), bar_icons(cfg)),
                                  ("menu", t("icons_menu"), menu_icons(cfg))):
        print(f"--{clean(label)}")
        for value in ICON_SCOPES[scope]:
            checked = " checked=true" if value == current else ""
            print(f'----{clean(names[value])} | bash="{SELF}" param1=--set-icons '
                  f'param2={scope} param3={value} terminal=false refresh=true{checked}')


def menu_footer(cfg, t):
    print("---")
    print(f"{clean(t('open_deye_cloud'))} | href={DEYE_CLOUD_URL}")
    raw = DEVICE_FILE if os.path.exists(DEVICE_FILE) else LAST_FILE
    if os.path.exists(raw):
        print(f'{clean(t("show_raw_json"))} | bash=/usr/bin/open param1=-t '
              f'param2="{raw}" terminal=false')
    menu_interval(cfg, t)
    menu_icons_options(cfg, t)
    print(clean(t("language")))
    current = str(cfg.get("language") or "auto").lower()
    for code, name in [("auto", t("language_auto"))] + available_languages():
        checked = " checked=true" if code == current else ""
        print(f'--{clean(name)} | bash="{SELF}" param1=--set-language param2={code} '
              f'terminal=false refresh=true{checked}')
    print(f'{clean(t("refresh_now"))} | bash="{SELF}" param1=--refresh-now terminal=false refresh=true')
    print(f"Deye Menubar v{VERSION} | size=11 color=gray")


def render_error(cfg, t, message):
    if bar_icons(cfg) == "color":
        print("☀️⚠️ | symbolize=false")
    else:
        print(":sun.max.fill: :exclamationmark.triangle.fill: | symbolize=true")
    print("---")
    print(clean(t("error", error=message)))
    menu_footer(cfg, t)


def render_not_configured(t):
    print(":sun.max.fill: :gearshape: | symbolize=true")
    print("---")
    print(clean(t("not_configured")))


# ---------------------------------------------------------------- entry points

def check():
    """Used by the installer: force a fresh login and list stations."""
    try:
        cfg = load_config()
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
        print(json.dumps({"ok": True, "stations": list_stations(cfg)}))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1


def dump_device():
    """Print every data point the inverter reports."""
    cfg = load_config()
    serials, d = find_inverters(cfg)
    if not serials:
        print("No inverter found. Raw station/device response:")
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return 1
    r = api_call(cfg, "/device/latest", {"deviceList": serials[:10]})
    devices = r.get("deviceDataList") or []
    if not devices:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 1
    for dev in devices:
        print(f"# inverter {dev.get('deviceSn')}")
        for item in dev.get("dataList") or []:
            print(f"{item.get('key')}\t{item.get('value')}\t{item.get('unit') or ''}")
    return 0


def collect(cfg):
    """
    dataSource "device" (default): one /device/latest request gives the summary and
    all details. Falls back to /station/latest if device data is unusable.
    dataSource "station": station summary every run, device details at most every
    detailsRefreshSeconds.
    """
    source = str(cfg.get("dataSource") or "device").lower()
    if source == "device":
        try:
            dev = fetch_device(cfg)
            return summary_from_device(dev), dev
        except ConfigError:
            raise
        except ApiError as e:
            if e.transient:          # rate limited / server down: no second call, show cache
                raise
        except Exception:
            pass
    summary = summary_from_station(fetch_station(cfg))
    dev = cached_device()
    if cfg.get("showDetails", True):
        ttl = float(cfg.get("detailsRefreshSeconds", 120))
        if not dev or time.time() - dev.get("_fetched", 0) >= ttl:
            try:
                dev = fetch_device(cfg)
            except Exception:
                pass
    return summary, dev


def fresh_cache(cfg):
    """Last summary if it is too recent to fetch again (manual refresh, menu setting
    changes), else None. Keeps the effective interval at or above minFetchSeconds."""
    try:
        min_age = float(cfg.get("minFetchSeconds", MIN_FETCH_SECONDS))
        iv = current_interval()
        if iv:                       # never skip a scheduled run of a short interval
            min_age = min(min_age, 0.8 * interval_seconds(iv))
        age = time.time() - os.path.getmtime(LAST_FILE)
        if min_age <= 0 or not 0 <= age < min_age:
            return None
        last = read_json(LAST_FILE)
        return last if "source" in last else None
    except Exception:
        return None


def run():
    try:
        cfg = load_config()
    except ConfigError:
        render_not_configured(I18n(resolve_language({})))
        return 0

    t = I18n(resolve_language(cfg))
    cached = fresh_cache(cfg)
    if cached:
        render(cached, cfg, t, dev=cached_device())
        return 0
    if not fetch_due(cfg):                    # time-of-day schedule: not due yet, show the cache
        try:
            last = read_json(LAST_FILE)
            if "source" in last:
                render(last, cfg, t, dev=cached_device(),
                       stale_error=read_state().get("error") or None)
                return 0
        except Exception:
            pass
    try:
        summary, dev = collect(cfg)
        write_private(LAST_FILE, summary)
        write_state()
        render(summary, cfg, t, dev=dev)
    except ConfigError:
        render_not_configured(t)
    except Exception as e:
        write_state(str(e))
        try:
            last = read_json(LAST_FILE)
            if "source" not in last:              # v1.0-1.2 stored the raw station response
                last = summary_from_station(last)
            render(last, cfg, t, dev=cached_device(), stale_error=str(e))
        except Exception:
            render_error(cfg, t, str(e))
    return 0


def main():
    args = sys.argv[1:]
    if args[:1] == ["--check"]:
        return check()
    if args[:1] == ["--set-language"] and len(args) > 1:
        return set_language(args[1])
    if args[:1] == ["--set-icons"] and len(args) > 2:
        return set_icons(args[1], args[2])
    if args[:1] == ["--set-interval"] and len(args) > 1:
        return set_interval(args[1])
    if args[:1] == ["--set-schedule"] and len(args) > 2:
        return set_schedule(args[1], args[2].lower())
    if args[:1] == ["--refresh-now"]:
        return refresh_now()
    if args[:1] == ["--dump-device"]:
        try:
            return dump_device()
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if args[:1] in (["--version"], ["-v"]):
        print(VERSION)
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
