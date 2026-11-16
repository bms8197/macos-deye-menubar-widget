#!/usr/bin/env bash
# Deye Menubar installer for macOS.
# Installs SwiftBar (if needed), stores credentials in the Keychain,
# writes the config, installs the plugin and verifies the Deye Cloud login.
# Safe to re-run: existing settings are offered as defaults.
# Compatible with the macOS system bash (3.2).
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG_DIR="$HOME/.config/deye"
CFG_FILE="$CFG_DIR/config.json"
KC_SERVICE="deye-menubar"
SWIFTBAR_DOMAIN="com.ameba.SwiftBar"
SWIFTBAR_APP="/Applications/SwiftBar.app"
DEFAULT_PLUGIN_DIR="$HOME/SwiftBar"
DEFAULT_INTERVAL="1m"

bold=$'\033[1m'; green=$'\033[32m'; yellow=$'\033[33m'; red=$'\033[31m'; reset=$'\033[0m'
info() { printf '\n%s==> %s%s\n' "$bold" "$*" "$reset"; }
ok()   { printf '%s✓%s %s\n' "$green" "$reset" "$*"; }
warn() { printf '%s!%s %s\n' "$yellow" "$reset" "$*"; }
die()  { printf '%s✗ %s%s\n' "$red" "$*" "$reset" >&2; exit 1; }

# ask VAR "Prompt" [default]
ask() {
  local __var=$1 __prompt=$2 __def=${3:-} __ans=""
  if [[ -n $__def ]]; then read -r -p "$__prompt [$__def]: " __ans || true
  else read -r -p "$__prompt: " __ans || true; fi
  printf -v "$__var" '%s' "${__ans:-$__def}"
}

# ask_secret VAR "Prompt" has_existing(0|1)
ask_secret() {
  local __var=$1 __prompt=$2 __has=${3:-0} __ans=""
  [[ $__has == 1 ]] && __prompt="$__prompt (Enter = keep current)"
  read -r -s -p "$__prompt: " __ans || true
  echo
  printf -v "$__var" '%s' "$__ans"
}

yes_no() { # yes_no "Question" default(y|n)
  local ans
  read -r -p "$1 [$( [[ $2 == y ]] && echo Y/n || echo y/N )]: " ans || true
  ans=${ans:-$2}
  [[ $ans =~ ^[Yy] ]]
}

kc_get() { security find-generic-password -s "$KC_SERVICE" -a "$1" -w 2>/dev/null || true; }
kc_set() { security add-generic-password -U -s "$KC_SERVICE" -a "$1" -w "$2" >/dev/null; }

cfg_get() { # cfg_get key  -> prints value from existing config (empty if none)
  [[ -f $CFG_FILE ]] || return 0
  "$PY" - "$CFG_FILE" "$1" <<'PY'
import json, sys
try:
    v = json.load(open(sys.argv[1])).get(sys.argv[2], "")
except Exception:
    v = ""
print("" if v is None else v)
PY
}

# ------------------------------------------------------------------ checks
[[ $(uname -s) == Darwin ]] || die "This installer is for macOS only."
[[ -f $SRC_DIR/plugin/deye.py ]] || die "plugin/deye.py not found next to install.sh."

SRC_VERSION=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$SRC_DIR/plugin/deye.py")
printf '%sDeye Menubar installer v%s%s\n' "$bold" "$SRC_VERSION" "$reset"
echo "Source: $SRC_DIR"
echo "Shows Deye inverter data (PV, battery, consumption, grid) in the macOS menu bar."

info "Checking Python 3"
PY=""
if xcode-select -p >/dev/null 2>&1 && [[ -x /usr/bin/python3 ]]; then
  PY=/usr/bin/python3
else
  for p in /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    [[ -x $p ]] && { PY=$p; break; }
  done
fi
if [[ -z $PY ]]; then
  warn "Python 3 not found. Starting the Command Line Tools installer..."
  xcode-select --install 2>/dev/null || true
  die "Finish installing the Command Line Tools, then run this installer again."
