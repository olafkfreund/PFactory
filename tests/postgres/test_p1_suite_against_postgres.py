"""P1.2 — full existing pytest suite passes against Postgres (not just SQLite)."""

import os
import subprocess
from pathlib import Path

import pytest

from tests.postgres.helpers import REPO_ROOT


@pytest.mark.postgres
@pytest.mark.slow
def test_full_pytest_suite_passes_against_postgres(test_postgres_url: str) -> None:
    """P1.2 — the existing `pytest tests/ -m 'not slow'` suite runs green
    against a real Postgres (no SQLite-isms in queries/migrations).

    Run as a subprocess so the inner pytest gets a fresh module state with
    DATABASE_URL pointing at Postgres. Excludes -m postgres/-m slow to keep
    runtime under ~30s.
    """
    venv_python = REPO_ROOT / "apps" / "backend" / ".venv" / "bin" / "python3"
    if not venv_python.exists():
        pytest.skip("backend venv not present — `uv pip install -r tests/requirements-test.txt`")

    env = os.environ.copy()
    env["DATABASE_URL"] = test_postgres_url
    # Don't recursively trigger the postgres-acceptance suite or we get infinite recursion.
    env["TEST_POSTGRES_URL"] = ""

    result = subprocess.run(
        [
            str(venv_python), "-m", "pytest",
            "tests/", "-m", "not slow and not postgres",
            "-q", "--tb=short",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, (
        f"existing pytest suite failed against Postgres:\n"
        f"--- last 3000 chars of stdout ---\n{result.stdout[-3000:]}\n"
        f"--- last 2000 chars of stderr ---\n{result.stderr[-2000:]}"
    )
