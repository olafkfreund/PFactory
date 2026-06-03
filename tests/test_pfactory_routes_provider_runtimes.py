"""Tests for the /api/provider-runtimes routes (#121 phase 2).

Mirrors the pfactory_templates route test: add apps/web-server to sys.path and
call the handlers directly with the provider_runtime layer mocked (no real CLI,
network, or install). Runs under the web-server venv / CI where FastAPI is
installed; conftest ignore-collect skips it in the backend-only venv.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import HTTPException  # noqa: E402
from server.routes import provider_runtimes as mod  # noqa: E402


def _status(**over):
    base = {
        "name": "codex", "kind": "npm", "managed": True, "installed": True,
        "installed_version": "1.0.0", "latest_version": "1.2.0",
        "pinned_version": None, "update_available": True,
    }
    base.update(over)
    return mod.pr.RuntimeStatus(**base)


def test_list_returns_runtime_status_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.pr, "get_all_status", lambda **k: [_status()])
    payload = mod.list_provider_runtimes(check_latest=True)
    rows = payload["runtimes"]
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "codex"
    assert row["installedVersion"] == "1.0.0"
    assert row["latestVersion"] == "1.2.0"
    assert row["updateAvailable"] is True


def test_pin_sets_and_returns_status(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    monkeypatch.setattr(mod.pr, "set_pin", lambda n, v: captured.update(name=n, version=v))
    monkeypatch.setattr(mod.pr, "get_status", lambda n, **k: _status(pinned_version="1.0.0"))
    out = mod.pin_provider_runtime("codex", mod._VersionBody(version="1.0.0"))
    assert captured == {"name": "codex", "version": "1.0.0"}
    assert out["pinnedVersion"] == "1.0.0"


def test_pin_unknown_runtime_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(n, v):
        raise KeyError("unknown provider runtime 'nope'")

    monkeypatch.setattr(mod.pr, "set_pin", _boom)
    with pytest.raises(HTTPException) as exc:
        mod.pin_provider_runtime("nope", mod._VersionBody(version=None))
    assert exc.value.status_code == 404


def test_update_returns_install_result(monkeypatch: pytest.MonkeyPatch) -> None:
    result = mod.pr.InstallResult(
        name="codex", command=["npm", "install", "-g", "@openai/codex@latest"],
        returncode=0, output="added 1 package", installed_version="1.2.0",
    )
    monkeypatch.setattr(mod.pr, "run_install", lambda n, v: result)
    out = mod.update_provider_runtime("codex", mod._VersionBody(version=None))
    assert out["ok"] is True
    assert out["installedVersion"] == "1.2.0"
    assert out["command"][0] == "npm"


def test_update_unmanaged_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(n, v):
        raise ValueError("ollama is user-managed")

    monkeypatch.setattr(mod.pr, "run_install", _boom)
    with pytest.raises(HTTPException) as exc:
        mod.update_provider_runtime("ollama", mod._VersionBody(version=None))
    assert exc.value.status_code == 400