fi
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  || die "Python 3.8+ required ($PY is too old)."
ok "Using $PY ($("$PY" -V 2>&1))"

# ------------------------------------------------------------------ SwiftBar
info "Checking SwiftBar"
if [[ -d $SWIFTBAR_APP ]]; then
  ok "SwiftBar already installed"
elif [[ -d $HOME/Applications/SwiftBar.app ]]; then
  SWIFTBAR_APP="$HOME/Applications/SwiftBar.app"
  ok "SwiftBar already installed in ~/Applications"
elif command -v brew >/dev/null 2>&1; then
  echo "Installing SwiftBar with Homebrew..."
  brew install --cask swiftbar
  ok "SwiftBar installed"
else
  echo "Downloading the latest SwiftBar release from GitHub..."
  url=$(curl -fsSL https://api.github.com/repos/swiftbar/SwiftBar/releases/latest | "$PY" -c '
import json, sys
assets = json.load(sys.stdin).get("assets", [])
zips = [a["browser_download_url"] for a in assets if a["name"].endswith(".zip")]
print(zips[0] if zips else "")')
  [[ -n $url ]] || die "Could not find a SwiftBar download. Install it manually from https://swiftbar.app"
  tmp=$(mktemp -d)
  curl -fL --progress-bar -o "$tmp/SwiftBar.zip" "$url"
  ditto -x -k "$tmp/SwiftBar.zip" "$tmp/x"
  [[ -d $tmp/x/SwiftBar.app ]] || die "Unexpected SwiftBar archive layout."
  if ! ditto "$tmp/x/SwiftBar.app" "$SWIFTBAR_APP" 2>/dev/null; then
    mkdir -p "$HOME/Applications"
    SWIFTBAR_APP="$HOME/Applications/SwiftBar.app"
    ditto "$tmp/x/SwiftBar.app" "$SWIFTBAR_APP"
  fi
  xattr -dr com.apple.quarantine "$SWIFTBAR_APP" 2>/dev/null || true
  rm -rf "$tmp"
  ok "SwiftBar installed in $SWIFTBAR_APP"
fi

# SwiftBar must not be running while we change its preferences
osascript -e 'tell application "SwiftBar" to quit' >/dev/null 2>&1 || true
sleep 1

PLUGIN_DIR=$(defaults read "$SWIFTBAR_DOMAIN" PluginDirectory 2>/dev/null || true)
PLUGIN_DIR=${PLUGIN_DIR:-$DEFAULT_PLUGIN_DIR}
PLUGIN_DIR=${PLUGIN_DIR/#\~/$HOME}
mkdir -p "$PLUGIN_DIR"
defaults write "$SWIFTBAR_DOMAIN" PluginDirectory -string "$PLUGIN_DIR"
ok "Plugin folder: $PLUGIN_DIR"

# ------------------------------------------------------------------ credentials
mkdir -p "$CFG_DIR/locales" "$CFG_DIR/backup"
chmod 700 "$CFG_DIR"

info "Deye Cloud API credentials"
echo "Get appId / appSecret at https://developer.deyecloud.com (create an application)."
echo

cur_base=$(cfg_get base)
case "$cur_base" in
  *us1-developer*) def_region=2 ;;
  ""|*eu1-developer*) def_region=1 ;;
  *) def_region=3 ;;
esac
echo "Region:  1) Europe (eu1)   2) Americas / other (us1)   3) custom URL"
region=""
ask region "Choose" "$def_region"
case "$region" in
  1) BASE="https://eu1-developer.deyecloud.com/v1.0" ;;
  2) BASE="https://us1-developer.deyecloud.com/v1.0" ;;
  3) ask BASE "API base URL (ending in /v1.0)" "$cur_base" ;;
  *) die "Invalid region." ;;
esac

ask APP_ID "appId" "$(cfg_get appId)"
[[ -n $APP_ID ]] || die "appId is required."

