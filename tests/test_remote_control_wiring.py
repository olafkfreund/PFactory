#!/usr/bin/env python3
"""Test that ``create_client(remote_control_session=...)`` correctly
threads through to ``ClaudeAgentOptions.extra_args["remote-control"]``
(#149).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def test_create_client_injects_remote_control_into_extra_args(tmp_path: Path):
    """When ``remote_control_session`` is set, the SDK options must
    carry ``extra_args={"remote-control": session_name}`` so the
    underlying ``claude`` CLI registers the Remote Control session."""
    captured: dict = {}

    def _capture(options):
        captured["options"] = options
        return MagicMock()

    spec_dir = tmp_path / "001-test-spec"
    spec_dir.mkdir()
    project_dir = tmp_path

    with patch("core.client.ClaudeSDKClient", side_effect=_capture), \
         patch("core.client.require_auth_token", return_value="sk-ant-oat01-test"):
        from core.client import create_client
        create_client(
            project_dir=project_dir,
            spec_dir=spec_dir,
            model="claude-sonnet-4-5-20250929",
            agent_type="coder",
            remote_control_session="PFactory: 001-test-spec",
        )

    options = captured.get("options")
    assert options is not None, "ClaudeSDKClient was never instantiated"
    extra = getattr(options, "extra_args", None)
    assert isinstance(extra, dict), f"extra_args must be a dict, got {type(extra)}"
    assert extra.get("remote-control") == "PFactory: 001-test-spec", (
        f"Expected extra_args['remote-control']='PFactory: 001-test-spec', "
        f"got {extra!r}"
    )


def test_create_client_omits_remote_control_when_session_none(tmp_path: Path):
    """When ``remote_control_session`` is None (the default), the SDK
    options must NOT carry the remote-control key — that key registers
    a Remote Control session on Anthropic's API and shouldn't fire
    for tasks where the user hasn't opted in."""
    captured: dict = {}

    def _capture(options):
        captured["options"] = options
        return MagicMock()

    spec_dir = tmp_path / "001-test-spec"
    spec_dir.mkdir()

    with patch("core.client.ClaudeSDKClient", side_effect=_capture), \
         patch("core.client.require_auth_token", return_value="sk-ant-oat01-test"):
        from core.client import create_client
        create_client(
            project_dir=tmp_path,
            spec_dir=spec_dir,
            model="claude-sonnet-4-5-20250929",
            agent_type="coder",
        )

    options = captured.get("options")
    extra = getattr(options, "extra_args", None) or {}
    assert "remote-control" not in extra, (
        f"extra_args must not contain 'remote-control' when "
        f"remote_control_session is None — got {extra!r}"
    )
