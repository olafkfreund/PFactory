"""Tests for ``apps/web-server/server/rmux/integration.py``.

The integration shim is what ``agent_service`` calls.  Its single job
is to gate everything on ``PFACTORY_RMUX_ENABLED`` so the bank-pilot
image's existing PTY behaviour is byte-for-byte unchanged when the
flag is unset.

The big-picture acceptance from issue #46:

  "Flag-off regression test verifies existing behavior unchanged."

That's exactly what this file enforces.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Flag parsing
# ---------------------------------------------------------------------------


class TestIsEnabled:
    """Truthy parsing of ``PFACTORY_RMUX_ENABLED``."""

    def test_unset_is_false(self, monkeypatch) -> None:
        from server.rmux.integration import is_enabled
        monkeypatch.delenv("PFACTORY_RMUX_ENABLED", raising=False)
        assert is_enabled() is False

    def test_empty_string_is_false(self, monkeypatch) -> None:
        from server.rmux.integration import is_enabled
        monkeypatch.setenv("PFACTORY_RMUX_ENABLED", "")
        assert is_enabled() is False

    def test_false_is_false(self, monkeypatch) -> None:
        from server.rmux.integration import is_enabled
        monkeypatch.setenv("PFACTORY_RMUX_ENABLED", "false")
        assert is_enabled() is False

    @pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes", "on"])
    def test_truthy_values(self, monkeypatch, value) -> None:
        from server.rmux.integration import is_enabled
        monkeypatch.setenv("PFACTORY_RMUX_ENABLED", value)
        assert is_enabled() is True


# ---------------------------------------------------------------------------
# Flag-off contract — the crucial regression guard
# ---------------------------------------------------------------------------


class TestFlagOffIsByteForByteUnchanged:
    """When the flag is unset:

      - ``create_if_enabled`` returns ``None`` without touching rmux
      - ``reap_if_enabled`` returns without raising

    This is the acceptance criterion in design §7:

      "PFACTORY_RMUX_ENABLED=false (default) leaves existing behavior
      byte-for-byte unchanged"
    """

    @pytest.mark.asyncio
    async def test_create_returns_none_when_flag_unset(self, monkeypatch, tmp_path) -> None:
        from server.rmux import integration
        monkeypatch.delenv("PFACTORY_RMUX_ENABLED", raising=False)
        # Patch get_registry to make sure it's NEVER called when off.
        with patch("server.rmux.integration.get_registry") as mock_get:
            result = await integration.create_if_enabled(
                spec_id="001", project_path=tmp_path, agent_cmd="true"
            )
            assert result is None
            mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_reap_is_noop_when_flag_unset(self, monkeypatch) -> None:
        from server.rmux import integration
        monkeypatch.delenv("PFACTORY_RMUX_ENABLED", raising=False)
        with patch("server.rmux.integration.get_registry") as mock_get:
            await integration.reap_if_enabled("001")
            mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# Flag-on path — wires through to the registry
# ---------------------------------------------------------------------------


class TestFlagOnInvokesRegistry:
    """When the flag is on, the shim must delegate to the registry."""

    @pytest.mark.asyncio
    async def test_create_calls_registry_create_for_task(
        self, monkeypatch, tmp_path
    ) -> None:
        from server.rmux import integration
        monkeypatch.setenv("PFACTORY_RMUX_ENABLED", "true")
        fake_fifo = tmp_path / "fake.fifo"
        mock_registry = type(
            "MockRegistry", (), {"create_for_task": AsyncMock(return_value=fake_fifo)}
        )()
        with patch("server.rmux.integration.get_registry", return_value=mock_registry):
            result = await integration.create_if_enabled(
                spec_id="001-feature", project_path=tmp_path, agent_cmd="ls"
            )
            assert result == fake_fifo
            assert mock_registry.create_for_task.await_count == 1
            # The worktree path must follow the .pfactory/worktrees/tasks/<spec> convention
            call_kwargs = mock_registry.create_for_task.await_args.kwargs
            assert call_kwargs["spec_id"] == "001-feature"
            assert call_kwargs["worktree_path"] == \
                tmp_path / ".pfactory" / "worktrees" / "tasks" / "001-feature"
            assert call_kwargs["agent_cmd"] == "ls"

    @pytest.mark.asyncio
    async def test_create_swallows_registry_exceptions(
        self, monkeypatch, tmp_path
    ) -> None:
        """Per design §6 failure-mode: rmux create errors must NOT take
        down task execution — they fall back to PTY + UI banner."""
        from server.rmux import integration
        monkeypatch.setenv("PFACTORY_RMUX_ENABLED", "true")
        mock_registry = type(
            "MockRegistry", (),
            {"create_for_task": AsyncMock(side_effect=RuntimeError("rmux died"))},
        )()
        with patch("server.rmux.integration.get_registry", return_value=mock_registry):
            # Must not raise
            result = await integration.create_if_enabled(
                spec_id="001", project_path=tmp_path, agent_cmd="true"
            )
            assert result is None  # fell back gracefully

    @pytest.mark.asyncio
    async def test_reap_calls_registry_reap_for_task(self, monkeypatch) -> None:
        from server.rmux import integration
        monkeypatch.setenv("PFACTORY_RMUX_ENABLED", "true")
        mock_registry = type(
            "MockRegistry", (), {"reap_for_task": AsyncMock(return_value=None)}
        )()
        with patch("server.rmux.integration.get_registry", return_value=mock_registry):
            await integration.reap_if_enabled("001-feature")
            mock_registry.reap_for_task.assert_awaited_once_with("001-feature")
