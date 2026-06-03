"""Test harness for ``scripts/e2e-smoke.sh`` — Task 11 (#12) commit 2.

The smoke script is intentionally *manual* — its real value comes
from running against a live AIFactory project + Claude API + docker.
This harness can't drive the real scenarios; it verifies the
structural surface: the dispatcher works, all 9 scenarios are
callable, pre-flight is sensible, --dry-run never executes anything
expensive, and unknown args fail loudly.

Covered:
  - Script exists + is executable
  - --list output contains all 9 scenarios in order
  - --help exits 2 with usage text
  - --dry-run --scenario N (N=1..9) each pass
  - --dry-run --all reports 9 passed / 0 failed
  - Unknown args exit with code 2
  - State file lands at PFACTORY_E2E_STATE_DIR + records the run
  - Invalid scenario index (0, 10, "abc") exits non-zero
  - Missing required mode (no --list / --scenario / --all) exits 2
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "e2e-smoke.sh"


# ─── Helpers ────────────────────────────────────────────────────────────


def _run(
    *args: str,
    env_extra: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Invoke the smoke script with a clean env.

    NO_COLOR=1 keeps stdout assertable; PATH inherited so bash + python
    on the shebang resolve. PFACTORY_E2E_STATE_DIR is set per-call by
    the caller via env_extra.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "NO_COLOR": "1",
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


# ─── Structural ────────────────────────────────────────────────────────


def test_script_file_exists() -> None:
    assert SCRIPT.exists(), f"missing {SCRIPT}"


def test_script_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


def test_script_has_bash_shebang() -> None:
    first_line = SCRIPT.read_text().splitlines()[0]
    assert first_line.startswith("#!"), "missing shebang"
    assert "bash" in first_line, "shebang doesn't reference bash"


# ─── --list ────────────────────────────────────────────────────────────


def test_list_exits_zero(state_dir: Path) -> None:
    proc = _run("--list", env_extra={"PFACTORY_E2E_STATE_DIR": str(state_dir)})
    assert proc.returncode == 0, proc.stderr


def test_list_shows_all_nine_scenarios(state_dir: Path) -> None:
    proc = _run("--list", env_extra={"PFACTORY_E2E_STATE_DIR": str(state_dir)})
    for i in range(1, 10):
        assert f"  {i}." in proc.stdout, f"scenario {i} missing from --list output"


def test_list_includes_expected_scenario_function_names(
    state_dir: Path,
) -> None:
    """The function-name lines (dimmed) lock down the registry ordering."""
    proc = _run("--list", env_extra={"PFACTORY_E2E_STATE_DIR": str(state_dir)})
    expected_fns = [
        "scenario_1_workspace_creation",
        "scenario_2_portal_starts",
        "scenario_3_handover_progression",
        "scenario_4_tests_committed",
        "scenario_5_pytest_passes",
        "scenario_6_mutation_kills_test",
        "scenario_7_pr_comment_posted",
        "scenario_8_hallucination_replan",
        "scenario_9_docker_down_failure",
    ]
    for fn in expected_fns:
        assert fn in proc.stdout, f"function {fn} not in --list output"


# ─── --help ────────────────────────────────────────────────────────────


def test_help_exits_two() -> None:
    """`--help` prints usage to stderr and exits 2 (BSD convention used
    throughout this script)."""
    proc = _run("--help")
    assert proc.returncode == 2


def test_help_mentions_required_envs() -> None:
    proc = _run("--help")
    output = proc.stdout + proc.stderr
    for var in (
        "ANTHROPIC_API_KEY",
        "PFACTORY_AIFACTORY_ROOT",
        "PFACTORY_AIFACTORY_BRANCH",
        "PFACTORY_AIFACTORY_PR",
    ):
        assert var in output, f"--help missing env var: {var}"


def test_no_mode_exits_two() -> None:
    """Calling without --list / --scenario / --all should fail usage."""
    proc = _run()
    assert proc.returncode == 2


def test_unknown_arg_exits_two() -> None:
    proc = _run("--bogus")
    assert proc.returncode == 2


# ─── --dry-run --scenario N ─────────────────────────────────────────────


@pytest.mark.parametrize("n", list(range(1, 10)))
def test_dry_run_scenario_passes(n: int, state_dir: Path) -> None:
    proc = _run(
        "--dry-run", "--scenario", str(n),
        env_extra={"PFACTORY_E2E_STATE_DIR": str(state_dir)},
    )
    assert proc.returncode == 0, (
        f"scenario {n} failed under dry-run\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    assert "✓ PASS" in proc.stdout
    assert f"scenario {n} done" in proc.stdout


def test_dry_run_scenario_zero_rejected(state_dir: Path) -> None:
    proc = _run(
        "--dry-run", "--scenario", "0",
        env_extra={"PFACTORY_E2E_STATE_DIR": str(state_dir)},
    )
    assert proc.returncode != 0


def test_dry_run_scenario_ten_rejected(state_dir: Path) -> None:
    """The dispatcher's regex caps scenarios at 1-9."""
    proc = _run(
        "--dry-run", "--scenario", "10",
        env_extra={"PFACTORY_E2E_STATE_DIR": str(state_dir)},
    )
    # Either rejected (rc != 0) OR clearly noted as invalid
    assert proc.returncode != 0 or "Invalid scenario" in proc.stderr


