#!/usr/bin/env bash
# Run all checks locally (same as CI): syntax, shellcheck, locales, smoke tests.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> bash syntax";  for f in install.sh uninstall.sh scripts/*.sh; do bash -n "$f"; done
if command -v shellcheck >/dev/null 2>&1; then
  echo "==> shellcheck"; shellcheck -S warning install.sh uninstall.sh scripts/*.sh
else
  echo "==> shellcheck not installed, skipped (brew install shellcheck)"
fi
echo "==> python syntax"; python3 -m py_compile plugin/deye.py scripts/check_locales.py tests/smoke_test.py
echo "==> locales";       python3 scripts/check_locales.py
echo "==> smoke tests";   python3 tests/smoke_test.py
echo "All checks passed."
