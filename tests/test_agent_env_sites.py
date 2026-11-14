"""Every agent / repository-code spawn site hands its child an allowlisted env (#3708).

Assertions are on sentinel VALUES, not names: a renamed copy must not slip through.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).parent.parent
for _p in (_ROOT / "apps" / "backend", _ROOT / "apps" / "web-server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

CONTROL_PLANE = {
    "APP_API_TOKEN": "sentinel-app-api-token",
    "APP_JWT_SECRET": "sentinel-jwt-secret",
    "DATABASE_URL": "sqlite+aiosqlite:///sentinel-database.db",
    "S3_ACCESS_KEY": "sentinel-s3-access",
    "S3_SECRET_KEY": "sentinel-s3-secret",
    "GITHUB_TOKEN": "sentinel-github-token",
    "GH_TOKEN": "sentinel-gh-token",
}
CLAUDE_SENTINEL = "sentinel-claude-oauth"


@pytest.fixture(autouse=True)
def seeded_env(monkeypatch):
    for name, value in CONTROL_PLANE.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", CLAUDE_SENTINEL)
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("PFACTORY_ALLOW_API_KEYS", raising=False)


def assert_clean(env, *, claude: bool) -> None:
    values = set(env.values())
    assert not values & set(CONTROL_PLANE.values())
    assert (CLAUDE_SENTINEL in values) is claude


def capture_exec(captured: dict):
    async def fake(*args, **kwargs):
        captured["env"] = kwargs.get("env")
        raise RuntimeError("stop after capture")

    return fake


def _sdk_env(options) -> dict:
    # The SDK builds the CLI env as {**os.environ, **options.env}.
    return {**os.environ, **options.env}


# --- SDK and MCP wiring ------------------------------------------------------


def _create_client(tmp_path, monkeypatch):
    captured: dict = {}
    spec_dir = tmp_path / "001-spec"
    spec_dir.mkdir()
    monkeypatch.setattr("core.client.ClaudeSDKClient", lambda options: captured.update(o=options))
    monkeypatch.setattr("core.client.require_auth_token", lambda: CLAUDE_SENTINEL)
    from core.client import create_client

    create_client(tmp_path, spec_dir, "claude-sonnet-4-5-20250929")
    return captured["o"]


def test_create_client_sdk_env(tmp_path, monkeypatch):
    assert_clean(_sdk_env(_create_client(tmp_path, monkeypatch)), claude=True)


def test_create_client_catalog_github_server_has_no_host_token(tmp_path, monkeypatch):
    monkeypatch.setattr("core.client.get_required_mcp_servers", lambda *a, **k: ["github"])
    options = _create_client(tmp_path, monkeypatch)
    github = options.mcp_servers["github"]
    assert CONTROL_PLANE["GITHUB_TOKEN"] not in (github.get("env") or {}).values()


def test_create_simple_client_sdk_env(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "core.simple_client.ClaudeSDKClient", lambda options: captured.update(o=options)
    )
    monkeypatch.setattr("core.simple_client.require_auth_token", lambda: CLAUDE_SENTINEL)
    from core.simple_client import create_simple_client

    create_simple_client(agent_type="qa_reviewer")
    assert_clean(_sdk_env(captured["o"]), claude=True)


# --- Backend agent runners ---------------------------------------------------


def test_codex_env_keeps_only_its_key(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sentinel-openai")
    monkeypatch.setenv("PFACTORY_ALLOW_API_KEYS", "1")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from providers.codex_agentic import CodexAgenticProvider

    env = CodexAgenticProvider._build_subprocess_env()
    assert_clean(env, claude=False)
    assert env["OPENAI_API_KEY"] == "sentinel-openai"
    assert env["CODEX_HOME"] == str(tmp_path / ".pfactory" / "codex-home")


def test_gemini_env_keeps_only_its_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sentinel-gemini")
    monkeypatch.setenv("PFACTORY_ALLOW_API_KEYS", "1")
    captured: dict = {}
    from providers.gemini_agentic import GeminiAgenticProvider

    provider = GeminiAgenticProvider(gemini_path="/bin/sh")
    provider._pending_prompt = "hi"

    async def run():
        with patch("asyncio.create_subprocess_exec", capture_exec(captured)):
            with pytest.raises(RuntimeError):
                async for _ in provider._run_gemini():
                    pass

    asyncio.run(run())
    assert_clean(captured["env"], claude=False)
    assert captured["env"]["GEMINI_API_KEY"] == "sentinel-gemini"


def test_copilot_env_keeps_only_its_token(monkeypatch):
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "sentinel-copilot")
    captured: dict = {}
    from providers.copilot_agentic import CopilotAgenticProvider

    provider = CopilotAgenticProvider()
    provider._pending_prompt = "hi"
    provider._resolved_path = "/bin/true"

    async def run():
        with patch("asyncio.create_subprocess_exec", capture_exec(captured)):
            with pytest.raises(RuntimeError):
                async for _ in provider._run_copilot():
                    pass

    asyncio.run(run())
    assert_clean(captured["env"], claude=False)
    assert captured["env"]["COPILOT_GITHUB_TOKEN"] == "sentinel-copilot"


def test_executor_bash_runs_in_a_clean_real_process(tmp_path):
    from tools.executor import ToolExecutor

    result = asyncio.run(ToolExecutor(tmp_path)._exec_bash({"command": "env"}))
    assert not result.is_error, result.content
    assert "PATH=" in result.content
    for value in [*CONTROL_PLANE.values(), CLAUDE_SENTINEL]:
        assert value not in result.content


def test_executor_grep_env(tmp_path):
    from tools.executor import ToolExecutor

    captured: dict = {}
    (tmp_path / "a.txt").write_text("x")

    async def run():
        with patch("asyncio.create_subprocess_exec", capture_exec(captured)):
            await ToolExecutor(tmp_path)._exec_grep({"pattern": "x"})

    try:
        asyncio.run(run())
    except RuntimeError:
        pass
    assert_clean(captured["env"], claude=False)


# --- Repository-code children ------------------------------------------------


def test_preflight_check_import_env(tmp_path):
    from agents.preflight_static import PreflightImport, check_import

    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured["env"] = kwargs["env"]
        return MagicMock(returncode=0, stdout="", stderr="")

    imp = PreflightImport(module="json", name=None, lineno=1)
    with patch("subprocess.run", fake_run):
        check_import(imp, project_dir=tmp_path)
    assert_clean(captured["env"], claude=False)
    assert captured["env"]["PYTHONPATH"] == str(tmp_path)


def test_orchestrator_children_env(tmp_path):
    from services.orchestrator import ServiceConfig, ServiceOrchestrator

    envs: list = []

    def fake_run(cmd, **kwargs):
        envs.append(kwargs["env"])
        return MagicMock(returncode=1 if "up" in cmd else 0, stderr="")

    orch = ServiceOrchestrator(tmp_path)
    orch._compose_file = tmp_path / "docker-compose.yml"
    with patch("subprocess.run", fake_run):
        orch._start_docker_compose(1)  # version probe + up
        orch._stop_docker_compose()  # version probe + down

    orch._services = [ServiceConfig(name="svc", type="local", startup_command="true")]

    def fake_popen(cmd, **kwargs):
        envs.append(kwargs["env"])
        return MagicMock()

    with patch("subprocess.Popen", fake_popen):
        orch._start_local_services(1)

    assert len(envs) >= 4
    for env in envs:
        assert_clean(env, claude=False)


# --- Web-server agent CLIs ---------------------------------------------------


def test_clarification_service_env(tmp_path):
    from server.services import clarification_service as svc
    from server.services.insights_providers.claude_provider import ClaudeProvider

    captured: dict = {}

    async def run():
        with (
            patch("asyncio.create_subprocess_exec", capture_exec(captured)),
            patch.object(ClaudeProvider, "_resolve_claude_token", return_value=(None, None, None)),
        ):
            await svc.generate_clarification_questions("t", "d", tmp_path)

    asyncio.run(run())
    assert_clean(captured["env"], claude=True)


def test_insights_generate_task_env(tmp_path):
    from server.services.insights_providers.claude_provider import ClaudeProvider
    from server.services.insights_service import InsightsService

    captured: dict = {}
    session = MagicMock(messages=[MagicMock(role="user", content="hi")], model_config=None)

    async def run():
        service = InsightsService.__new__(InsightsService)
        with (
            patch("asyncio.create_subprocess_exec", capture_exec(captured)),
            patch.object(ClaudeProvider, "_resolve_claude_token", return_value=(None, None, None)),
            patch.object(InsightsService, "get_current_session", return_value=session),
        ):
            await service.generate_task_from_chat(tmp_path, "p")

    asyncio.run(run())
    assert_clean(captured["env"], claude=True)


@pytest.mark.parametrize(
    ("module", "cls", "claude", "kept"),
    [
        ("claude_provider", "ClaudeProvider", True, None),
        ("codex_provider", "CodexProvider", False, ("OPENAI_API_KEY", "sentinel-openai")),
        ("gemini_provider", "GeminiProvider", False, ("GEMINI_API_KEY", "sentinel-gemini")),
    ],
)
def test_insights_provider_env(tmp_path, monkeypatch, module, cls, claude, kept):
    mod = importlib.import_module(f"server.services.insights_providers.{module}")
    provider_cls = getattr(mod, cls)
    if kept:
        monkeypatch.setenv(*kept)
    captured: dict = {}

    async def noop(*args, **kwargs):
        return None

    async def run():
        with (
            patch("asyncio.create_subprocess_exec", capture_exec(captured)),
            patch.object(mod, "broadcast_event", noop, create=True),
            patch.object(
                provider_cls, "_resolve_claude_token", return_value=(None, None, None), create=True
            ),
            patch.object(provider_cls, "_resolve_claude_path", return_value="claude", create=True),
        ):
            try:
                await provider_cls().send_message(tmp_path, "p", "hi", None, None, None)
            except Exception:
                pass

    asyncio.run(run())
    assert_clean(captured["env"], claude=claude)
    if kept:
        assert captured["env"][kept[0]] == kept[1]
