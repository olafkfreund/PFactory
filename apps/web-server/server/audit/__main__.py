"""``python -m server.audit verify-chain <export.ndjson>``

External verifier for an exported audit log. Re-computes the
hash chain end-to-end and exits 0 if every row's prev_hash matches
expectation, non-zero otherwise.

Designed to run in an AIR-GAPPED environment — the verifier has
zero external dependencies beyond Python stdlib + ``server.services.
audit_chain``. Operators copy the export tarball + the PFactory
source to a clean machine to verify the chain hasn't been tampered
with at-rest.
"""

from __future__ import annotations

import argparse
import json
import sys

from ..services.audit_chain import verify_chain


def _cmd_verify_chain(args: argparse.Namespace) -> int:
    """Read NDJSON from a file, run verify_chain, exit accordingly."""
    rows: list[dict] = []
    try:
        with open(args.path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    print(
                        f"FAIL: line {lineno} is not valid JSON: {exc}",
                        file=sys.stderr,
                    )
                    return 2
    except OSError as exc:
        print(f"FAIL: cannot read {args.path}: {exc}", file=sys.stderr)
        return 2

    ok, bad_idx, reason = verify_chain(rows)
    if not ok:
        print(
            f"FAIL: chain verification failed at row {bad_idx}: {reason}",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {len(rows)} rows verified")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m server.audit",
        description="Audit log utilities for PFactory (Epic #26 P5).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    verify = sub.add_parser(
        "verify-chain",
        help="Re-verify the hash chain in an exported NDJSON audit log.",
    )
    verify.add_argument("path", help="Path to the NDJSON export file.")
    verify.set_defaults(func=_cmd_verify_chain)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
