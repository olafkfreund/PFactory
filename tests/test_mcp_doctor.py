"""Smoke tests for the ``pfactory --mcp-doctor`` command.

The doctor command is a thin wrapper over the catalog + credentials probe
machinery (already exhaustively tested in test_mcp_catalog.py /
test_mcp_credentials.py). These tests just verify the CLI surface:
- prints all V1 catalog entries
- honours --project-dir for marker resolution
- exits 0 on a clean run (missing creds are informational, not fatal)
- handles the operator-config-perms warning path
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

import pytest
from cli.mcp_commands import handle_mcp_doctor_command


@pytest.fixture(autouse=True)
def isolate_creds(monkeypatch, tmp_path):
    """Force the doctor to see a clean credential environment.

    Without this, the developer's real creds bleed through and the
    'X missing → here's the hint' branch never gets exercised.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("KUBECONFIG", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("AZURE_USE_MSI", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    # Suppress colour codes so substring assertions are reliable on TTY hosts
    monkeypatch.setenv("NO_COLOR", "1")
    # Drop the cached operator config so a previous test's HOME doesn't leak in
    from core import mcp_credentials
    mcp_credentials.reset_cache()


def test_doctor_lists_all_v1_catalog_entries(capsys):
    rc = handle_mcp_doctor_command(project_dir=None)
    out = capsys.readouterr().out
    assert rc == 0
    for entry_id in ("github", "kubernetes", "aws", "azure"):
        assert entry_id in out, f"expected catalog entry '{entry_id}' in doctor output"


def test_doctor_returns_zero_even_with_no_creds(capsys):
    """Missing creds are informational, not fatal — operators expect this on fresh machines."""
    rc = handle_mcp_doctor_command(project_dir=None)
    assert rc == 0


def test_doctor_shows_marker_pending_without_project(capsys):
    """Without --project-dir, the doctor must NOT pretend to know markers."""
    handle_mcp_doctor_command(project_dir=None)
    out = capsys.readouterr().out
    # The 3 marker-gated entries should say "not checked"; github (always-on)
    # should not — verify both branches show up.
    assert "markers not checked" in out
    assert "always-on" in out


def test_doctor_resolves_markers_for_project(tmp_path, capsys):
    """With --project-dir, marker detection runs and the output reflects it."""
    # Simulate a Kubernetes project — charts/ is one of has_kubernetes signals
    (tmp_path / "charts").mkdir()
    handle_mcp_doctor_command(project_dir=tmp_path)
    out = capsys.readouterr().out
    # Should show "matched: has_kubernetes" for the kubernetes entry
    assert "matched: has_kubernetes" in out
    # And "none of: has_aws" for AWS since no terraform/ dir
    assert "none of:" in out and "has_aws" in out


def test_doctor_emits_hint_when_creds_missing(tmp_path, capsys):
    """When a marker matches but creds are absent, the doctor should suggest a fix."""
    (tmp_path / "charts").mkdir()  # has_kubernetes → True
    # No KUBECONFIG / ~/.kube/config → kubernetes creds → unavailable
    handle_mcp_doctor_command(project_dir=tmp_path)
    out = capsys.readouterr().out
    # Hint string is "configure ~/.kube/config OR export KUBECONFIG=<path>"
    assert "KUBECONFIG" in out or ".kube/config" in out


def test_doctor_warns_on_loose_operator_config_perms(tmp_path, capsys):
    """The operator config must be 0600; the doctor must warn loudly if not."""
    cfg = tmp_path / ".pfactory" / "mcp-credentials.json"
    cfg.parent.mkdir()
    cfg.write_text("{}")
    cfg.chmod(0o644)  # too loose
    handle_mcp_doctor_command(project_dir=None)
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "chmod 0600" in out


def test_doctor_no_warning_when_operator_config_absent(capsys):
    """No warning when the file simply doesn't exist (the common case)."""
    handle_mcp_doctor_command(project_dir=None)
    out = capsys.readouterr().out
    assert "WARNING" not in out
    # ...but does mention the path in the footer
    assert "Operator config" in out