def test_dry_run_scenario_non_numeric_rejected(state_dir: Path) -> None:
    proc = _run(
        "--dry-run", "--scenario", "abc",
        env_extra={"PFACTORY_E2E_STATE_DIR": str(state_dir)},
    )
    assert proc.returncode != 0


# ─── --dry-run --all ────────────────────────────────────────────────────


def test_dry_run_all_passes(state_dir: Path) -> None:
    proc = _run(
        "--dry-run", "--all",
        env_extra={"PFACTORY_E2E_STATE_DIR": str(state_dir)},
    )
    assert proc.returncode == 0, proc.stderr
    # Summary tally — log_fail writes the "failed:" line to stderr by
    # design (it's the FAIL helper), so check combined output.
    combined = proc.stdout + proc.stderr
    assert "passed:  9" in combined
    assert "failed:  0" in combined


def test_dry_run_all_records_state(state_dir: Path) -> None:
    """After --dry-run --all, the state file should have an entry for
    each of the 9 scenarios."""
    _run(
        "--dry-run", "--all",
        env_extra={"PFACTORY_E2E_STATE_DIR": str(state_dir)},
    )
    state_file = state_dir / "e2e-state.json"
    assert state_file.exists()
    doc = json.loads(state_file.read_text())
    assert "scenarios" in doc
    # 9 scenario entries (all marked pass under dry-run)
    assert len(doc["scenarios"]) == 9
    for outcome_record in doc["scenarios"].values():
        assert outcome_record["outcome"] == "pass"


# ─── Pre-flight detection ──────────────────────────────────────────────


def test_dry_run_skips_env_var_checks(state_dir: Path) -> None:
    """In dry-run mode the script must NOT require ANTHROPIC_API_KEY +
    friends — those checks live behind the dry-run conditional."""
    proc = _run(
        "--dry-run", "--scenario", "1",
        env_extra={"PFACTORY_E2E_STATE_DIR": str(state_dir)},
    )
    assert proc.returncode == 0
    # Confirm the "skipping env / project checks" message appears
    assert "skipping env" in proc.stdout


def test_no_color_disables_ansi(state_dir: Path) -> None:
    """NO_COLOR=1 (or non-TTY stdout) → no ANSI escape sequences."""
    proc = _run("--list", env_extra={"PFACTORY_E2E_STATE_DIR": str(state_dir)})
    # \033[ — escape char ESC followed by [ — would indicate colour
    assert "\x1b[" not in proc.stdout
