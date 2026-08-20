#!/usr/bin/env python3
"""Diffs .env against .env.example's keys and warns on anything missing.
Never prints values — only key names — to avoid leaking secrets into logs."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def main() -> int:
    example_keys = _read_keys(ROOT / ".env.example")
    env_path = ROOT / ".env"

    if not env_path.exists():
        print(".env not found — copy .env.example to .env and fill in values as needed.")
        return 1

    actual_keys = _read_keys(env_path)
    missing = example_keys - actual_keys
    if missing:
        print("Missing keys in .env (present in .env.example):")
        for key in sorted(missing):
            print(f"  - {key}")
        return 1

    print(f".env has all {len(example_keys)} keys from .env.example.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
