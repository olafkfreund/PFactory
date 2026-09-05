"""Legacy ``API_TOKEN`` matches must go through the shared helper.

``server/auth.py::_is_legacy_api_token`` is the one legacy-token comparison
in the codebase: it uses ``hmac.compare_digest`` (no timing oracle, Factory#324
M1) and refuses to match anything when ``API_TOKEN`` is unset. Three call
sites used to compare with plain ``==`` instead:

* ``server/mcp_stdio/auth.py`` — the legacy admin wildcard.
* ``server/routes/files.py`` — the ``/serve`` Authorization header, and the
  ``?token=`` query param.

The header site was the sharp one: ``auth_header[7:]`` is ``""`` for a bare
``Authorization: Bearer `` header, so with an empty configured ``API_TOKEN``
``"" == ""`` authenticated an EMPTY credential on the file-serving endpoint.

These tests pin the behaviour AND the call path: each site is exercised with
the shared helper stubbed out in the calling module's namespace, so
reintroducing ``==`` at any of the three sites fails a named test here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "apps" / "web-server", _ROOT / "apps" / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from server import config  # noqa: E402
from server.mcp_stdio import auth as mcp_stdio_auth  # noqa: E402
from server.routes import files as files_routes  # noqa: E402

LEGACY_TOKEN = "legacy-admin-token-for-test"


def _pin_settings(monkeypatch, *, api_token: str) -> None:
    """Force auth ON with a known configured legacy token."""
    settings = config.get_settings()
    monkeypatch.setattr(settings, "API_TOKEN", api_token)
    monkeypatch.setattr(settings, "DISABLE_AUTH", False)


def _serve_request(auth_header: str | None) -> Request:
    headers = []
    if auth_header is not None:
        headers.append((b"authorization", auth_header.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/files/serve",
            "headers": headers,
            "query_string": b"",
        }
    )


# ── /serve: an empty credential is never valid ───────────────────────


def test_bare_bearer_header_rejected_when_api_token_unset(monkeypatch):
    """The headline regression: empty API_TOKEN + ``Bearer `` must NOT auth.

    ``auth_header[7:]`` is ``""`` here. Under the old ``header_token ==
    settings.API_TOKEN`` this compared ``"" == ""`` and served the file.
    """
    _pin_settings(monkeypatch, api_token="")
    assert files_routes._validate_serve_token(_serve_request("Bearer "), "") is False


def test_bare_bearer_header_rejected_when_api_token_set(monkeypatch):
    _pin_settings(monkeypatch, api_token=LEGACY_TOKEN)
    assert files_routes._validate_serve_token(_serve_request("Bearer "), "") is False


def test_empty_query_token_rejected_when_api_token_unset(monkeypatch):
    _pin_settings(monkeypatch, api_token="")
    assert files_routes._validate_serve_token(_serve_request(None), "") is False


def test_wrong_token_rejected(monkeypatch):
    _pin_settings(monkeypatch, api_token=LEGACY_TOKEN)
    assert (
        files_routes._validate_serve_token(_serve_request("Bearer nope"), "") is False
    )


def test_correct_token_still_accepted_via_header_and_query(monkeypatch):
    """The fix must not break the legitimate legacy path."""
    _pin_settings(monkeypatch, api_token=LEGACY_TOKEN)
    assert (
        files_routes._validate_serve_token(_serve_request(f"Bearer {LEGACY_TOKEN}"), "")
        is True
    )
    assert (
        files_routes._validate_serve_token(_serve_request(None), LEGACY_TOKEN) is True
    )


# ── Call path: each site goes through the shared helper ──────────────


def test_serve_header_path_calls_shared_helper(monkeypatch):
    """Stub the helper to deny; a byte-identical token must still be refused.

    Fails if ``files.py`` ever compares the header token with ``==`` again.
    """
    _pin_settings(monkeypatch, api_token=LEGACY_TOKEN)
    monkeypatch.setattr(files_routes, "_is_legacy_api_token", lambda token: False)
    assert (
        files_routes._validate_serve_token(_serve_request(f"Bearer {LEGACY_TOKEN}"), "")
        is False
    )


def test_serve_query_path_calls_shared_helper(monkeypatch):
    """Same, for the ``?token=`` query-param branch."""
    _pin_settings(monkeypatch, api_token=LEGACY_TOKEN)
    monkeypatch.setattr(files_routes, "_is_legacy_api_token", lambda token: False)
    assert (
        files_routes._validate_serve_token(_serve_request(None), LEGACY_TOKEN) is False
    )


def _mcp_probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    async def probe(_=Depends(mcp_stdio_auth.require_acw_scope("mcp:read"))):
        return {"ok": True}

    return app


def test_mcp_stdio_legacy_path_calls_shared_helper(monkeypatch):
    """Deny in the helper → the legacy wildcard must not authenticate.

    Falls through to the ``acw_`` lookup, which rejects → 401. Fails if
    ``mcp_stdio/auth.py`` compares against ``settings.API_TOKEN`` with ``==``.
    """
    _pin_settings(monkeypatch, api_token=LEGACY_TOKEN)
    monkeypatch.setattr(mcp_stdio_auth, "_is_legacy_api_token", lambda token: False)

    async def _reject(_header):
        raise mcp_stdio_auth.mcp_remote_auth.MCPAuthError("Invalid API key")

    monkeypatch.setattr(mcp_stdio_auth.mcp_remote_auth, "authenticate", _reject)
    client = TestClient(_mcp_probe_app())
    r = client.get("/probe", headers={"Authorization": f"Bearer {LEGACY_TOKEN}"})
    assert r.status_code == 401, r.text


def test_mcp_stdio_legacy_token_still_accepted(monkeypatch):
    _pin_settings(monkeypatch, api_token=LEGACY_TOKEN)
    client = TestClient(_mcp_probe_app())
    r = client.get("/probe", headers={"Authorization": f"Bearer {LEGACY_TOKEN}"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}


def test_mcp_stdio_rejects_bare_bearer_when_api_token_unset(monkeypatch):
    _pin_settings(monkeypatch, api_token="")
    client = TestClient(_mcp_probe_app())
    r = client.get("/probe", headers={"Authorization": "Bearer "})
    assert r.status_code == 401, r.text
