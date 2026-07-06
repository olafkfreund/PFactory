"""Tests for GET /api/search — the federated cross-portal search proxy (#149).

The route forwards to the cockpit's /api/search with a read-scoped cockpit key
and degrades to an empty result set on any failure. Tests call the route
function directly (matching the other route tests) with httpx + settings mocked.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from server.routes import search


def _settings(url="http://cockpit:3111", key="cfr_test"):
    return SimpleNamespace(CFACTORY_SEARCH_URL=url, CFACTORY_READ_KEY=key)


class _Resp:
    def __init__(self, payload, error=None):
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._payload


class _Client:
    """Minimal async-context-manager stand-in for httpx.AsyncClient."""

    def __init__(self, resp, captured):
        self._resp = resp
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, url, params=None, headers=None):
        self._captured.update(url=url, params=params, headers=headers or {})
        return self._resp


def _run(resp, captured, *, q="checkout", limit=20, settings=None):
    with (
        patch.object(search, "get_settings", return_value=settings or _settings()),
        patch.object(search.httpx, "AsyncClient", lambda **_kw: _Client(resp, captured)),
    ):
        return asyncio.run(search.federated_search(q=q, limit=limit, _user=None))


def test_blank_query_short_circuits_without_calling_cockpit():
    captured: dict = {}
    with patch.object(search, "get_settings", return_value=_settings()):
        out = asyncio.run(search.federated_search(q="   ", limit=20, _user=None))
    assert out == {"query": "   ", "count": 0, "results": []}
    assert captured == {}  # httpx never touched


def test_unconfigured_base_returns_empty():
    with patch.object(search, "get_settings", return_value=_settings(url="")):
        out = asyncio.run(search.federated_search(q="x", limit=20, _user=None))
    assert out == {"query": "x", "count": 0, "results": []}


def test_proxies_and_forwards_read_key():
    captured: dict = {}
    payload = {"query": "checkout", "count": 1, "results": [{"correlation_key": "42"}]}
    out = _run(_Resp(payload), captured)
    assert out == payload
    assert captured["url"] == "http://cockpit:3111/api/search"
    assert captured["params"] == {"q": "checkout", "limit": 20}
    assert captured["headers"]["Authorization"] == "Bearer cfr_test"


def test_cockpit_error_degrades_to_empty():
    captured: dict = {}
    resp = _Resp({}, error=httpx.HTTPError("boom"))
    out = _run(resp, captured, q="widgets")
    assert out == {"query": "widgets", "count": 0, "results": []}
