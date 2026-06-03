"""Tests for the ``PFACTORY_TEST_AGENT_CMD`` override in
``apps/web-server/server/services/agent_service.py``.

Epic #44 R4 — the Playwright suite needs to start tasks without firing
``run.py`` (which makes real LLM calls).  The override replaces the
agent subprocess command with whatever the test harness sets — typically
``sleep 300`` — while still letting the rmux create hook fire so the
fixture can probe ``/attach`` and assert session lifecycle behaviour.

Acceptance:
  1. Without the env var, ``cmd`` argv[0] is the Python executable.
  2. With the env var, ``cmd`` is replaced verbatim via shlex.split.
  3. The rmux create hook fires in BOTH cases.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def fake_proc() -> MagicMock:
    """Stand-in for ``asyncio.subprocess.Process`` — captures stdin/out
    references but performs no IO.  ``stdout`` / ``stderr`` are async
    readers that produce no data; ``wait`` returns 0 immediately."""
    proc = MagicMock()
    proc.pid = 12345
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    # ``_process_output`` drains stdout via ``readline()``; return empty
    # bytes once then None to terminate the loop cleanly.
    proc.stdout.readline = AsyncMock(side_effect=[b"", b""])
    proc.stderr.readline = AsyncMock(side_effect=[b"", b""])
    proc.wait = AsyncMock(return_value=0)
    proc.returncode = 0
    return proc


@pytest.fixture
def agent_service(tmp_path, monkeypatch):
    """Minimal AgentService instance with paths pointing at tmp_path.

    ``backend_path`` is a ``@property`` reading from settings — we
    override it at the class level via monkeypatch so the constructor
    works with default settings.
    """
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "run.py").touch()  # not actually executed under override

    project = tmp_path / "project"
    (project / ".pfactory" / "specs" / "001-test").mkdir(parents=True)

    from server.services.agent_service import AgentService

    # Replace the property with a class-level attribute that returns the
    # tmp backend; reverted automatically when the test ends.
    monkeypatch.setattr(AgentService, "backend_path", backend, raising=False)

    svc = AgentService()
    return svc, project, "001-test"


class TestTestAgentCmdOverride:
    """Cover the cmd-substitution path."""

    @pytest.mark.asyncio
    async def test_override_replaces_cmd(
        self, agent_service, fake_proc, monkeypatch
    ) -> None:
        """When PFACTORY_TEST_AGENT_CMD is set, the cmd handed to
        ``create_subprocess_exec`` is ``shlex.split`` of the env var,
        NOT the python+run.py default."""
        svc, project, spec_id = agent_service
        monkeypatch.setenv("PFACTORY_TEST_AGENT_CMD", "sleep 999")

        captured: dict[str, Any] = {}

        async def _fake_exec(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return fake_proc

        rmux_mock = AsyncMock()

        with patch(
            "asyncio.create_subprocess_exec", side_effect=_fake_exec
        ), patch(
            "pty.openpty", return_value=(7, 8)
        ), patch(
            "os.close"
        ), patch(
            "server.rmux.integration.create_if_enabled", new=rmux_mock
        ):
            await svc.start_task_execution(
                task_id=f"proj1:{spec_id}",
                project_path=project,
                spec_id=spec_id,
                auto_continue=True,
            )

        # The override won — cmd[0] is "sleep", not the Python executable.
        assert captured["args"] == ("sleep", "999"), (
            f"expected ('sleep', '999'), got {captured['args']!r}"
        )

        # rmux create hook still fired — that's the whole point of this
        # override: rmux session exists, Playwright can probe /attach.
        rmux_mock.assert_awaited_once()
        rmux_call_args = rmux_mock.await_args
        assert rmux_call_args.args[0] == spec_id, (
            f"rmux_create called with wrong spec_id: {rmux_call_args.args}"
        )

    @pytest.mark.asyncio
    async def test_no_override_uses_python_runpy(
        self, agent_service, fake_proc, monkeypatch
    ) -> None:
        """Without the env var, cmd[0] is sys.executable (python)."""
        svc, project, spec_id = agent_service
        monkeypatch.delenv("PFACTORY_TEST_AGENT_CMD", raising=False)

        captured: dict[str, Any] = {}

        async def _fake_exec(*args, **kwargs):
            captured["args"] = args
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

        import sys
        assert captured["args"][0] == sys.executable, (
            f"expected {sys.executable!r}, got {captured['args'][0]!r}"
        )
        # ...and run.py is right after it.
        assert captured["args"][1].endswith("run.py"), (
            f"expected run.py in cmd, got {captured['args']!r}"
        )

    @pytest.mark.asyncio
    async def test_empty_override_falls_back_to_runpy(
        self, agent_service, fake_proc, monkeypatch
    ) -> None:
        """Whitespace-only PFACTORY_TEST_AGENT_CMD is treated as unset
        so prod misconfigurations don't accidentally noop the agent."""
        svc, project, spec_id = agent_service
        monkeypatch.setenv("PFACTORY_TEST_AGENT_CMD", "   ")

        captured: dict[str, Any] = {}

        async def _fake_exec(*args, **kwargs):
            captured["args"] = args
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

        import sys
        assert captured["args"][0] == sys.executable
