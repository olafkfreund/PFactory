"""fix-stuck-tasks.py must not rewrite a terminal or unknown status (Factory#3589)."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "apps" / "backend" / "scripts" / "fix-stuck-tasks.py"
)
_spec = importlib.util.spec_from_file_location("fix_stuck_tasks", _SCRIPT)
assert _spec and _spec.loader
fst = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fst)

# The forks differ only in where the plan lives; read it from the script.
_SOURCE = _SCRIPT.read_text()
_SPECS = re.search(r'project_path / "(\.\w+)" / "specs"', _SOURCE).group(1)  # type: ignore[union-attr]
_PLAN = re.search(r'plan_file = spec_dir / "(\w+\.json)"', _SOURCE).group(1)  # type: ignore[union-attr]


@pytest.mark.parametrize(("status", "flagged"), [("failed", False), ("bogus", True)])
def test_fix_task_never_rewrites_terminal_or_unknown_status(
    tmp_path: Path, status: str, flagged: bool
) -> None:
    spec_dir = tmp_path / _SPECS / "specs" / "001-task"
    spec_dir.mkdir(parents=True)
    plan_file = spec_dir / _PLAN
    plan_file.write_text(json.dumps({"status": status, "phases": []}))
    before = plan_file.read_bytes()

    _, issues = fst.check_task_state(spec_dir)
    assert any("Invalid status" in i for i in issues) is flagged

    fst.fix_task(spec_dir)
    assert plan_file.read_bytes() == before
