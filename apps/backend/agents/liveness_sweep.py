"""Liveness sweep driver (#95) — periodically flag stalled tasks.

The watchdog in ``agents.liveness`` decides whether *one* task has stalled;
this is the driver that walks every in-flight task under the PFactory
workspace root and applies it. Run it on a timer — cron, a systemd timer, or
the web-server's background loop:

    python -m agents.liveness_sweep                 # sweep the default root
    python -m agents.liveness_sweep --deadline 600  # tighter idle budget

Backend-only and side-effect-light: it only ever flips a genuinely-silent
*active* stage to ``stalled`` (see ``agents.liveness`` for the fail-safe
rules) and emits a #95 stage event for each flip. Walking a workspace with no
in-flight tasks is a cheap no-op.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from agents.liveness import StallVerdict, check_and_mark

__all__ = ["default_workspace_root", "iter_spec_dirs", "sweep"]

_DEFAULT_ROOT = Path.home() / ".pfactory"


def default_workspace_root() -> Path:
    """Resolve the workspace root the same way as the rest of the backend:
    ``PFACTORY_WORKSPACE_ROOT`` (expanded) > ``~/.pfactory``."""
    root = os.environ.get("PFACTORY_WORKSPACE_ROOT")
    return Path(root).expanduser() if root else _DEFAULT_ROOT


def iter_spec_dirs(workspace_root: Path) -> Iterator[Path]:
    """Yield every ``<root>/workspaces/<project>/specs/<spec>`` dir that holds
    a ``status.json``. A missing/partial tree yields nothing (never raises)."""
    base = workspace_root / "workspaces"
    if not base.is_dir():
        return
    for project_dir in sorted(base.iterdir()):
        specs = project_dir / "specs"
        if not specs.is_dir():
            continue
        for spec_dir in sorted(specs.iterdir()):
            if (spec_dir / "status.json").is_file():
                yield spec_dir


def sweep(
    workspace_root: Path | None = None,
    *,
    now: datetime | None = None,
    deadline_seconds: float | None = None,
) -> list[tuple[Path, StallVerdict]]:
    """Apply the watchdog to every in-flight task under ``workspace_root``.

    Returns ``(spec_dir, verdict)`` for each task inspected; the ones just
    flipped have ``verdict.stalled is True``. ``now`` defaults to current UTC.
    """
    root = workspace_root or default_workspace_root()
    when = now or datetime.now(UTC)
    results: list[tuple[Path, StallVerdict]] = []
    for spec_dir in iter_spec_dirs(root):
        verdict = check_and_mark(spec_dir, now=when, deadline_seconds=deadline_seconds)
        results.append((spec_dir, verdict))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flag stalled PFactory tasks as `stalled` (#95).")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root (default: $PFACTORY_WORKSPACE_ROOT or ~/.pfactory).",
    )
    parser.add_argument(
        "--deadline",
        type=float,
        default=None,
        help=(
            "Idle seconds before an active stage is stalled "
            "(default: $PFACTORY_STALL_DEADLINE_SECONDS or 900)."
        ),
    )
    args = parser.parse_args(argv)

    results = sweep(args.root, deadline_seconds=args.deadline)
    stalled = [(d, v) for d, v in results if v.stalled]
    for spec_dir, verdict in stalled:
        print(f"STALLED {spec_dir}  {verdict.reason}")
    print(f"swept {len(results)} task(s), flagged {len(stalled)} stalled")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
