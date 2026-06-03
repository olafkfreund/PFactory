"""CLI intake channel — normalise a plan file into a :class:`NormalizedPlan` (issue #4).

Usage::

    python -m plan.ingest.cli <file> [--channel cli] [--title T] \\
        [--format markdown|gherkin|ears] [--json]

Reads a plan document (PDF / DOCX / markdown / Gherkin / EARS) from disk and
prints the resulting :class:`~plan.models.NormalizedPlan` as JSON to stdout.
Ingestion errors are written to stderr and exit with code 2.

``main(argv=None)`` is factored out for testability; the module guard at the
bottom dispatches it as the process entry point.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from plan.ingest.channels import ingest_path
from plan.ingest.document_loaders import DocumentLoadError
from spec_sources import SpecFormat, SpecSourceError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plan.ingest.cli",
        description="Ingest a plan file into a NormalizedPlan and print it as JSON.",
    )
    parser.add_argument("file", help="path to the plan document (PDF / DOCX / text)")
    parser.add_argument(
        "--channel",
        default="cli",
        help="source channel to record on the plan (default: cli)",
    )
    parser.add_argument("--title", help="override the detected plan title")
    parser.add_argument(
        "--format",
        choices=[f.value for f in SpecFormat],
        help="force a source format (default: auto-detect)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit JSON (default; flag kept for explicitness)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; return a process exit code (0 ok, 2 on ingestion error)."""
    args = _build_parser().parse_args(argv)
    fmt = SpecFormat(args.format) if args.format else None
    try:
        plan = ingest_path(
            args.file,
            source_channel=args.channel,
            fmt=fmt,
            title=args.title,
        )
    except (DocumentLoadError, SpecSourceError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(plan.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
