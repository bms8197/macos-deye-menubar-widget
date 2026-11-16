#!/usr/bin/env bash
# Deye Menubar uninstaller for macOS.
set -euo pipefail

CFG_DIR="$HOME/.config/deye"
KC_SERVICE="deye-menubar"
SWIFTBAR_DOMAIN="com.ameba.SwiftBar"

green=$'\033[32m'; reset=$'\033[0m'
ok() { printf '%s✓%s %s\n' "$green" "$reset" "$*"; }
yes_no() {
  local ans
  read -r -p "$1 [$( [[ $2 == y ]] && echo Y/n || echo y/N )]: " ans || true
  ans=${ans:-$2}
  [[ $ans =~ ^[Yy] ]]
}

PLUGIN_DIR=$(defaults read "$SWIFTBAR_DOMAIN" PluginDirectory 2>/dev/null || echo "$HOME/SwiftBar")
PLUGIN_DIR=${PLUGIN_DIR/#\~/$HOME}

for f in "$PLUGIN_DIR"/deye.*.py; do
  [[ -e $f ]] || continue
  rm -f "$f"
  ok "Removed plugin $f"
done
open -g "swiftbar://refreshallplugins" 2>/dev/null || true

for account in appSecret passwordHash; do
  security delete-generic-password -s "$KC_SERVICE" -a "$account" >/dev/null 2>&1 \
    && ok "Removed Keychain item $KC_SERVICE/$account"
done

if [[ -d $CFG_DIR ]] && yes_no "Delete settings, translations and backups in $CFG_DIR?" y; then
  rm -rf "$CFG_DIR"
  ok "Removed $CFG_DIR"
fi

if yes_no "Also uninstall SwiftBar (only if you don't use it for other plugins)?" n; then
  osascript -e 'tell application "SwiftBar" to quit' >/dev/null 2>&1 || true
  osascript -e 'tell application "System Events" to if exists login item "SwiftBar" then delete login item "SwiftBar"' >/dev/null 2>&1 || true
  if command -v brew >/dev/null 2>&1 && brew list --cask swiftbar >/dev/null 2>&1; then
    brew uninstall --cask swiftbar
  else
    rm -rf /Applications/SwiftBar.app "$HOME/Applications/SwiftBar.app"
  fi
  defaults delete "$SWIFTBAR_DOMAIN" >/dev/null 2>&1 || true
  ok "SwiftBar removed"
fi

ok "Deye Menubar uninstalled."
