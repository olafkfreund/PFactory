"""Tests for the Claude Code Remote Control wiring in
``apps/web-server/server/services/agent_service.py``.

When per-task ``enableRemoteControl`` is set (or the project-level
``remoteControlByDefault`` is set), the spawned ``claude`` subprocess
must:

  1. Get ``--remote-control "PFactory: <spec-id>"`` appended to its cmd
  2. Have ``CLAUDE_CODE_OAUTH_TOKEN`` + ``ANTHROPIC_AUTH_TOKEN`` scrubbed
     from env so the subprocess falls back to
     ``~/.claude/.credentials.json`` (Remote Control requires
     full-scope auth — setup-token tokens are rejected).

Default behaviour (no opt-in): neither change occurs.

Lives under ``tests/rmux/`` because that's where the existing
``test_test_mode.py`` AgentService tests live; same conftest +
sys.path bootstrap applies.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures — same shape as test_test_mode.py
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_proc() -> MagicMock:
    """Stand-in for ``asyncio.subprocess.Process`` — no IO, exits clean."""
    proc = MagicMock()
    proc.pid = 23456
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.stdout.readline = AsyncMock(side_effect=[b"", b""])
    proc.stderr.readline = AsyncMock(side_effect=[b"", b""])
    proc.wait = AsyncMock(return_value=0)
    proc.returncode = 0
    return proc


@pytest.fixture
def agent_service(tmp_path, monkeypatch):
    """Minimal AgentService whose backend_path + project paths point at tmp."""
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "run.py").touch()

    project = tmp_path / "project"
    spec_dir = project / ".pfactory" / "specs" / "001-test"
    spec_dir.mkdir(parents=True)

    from server.services.agent_service import AgentService

    monkeypatch.setattr(AgentService, "backend_path", backend, raising=False)
    return AgentService(), project, "001-test", spec_dir


def _write_task_metadata(spec_dir, **fields):
    (spec_dir / "task_metadata.json").write_text(json.dumps(fields))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRemoteControlPerTaskToggle:
    """When task_metadata.json sets enableRemoteControl=true, the cmd and
    env are modified accordingly.
    """

    @pytest.mark.asyncio
    async def test_appends_remote_control_flag(
        self, agent_service, fake_proc, monkeypatch
    ) -> None:
        svc, project, spec_id, spec_dir = agent_service
        _write_task_metadata(spec_dir, enableRemoteControl=True)

        # Ensure the OAuth token is set BEFORE the call so we can verify the
        # scrub happened.  agent_service sets this inside its flow if a token
        # is resolvable — for the test, pre-set in the env so we know the
        # scrub path is exercised.
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token-sk-ant-oat01-FAKE")
        monkeypatch.delenv("PFACTORY_TEST_AGENT_CMD", raising=False)

        captured: dict[str, Any] = {}

        async def _fake_exec(*args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs.get("env", {})
            return fake_proc

        with patch(
            "asyncio.create_subprocess_exec", side_effect=_fake_exec
        ), patch(
            "pty.openpty", return_value=(7, 8)
        ), patch(
            "os.close"
        ), patch(
            "server.rmux.integration.create_if_enabled", new=AsyncMock()
        ):
            await svc.start_task_execution(
                task_id=f"proj1:{spec_id}",
                project_path=project,
                spec_id=spec_id,
                auto_continue=True,
            )

        # The cmd tuple should contain --remote-control followed by the
        # session-name argument
        cmd_tuple = captured["args"]
        assert "--remote-control" in cmd_tuple, (
            f"--remote-control not appended; got {cmd_tuple!r}"
        )
        idx = cmd_tuple.index("--remote-control")
        assert cmd_tuple[idx + 1] == f"PFactory: {spec_id}", (
            f"session name wrong; got {cmd_tuple[idx + 1]!r}"
        )

    @pytest.mark.asyncio
    async def test_scrubs_oauth_env_vars(
        self, agent_service, fake_proc, monkeypatch
    ) -> None:
        """With Remote Control on, the subprocess env must NOT carry
        CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_AUTH_TOKEN — Remote Control
        rejects setup-token tokens; full-scope auth must come from
        ~/.claude/.credentials.json instead.
        """
        svc, project, spec_id, spec_dir = agent_service
        _write_task_metadata(spec_dir, enableRemoteControl=True)
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token-sk-ant-oat01-FAKE")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token-claude-ai-FAKE")
        monkeypatch.delenv("PFACTORY_TEST_AGENT_CMD", raising=False)

        captured: dict[str, Any] = {}

        async def _fake_exec(*args, **kwargs):
            captured["env"] = dict(kwargs.get("env", {}))
            return fake_proc

        with patch(
            "asyncio.create_subprocess_exec", side_effect=_fake_exec
        ), patch(
            "pty.openpty", return_value=(7, 8)
        ), patch(
            "os.close"
        ), patch(
            "server.rmux.integration.create_if_enabled", new=AsyncMock()
        ):
            await svc.start_task_execution(
                task_id=f"proj1:{spec_id}",
                project_path=project,
                spec_id=spec_id,
                auto_continue=True,
            )

        sub_env = captured["env"]
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in sub_env, (
            f"OAuth token leaked into subprocess env: {sub_env.get('CLAUDE_CODE_OAUTH_TOKEN')!r}"
        )
        assert "ANTHROPIC_AUTH_TOKEN" not in sub_env, (
            f"Anthropic auth token leaked into subprocess env: "
            f"{sub_env.get('ANTHROPIC_AUTH_TOKEN')!r}"
        )


class TestRemoteControlDefaultOff:
    """When neither task_metadata nor project settings enable Remote
    Control, cmd + env are unchanged from the standard flow.
    """

    @pytest.mark.asyncio
    async def test_no_flag_when_disabled(
        self, agent_service, fake_proc, monkeypatch
    ) -> None:
        svc, project, spec_id, spec_dir = agent_service
        # No task_metadata.json written — defaults apply
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token-sk-ant-oat01-FAKE")
        monkeypatch.delenv("PFACTORY_TEST_AGENT_CMD", raising=False)

        captured: dict[str, Any] = {}

        async def _fake_exec(*args, **kwargs):
            captured["args"] = args
            captured["env"] = dict(kwargs.get("env", {}))
            return fake_proc

        with patch(
            "asyncio.create_subprocess_exec", side_effect=_fake_exec
        ), patch(
            "pty.openpty", return_value=(7, 8)
        ), patch(
            "os.close"
        ), patch(
            "server.rmux.integration.create_if_enabled", new=AsyncMock()
        ):
            await svc.start_task_execution(
                task_id=f"proj1:{spec_id}",
                project_path=project,
                spec_id=spec_id,
                auto_continue=True,
            )

        assert "--remote-control" not in captured["args"], (
            f"--remote-control should NOT appear in cmd when disabled; "
            f"got {captured['args']!r}"
        )
        # OAuth token should be present (or at least not deliberately scrubbed)
        assert captured["env"].get("CLAUDE_CODE_OAUTH_TOKEN") is not None or \
               "CLAUDE_CODE_OAUTH_TOKEN" in captured["env"], (
            "When Remote Control is OFF, the OAuth token env path must remain "
            "the standard one (token in env)."
        )
