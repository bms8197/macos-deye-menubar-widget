#!/usr/bin/env python3
"""
End-to-end smoke test: runs the plugin against a local mock of the Deye Cloud API
(fixture = real /device/latest data point names) and checks the SwiftBar output.
Runs on Linux and macOS; no network and no Keychain needed.
"""
import base64
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "device_dump.tsv"
STATE = {"fail_device": False, "down": False, "fail_code": "429", "station_calls": 0}


def load_points():
    rows = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            parts = (line.split("\t") + ["", ""])[:3]
            rows.append({"key": parts[0], "value": parts[1], "unit": parts[2]})
    return rows


class MockDeye(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        path = self.path.split("?")[0]
        if path == "/v1.0/account/token":
            r = {"success": True, "accessToken": "tok", "expiresIn": "5183999"}
        elif path == "/v1.0/station/list":
            r = {"success": True, "stationList": [{"id": 1, "name": "Home"}]}
        elif path == "/v1.0/station/listWithDevice":
            r = {"success": True, "stationList": [{"id": 1, "deviceListItems": [
                {"deviceSn": "SN0001", "deviceType": "INVERTER"}]}]}
        elif path == "/v1.0/device/latest":
            r = ({"success": False, "code": STATE["fail_code"], "msg": "Device request failed"} if STATE["fail_device"]
                 else {"success": True, "deviceDataList": [{"deviceSn": "SN0001",
                       "collectionTime": "1794830000", "dataList": load_points()}]})
        elif path == "/v1.0/station/latest":
            STATE["station_calls"] += 1
            r = {"success": True, "generationPower": 600, "consumptionPower": 400,
                 "wirePower": 30, "batteryPower": -170, "batterySOC": 42,
                 "lastUpdateTime": 1794830000}
        else:
            r = {"success": False, "msg": "unknown endpoint"}
        body = json.dumps(r).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


FAILURES = []


def expect(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        FAILURES.append(name)


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockDeye)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    home = Path(tempfile.mkdtemp())
    cfg_dir = home / ".config" / "deye"
    (cfg_dir / "locales").mkdir(parents=True)
    for f in (ROOT / "locales").glob("*.json"):
        shutil.copy(f, cfg_dir / "locales")
    plugin_dir = home / "SwiftBar"
    plugin_dir.mkdir()
    plugin = plugin_dir / "deye.1m.py"
    shutil.copy(ROOT / "plugin" / "deye.py", plugin)
    plugin.chmod(0o755)
    env = dict(os.environ, HOME=str(home))

    def run(*args):
        r = subprocess.run([sys.executable, str(plugin), *args], capture_output=True,
                           text=True, env=env, timeout=30)
        return r.returncode, r.stdout, r.stderr

    print("not configured")
    _, out, _ = run()
    expect("shows not-configured item", "install.sh" in out)

    # Legacy plaintext credentials avoid the Keychain, so this also runs on Linux CI.
    (cfg_dir / "config.json").write_text(json.dumps({
        "base": f"http://127.0.0.1:{port}/v1.0", "appId": "app", "email": "user@example.com",
        "appSecret": "secret", "password": "pw", "language": "en",
        "minFetchSeconds": 0}))

    print("--check")
    code, out, _ = run("--check")
    expect("check succeeds", code == 0 and json.loads(out)["ok"])

    print("device mode (default)")
    _, out, _ = run()
    title = out.splitlines()[0]
    expect("title has PV, SoC, load", "593W" in title and "42%↑" in title and "393W" in title)
    main_menu = out.split("\n---\n")[1]          # section between title and submenus
    expect("PV strings listed", "PV1: 255W" in main_menu and "PV2: 338W" in main_menu
           and "PV3" not in main_menu)
    expect("grid positive = import", "Grid: importing 32W" in out)
    expect("battery submenu", "\nBattery\n" in out and "Cycles (estimated)" in out)
    expect("energy submenus", "Energy today" in out and "Grid independence: 36%" in out)
    expect("no frequency in main menu", "Frequency" not in main_menu)
    expect("interval menu with quota", "Refresh interval: 1 min" in out and "43.2k" in out)
    expect("usage counter written", (cfg_dir / "usage.json").exists())
    expect("no raw data-point dump in the menu", "ACVoltageRUA" not in out)

    expect("grid power in title", ":bolt.fill: 32W" in title)

    print("icons")
    expect("default: mono bar", out.splitlines()[0].startswith(":sun.max.fill:"))
    expect("default: mono menu icons", "Solar production: 593W | sfimage=sun.max.fill\n" in out
           and "sfcolor" not in main_menu)
    expect("icons menu present", "\nIcons\n--Menu bar\n----Monochrome" in out
           and "param1=--set-icons param2=menu param3=none" in out)
    code, _, _ = run("--set-icons", "menu", "color")
    _, out, _ = run()
    expect("color menu icons", "sfimage=house.fill sfcolor=#0A84FF sfconfig=" in out
           and "sfimage=sun.max.fill sfcolor=#FF9F0A sfconfig=" in out)
    cfg_b64 = out.split("sfimage=house.fill sfcolor=#0A84FF sfconfig=")[1].split()[0]
    palette = json.loads(base64.b64decode(cfg_b64))
    expect("palette config carries the color",
           palette["renderingMode"] == "Palette" and palette["colors"][0] == "#0A84FF")
    code, _, _ = run("--set-icons", "bar", "color")
    _, out, _ = run()
    expect("color bar uses emoji", out.splitlines()[0].startswith("☀️") and "symbolize=false" in out.splitlines()[0])
    run("--set-icons", "bar", "symbols")
    _, out, _ = run()
    title = out.splitlines()[0]
    expect("colored symbols in bar", title.startswith(":sun.max.fill:") and "sfcolor=#D99A00,#FFD60A" in title
           and "sfcolor2=" in title and "sfcolor3=#0A84FF,#64D2FF" in title and "symbolize=true" in title)
    expect("grid symbol colored", "sfcolor4=#A04BD6,#D6A2FF" in title and ":bolt.fill: 32W" in title)
    expect("symbols option in icons menu", "----Color (symbols)" in out)
    run("--set-icons", "bar", "color")
    _, out, _ = run()
    expect("grid power in emoji title", out.splitlines()[0].count("⚡32W") == 1)
    run("--set-icons", "bar", "symbols")
    run("--set-icons", "menu", "none")
    _, out, _ = run()
    expect("no menu icons", "sfimage" not in out)
    expect("invalid icon option rejected", run("--set-icons", "bar", "none")[0] == 1
           and run("--set-icons", "foo", "mono")[0] == 1)
    run("--set-icons", "bar", "mono")
    run("--set-icons", "menu", "mono")

    print("small grid power")
    original = FIXTURE.read_text(encoding="utf-8")
    try:
        FIXTURE.write_text(original.replace("TotalGridPower\t32\tW", "TotalGridPower\t17\tW"),
                           encoding="utf-8")
        _, out, _ = run()
        expect("17 W grid shown exactly", "Grid: importing 17W" in out)
        cfg = json.loads((cfg_dir / "config.json").read_text())
        (cfg_dir / "config.json").write_text(json.dumps(dict(cfg, thresholds={"gridIdleWatts": 20})))
        _, out, _ = run()
        expect("gridIdleWatts hides small flows", "Grid: ~0" in out)
        (cfg_dir / "config.json").write_text(json.dumps(cfg))
    finally:
        FIXTURE.write_text(original, encoding="utf-8")

    print("Romanian")
    run("--set-language", "ro")
    _, out, _ = run()
    expect("translated", "Producție PV: 593W" in out and "Rețea: import 32W" in out)
    code, _, _ = run("--set-language", "xx")
    expect("unknown language rejected", code == 1)
    run("--set-language", "en")

    print("grid outage")
    original = FIXTURE.read_text(encoding="utf-8")
    try:
        FIXTURE.write_text(original.replace("GridRelayStatus\tPull-in", "GridRelayStatus\tBreak"),
                           encoding="utf-8")
        _, out, _ = run()
        expect("bolt-slash in title", "bolt.slash.fill" in out.splitlines()[0]
               and ":bolt.fill:" not in out.splitlines()[0])
        expect("grid disconnected line", "DISCONNECTED" in out)
    finally:
        FIXTURE.write_text(original, encoding="utf-8")

    print("minimum fetch age")
    def api_count():
        return json.loads((cfg_dir / "usage.json").read_text())["count"]
    cfg = json.loads((cfg_dir / "config.json").read_text())
    (cfg_dir / "config.json").write_text(json.dumps(dict(cfg, minFetchSeconds=60)))
    run()                                       # last.json was just written: served from cache
    before = api_count()
    _, out, _ = run()
    expect("recent data served from cache, no API call", api_count() == before
           and "593W" in out.splitlines()[0])
    (cfg_dir / "config.json").write_text(json.dumps(cfg))
    run()
    expect("minFetchSeconds 0 fetches again", api_count() == before + 1)

    print("time-of-day schedule")
    import time as _time
    hour = _time.localtime().tm_hour
    inside_from, inside_to = hour, (hour + 12) % 24          # now is inside this window
    outside_from, outside_to = (hour + 6) % 24, (hour + 18) % 24   # now is outside
    state_file = cfg_dir / "state.json"

    def set_attempt_age(seconds):
        state_file.write_text(json.dumps({"attempt": _time.time() - seconds, "error": ""}))

    cfg = json.loads((cfg_dir / "config.json").read_text())
    _, out, _ = run()
    expect("schedule is off by default", "\n--Schedule by time of day\n----Use a schedule | " in out
           and "Refresh interval: 1 min\n" in out)
    expect("invalid schedule setting rejected", run("--set-schedule", "from", "24")[0] == 1
           and run("--set-schedule", "inside", "soon")[0] == 1 and run("--set-schedule", "x", "1")[0] == 1)
    for key, value in (("from", str(inside_from)), ("to", str(inside_to)),
                       ("inside", "1m"), ("outside", "5m"), ("enabled", "on")):
        run("--set-schedule", key, value)
    sch = json.loads((cfg_dir / "config.json").read_text())["schedule"]
    expect("schedule saved", sch == {"enabled": True, "from": inside_from, "to": inside_to,
                                     "inside": "1m", "outside": "5m"})
    set_attempt_age(40)
    before = api_count()
    run()
    expect("inside the window: fetches every run", api_count() == before + 1)
    run("--set-schedule", "from", str(outside_from))
    run("--set-schedule", "to", str(outside_to))
    set_attempt_age(100)
    before = api_count()
    _, out, _ = run()
    expect("outside the window: no call before the slow interval", api_count() == before
           and "593W" in out.splitlines()[0])
    set_attempt_age(290)
    run()
    expect("outside the window: fetches after the slow interval", api_count() == before + 1)
    set_attempt_age(100)
    run("--refresh-now")
    run()
    expect("Refresh now fetches outside the schedule", api_count() == before + 2)
    _, out, _ = run()
    expect("schedule shown in the menu", f"Refresh: {outside_from:02d}:00–{outside_to:02d}:00 (1m), else 5m" in out
           and "Inside the window: 1 min" in out and "param1=--refresh-now" in out
           and f"param1=--set-schedule param2=from param3={inside_from} " in out)
    run("--set-interval", "2m")
    expect("--set-interval turns the schedule off", json.loads(
        (cfg_dir / "config.json").read_text())["schedule"]["enabled"] is False
        and (plugin_dir / "deye.2m.py").exists())
    (plugin_dir / "deye.2m.py").rename(plugin)
    (cfg_dir / "config.json").write_text(json.dumps(cfg))
    state_file.unlink()

    print("fallback to station when device data fails")
    STATE["fail_device"], STATE["fail_code"] = True, "2101999"
    _, out, _ = run()
    expect("station summary used", out.splitlines()[0].count("600W") == 1)
    print("rate limited (429): no fallback call")
    STATE["fail_code"], STATE["station_calls"] = "429", 0
    _, out, _ = run()
    expect("429 shows cached data as stale", "pause.circle" in out.splitlines()[0])
    expect("429 does not trigger the station fallback", STATE["station_calls"] == 0)
    STATE["fail_device"] = False

    print("API down -> cached data")
    server.shutdown()
    server.server_close()
    _, out, _ = run()
    expect("stale indicator", "pause.circle" in out.splitlines()[0])

    print("installer default")
    expect("installer default interval is 5m",
           'DEFAULT_INTERVAL="5m"' in (ROOT / "install.sh").read_text(encoding="utf-8"))

    print("--set-interval")
    code, _, _ = run("--set-interval", "5m")
    expect("plugin renamed", code == 0 and (plugin_dir / "deye.5m.py").exists() and not plugin.exists())

    shutil.rmtree(home, ignore_errors=True)
    print(f"\n{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
