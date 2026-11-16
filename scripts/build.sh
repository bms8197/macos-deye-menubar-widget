#!/usr/bin/env bash
# Build dist/deye-menubar-<version>.zip from the committed tree (git archive).
# Usage: scripts/build.sh [ref]   (default: HEAD)
set -euo pipefail
cd "$(dirname "$0")/.."

ref=${1:-HEAD}
version=$(git show "$ref:plugin/deye.py" | sed -n 's/^VERSION = "\(.*\)"$/\1/p')
[[ -n $version ]] || { echo "Could not read VERSION from plugin/deye.py" >&2; exit 1; }

mkdir -p dist
out="dist/deye-menubar-$version.zip"
rm -f "$out"
git archive --format=zip --prefix=deye-menubar/ -o "$out" "$ref"
echo "$out"
