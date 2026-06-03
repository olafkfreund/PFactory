"""CLI for the PFactory→AIFactory hand-back (#186, epic #182).

Build a correction request from a finished task workspace and either preview it
(default, dry-run) or send it to AIFactory:

    python -m agents.handback <spec_dir>            # prepare + preview (no send)
    python -m agents.handback <spec_dir> --send     # actually POST to AIFactory

The local fallback for the ``/handback-to-aifactory`` skill when the AIFactory
MCP server isn't registered in the session. Dry-run by default, honoring the
"no automatic pushes" policy — ``--send`` is the explicit opt-in.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .request import build_correction_request
from .send import send_correction
from .trigger import _load_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agents.handback")
    parser.add_argument(
        "spec_dir", help="PFactory workspace spec dir for a finished task"
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually POST the correction to AIFactory (default: prepare only)",
    )
    args = parser.parse_args(argv)

    spec = Path(args.spec_dir).expanduser()
    findings = spec / "findings"
    verdicts = _load_json(findings / "verdicts.json")
    source = _load_json(spec / "context" / "source.json")
    if not verdicts or not source:
        print(
            f"error: missing verdicts.json or source.json under {spec}", file=sys.stderr
        )
        return 2
    triage = _load_json(findings / "triage_report.json")

    request = build_correction_request(verdicts, triage, source)
    if request.nothing_to_hand_back:
        print("Nothing to hand back — no failing tests in this run.")
        return 0

    result = send_correction(request, spec, dry_run=not args.send, confirm=args.send)
    print(json.dumps(result.to_dict(), indent=2))
    if args.send and not result.ok:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
