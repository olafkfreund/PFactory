#!/usr/bin/env python3
"""Fail CI when the vendored Task Contract schema drifts from the canonical one.

The canonical RFC-0002 schema lives in the Factory hub
(``apis/task-contract.schema.json``); PFactory vendors a copy at
``apps/backend/plan/emit/contracts/task-contract.schema.json`` for offline
validation. RFC-0010 added fields to both — this guard stops them silently
diverging again.

The check is **directional**: canonical ⊆ vendored. Every property, ``$def`` and
enum value the canonical defines must exist in the vendored copy (vendored may
carry extra, intentional additions), so any contract valid under the canonical
schema stays valid under the vendored one. Descriptions are ignored.

Usage:
    python scripts/check_schema_drift.py                 # fetch canonical from main
    python scripts/check_schema_drift.py --canonical PATH_OR_URL
    python scripts/check_schema_drift.py --ref <git-ref>

Network failures fetching the canonical schema are a SOFT SKIP (warn, exit 0) so
a GitHub outage can't red the build; real drift fails hard (exit 1).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_VENDORED = (
    Path(__file__).resolve().parent.parent
    / "apps/backend/plan/emit/contracts/task-contract.schema.json"
)
_RAW = "https://raw.githubusercontent.com/olafkfreund/Factory/{ref}/apis/task-contract.schema.json"


def check_drift(canonical: Any, vendored: Any, path: str = "") -> list[str]:
    """Return drift problems: keys/enum-values in canonical missing from vendored."""
    problems: list[str] = []
    if isinstance(canonical, dict):
        if not isinstance(vendored, dict):
            return [f"{path or '<root>'}: canonical is an object, vendored is not"]
        for key, cval in canonical.items():
            if key == "description":  # prose drift is allowed
                continue
            if key not in vendored:
                problems.append(
                    f"{path}/{key}: present in canonical, missing in vendored"
                )
                continue
            problems += check_drift(cval, vendored[key], f"{path}/{key}")
    elif isinstance(canonical, list):
        # Treated as a set (enums, required[]): vendored must include every value.
        if not isinstance(vendored, list):
            return [f"{path}: canonical is a list, vendored is not"]
        missing = [v for v in canonical if v not in vendored]
        if missing:
            problems.append(f"{path}: vendored is missing values {missing}")
    else:
        if canonical != vendored:
            problems.append(f"{path}: canonical={canonical!r} != vendored={vendored!r}")
    return problems


def fetch_canonical(source: str) -> dict | None:
    """Load the canonical schema from a local path or an http(s) URL.

    Returns None on a network error (soft skip); raises on a local read error.
    """
    if source.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(source, timeout=15) as resp:  # noqa: S310 - pinned host
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(
                f"WARN: could not fetch canonical schema ({exc}); skipping drift check"
            )
            return None
    return json.loads(Path(source).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--canonical", help="path or URL to the canonical schema")
    ap.add_argument(
        "--ref", default="main", help="git ref of the Factory hub (default: main)"
    )
    args = ap.parse_args(argv)

    source = args.canonical or _RAW.format(ref=args.ref)
    canonical = fetch_canonical(source)
    if canonical is None:
        return 0  # soft skip on network failure

    vendored = json.loads(_VENDORED.read_text(encoding="utf-8"))
    problems = check_drift(canonical, vendored)
    if problems:
        print("Schema drift — the vendored copy is missing canonical definitions:")
        for p in problems:
            print(f"  - {p}")
        print(
            "\nSync apps/backend/plan/emit/contracts/task-contract.schema.json with "
            "the Factory hub apis/task-contract.schema.json."
        )
        return 1
    print("OK: vendored Task Contract schema is in sync with the canonical hub copy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
