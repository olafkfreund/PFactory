"""Spec listing for the build CLI.

``main.py`` has imported ``print_specs_list`` from this module since the fork,
and the module has never existed here -- the path has no history at all in this
repository. That made ``run.py`` unimportable, so the primary entry point died
on ``--help`` (PFactory#621).

It is implemented rather than stubbed because the two call sites need it to do
something real: ``--list`` prints the available specs, and the "spec not found"
path prints them so the user can see what they could have typed. A stub that
printed nothing would turn a crash into a silent wrong answer.

The layout comes from :func:`spec.pipeline.get_specs_dir` rather than being
spelled out again here, so this cannot drift from where ``agent_service``
actually writes specs.
"""

from __future__ import annotations

import json
from pathlib import Path

from spec.pipeline import get_specs_dir
from ui import print_key_value, print_status


def iter_spec_dirs(project_dir: Path | str) -> list[Path]:
    """Every spec directory under *project_dir*, oldest name first.

    Returns an empty list when the project has no ``specs/`` directory, which
    is a normal state for a fresh project and not an error.
    """
    specs_root = get_specs_dir(project_dir)
    if not specs_root.is_dir():
        return []
    return sorted((p for p in specs_root.iterdir() if p.is_dir()), key=lambda p: p.name)


def _status_of(spec_dir: Path) -> str:
    """Best-effort status for one spec, for display only.

    Reads ``implementation_plan.json`` when it is present and parseable. A spec
    with no plan yet is ``pending``; an unreadable or malformed plan reports
    ``unknown`` rather than raising, because this function exists to help
    someone see their specs and must not be the thing that fails.
    """
    plan = spec_dir / "implementation_plan.json"
    if not plan.is_file():
        return "pending"
    try:
        data = json.loads(plan.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown"
    if not isinstance(data, dict):
        return "unknown"
    status = data.get("status")
    return str(status) if status else "unknown"


def print_specs_list(project_dir: Path | str) -> None:
    """Print the specs in *project_dir*, one per line, with status."""
    spec_dirs = iter_spec_dirs(project_dir)
    if not spec_dirs:
        # Routed through the shared ui helpers rather than bare print(), so this
        # new module carries no T201 of its own -- the existing CLI modules trip
        # it dozens of times and a new file starts from a baseline of zero.
        print_status(f"no specs found under {get_specs_dir(project_dir)}", "info")
        return
    for spec_dir in spec_dirs:
        print_key_value(spec_dir.name, _status_of(spec_dir))
