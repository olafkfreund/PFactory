"""make_subprocess_env drops control-plane-only secrets, nothing a runner reads (Factory#3737)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
for _p in (_ROOT / "apps" / "backend", _ROOT / "apps" / "web-server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from server.utils.subprocess_env import RUNNER_STRIP, make_subprocess_env  # noqa: E402

KEPT = {
    "APP_API_TOKEN": "sentinel-app-api-token",
    "DATABASE_URL": "sentinel-database-url",
    "S3_SECRET_KEY": "sentinel-s3-secret",
    "OPENAI_API_KEY": "sentinel-openai",
    "PFACTORY_MCP_KEY": "sentinel-mcp-key",
}


@pytest.fixture
def env(monkeypatch):
    for name in RUNNER_STRIP:
        monkeypatch.setenv(name, "sentinel-" + name.lower())
    for name, value in KEPT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sentinel-anthropic")
    monkeypatch.setenv("jwt_secret", "lowercase-copy")


@pytest.mark.usefixtures("env")
def test_control_plane_only_names_are_dropped():
    out = make_subprocess_env()
    assert not RUNNER_STRIP & set(out)
    assert "jwt_secret" not in out  # a renamed-case copy goes too


@pytest.mark.usefixtures("env")
def test_runner_credentials_still_pass():
    out = make_subprocess_env()
    assert {k: out.get(k) for k in KEPT} == KEPT


@pytest.mark.usefixtures("env")
def test_anthropic_key_strip_and_opt_in_are_unchanged():
    assert "ANTHROPIC_API_KEY" not in make_subprocess_env()
    kept = make_subprocess_env(strip_anthropic_api_key=False)
    assert kept["ANTHROPIC_API_KEY"] == "sentinel-anthropic"


@pytest.mark.usefixtures("env")
def test_extra_wins_even_for_a_stripped_name():
    assert make_subprocess_env({"JWT_SECRET": "x"})["JWT_SECRET"] == "x"  # noqa: S105


def test_env_file_cannot_restore_a_stripped_name(tmp_path):
    from server.services.pr_review_service import _load_env_file  # noqa: PLC0415

    f = tmp_path / ".env"
    f.write_text("JWT_SECRET=from-file\nGRAPHITI_ENABLED=true\n")
    env: dict[str, str] = {}
    _load_env_file(f, env)
    assert env == {"GRAPHITI_ENABLED": "true"}
