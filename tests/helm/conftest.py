"""Pytest fixtures for P4 Helm chart acceptance tests.

Tests are marked ``@pytest.mark.helm``. They run against locally-
installed `helm`, `kubeconform`, and (for end-to-end install tests)
a `kind` cluster. CI's helm-acceptance job installs all three.

Locally, tests skip cleanly when the required binaries aren't on
PATH — operators who don't run Kubernetes still get a passing test
suite for the rest of the codebase.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART_DIR = REPO_ROOT / "charts" / "pfactory"


def _binary_available(name: str) -> bool:
    """True iff ``name`` resolves on PATH (and is executable)."""
    return shutil.which(name) is not None


def _binary_version(name: str) -> str | None:
    """Return the binary's version string or None if it doesn't run."""
    try:
        result = subprocess.run(
            [name, "version", "--short"]
            if name == "helm"
            else [name, "version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or result.stderr.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


@pytest.fixture
def helm_available() -> bool:
    if not _binary_available("helm"):
        pytest.skip("helm not installed")
    return True


@pytest.fixture
def kubeconform_available() -> bool:
    if not _binary_available("kubeconform"):
        pytest.skip("kubeconform not installed")
    return True


@pytest.fixture
def kind_available() -> bool:
    if not _binary_available("kind"):
        pytest.skip("kind not installed")
    return True


@pytest.fixture
def kubectl_available() -> bool:
    if not _binary_available("kubectl"):
        pytest.skip("kubectl not installed")
    return True


@pytest.fixture
def chart_dir() -> Path:
    """Absolute path to the pfactory Helm chart directory."""
    if not CHART_DIR.is_dir():
        pytest.skip(f"chart not present at {CHART_DIR} (pre-P4.1 state)")
    return CHART_DIR


@pytest.fixture
def helm_template(helm_available, chart_dir):
    """Render the chart via `helm template`; return the YAML string.

    Tests that need to inspect specific manifests (e.g. NetworkPolicy,
    Deployment securityContext) parse this output once per test
    rather than re-rendering.
    """
    result = subprocess.run(
        ["helm", "template", "pfactory", str(chart_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(
            f"`helm template` failed (exit {result.returncode}):\n"
            f"stderr: {result.stderr[-1500:]}"
        )
    return result.stdout
