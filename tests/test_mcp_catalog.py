"""Unit tests for the MCP server catalog and its integration with
``get_required_mcp_servers()``.

Two layers:

1. **Catalog shape** — every V1 entry is wired correctly (id, launcher,
   defaults, agent inclusion) and ``build_server_config`` produces the dict
   the Claude Agent SDK expects.

2. **Integration with the resolver** — ``get_required_mcp_servers()`` walks
   the catalog when ``infra_markers`` is provided, skips entries without
   matching markers or credentials, and honours the existing
   ``AGENT_MCP_<agent>_REMOVE`` override (force-disable).
"""

from __future__ import annotations

import pytest
from agents.tools_pkg import mcp_catalog
from agents.tools_pkg.models import _map_mcp_server_name, get_required_mcp_servers
from core.mcp_credentials import CredentialStatus

# ---------------------------------------------------------------------------
# Catalog shape
# ---------------------------------------------------------------------------


def test_v1_entries_present():
    """The four ship-today servers are in the catalog."""
    ids = set(mcp_catalog.catalog_ids())
    assert {"github", "kubernetes", "aws", "azure"}.issubset(ids)


def test_v1_5_entries_present():
    """GitLab + ADO catalog entries exist (V1.5 ships behind V1)."""
    ids = set(mcp_catalog.catalog_ids())
    assert "gitlab" in ids
    assert "azure_devops" in ids


def test_gitlab_uses_community_fork():
    """V1.5 GitLab ships the @zereight community fork (vendor disclosure
    lives in the launcher arg itself, not just docs)."""
    entry = mcp_catalog.get_catalog_entry("gitlab")
    assert entry is not None
    joined = " ".join(entry.launcher_args)
    assert "@zereight/mcp-gitlab" in joined, (
        "GitLab catalog entry must launch the @zereight community fork — "
        "the launcher arg is the disclosure to operators."
    )


def test_gitlab_marker_is_has_gitlab_ci():
    entry = mcp_catalog.get_catalog_entry("gitlab")
    assert entry.marker_capability_keys == ["has_gitlab_ci"]


def test_azure_devops_uses_next_channel():
    """ADO catalog entry uses Microsoft's @next channel (the local server
    they still publish while Remote MCP goes through preview)."""
    entry = mcp_catalog.get_catalog_entry("azure_devops")
    assert entry is not None
    joined = " ".join(entry.launcher_args)
    assert "@azure-devops/mcp@next" in joined


def test_azure_devops_marker():
    entry = mcp_catalog.get_catalog_entry("azure_devops")
    assert entry.marker_capability_keys == ["has_azure_devops"]


def test_gitlab_auto_enables_with_marker_and_creds(monkeypatch):
    _stub_creds(monkeypatch, gitlab=True)
    servers = get_required_mcp_servers(
        "coder", None, {}, infra_markers={"has_gitlab_ci": True}
    )
    assert "gitlab" in servers


def test_gitlab_skipped_without_marker(monkeypatch):
    _stub_creds(monkeypatch, gitlab=True)
    servers = get_required_mcp_servers(
        "coder", None, {}, infra_markers={"has_gitlab_ci": False}
    )
    assert "gitlab" not in servers


def test_azure_devops_auto_enables_with_marker_and_creds(monkeypatch):
    _stub_creds(monkeypatch, azure_devops=True)
    servers = get_required_mcp_servers(
        "coder", None, {}, infra_markers={"has_azure_devops": True}
    )
    assert "azure_devops" in servers


def test_kubernetes_pins_safe_version():
    """CVE-2026-46519 is patched in v3.6.0 — pin must enforce that."""
    entry = mcp_catalog.get_catalog_entry("kubernetes")
    assert entry is not None
    joined = " ".join(entry.launcher_args)
    assert ">=3.6.0" in joined, "kubernetes-mcp-server must be pinned >=3.6.0 (CVE-2026-46519)"


def test_kubernetes_uses_readonly_flag():
    entry = mcp_catalog.get_catalog_entry("kubernetes")
    assert entry.readonly_args == ["--read-only"]


def test_github_is_always_on():
    """Empty marker list means every project gets github when creds exist."""
    entry = mcp_catalog.get_catalog_entry("github")
    assert entry is not None
    assert entry.marker_capability_keys == []


def test_get_catalog_entry_unknown_returns_none():
    assert mcp_catalog.get_catalog_entry("not-a-server") is None


def test_is_catalog_server():
    assert mcp_catalog.is_catalog_server("github")
    assert not mcp_catalog.is_catalog_server("playwright")  # playwright is NOT catalog
    assert not mcp_catalog.is_catalog_server("")


