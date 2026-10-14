#!/usr/bin/env python3
"""Fail CI when the vendored schema, control catalog, or extension registry drifts.

The canonical RFC-0002 schema lives in the Factory hub
(``apis/task-contract.schema.json``); PFactory vendors a copy at
``apps/backend/plan/emit/contracts/task-contract.schema.json`` for offline
validation. RFC-0010 added fields to both — this guard stops them silently
diverging again.

The check is **directional**: canonical ⊆ vendored. Every property, ``$def`` and
enum value the canonical defines must exist in the vendored copy (vendored may
carry extra, intentional additions), so any contract valid under the canonical
schema stays valid under the vendored one. Descriptions are ignored.
The control catalog and extension registry must each equal their hub copies.

Usage:
    python scripts/check_schema_drift.py                 # fetch canonical from main
    python scripts/check_schema_drift.py --canonical PATH_OR_URL
    python scripts/check_schema_drift.py --catalog PATH_OR_URL
    python scripts/check_schema_drift.py --extension-registry PATH_OR_URL
    python scripts/check_schema_drift.py --ref <git-ref>

Only a *transient* network failure fetching the canonical schema is a SOFT SKIP
(loud warning, exit 0) so a GitHub outage can't red the build. A deterministic
failure — a TLS/certificate error, a 404, malformed JSON — fails hard (exit 1):
it recurs on every run, so soft-skipping it would leave this gate permanently
green while never once running, and a silent skip is indistinguishable from a
pass (Factory#433). Real drift also fails hard (exit 1).
"""

# CLI drift-check script: stdout `print` is its reporting surface for CI logs.
# ruff: noqa: T201

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

_VENDORED = (
    Path(__file__).resolve().parent.parent
    / "apps/backend/plan/emit/contracts/task-contract.schema.json"
)
_RAW = "https://raw.githubusercontent.com/olafkfreund/Factory/{ref}/apis/task-contract.schema.json"
_CATALOG_VENDORED = (
    Path(__file__).resolve().parent.parent / "apps/backend/plan/emit/contracts/control-catalog.json"
)
_CATALOG_RAW = (
    "https://raw.githubusercontent.com/olafkfreund/Factory/{ref}/apis/control-catalog.json"
)
_REGISTRY_VENDORED = (
    Path(__file__).resolve().parent.parent
    / "apps/backend/plan/emit/contracts/extension-registry.json"
)
_REGISTRY_RAW = (
    "https://raw.githubusercontent.com/olafkfreund/Factory/{ref}/apis/extension-registry.json"
)


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
                problems.append(f"{path}/{key}: present in canonical, missing in vendored")
                continue
            problems += check_drift(cval, vendored[key], f"{path}/{key}")
    elif isinstance(canonical, list):
        # Treated as a set (enums, required[]): vendored must include every value.
        if not isinstance(vendored, list):
            return [f"{path}: canonical is a list, vendored is not"]
        missing = [v for v in canonical if v not in vendored]
        if missing:
            problems.append(f"{path}: vendored is missing values {missing}")
    elif canonical != vendored:
        problems.append(f"{path}: canonical={canonical!r} != vendored={vendored!r}")
    return problems


def _catalog_diff_ids(canonical: dict[str, Any], vendored: dict[str, Any]) -> list[str]:
    """Return control ids that are added, removed, modified, or reordered."""
    c_controls = canonical.get("controls")
    v_controls = vendored.get("controls")
    c_list = [
        c
        for c in (c_controls if isinstance(c_controls, list) else [])
        if isinstance(c, dict) and isinstance(c.get("id"), str)
    ]
    v_list = [
        c
        for c in (v_controls if isinstance(v_controls, list) else [])
        if isinstance(c, dict) and isinstance(c.get("id"), str)
    ]
    c_by_id = {c["id"]: c for c in c_list}
    v_by_id = {c["id"]: c for c in v_list}
    diff = [
        cid for cid in sorted(set(c_by_id) | set(v_by_id)) if c_by_id.get(cid) != v_by_id.get(cid)
    ]
    if not diff and [c["id"] for c in c_list] != [c["id"] for c in v_list]:
        c_order = [c["id"] for c in c_list]
        v_order = [c["id"] for c in v_list]
        diff = [
            cid for idx, cid in enumerate(c_order) if idx >= len(v_order) or v_order[idx] != cid
        ]
    return diff


def is_transient(exc: BaseException) -> bool:
    """True only for a failure that could plausibly succeed on the next run.

    ``urlopen`` wraps the real cause in ``urllib.error.URLError``, so a
    certificate failure arrives as ``URLError(reason=SSLCertVerificationError)``
    — catching ``URLError`` and calling it transient is exactly the bug in #440.
    Unwrap ``.reason`` and judge the cause, defaulting to "not transient".
    """
    if isinstance(exc, ssl.SSLError):  # includes ssl.SSLCertVerificationError
        return False
    reason = getattr(exc, "reason", None)
    if isinstance(reason, BaseException):
        return is_transient(reason)
    # A read timeout or a DNS failure may genuinely differ next run. Everything
    # else (TLS, HTTP 4xx/5xx, bad JSON) is deterministic and must fail.
    return isinstance(exc, TimeoutError | socket.gaierror)


