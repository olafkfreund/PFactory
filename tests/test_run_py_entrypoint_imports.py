"""Guard: `run.py` -- the command the service spawns -- must be importable.

`agent_service` executes a build by spawning

    python run.py --spec <id> --project-dir <path>

in two places. That command raised ModuleNotFoundError in the repo AND in the
deployed image, so PFactory could author a spec and then never build it
(PFactory#621). Three modules were missing from this fork, discovered one at a
time because each crash hid the next:

    qa_loop         -- a TFactory/AIFactory module, imported at module scope by
                       cli.qa_commands and inside cli.build_commands
    cli.spec_commands
    spec.pipeline   -- imported by cli.utils, so it took the whole CLI down

Nothing detected it because nothing imported the entry point: the unit tests
import individual modules, and `cli/__init__.py` defers `.main` behind a lazy
`_get_legacy_main()`, which moves the failure from import time to call time --
so the package imported cleanly and the command still died.

These tests import what the service actually runs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "apps" / "backend"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    # S603: the argv is this file's own literals plus sys.executable; there is
    # no untrusted input. Running the real entry point as a subprocess is the
    # point -- importing it in-process would not prove the command works.
    return subprocess.run(  # noqa: S603
        [sys.executable, "run.py", *args],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_run_py_help_does_not_crash() -> None:
    """`run.py --help` is the cheapest proof the import chain is intact."""
    proc = _run("--help")
    assert "ModuleNotFoundError" not in proc.stderr, (
        f"run.py cannot be imported:\n{proc.stderr[-800:]}"
    )
    assert proc.returncode == 0, f"run.py --help exited {proc.returncode}:\n{proc.stderr[-800:]}"


def test_spec_lookup_path_matches_where_the_service_writes(tmp_path: Path) -> None:
    """`get_specs_dir` must agree with agent_service's own layout.

    The service writes `<project>/.pfactory/specs/<id>`. A resolver pointing
    anywhere else would leave run.py unable to find the spec that had just been
    created -- quieter than a crash, and worse.
    """
    sys.path.insert(0, str(BACKEND))
    try:
        # PLC0415: the import has to happen AFTER sys.path is extended, which is
        # what makes it function-local rather than a style choice.
        from spec.pipeline import get_specs_dir  # noqa: PLC0415
    finally:
        sys.path.pop(0)
    assert get_specs_dir(tmp_path) == tmp_path / ".pfactory" / "specs"


def test_list_reads_real_spec_directories(tmp_path: Path) -> None:
    """--list must actually find specs, not merely not crash."""
    (tmp_path / ".pfactory" / "specs" / "001-demo").mkdir(parents=True)
    (tmp_path / ".pfactory" / "specs" / "001-demo" / "implementation_plan.json").write_text(
        '{"status": "in_progress"}', encoding="utf-8"
    )
    proc = _run("--list", "--project-dir", str(tmp_path))
    assert "001-demo" in proc.stdout, f"--list did not list the spec:\n{proc.stdout[-500:]}"
    assert "in_progress" in proc.stdout, "status column missing"


def test_missing_spec_lists_what_is_available(tmp_path: Path) -> None:
    """The not-found path prints the available specs, so a typo is recoverable."""
    (tmp_path / ".pfactory" / "specs" / "007-real").mkdir(parents=True)
    proc = _run("--spec", "nope", "--project-dir", str(tmp_path))
    assert "007-real" in proc.stdout, f"available specs not shown:\n{proc.stdout[-500:]}"