existing_secret=$(kc_get appSecret)
[[ -z $existing_secret ]] && existing_secret=$(cfg_get appSecret)   # legacy plaintext config
ask_secret APP_SECRET "appSecret" "$( [[ -n $existing_secret ]] && echo 1 || echo 0 )"
APP_SECRET=${APP_SECRET:-$existing_secret}
[[ -n $APP_SECRET ]] || die "appSecret is required."

ask EMAIL "Deye Cloud e-mail" "$(cfg_get email)"
[[ -n $EMAIL ]] || die "E-mail is required."

existing_hash=$(kc_get passwordHash)
legacy_pw=$(cfg_get password)
if [[ -z $existing_hash && -n $legacy_pw ]]; then
  existing_hash=$(printf '%s' "$legacy_pw" | shasum -a 256 | awk '{print $1}')
fi
ask_secret PASSWORD "Deye Cloud password" "$( [[ -n $existing_hash ]] && echo 1 || echo 0 )"
if [[ -n $PASSWORD ]]; then
  PW_HASH=$(printf '%s' "$PASSWORD" | shasum -a 256 | awk '{print $1}')
else
  PW_HASH=$existing_hash
fi
unset PASSWORD legacy_pw
[[ -n $PW_HASH ]] || die "Password is required."

# Only the SHA-256 hash of the password is stored (that's what the Deye API expects).
kc_set appSecret "$APP_SECRET"
kc_set passwordHash "$PW_HASH"
unset APP_SECRET PW_HASH
ok "Secrets saved in the macOS Keychain (service: $KC_SERVICE)"

