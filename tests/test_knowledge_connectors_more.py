"""Tests for the Confluence, GitBook, and Notion knowledge connectors (#12)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.enrich.knowledge.confluence import ConfluenceConnector  # noqa: E402
from plan.enrich.knowledge.gitbook import GitBookConnector  # noqa: E402
from plan.enrich.knowledge.notion import NotionConnector  # noqa: E402

# ── fake HTTP plumbing ─────────────────────────────────────────────────


class _FakeResponse:
    """Canned response with the shape the connectors expect."""

    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeGetHttp:
    """Fake client serving canned JSON over GET; records URLs and params."""

    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self._status = status_code
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> _FakeResponse:
        self.calls.append((url, params))
        return _FakeResponse(self._payload, self._status)


class _FakePostHttp:
    """Fake client serving canned JSON over POST; records URLs and bodies."""

    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self._status = status_code
        self.calls: list[tuple[str, dict[str, Any] | None, dict[str, str] | None]] = []

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> _FakeResponse:
        self.calls.append((url, json, headers))
        return _FakeResponse(self._payload, self._status)


class _BoomGetHttp:
    """Fake GET client that raises on every request."""

    def get(self, url: str, *, headers=None, params=None):
        raise RuntimeError("network down")


class _BoomPostHttp:
    """Fake POST client that raises on every request."""

    def post(self, url: str, *, headers=None, json=None):
        raise RuntimeError("network down")


# ── Confluence connector ───────────────────────────────────────────────

_CONFLUENCE = {
    "results": [
        {
            "id": "111",
            "type": "page",
            "title": "Data Retention Policy",
            "space": {"key": "SEC", "name": "Security Policy"},
            "excerpt": "All customer data retention is governed by this policy.",
            "_links": {"webui": "/spaces/SEC/pages/111/Data+Retention+Policy"},
        },
        {
            "id": "222",
            "type": "page",
            "title": "Onboarding Guide",
            "space": {"key": "ENG", "name": "Engineering"},
            "body": {"view": {"value": "Welcome to the team and laptop setup."}},
            "_links": {"webui": "/spaces/ENG/pages/222/Onboarding+Guide"},
        },
    ]
}


def test_confluence_builds_refs_and_policy_kind() -> None:
    http = _FakeGetHttp(_CONFLUENCE)
    conn = ConfluenceConnector(base_url="https://acme.atlassian.net/", http=http)
    refs = conn.search("data retention policy", limit=10)

    assert http.calls, "expected a search request"
    url, params = http.calls[0]
    assert url == "https://acme.atlassian.net/wiki/rest/api/content/search"
    assert params is not None and 'text ~ "data retention policy"' == params["cql"]

    top = refs[0]
    assert top.connector == "confluence"
    assert top.title == "Data Retention Policy"
    assert top.kind == "policy"
    assert top.uri == "https://acme.atlassian.net/spaces/SEC/pages/111/Data+Retention+Policy"
    assert "retention" in top.snippet.lower()
    assert top.score > 0


def test_confluence_doc_kind_for_generic() -> None:
    http = _FakeGetHttp(_CONFLUENCE)
    conn = ConfluenceConnector(base_url="https://acme.atlassian.net", http=http)
    refs = conn.search("onboarding laptop", limit=10)
    onboarding = next(r for r in refs if r.metadata["id"] == "222")
    assert onboarding.kind == "doc"
    assert "laptop" in onboarding.snippet.lower()


def test_confluence_available_logic() -> None:
    assert ConfluenceConnector(base_url="https://acme.atlassian.net").available() is True
    assert ConfluenceConnector(base_url=None).available() is False
    # token/email are optional
    assert ConfluenceConnector(base_url="https://acme", token=None).available() is True


def test_confluence_to_enrichment_empty_when_unset() -> None:
    conn = ConfluenceConnector(base_url=None, http=_FakeGetHttp(_CONFLUENCE))
    assert conn.to_enrichment("policy") == []


def test_confluence_to_enrichment_empty_on_http_error() -> None:
    conn = ConfluenceConnector(base_url="https://acme", http=_BoomGetHttp())
    assert conn.to_enrichment("policy") == []


def test_confluence_handles_odd_payload() -> None:
    http = _FakeGetHttp({"results": ["not-a-dict", {"title": "Ok"}]})
    conn = ConfluenceConnector(base_url="https://acme", http=http)
    refs = conn.search("ok")
    assert refs and refs[0].title == "Ok"


# ── GitBook connector ──────────────────────────────────────────────────

_GITBOOK = {
    "items": [
        {
            "id": "p1",
            "title": "Deployment Runbook",
            "excerpt": "How to deploy the service safely.",
            "url": "https://docs.acme.com/runbooks/deployment",
        },
        {
            "id": "p2",
            "title": "Style Guide",
            "description": "Coding style conventions.",
            "urls": {"app": "https://app.gitbook.com/o/acme/s/x/style"},
        },
    ]
}


def test_gitbook_builds_refs() -> None:
    http = _FakeGetHttp(_GITBOOK)
    conn = GitBookConnector(token="tok", space_id="space-1", http=http)
    refs = conn.search("deployment runbook", limit=10)

    url, params = http.calls[0]
    assert url == "https://api.gitbook.com/v1/spaces/space-1/search"
    assert params is not None and params["query"] == "deployment runbook"

    top = refs[0]
    assert top.connector == "gitbook"
    assert top.kind == "doc"
    assert top.title == "Deployment Runbook"
    assert top.uri == "https://docs.acme.com/runbooks/deployment"
    assert "deploy" in top.snippet.lower()
    assert top.score > 0


def test_gitbook_global_search_without_space() -> None:
    http = _FakeGetHttp(_GITBOOK)
    conn = GitBookConnector(token="tok", http=http)
    conn.search("style", limit=5)
    assert http.calls[0][0] == "https://api.gitbook.com/v1/search"


def test_gitbook_resolves_urls_field() -> None:
    http = _FakeGetHttp(_GITBOOK)
    conn = GitBookConnector(token="tok", http=http)
    refs = conn.search("style guide")
    style = next(r for r in refs if r.metadata["id"] == "p2")
    assert style.uri == "https://app.gitbook.com/o/acme/s/x/style"


def test_gitbook_available_logic() -> None:
    assert GitBookConnector(token="tok").available() is True
    assert GitBookConnector(token=None).available() is False


def test_gitbook_to_enrichment_empty_when_unset() -> None:
    conn = GitBookConnector(token=None, http=_FakeGetHttp(_GITBOOK))
    assert conn.to_enrichment("anything") == []


def test_gitbook_to_enrichment_empty_on_http_error() -> None:
    conn = GitBookConnector(token="tok", http=_BoomGetHttp())
    assert conn.to_enrichment("anything") == []


def test_gitbook_handles_odd_payload() -> None:
    http = _FakeGetHttp({"items": [42, {"title": "Ok"}]})
    conn = GitBookConnector(token="tok", http=http)
    refs = conn.search("ok")
    assert refs and refs[0].title == "Ok"


# ── Notion connector ───────────────────────────────────────────────────

_NOTION = {
    "results": [
        {
            "object": "page",
            "id": "page-1",
            "url": "https://notion.so/Runbook-page1",
            "properties": {
                "Name": {
                    "type": "title",
                    "title": [{"plain_text": "Incident Runbook"}],
                }
            },
        },
        {
            "object": "database",
            "id": "db-1",
            "url": "https://notion.so/Templates-db1",
            "title": [{"plain_text": "Project Templates"}],
        },
    ]
}


def test_notion_builds_refs_and_kinds() -> None:
    http = _FakePostHttp(_NOTION)
    conn = NotionConnector(token="secret", http=http)
    refs = conn.search("runbook", limit=10)

    url, body, headers = http.calls[0]
    assert url == "https://api.notion.com/v1/search"
    assert body is not None and body["query"] == "runbook"
    assert headers is not None and headers["Notion-Version"] == "2022-06-28"

    top = refs[0]
    assert top.connector == "notion"
    assert top.title == "Incident Runbook"
    assert top.kind == "doc"
    assert top.uri == "https://notion.so/Runbook-page1"
    assert top.score > 0


def test_notion_database_maps_to_template() -> None:
    http = _FakePostHttp(_NOTION)
    conn = NotionConnector(token="secret", http=http)
    refs = conn.search("project templates")
    db = next(r for r in refs if r.metadata["id"] == "db-1")
    assert db.kind == "template"
    assert db.title == "Project Templates"


def test_notion_available_logic() -> None:
    assert NotionConnector(token="secret").available() is True
    assert NotionConnector(token=None).available() is False


def test_notion_to_enrichment_empty_when_unset() -> None:
    conn = NotionConnector(token=None, http=_FakePostHttp(_NOTION))
    assert conn.to_enrichment("runbook") == []


def test_notion_to_enrichment_empty_on_http_error() -> None:
    conn = NotionConnector(token="secret", http=_BoomPostHttp())
    assert conn.to_enrichment("runbook") == []


def test_notion_handles_odd_payload() -> None:
    http = _FakePostHttp({"results": [None, {"object": "page", "id": "x"}]})
    conn = NotionConnector(token="secret", http=http)
    refs = conn.search("anything")
    # untitled page still builds a ref without crashing
    assert any(r.metadata["id"] == "x" for r in refs)