def test_build_server_config_with_creds():
    entry = mcp_catalog.get_catalog_entry("github")
    creds = CredentialStatus(True, "env:GITHUB_TOKEN", {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x"})
    cfg = entry.build_server_config(creds, read_only=True)
    assert cfg["command"] == "npx"
    assert cfg["args"][0] == "-y"
    assert cfg["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_x"


def test_build_server_config_appends_readonly_args():
    entry = mcp_catalog.get_catalog_entry("kubernetes")
    creds = CredentialStatus(True, "file:~/.kube/config", {"KUBECONFIG": "/tmp/kc"})
    cfg = entry.build_server_config(creds, read_only=True)
    assert "--read-only" in cfg["args"]


def test_build_server_config_without_readonly_omits_extra_args():
    entry = mcp_catalog.get_catalog_entry("kubernetes")
    creds = CredentialStatus(True, "file:~/.kube/config", {"KUBECONFIG": "/tmp/kc"})
    cfg = entry.build_server_config(creds, read_only=False)
    assert "--read-only" not in cfg["args"]


# ---------------------------------------------------------------------------
# Name mapping (so AGENT_MCP_<agent>_ADD=github works)
# ---------------------------------------------------------------------------


def test_catalog_server_names_resolve_in_map():
    assert _map_mcp_server_name("github") == "github"
    assert _map_mcp_server_name("kubernetes") == "kubernetes"
    assert _map_mcp_server_name("aws") == "aws"
    assert _map_mcp_server_name("azure") == "azure"
    # Case + whitespace tolerance from the existing helper
    assert _map_mcp_server_name("  GITHUB  ") == "github"


def test_existing_servers_still_map():
    # Defensive: make sure I didn't break the existing servers
    assert _map_mcp_server_name("context7") == "context7"
    assert _map_mcp_server_name("graphiti") == "graphiti"
    assert _map_mcp_server_name("playwright") == "playwright"


# ---------------------------------------------------------------------------
# Integration with get_required_mcp_servers — needs creds + marker probes
# ---------------------------------------------------------------------------


def _stub_creds(monkeypatch, **provider_to_available):
    """Stub get_credential_status to return ``available=True`` for each named provider."""
    def fake(provider: str) -> CredentialStatus:
        if provider_to_available.get(provider):
            return CredentialStatus(True, "stub", {})
        return CredentialStatus(False, "stub-none")

    # The resolver imports lazily inside the function, so patch the module
    # the function actually sees.
    monkeypatch.setattr("core.mcp_credentials.get_credential_status", fake)


def test_catalog_skipped_when_infra_markers_is_None(monkeypatch):
    """Legacy callers (no infra_markers) keep existing behavior — no catalog walk."""
    _stub_creds(monkeypatch, github=True, kubernetes=True, aws=True, azure=True)
    # spec_gatherer base config has empty mcp_servers list
    servers = get_required_mcp_servers("spec_gatherer", None, {}, infra_markers=None)
    assert "github" not in servers
    assert "kubernetes" not in servers


def test_github_auto_enables_when_creds_present(monkeypatch):
    """github has empty markers ⇒ always-on if creds present and agent is eligible."""
    _stub_creds(monkeypatch, github=True)
    servers = get_required_mcp_servers("coder", None, {}, infra_markers={})
    assert "github" in servers


def test_github_skipped_when_no_creds(monkeypatch):
    _stub_creds(monkeypatch)  # nothing available
    servers = get_required_mcp_servers("coder", None, {}, infra_markers={})
    assert "github" not in servers


def test_kubernetes_requires_has_kubernetes_marker(monkeypatch):
    _stub_creds(monkeypatch, kubernetes=True)
    # No marker — should NOT enable
    servers = get_required_mcp_servers("coder", None, {}, infra_markers={"has_kubernetes": False})
    assert "kubernetes" not in servers
    # With marker — should enable
    servers = get_required_mcp_servers("coder", None, {}, infra_markers={"has_kubernetes": True})
    assert "kubernetes" in servers


def test_aws_requires_has_aws_marker(monkeypatch):
    _stub_creds(monkeypatch, aws=True)
    servers = get_required_mcp_servers("coder", None, {}, infra_markers={"has_aws": True})
    assert "aws" in servers
    servers = get_required_mcp_servers("coder", None, {}, infra_markers={})
    assert "aws" not in servers


def test_force_disable_via_REMOVE_override(monkeypatch):
    """Operators can force-disable a catalog server via .pfactory/.env."""
    _stub_creds(monkeypatch, github=True)
    mcp_config = {"AGENT_MCP_coder_REMOVE": "github"}
    servers = get_required_mcp_servers("coder", None, mcp_config, infra_markers={})
    assert "github" not in servers


def test_force_enable_via_ADD_override(monkeypatch):
    """ADD applies even without marker match — operator override."""
    _stub_creds(monkeypatch)  # no creds — wouldn't auto-enable
    mcp_config = {"AGENT_MCP_coder_ADD": "kubernetes"}
    servers = get_required_mcp_servers("coder", None, mcp_config, infra_markers={})
    # ADD adds by mapped name regardless of creds; the runtime will surface
    # any missing-creds error when the subprocess tries to start. This matches
    # the operator-override-trumps-detection philosophy of the existing system.
    assert "kubernetes" in servers


def test_agent_not_in_default_for_agents_does_not_get_server(monkeypatch):
    """commit_message agent has no entries — catalog walk shouldn't add anything."""
    _stub_creds(monkeypatch, github=True, kubernetes=True, aws=True, azure=True)
    servers = get_required_mcp_servers(
        "commit_message", None, {}, infra_markers={"has_kubernetes": True}
    )
    # commit_message is not in any entry's default_for_agents
    assert "github" not in servers
    assert "kubernetes" not in servers
