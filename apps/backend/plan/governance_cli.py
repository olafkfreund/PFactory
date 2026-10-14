"""CLI: query governed plan signatures from a docs directory (#1961).

Reads ``<root>/registry.json`` and each row's ``signature_file`` without any
server or network access:

    python -m plan.governance_cli --root <docs-dir> --control change-mgmt-sod

Pure ``scan`` + ``select`` + formatter functions back a thin ``main(argv)``.
Every registry row is counted so unsigned rows (e.g. TFactory's fork) and
schema-1 rows (controls not recorded) are visible in the summary line rather
than silently dropped.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from plan.emit.docs.targets.registry import REGISTRY_FILE, parse_registry

MATCH_KEYS: tuple[str, ...] = (
    "correlation_key",
    "title",
    "autonomy_tier",
    "autonomy_verdict",
    "approved_by",
    "approved_at",
    "originator",
    "risk_class",
    "created_at",
)


MIN_GOVERNANCE_SCHEMA = 2


def _is_schema2(sig: dict[str, Any]) -> bool:
    schema = sig.get("signature_schema")
    return (
        isinstance(schema, int) and not isinstance(schema, bool) and schema >= MIN_GOVERNANCE_SCHEMA
    )


def scan(root: Path | str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read ``<root>/registry.json`` and each row's ``signature_file``.

    Malformed or missing files/rows are counted under ``unsigned`` and never
    raised.
    """
    root_path = Path(root)
    reg_path = root_path / REGISTRY_FILE
    try:
        text = reg_path.read_text(encoding="utf-8") if reg_path.is_file() else None
    except OSError:
        text = None

    plans = parse_registry(text)
    if not isinstance(plans, dict):
        plans = {}

    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {
        "scanned": 0,
        "signed": 0,
        "schema1": 0,
        "unsigned": 0,
    }

    for ck in sorted(plans):
        counts["scanned"] += 1
        entry = plans[ck]
        if not isinstance(entry, dict):
            counts["unsigned"] += 1
            continue
        sig_name = entry.get("signature_file")
        # A bare file name only: a registry row must not point the reader outside root.
        if not isinstance(sig_name, str) or not sig_name.strip() or Path(sig_name).name != sig_name:
            counts["unsigned"] += 1
            continue
        sig_path = root_path / sig_name
        try:
            sig = json.loads(sig_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — malformed/missing signature counts as unsigned
            counts["unsigned"] += 1
            continue
        if not isinstance(sig, dict):
            counts["unsigned"] += 1
            continue

        counts["signed"] += 1
        row = dict(sig)
        row.setdefault("correlation_key", entry.get("correlation_key", ck))
        row.setdefault("title", entry.get("title", ""))
        if not _is_schema2(row):
            counts["schema1"] += 1
        rows.append(row)

    return rows, counts


def select(
    rows: Iterable[dict[str, Any]],
    *,
    control: str | None = None,
    tier: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Filter signed rows down to schema-2+ matches and project governance keys."""
    matches: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not _is_schema2(row):
            continue
        if control is not None:
            controls = row.get("controls")
            if not isinstance(controls, list) or control not in controls:
                continue
        if tier is not None and row.get("autonomy_tier") != tier:
            continue
        if since is not None:
            created_at = row.get("created_at")
            if (
                not isinstance(created_at, str)
                or len(created_at) < len(since)
                or created_at[: len(since)] < since
            ):
                continue
        matches.append({k: row.get(k) for k in MATCH_KEYS})
    return matches


def format_result(matches: Sequence[dict[str, Any]], counts: dict[str, int]) -> str:
    """Render one human line per match followed by the mandatory count line."""
    lines: list[str] = []
    for m in matches:
        lines.append(
            f"{m.get('correlation_key')}\t{m.get('title')}\t"
            f"autonomy_tier={m.get('autonomy_tier')}\t"
            f"autonomy_verdict={m.get('autonomy_verdict')}\t"
            f"approved_by={m.get('approved_by')}\t"
            f"approved_at={m.get('approved_at')}\t"
            f"originator={m.get('originator')}\t"
            f"risk_class={m.get('risk_class')}\t"
            f"created_at={m.get('created_at')}"
        )
    lines.append(
        f"scanned {counts.get('scanned', 0)} rows, "
        f"{counts.get('signed', 0)} with a signature, "
        f"{counts.get('schema1', 0)} schema-1, "
        f"{counts.get('unsigned', 0)} without a signature, "
        f"{len(matches)} matched"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="plan.governance_cli",
        description="Query governed plan signatures from a docs directory (#1961).",
    )
    p.add_argument(
        "--root",
        type=Path,
        default=Path(),
        help="directory holding registry.json and *.run.json signatures",
    )
    p.add_argument("--control", default=None, help="filter by engaged control id")
    p.add_argument("--tier", default=None, help="filter by autonomy_tier")
    p.add_argument(
        "--since",
        default=None,
        help="filter by created_at ISO date prefix (YYYY-MM-DD)",
    )
    p.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    args = p.parse_args(argv)

    rows, counts = scan(args.root)
    matches = select(rows, control=args.control, tier=args.tier, since=args.since)
    full_counts = {**counts, "matched": len(matches)}

    if args.as_json:
        print(  # noqa: T201
            json.dumps({"matches": matches, "counts": full_counts}, indent=2, sort_keys=True)
        )
    else:
        print(format_result(matches, full_counts))  # noqa: T201
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
