#!/usr/bin/env python3
"""Validate translations against locales/en.json (keys and {placeholders})."""
import json
import re
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parent.parent / "locales"
PH = re.compile(r"\{(\w+)\}")


def main():
    ref = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
    errors, warnings = [], []
    for path in sorted(LOCALES.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            errors.append(f"{path.name}: invalid JSON: {e}")
            continue
        if not data.get("_language_name"):
            errors.append(f"{path.name}: missing _language_name")
        for key, text in data.items():
            if key.startswith("_"):
                continue
            if key not in ref:
                errors.append(f"{path.name}: unknown key '{key}' (not in en.json)")
            elif set(PH.findall(text)) != set(PH.findall(ref[key])):
                errors.append(f"{path.name}: '{key}' placeholders {sorted(set(PH.findall(text)))} "
                              f"!= en {sorted(set(PH.findall(ref[key])))}")
        missing = [k for k in ref if not k.startswith("_") and k not in data]
        if missing and path.name != "en.json":
            warnings.append(f"{path.name}: {len(missing)} untranslated key(s), English used: {', '.join(missing)}")
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    print(f"{len(list(LOCALES.glob('*.json')))} locale file(s) checked, {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