def _warn_skipped(exc: BaseException) -> None:
    """Announce a soft skip loudly: a skipping gate must not look like a pass."""
    msg = (
        f"SCHEMA DRIFT CHECK SKIPPED — NOT VERIFIED ({exc}). The vendored Task "
        "Contract schema was NOT compared against the canonical hub copy. If you "
        "see this on every run, the gate is dead — fix the fetch, don't ignore it."
    )
    banner = "!" * 78
    print(f"{banner}\n{msg}\n{banner}")
    # GitHub Actions annotation: surfaces on the checks page, so a permanently
    # skipping gate is visible without reading the log.
    print(f"::warning title=Schema-drift guard SKIPPED (not verified)::{msg}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as fh:
            fh.write(f"> [!WARNING]\n> {msg}\n")


def fetch_canonical(source: str) -> dict[str, Any] | None:
    """Load the canonical JSON from a local path or an http(s) URL.

    Returns None only on a *transient* network error (soft skip). A deterministic
    failure is re-raised so the caller can fail the gate.
    """
    if source.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(source, timeout=15) as resp:  # noqa: S310 - pinned host
                return cast("dict[str, Any]", json.loads(resp.read().decode("utf-8")))
        except (OSError, ValueError) as exc:  # URLError/SSLError/TimeoutError ⊂ OSError
            if not is_transient(exc):
                raise
            _warn_skipped(exc)
            return None
    return cast("dict[str, Any]", json.loads(Path(source).read_text(encoding="utf-8")))


def _catalog_detail(canonical: dict[str, Any], vendored: dict[str, Any]) -> str:
    return (
        f"  canonical version: {canonical.get('version')!r}, "
        f"vendored version: {vendored.get('version')!r}\n"
        f"  differing ids: {_catalog_diff_ids(canonical, vendored)}\n"
        "\nSync apps/backend/plan/emit/contracts/control-catalog.json with "
        "the Factory hub apis/control-catalog.json."
    )


def _registry_detail(canonical: dict[str, Any], vendored: dict[str, Any]) -> str:
    keys = sorted(
        key
        for key in canonical.keys() | vendored.keys()
        if key not in canonical or key not in vendored or canonical[key] != vendored[key]
    )
    versions = (
        f"  canonical version: {canonical.get('version')!r}, "
        f"vendored version: {vendored.get('version')!r}\n"
        if "version" in canonical or "version" in vendored
        else ""
    )
    return (
        f"{versions}  differing top-level keys: {keys}\n"
        "\nSync apps/backend/plan/emit/contracts/extension-registry.json with "
        "the Factory hub apis/extension-registry.json."
    )


def _check_equal(
    name: str,
    vendored: Path,
    source: str,
    detail: Callable[[dict[str, Any], dict[str, Any]], str],
) -> int:
    try:
        canonical = fetch_canonical(source)
    except (OSError, ValueError) as exc:
        print(f"FAIL: could not fetch the canonical {name} from {source}: {exc}")
        print(
            "This failure is deterministic — it will recur every run — so the "
            "drift gate cannot run and therefore fails. A gate that cannot run "
            "must never report success (Factory#433)."
        )
        return 1
    if canonical is None:
        return 0  # soft skip: transient network failure only, warned loudly above

    vendored_data = json.loads(vendored.read_text(encoding="utf-8"))
    if canonical != vendored_data:
        print(
            f"{name.capitalize()} drift — the vendored copy differs from the canonical hub copy:\n"
            f"{detail(canonical, vendored_data)}"
        )
        return 1
    print(f"OK: vendored {name} is in sync with the canonical hub copy.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--canonical", help="path or URL to the canonical schema")
    ap.add_argument("--catalog", help="path or URL to the canonical control catalog")
    ap.add_argument("--extension-registry", help="path or URL to the canonical extension registry")
    ap.add_argument("--ref", default="main", help="git ref of the Factory hub (default: main)")
    args = ap.parse_args(argv)

    source = args.canonical or _RAW.format(ref=args.ref)
    try:
        canonical = fetch_canonical(source)
    except (OSError, ValueError) as exc:
        print(f"FAIL: could not fetch the canonical schema from {source}: {exc}")
        print(
            "This failure is deterministic — it will recur every run — so the "
            "drift gate cannot run and therefore fails. A gate that cannot run "
            "must never report success (Factory#433)."
        )
        return 1
    if canonical is None:
        return 0  # soft skip: transient network failure only, warned loudly above

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
    catalog_rc = _check_equal(
        "control catalog",
        _CATALOG_VENDORED,
        args.catalog or _CATALOG_RAW.format(ref=args.ref),
        _catalog_detail,
    )
    registry_rc = _check_equal(
        "extension registry",
        _REGISTRY_VENDORED,
        args.extension_registry or _REGISTRY_RAW.format(ref=args.ref),
        _registry_detail,
    )
    return max(catalog_rc, registry_rc)


if __name__ == "__main__":
    sys.exit(main())
