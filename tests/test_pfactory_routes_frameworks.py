"""Tests for /api/pfactory/frameworks portal endpoints — Task 14 (#30).

Tests the route functions in
``apps/web-server/server/routes/pfactory_frameworks.py`` directly, without
running a real HTTP stack. FastAPI is stubbed out via sys.modules injection
(same pattern as test_pfactory_routes_tasks.py).

Covered:
  - list_frameworks: sorted result, fields present, all 3 registered frameworks
  - get_framework: full descriptor fields for pytest/jest/playwright
  - get_framework: 404 for unknown name
  - get_framework: 400 for path-traversal / invalid name
  - list_frameworks: deterministic ordering across repeated calls
  - Descriptor field completeness (name, language, lanes, coverage_strategy,
    version_range, runtime, templates keys present)
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

# ── FastAPI stub (same pattern as test_pfactory_routes_tasks.py) ───────────
if "fastapi" not in sys.modules:
    _fastapi = types.ModuleType("fastapi")

    class _APIRouter:
        def __init__(self, *a, **kw): pass
        def get(self, *a, **kw):
            def _d(fn): return fn
            return _d
        def websocket(self, *a, **kw):
            def _d(fn): return fn
            return _d

    class _HTTPException(Exception):
        def __init__(self, status_code: int, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class _Response:
        def __init__(self, content=b"", media_type: str = "", status_code: int = 200) -> None:
            self.content = (
                content if isinstance(content, (bytes, bytearray))
                else str(content).encode()
            )
            self.media_type = media_type
            self.status_code = status_code
            self.body = self.content

    class _WebSocket:
        async def accept(self): pass
        async def send_text(self, _t: str): pass
        async def receive_text(self) -> str: return ""
        async def close(self, code: int = 1000, reason: str = ""): pass

    class _WebSocketDisconnect(Exception):
        pass

    _status = types.ModuleType("fastapi.status")
    _status.HTTP_400_BAD_REQUEST = 400
    _status.HTTP_404_NOT_FOUND = 404
    _status.HTTP_500_INTERNAL_SERVER_ERROR = 500

    _fastapi.APIRouter = _APIRouter
    _fastapi.HTTPException = _HTTPException
    _fastapi.Response = _Response
    _fastapi.WebSocket = _WebSocket
    _fastapi.WebSocketDisconnect = _WebSocketDisconnect
    _fastapi.status = _status
    sys.modules["fastapi"] = _fastapi
    sys.modules["fastapi.status"] = _status

from fastapi import HTTPException as _HTTPException  # noqa: E402

# ── sys.path setup ──────────────────────────────────────────────────────────
WEB_SERVER_PATH = Path(__file__).parent.parent / "apps" / "web-server"
BACKEND_PATH = Path(__file__).parent.parent / "apps" / "backend"
for _p in (str(WEB_SERVER_PATH), str(BACKEND_PATH)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


from server.routes.pfactory_frameworks import (  # noqa: E402
    _descriptor_to_dict,
    _load_registry,
    _summary_row,
    _validate_name,
    get_framework,
    list_frameworks,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _registry():
    """Load the real registry once per process (cached by Python)."""
    return _load_registry()


# ── _validate_name ──────────────────────────────────────────────────────────


def test_validate_name_accepts_simple() -> None:
    _validate_name("pytest")
    _validate_name("jest")
    _validate_name("playwright")
    _validate_name("my-framework.v2")


def test_validate_name_rejects_path_traversal() -> None:
    for bad in ("../etc/passwd", "../../x", "x/y", "a/../b"):
        with pytest.raises(_HTTPException) as exc:
            _validate_name(bad)
        assert exc.value.status_code == 400, bad


def test_validate_name_rejects_empty() -> None:
    with pytest.raises(_HTTPException) as exc:
        _validate_name("")
    assert exc.value.status_code == 400


def test_validate_name_rejects_spaces() -> None:
    with pytest.raises(_HTTPException) as exc:
        _validate_name("foo bar")
    assert exc.value.status_code == 400


# ── list_frameworks ─────────────────────────────────────────────────────────


def test_list_frameworks_returns_registered_frameworks() -> None:
    result = list_frameworks()
    names = {f["name"] for f in result["frameworks"]}
    # v0.2 spine + the #110 additions (Vitest unit, Cypress browser).
    assert {"pytest", "jest", "playwright", "vitest", "cypress"} <= names
    # count is self-consistent with the rows, whatever the registry size.
    assert result["count"] == len(result["frameworks"])
    assert result["count"] == len(names)


def test_list_frameworks_sorted_alphabetically() -> None:
    result = list_frameworks()
    names = [f["name"] for f in result["frameworks"]]
    assert names == sorted(names)


def test_list_frameworks_rows_contain_required_fields() -> None:
    result = list_frameworks()
    for row in result["frameworks"]:
        assert "name" in row
        assert "language" in row
        assert "coverage_strategy" in row
        assert "lanes" in row
        assert "version_range" in row
        assert "template_count" in row


def test_list_frameworks_sorts_results_deterministically() -> None:
    """Same JSON bytes on two consecutive calls."""
    r1 = list_frameworks()
    r2 = list_frameworks()
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


def test_list_frameworks_count_matches_list_length() -> None:
    result = list_frameworks()
    assert result["count"] == len(result["frameworks"])


def test_list_frameworks_lanes_are_strings() -> None:
    result = list_frameworks()
    for row in result["frameworks"]:
        for lane in row["lanes"]:
            assert isinstance(lane, str), f"lane {lane!r} is not a string"


# ── get_framework ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("fw_name", ["pytest", "jest", "playwright"])
def test_get_framework_returns_full_descriptor_for_known_name(fw_name: str) -> None:
    resp = get_framework(fw_name)
    payload = json.loads(resp.body)
    assert payload["name"] == fw_name
    # Required descriptor fields
    assert "language" in payload
    assert "lanes" in payload
    assert "version_range" in payload
    assert "runtime" in payload
    assert "manifest_signals" in payload
    assert "test_path_conventions" in payload
    assert "templates" in payload
    assert "coverage_strategy" in payload
    assert "context_block" in payload
    assert "evaluator_hooks" in payload


def test_get_framework_returns_404_for_unknown_name() -> None:
    with pytest.raises(_HTTPException) as exc:
        get_framework("nonexistent-framework")
    assert exc.value.status_code == 404


@pytest.mark.parametrize("bad", ["../etc/passwd", "../../x", "a/b", "a b"])
def test_get_framework_rejects_path_traversal(bad: str) -> None:
    with pytest.raises(_HTTPException) as exc:
        get_framework(bad)
    assert exc.value.status_code == 400


def test_get_framework_response_is_valid_json() -> None:
    resp = get_framework("pytest")
    assert resp.media_type == "application/json"
    parsed = json.loads(resp.body)
    assert isinstance(parsed, dict)


def test_get_framework_pytest_uses_cobertura_coverage() -> None:
    resp = get_framework("pytest")
    payload = json.loads(resp.body)
    assert payload["coverage_strategy"] == "cobertura"


def test_get_framework_playwright_uses_skip_coverage() -> None:
    resp = get_framework("playwright")
    payload = json.loads(resp.body)
    assert payload["coverage_strategy"] == "skip"


def test_get_framework_jest_has_typescript_language() -> None:
    resp = get_framework("jest")
    payload = json.loads(resp.body)
    assert payload["language"] == "typescript"


def test_get_framework_pytest_has_python_language() -> None:
    resp = get_framework("pytest")
    payload = json.loads(resp.body)
    assert payload["language"] == "python"


def test_get_framework_runtime_has_image_and_entrypoint() -> None:
    resp = get_framework("pytest")
    payload = json.loads(resp.body)
    rt = payload["runtime"]
    assert "image" in rt
    assert "entrypoint" in rt
    assert isinstance(rt["entrypoint"], list)


def test_get_framework_descriptor_to_dict_produces_serialisable_payload() -> None:
    """_descriptor_to_dict must not leave Lane/RuntimeSpec objects in the output."""
    registry = _registry()
    for name, desc in registry.items():
        raw = _descriptor_to_dict(desc)
        # Should round-trip through JSON without error
        serialised = json.dumps(raw)
        parsed = json.loads(serialised)
        assert parsed["name"] == name
