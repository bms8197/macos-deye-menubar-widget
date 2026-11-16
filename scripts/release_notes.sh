#!/usr/bin/env bash
# Print the CHANGELOG.md section for a version: scripts/release_notes.sh 1.0.0
set -euo pipefail
cd "$(dirname "$0")/.."
awk -v v="$1" '
  /^## / { if (found) exit; if (index($0, "## " v " ") == 1 || $0 == "## " v) { found = 1; next } }
  found { print }
' CHANGELOG.md
