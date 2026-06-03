"""Tests for /api/pfactory/templates portal endpoints — Task 14 (#30).

Tests the route functions in
``apps/web-server/server/routes/pfactory_templates.py`` directly.
FastAPI is stubbed out via sys.modules injection.

Covered:
  - list_templates: happy path for pytest / jest / playwright (5 each)
  - list_templates: missing 'framework' query param → 400
  - list_templates: unknown framework → 404
  - get_template: happy path — metadata + body returned
  - get_template: unknown template name → 404
  - get_template: unknown framework → 404
  - get_template: path-traversal on both segments → 400
  - Template bodies contain ${...} placeholder markers
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

# ── FastAPI stub ─────────────────────────────────────────────────────────────
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

# ── sys.path setup ───────────────────────────────────────────────────────────
WEB_SERVER_PATH = Path(__file__).parent.parent / "apps" / "web-server"
BACKEND_PATH = Path(__file__).parent.parent / "apps" / "backend"
for _p in (str(WEB_SERVER_PATH), str(BACKEND_PATH)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


from server.routes.pfactory_templates import (  # noqa: E402
    _validate_segment,
    get_template,
    list_templates,
)

# ── Request mock helper ──────────────────────────────────────────────────────


class _MockRequest:
    """Minimal request mock that exposes a query_params mapping."""
    def __init__(self, **query_params):
        self.query_params = query_params


# ── _validate_segment ────────────────────────────────────────────────────────


def test_validate_segment_accepts_valid() -> None:
    _validate_segment("pytest", "framework")
    _validate_segment("login-flow.spec.ts.tmpl", "name")
    _validate_segment("function-pure.py.tmpl", "name")


def test_validate_segment_rejects_path_traversal() -> None:
    for bad in ("../etc/passwd", "../../x", "x/y", "a/../b"):
        with pytest.raises(_HTTPException) as exc:
            _validate_segment(bad, "framework")
        assert exc.value.status_code == 400, bad


def test_validate_segment_rejects_empty() -> None:
    with pytest.raises(_HTTPException) as exc:
        _validate_segment("", "framework")
    assert exc.value.status_code == 400


# ── list_templates — happy paths ─────────────────────────────────────────────


def _disk_template_count(fw: str) -> int:
    """Curated `templates/` + shipped `library/` .tmpl files for a framework.

    The route surfaces both tiers, so the expected count is disk-driven (stays
    correct as the platform library grows).
    """
    fw_dir = Path(__file__).resolve().parents[1] / "frameworks" / fw
    n = len(list((fw_dir / "templates").glob("*.tmpl")))
    lib = fw_dir / "library"
    if lib.is_dir():
        n += len(list(lib.glob("*.tmpl")))
    return n


@pytest.mark.parametrize("fw_name", ["pytest", "jest", "playwright"])
def test_list_templates_for_framework_returns_expected_count(fw_name: str) -> None:
    expected_count = _disk_template_count(fw_name)
    req = _MockRequest(framework=fw_name)
    resp = list_templates(req)
    payload = json.loads(resp.body)
    assert payload["framework"] == fw_name
    assert payload["count"] == expected_count
    assert len(payload["templates"]) == expected_count


def test_list_templates_rows_have_name_and_metadata() -> None:
    req = _MockRequest(framework="pytest")
    resp = list_templates(req)
    payload = json.loads(resp.body)
    for row in payload["templates"]:
        assert "name" in row
        assert "metadata" in row
        meta = row["metadata"]
        assert "description" in meta
        assert "requires_target" in meta
        assert "requires_auth" in meta
        assert "vars" in meta


def test_list_templates_sorted_alphabetically() -> None:
    req = _MockRequest(framework="pytest")
    resp = list_templates(req)
    payload = json.loads(resp.body)
    names = [r["name"] for r in payload["templates"]]
    assert names == sorted(names)


def test_list_templates_returns_json_response() -> None:
    req = _MockRequest(framework="playwright")
    resp = list_templates(req)
    assert resp.media_type == "application/json"
    assert resp.status_code == 200


# ── list_templates — error paths ─────────────────────────────────────────────


def test_list_templates_requires_framework_query_param() -> None:
    req = _MockRequest()  # no 'framework' key
    with pytest.raises(_HTTPException) as exc:
        list_templates(req)
    assert exc.value.status_code == 400
    assert "framework" in exc.value.detail


def test_list_templates_missing_param_via_plain_dict() -> None:
    """Also works when caller passes a plain dict (test convenience)."""
    with pytest.raises(_HTTPException) as exc:
        list_templates({})
    assert exc.value.status_code == 400


def test_list_templates_unknown_framework_returns_404() -> None:
    req = _MockRequest(framework="nonexistent-fw")
    with pytest.raises(_HTTPException) as exc:
        list_templates(req)
    assert exc.value.status_code == 404


def test_list_templates_path_traversal_in_framework_param_returns_400() -> None:
    req = _MockRequest(framework="../etc/passwd")
    with pytest.raises(_HTTPException) as exc:
        list_templates(req)
    assert exc.value.status_code == 400


# ── get_template — happy paths ───────────────────────────────────────────────


def test_get_template_returns_metadata_and_body_for_pytest() -> None:
    resp = get_template("pytest", "function-pure.py.tmpl")
    payload = json.loads(resp.body)
    assert payload["name"] == "function-pure.py.tmpl"
    assert payload["framework"] == "pytest"
    assert "metadata" in payload
    assert "body" in payload
    assert len(payload["body"]) > 0


def test_get_template_returns_metadata_and_body_for_jest() -> None:
    resp = get_template("jest", "function-pure.test.ts.tmpl")
    payload = json.loads(resp.body)
    assert payload["name"] == "function-pure.test.ts.tmpl"
    assert payload["framework"] == "jest"
    assert "body" in payload


def test_get_template_returns_metadata_and_body_for_playwright() -> None:
    resp = get_template("playwright", "login-flow.spec.ts.tmpl")
    payload = json.loads(resp.body)
    assert payload["name"] == "login-flow.spec.ts.tmpl"
    assert payload["framework"] == "playwright"
    assert "body" in payload


def test_get_template_returns_json_response() -> None:
    resp = get_template("playwright", "login-flow.spec.ts.tmpl")
    assert resp.media_type == "application/json"
    assert resp.status_code == 200


def test_get_template_metadata_has_description() -> None:
    resp = get_template("pytest", "function-pure.py.tmpl")
    payload = json.loads(resp.body)
    assert isinstance(payload["metadata"]["description"], str)
    assert len(payload["metadata"]["description"]) > 0


def test_get_template_metadata_has_vars_list() -> None:
    resp = get_template("pytest", "function-pure.py.tmpl")
    payload = json.loads(resp.body)
    assert isinstance(payload["metadata"]["vars"], list)


# ── get_template — body placeholder markers ──────────────────────────────────


def test_playwright_template_body_contains_var_placeholders() -> None:
    """Playwright templates use ${var} syntax for target URL, selectors, etc."""
    resp = get_template("playwright", "login-flow.spec.ts.tmpl")
    payload = json.loads(resp.body)
    body = payload["body"]
    assert "${" in body, "Expected ${var} placeholders in playwright login-flow template"


def test_playwright_template_body_references_target_base_url() -> None:
    resp = get_template("playwright", "login-flow.spec.ts.tmpl")
    payload = json.loads(resp.body)
    assert "${target_base_url}" in payload["body"]


@pytest.mark.parametrize("fw,tmpl", [
    ("pytest", "parametrize.py.tmpl"),
    ("jest", "react-component.test.tsx.tmpl"),
    ("playwright", "form-submit-validation.spec.ts.tmpl"),
])
def test_template_body_is_non_empty(fw: str, tmpl: str) -> None:
    resp = get_template(fw, tmpl)
    payload = json.loads(resp.body)
    assert len(payload["body"].strip()) > 0


# ── get_template — error paths ───────────────────────────────────────────────


def test_get_template_unknown_template_name_returns_404() -> None:
    with pytest.raises(_HTTPException) as exc:
        get_template("pytest", "nonexistent-template.py.tmpl")
    assert exc.value.status_code == 404


def test_get_template_unknown_framework_returns_404() -> None:
    with pytest.raises(_HTTPException) as exc:
        get_template("nonexistent-fw", "some-template.py.tmpl")
    assert exc.value.status_code == 404


@pytest.mark.parametrize("fw,tmpl", [
    ("../etc/passwd", "template.tmpl"),
    ("pytest", "../../etc/passwd"),
    ("a/b", "template.tmpl"),
    ("pytest", "a/b/template.tmpl"),
])
def test_get_template_rejects_path_traversal_on_both_segments(fw: str, tmpl: str) -> None:
    with pytest.raises(_HTTPException) as exc:
        get_template(fw, tmpl)
    assert exc.value.status_code == 400
