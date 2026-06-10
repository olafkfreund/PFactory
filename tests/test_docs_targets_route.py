#!/usr/bin/env python3
"""Tests for plan→docs P4b — docs-target connections route + emit wiring.

Covers the route's pure helpers (no DB/app needed):
- ``_mask_token`` masks all but the last 4 chars
- ``_probe`` builds the right URL per kind and maps HTTP/transport outcomes
- the ``DocsTargetConnection`` model imports cleanly with the expected columns
- ``plan_pipeline._load_docs_connections`` maps ORM rows → emit-dict shape and
  degrades to ``None`` (env fallback) when no user / no rows
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


def test_model_imports_with_expected_columns():
    from server.database import DocsTargetConnection

    cols = {c.name for c in DocsTargetConnection.__table__.columns}
    assert {
        "id", "user_id", "kind", "label", "base_url",
        "api_token", "space", "enabled_by_default",
        "created_at", "updated_at", "last_used_at",
    } <= cols
    assert DocsTargetConnection.__tablename__ == "docs_target_connections"


# ---------------------------------------------------------------------------
# _mask_token
# ---------------------------------------------------------------------------


def test_mask_token():
    from server.routes.docs_targets import _mask_token

    assert _mask_token(None) is None
    assert _mask_token("") is None
    assert _mask_token("abcd") == "****"
    assert _mask_token("supersecrettoken") == "************oken"


# ---------------------------------------------------------------------------
# _probe — URL shape + outcome mapping (urlopen mocked)
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, code: int):
        self._code = code

    def getcode(self) -> int:
        return self._code

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_probe_backstage_hits_catalog_and_sends_bearer():
    from server.routes import docs_targets as dt

    seen = {}

    def fake_urlopen(req, timeout=10):
        seen["url"] = req.full_url
        seen["auth"] = req.headers.get("Authorization")
        return _Resp(200)

    with patch.object(dt.urllib.request, "urlopen", fake_urlopen):
        out = dt._probe("backstage", "https://bs/", "tok", None)

    assert out.ok is True
    assert out.status_code == 200
    assert seen["url"] == "https://bs/api/catalog/entities?limit=1"
    assert seen["auth"] == "Bearer tok"


def test_probe_confluence_uses_space_path():
    from server.routes import docs_targets as dt

    seen = {}

    def fake_urlopen(req, timeout=10):
        seen["url"] = req.full_url
        return _Resp(200)

    with patch.object(dt.urllib.request, "urlopen", fake_urlopen):
        out = dt._probe("confluence", "https://wiki", "t", "ENG")

    assert out.ok is True
    assert seen["url"] == "https://wiki/rest/api/space/ENG"


def test_probe_http_error_is_structured_not_raised():
    from server.routes import docs_targets as dt

    err = dt.urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    def fake_urlopen(req, timeout=10):
        raise err

    with patch.object(dt.urllib.request, "urlopen", fake_urlopen):
        out = dt._probe("backstage", "https://bs", None, None)

    assert out.ok is False
    assert out.status_code == 401
    assert "401" in out.error


def test_probe_connection_error_is_structured():
    from server.routes import docs_targets as dt

    def fake_urlopen(req, timeout=10):
        raise dt.urllib.error.URLError("nope")

    with patch.object(dt.urllib.request, "urlopen", fake_urlopen):
        out = dt._probe("confluence", "https://wiki", "t", "ENG")

    assert out.ok is False
    assert out.status_code is None
    assert "Connection failed" in out.error


# ---------------------------------------------------------------------------
# plan_pipeline._load_docs_connections — ORM → emit-dict mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_docs_connections_maps_rows():
    from server.routes import plan_pipeline as pp

    rows = [
        SimpleNamespace(
            kind="backstage", base_url="https://bs", api_token="tok",
            space=None, enabled_by_default=True,
        ),
        SimpleNamespace(
            kind="confluence", base_url="https://wiki", api_token="t2",
            space="ENG", enabled_by_default=False,
        ),
    ]

    fake_user = SimpleNamespace(id="u1")
    scalars = MagicMock()
    scalars.all.return_value = rows
    result = MagicMock()
    result.scalars.return_value = scalars

    db = MagicMock()

    async def _execute(*_a, **_k):
        return result

    db.execute = _execute

    async def _fake_get_current_user(request, db):
        return fake_user

    with patch("server.routes.auth_routes.get_current_user", _fake_get_current_user):
        out = await pp._load_docs_connections(MagicMock(), db)

    assert out == [
        {"kind": "backstage", "base_url": "https://bs", "api_token": "tok",
         "space": None, "enabled_by_default": True},
        {"kind": "confluence", "base_url": "https://wiki", "api_token": "t2",
         "space": "ENG", "enabled_by_default": False},
    ]


@pytest.mark.asyncio
async def test_load_docs_connections_no_user_returns_none():
    """Shared-token/MCP calls (get_current_user raises 401) → env fallback."""
    from server.routes import plan_pipeline as pp

    async def _raise(request, db):
        raise RuntimeError("401")

    with patch("server.routes.auth_routes.get_current_user", _raise):
        out = await pp._load_docs_connections(MagicMock(), MagicMock())

    assert out is None


@pytest.mark.asyncio
async def test_load_docs_connections_empty_returns_none():
    """A user with no connections → None (don't override env behaviour)."""
    from server.routes import plan_pipeline as pp

    scalars = MagicMock()
    scalars.all.return_value = []
    result = MagicMock()
    result.scalars.return_value = scalars
    db = MagicMock()

    async def _execute(*_a, **_k):
        return result

    db.execute = _execute

    async def _fake_get_current_user(request, db):
        return SimpleNamespace(id="u1")

    with patch("server.routes.auth_routes.get_current_user", _fake_get_current_user):
        out = await pp._load_docs_connections(MagicMock(), db)

    assert out is None