# ------------------------------------------------------------------ preferences
info "Preferences"
cp "$SRC_DIR"/locales/*.json "$CFG_DIR/locales/"
langs=""
for f in "$CFG_DIR"/locales/*.json; do langs="$langs $(basename "$f" .json)"; done
echo "Available languages: auto${langs}  (auto = follow macOS language, fallback English)"
cur_lang=$(cfg_get language)
ask LANGUAGE "Language" "${cur_lang:-en}"
if [[ $LANGUAGE != auto && ! -f $CFG_DIR/locales/$LANGUAGE.json ]]; then
  warn "No translation for '$LANGUAGE', using English."
  LANGUAGE=en
fi

cur_interval=""
for f in "$PLUGIN_DIR"/deye.*.py; do
  [[ -e $f ]] || continue
  cur_interval=$(basename "$f" | sed -E 's/^deye\.([^.]+)\.py$/\1/')
done
echo "Refresh interval examples: 30s, 1m, 2m, 5m"
while :; do
  ask INTERVAL "Refresh interval" "${cur_interval:-$DEFAULT_INTERVAL}"
  [[ $INTERVAL =~ ^[0-9]+[smhd]$ ]] && break
  warn "Use a number followed by s, m, h or d (e.g. 1m)."
done

DEYE_BASE="$BASE" DEYE_APPID="$APP_ID" DEYE_EMAIL="$EMAIL" DEYE_LANG="$LANGUAGE" \
"$PY" - "$CFG_FILE" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    cfg = json.load(open(path))
except Exception:
    cfg = {}
for legacy in ("appSecret", "password"):          # secrets now live in the Keychain
    cfg.pop(legacy, None)
if cfg.get("appId") != os.environ["DEYE_APPID"] or cfg.get("email") != os.environ["DEYE_EMAIL"]:
    cfg.pop("stationId", None)
cfg.update(base=os.environ["DEYE_BASE"], appId=os.environ["DEYE_APPID"],
           email=os.environ["DEYE_EMAIL"], language=os.environ["DEYE_LANG"])
cfg.setdefault("thresholds", {"lowBatteryPercent": 20, "idleWatts": 20})
cfg.setdefault("invertBatterySign", False)
cfg.setdefault("invertGridSign", False)
cfg.setdefault("titleStyle", "sf")
cfg.setdefault("titleColor", "")
cfg.setdefault("lowBatteryColor", "#FFD60A")
cfg.setdefault("dataSource", "device")
cfg.setdefault("showDetails", True)
cfg.setdefault("batteryReservePercent", 10)
cfg.setdefault("monthlyQuota", 100000)
cfg.setdefault("detailsRefreshSeconds", 120)
fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
os.replace(path + ".tmp", path)
PY
rm -f "$CFG_DIR/token.json"
ok "Config written to $CFG_FILE"

# ------------------------------------------------------------------ plugin
info "Installing plugin"
for old in "$PLUGIN_DIR"/deye.*.py; do
  [[ -e $old ]] || continue
  mv "$old" "$CFG_DIR/backup/$(basename "$old").$(date +%Y%m%d-%H%M%S)"
  ok "Previous plugin moved to $CFG_DIR/backup/"
done
PLUGIN="$PLUGIN_DIR/deye.$INTERVAL.py"
{ echo "#!$PY"; tail -n +2 "$SRC_DIR/plugin/deye.py"; } > "$PLUGIN"
chmod 755 "$PLUGIN"
ok "Plugin installed: $PLUGIN (v$("$PLUGIN" --version))"

# ------------------------------------------------------------------ verify
info "Testing Deye Cloud login"
result=$("$PLUGIN" --check || true)
stations=$(printf '%s' "$result" | "$PY" -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("ERR\tunexpected output from plugin"); sys.exit()
if not d.get("ok"):
    print("ERR\t" + str(d.get("error", "unknown error"))); sys.exit()
for s in d["stations"]:
    print(str(s["id"]) + "\t" + str(s["name"]))')

if [[ $stations == ERR* ]]; then
  die "Login failed: $(printf '%s' "$stations" | cut -f2-)
  Check appId/appSecret/e-mail/password and region, then run ./install.sh again."
fi

count=$(printf '%s\n' "$stations" | grep -c .)
cur_station=$(cfg_get stationId)
if [[ $count -eq 1 ]]; then
  STATION_ID=$(printf '%s' "$stations" | cut -f1)
  ok "Login OK. Station: $(printf '%s' "$stations" | cut -f2) (id $STATION_ID)"
else
  ok "Login OK. Stations found:"
  printf '%s\n' "$stations" | awk -F'\t' '{printf "  %d) %s (id %s)\n", NR, $2, $1}'
  def_idx=1
  if [[ -n $cur_station ]]; then
    idx=$(printf '%s\n' "$stations" | awk -F'\t' -v id="$cur_station" '$1==id {print NR}')
    def_idx=${idx:-1}
  fi
  choice=""
  while :; do
    ask choice "Station to show" "$def_idx"
    [[ $choice =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= count )) && break
    warn "Choose a number between 1 and $count."
  done
  STATION_ID=$(printf '%s\n' "$stations" | sed -n "${choice}p" | cut -f1)
fi

DEYE_SID="$STATION_ID" "$PY" - "$CFG_FILE" <<'PY'
import json, os, sys
path = sys.argv[1]
cfg = json.load(open(path))
sid = os.environ["DEYE_SID"]
cfg["stationId"] = int(sid) if sid.isdigit() else sid
fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
os.replace(path + ".tmp", path)
PY

# ------------------------------------------------------------------ launch
info "Starting SwiftBar"
if yes_no "Start SwiftBar automatically at login?" y; then
  if osascript -e "tell application \"System Events\" to if not (exists login item \"SwiftBar\") then make login item at end with properties {path:\"$SWIFTBAR_APP\", hidden:true}" >/dev/null 2>&1; then
    ok "Login item added"
  else
    warn "Could not add the login item (permission denied). Enable it in SwiftBar → Preferences → Launch at Login."
  fi
fi

open -a "$SWIFTBAR_APP"
sleep 3
open -g "swiftbar://refreshallplugins" 2>/dev/null || true

echo
ok "Done. Look for ☀️ in the menu bar."
echo "   Change language from the plugin menu → Language."
echo "   Re-run ./install.sh to change credentials, station or interval."
echo "   Run ./uninstall.sh to remove everything."
