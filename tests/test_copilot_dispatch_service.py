"""Copilot cloud-agent dispatch service (epic #87 / #88, Component 2).

Exercises dispatch / PR-discovery / enablement with an injected fake ``gh``
runner — no subprocess, no network. The service has no ``server.*`` imports, so
it is loaded by file path and runs in the backend test venv directly.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SERVICE_PATH = (
    Path(__file__).parent.parent
    / "apps" / "web-server" / "server" / "services" / "copilot_dispatch_service.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("cds_under_test", _SERVICE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cds = _load_module()


def _ok(stdout: str = "") -> "subprocess.CompletedProcess[str]":
    def runner(args):
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
    return runner


def _fail(stderr: str = "boom"):
    def runner(args):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr=stderr)
    return runner


# ---------------------------------------------------------------------------
# Enablement
# ---------------------------------------------------------------------------


def test_dispatch_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PFACTORY_COPILOT_DISPATCH_ENABLED", raising=False)
    assert cds.CopilotDispatchService.is_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_dispatch_enabled_truthy(monkeypatch, val):
    monkeypatch.setenv("PFACTORY_COPILOT_DISPATCH_ENABLED", val)
    assert cds.CopilotDispatchService.is_enabled() is True


def test_has_dispatch_label():
    assert cds.CopilotDispatchService.has_dispatch_label(["copilot:delegate"]) is True
    assert cds.CopilotDispatchService.has_dispatch_label(["bug"]) is False
    assert cds.CopilotDispatchService.has_dispatch_label([]) is False


# ---------------------------------------------------------------------------
# dispatch()
# ---------------------------------------------------------------------------


def test_dispatch_assigns_copilot_agent():
    captured = {}

    def runner(args):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    svc = cds.CopilotDispatchService(runner=runner)
    meta = svc.dispatch("owner/repo", 42)
    # gh PATCH assigns the bot handle to the issue
    assert "PATCH" in captured["args"]
    assert "/repos/owner/repo/issues/42" in captured["args"]
    assert any("copilot-swe-agent[bot]" in a for a in captured["args"])
    assert meta["issue_number"] == 42
    assert meta["agent_handle"] == "copilot-swe-agent[bot]"
    assert meta["enabled"] is True
    assert meta["pr_number"] is None
    assert meta["reviewed"] is False


def test_dispatch_raises_on_gh_failure():
    svc = cds.CopilotDispatchService(runner=_fail("missing copilot scope"))
    with pytest.raises(RuntimeError, match="missing copilot scope"):
        svc.dispatch("owner/repo", 1)


# ---------------------------------------------------------------------------
# find_copilot_pr()
# ---------------------------------------------------------------------------


def test_find_copilot_pr_returns_number():
    svc = cds.CopilotDispatchService(runner=_ok("7\n"))
    assert svc.find_copilot_pr("owner/repo", 42) == 7


def test_find_copilot_pr_none_when_absent():
    svc = cds.CopilotDispatchService(runner=_ok("null\n"))
    assert svc.find_copilot_pr("owner/repo", 42) is None
    svc_empty = cds.CopilotDispatchService(runner=_ok(""))
    assert svc_empty.find_copilot_pr("owner/repo", 42) is None


def test_find_copilot_pr_none_on_gh_failure():
    svc = cds.CopilotDispatchService(runner=_fail())
    assert svc.find_copilot_pr("owner/repo", 42) is None


def test_find_copilot_pr_filters_by_issue_and_bot():
    captured = {}

    def runner(args):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="3", stderr="")

    cds.CopilotDispatchService(runner=runner).find_copilot_pr("o/r", 99)
    jq = " ".join(captured["args"])
    assert "copilot-swe-agent" in jq
    assert '.user.type == "Bot"' in jq
    assert "#99" in jq
