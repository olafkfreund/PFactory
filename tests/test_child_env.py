"""Checks for the child-process environment boundary."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.child_env import (  # noqa: E402
    CLAUDE_KEEP,
    child_env,
    get_agent_env_blanks,
    without_host_secrets,
)


def test_child_env_excludes_host_credentials(monkeypatch):
    names = (
        "APP_API_TOKEN",
        "APP_JWT_SECRET",
        "DATABASE_URL",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "NIX_CONFIG",
        "AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY_2",
        "CLAUDE_CODE_OAUTH_TOKEN",
    )
    sentinels = {name: f"sentinel-{index}" for index, name in enumerate(names)}
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)

    env = child_env()
    assert not set(sentinels.values()) & set(env.values())


def test_opt_in_and_explicit_keep(monkeypatch):
    claude_sentinel = "claude-sentinel"
    monkeypatch.setenv("APP_API_TOKEN", "denied-sentinel")
    monkeypatch.setenv("PLAIN_AGENT_VAR", "plain-sentinel")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", claude_sentinel)
    monkeypatch.setenv("PFACTORY_AGENT_ENV_EXTRA", "APP_API_TOKEN, PLAIN_AGENT_VAR")

    env = child_env()
    assert "denied-sentinel" not in env.values()
    assert "plain-sentinel" in env.values()
    assert claude_sentinel not in env.values()
    kept = child_env(keep=("CLAUDE_CODE_OAUTH_TOKEN",))
    assert claude_sentinel in kept.values()
    assert "extra-sentinel" in child_env(extra={"EXPLICIT": "extra-sentinel"}).values()


def test_blanks_preserve_claude_keep(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "claude-sentinel")
    monkeypatch.setenv("ANTHROPIC_MODEL", "model-sentinel")
    monkeypatch.setenv("APP_API_TOKEN", "denied-sentinel")
    monkeypatch.setenv("PLAIN_AGENT_VAR", "plain-sentinel")
    monkeypatch.setenv("PATH", "path-sentinel")

    blanks = get_agent_env_blanks()
    assert {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_MODEL"} <= CLAUDE_KEEP
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in blanks
    assert "ANTHROPIC_MODEL" not in blanks
    assert blanks["APP_API_TOKEN"] == ""
    assert blanks["PLAIN_AGENT_VAR"] == ""
    assert "PATH" not in blanks


def test_without_host_secrets_drops_renamed_values(monkeypatch):
    monkeypatch.setenv("APP_API_TOKEN", "host-secret-sentinel")
    source = {"RENAMED": "host-secret-sentinel", "PLAIN": "safe-sentinel", "EMPTY": ""}

    result = without_host_secrets(source)
    assert result == {"PLAIN": "safe-sentinel", "EMPTY": ""}
    assert source["RENAMED"] == "host-secret-sentinel"


def test_child_env_keeps_no_proxy_and_drops_credential_shaped_names(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sentinel-claude")
    monkeypatch.setenv("MCP_FOO_TOKEN", "sentinel-mcp")
    monkeypatch.setenv("PFACTORY_AGENT_ENV_EXTRA", "MCP_FOO_TOKEN")
    env = child_env()
    assert env["NO_PROXY"] == "localhost"
    assert "sentinel-claude" not in env.values()
    assert "sentinel-mcp" not in env.values()
    assert get_agent_env_blanks().get("MCP_FOO_TOKEN") is None


def test_blanks_cover_credential_names_and_short_values_are_not_secrets(monkeypatch):
    from core.child_env import without_host_secrets

    monkeypatch.setenv("NIX_GITHUB_TOKEN", "sentinel-nix-token")
    monkeypatch.setenv("CLAUDE_CODE_FOO_KEY", "sentinel-foo-key")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sentinel-claude")
    monkeypatch.setenv("USE_SECRET_STORE", "true")
    blanks = get_agent_env_blanks()
    assert blanks["NIX_GITHUB_TOKEN"] == "" and blanks["CLAUDE_CODE_FOO_KEY"] == ""
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in blanks
    assert without_host_secrets({"FLAG": "true"}) == {"FLAG": "true"}


def test_github_probe_prefers_the_dedicated_token(monkeypatch):
    from core import mcp_credentials

    monkeypatch.setattr(mcp_credentials, "_load_operator_config", lambda: {})
    monkeypatch.setenv("GITHUB_TOKEN", "sentinel-pod-token")
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "sentinel-mcp-token")
    status = mcp_credentials._probe_github()
    assert status.env_vars == {"GITHUB_PERSONAL_ACCESS_TOKEN": "sentinel-mcp-token"}
