"""Tests for the Git-markdown and Backstage knowledge connectors (#11)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.enrich.knowledge.backstage import BackstageConnector  # noqa: E402
from plan.enrich.knowledge.git_markdown import GitMarkdownConnector  # noqa: E402

# ── Git-markdown connector ─────────────────────────────────────────────


@pytest.fixture()
def docs_root(tmp_path: Path) -> Path:
    """A docs tree with a policy doc, an ADR, and a generic doc."""
    docs = tmp_path / "docs"
    (docs / "adr").mkdir(parents=True)

    (docs / "security-policy.md").write_text(
        "# Data Retention Policy\n\n"
        "All customer data retention is governed by this policy. "
        "Records must be purged after the retention window.\n",
        encoding="utf-8",
    )
    (docs / "adr" / "0001-use-postgres.md").write_text(
        "# ADR 0001: Use Postgres\n\n"
        "We chose Postgres as the primary database for durability.\n",
        encoding="utf-8",
    )
    (docs / "onboarding.md").write_text(
        "# Onboarding Guide\n\n"
        "Welcome to the team. This guide covers laptop setup and VPN access.\n",
        encoding="utf-8",
    )
    return docs


def test_git_markdown_ranks_relevant_file_first(docs_root: Path) -> None:
    conn = GitMarkdownConnector(root=docs_root)
    refs = conn.search("data retention policy", limit=10)

    assert refs, "expected at least one hit"
    top = refs[0]
    assert top.uri == "security-policy.md"
    assert top.kind == "policy"
    assert top.score > 0
    assert "retention" in top.snippet.lower()


def test_git_markdown_infers_adr_kind(docs_root: Path) -> None:
    conn = GitMarkdownConnector(root=docs_root)
    refs = conn.search("postgres database", limit=10)

    adr = next(r for r in refs if r.uri.endswith("0001-use-postgres.md"))
    assert adr.kind == "adr"
    assert adr.title.startswith("ADR 0001")


def test_git_markdown_doc_kind_for_generic(docs_root: Path) -> None:
    conn = GitMarkdownConnector(root=docs_root)
    refs = conn.search("onboarding laptop setup", limit=10)
    onboarding = next(r for r in refs if r.uri == "onboarding.md")
    assert onboarding.kind == "doc"


def test_git_markdown_fetch_returns_text(docs_root: Path) -> None:
    conn = GitMarkdownConnector(root=docs_root)
    text = conn.fetch("security-policy.md")
    assert "Data Retention Policy" in text


def test_git_markdown_available(tmp_path: Path) -> None:
    assert GitMarkdownConnector(root=tmp_path).available() is True
    assert GitMarkdownConnector(root=tmp_path / "nope").available() is False


def test_git_markdown_no_query_terms(docs_root: Path) -> None:
    assert GitMarkdownConnector(root=docs_root).search("   ", limit=5) == []


def test_git_markdown_to_enrichment(docs_root: Path) -> None:
    refs = GitMarkdownConnector(root=docs_root).to_enrichment("retention policy")
    assert refs and refs[0]["connector"] == "git-markdown"


# ── Backstage connector (injected fake HTTP) ────────────────────────────


class _FakeResponse:
    """Canned response with the shape the connector expects."""

    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHttp:
    """Fake client returning canned catalog JSON; records requested URLs."""

    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self._status = status_code
        self.calls: list[str] = []

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> _FakeResponse:
        self.calls.append(url)
        return _FakeResponse(self._payload, self._status)


class _BoomHttp:
    """Fake client that raises on every request."""

    def get(self, url: str, *, headers: dict[str, str] | None = None):
        raise RuntimeError("network down")


_CATALOG = {
    "items": [
        {
            "kind": "Component",
            "metadata": {
                "name": "payments-api",
                "title": "Payments API",
                "description": "Handles payment processing and refunds.",
                "namespace": "default",
                "tags": ["payments", "go"],
                "uid": "u-1",
            },
            "spec": {"type": "service", "owner": "team-money"},
        },
        {
            "kind": "Template",
            "metadata": {
                "name": "go-service-template",
                "title": "Go Service Template",
                "description": "Scaffold a new Go microservice.",
                "namespace": "default",
                "tags": ["scaffold", "go"],
                "uid": "u-2",
            },
            "spec": {"type": "service"},
        },
    ]
}


def test_backstage_builds_refs_from_catalog() -> None:
    http = _FakeHttp(_CATALOG)
    conn = BackstageConnector(base_url="https://bs.example.com/", http=http)
    refs = conn.search("payments", limit=10)

    assert http.calls == ["https://bs.example.com/api/catalog/entities"]
    top = refs[0]
    assert top.connector == "backstage"
    assert top.title == "Payments API"
    assert top.kind == "catalog"
    assert top.uri.startswith("https://bs.example.com/catalog/")


def test_backstage_template_kind() -> None:
    http = _FakeHttp(_CATALOG)
    conn = BackstageConnector(base_url="https://bs.example.com", http=http)
    refs = conn.search("go scaffold template", limit=10)
    tmpl = next(r for r in refs if r.metadata["name"] == "go-service-template")
    assert tmpl.kind == "template"


def test_backstage_available_logic() -> None:
    assert BackstageConnector(base_url="https://bs.example.com").available() is True
    assert BackstageConnector(base_url=None).available() is False
    # token is optional
    assert BackstageConnector(base_url="https://bs", token=None).available() is True


def test_backstage_to_enrichment_empty_when_unset() -> None:
    conn = BackstageConnector(base_url=None, http=_FakeHttp(_CATALOG))
    assert conn.to_enrichment("payments") == []


def test_backstage_to_enrichment_empty_on_http_error() -> None:
    conn = BackstageConnector(base_url="https://bs.example.com", http=_BoomHttp())
    assert conn.to_enrichment("payments") == []


def test_backstage_search_empty_when_unset() -> None:
    conn = BackstageConnector(base_url=None, http=_FakeHttp(_CATALOG))
    assert conn.search("anything") == []


def test_backstage_bare_list_payload() -> None:
    http = _FakeHttp(_CATALOG["items"])
    conn = BackstageConnector(base_url="https://bs.example.com", http=http)
    refs = conn.search("payments")
    assert refs and refs[0].title == "Payments API"


# ── PFactory#386: a citation must not survive on a similarity accident ───────

# The plan from the issue: a pure-Python FastAPI service whose own spec says
# "Out of scope: ... a database", and whose cost estimate reads
# `source: no-resources`.
_SLUG_PLAN_TEXT = (
    "URL-safe slug service. A pure-Python FastAPI service that converts "
    "arbitrary text into a URL-safe slug. Exposes a single REST API endpoint. "
    "Out of scope: authentication, persistence, rate limiting, a database, and "
    "any frontend. POST /slugify returns a URL-safe slug for any input."
)


def test_backstage_ignores_entities_that_share_only_a_common_word() -> None:
    """`nixos-module` and `component/tfactory` were cited because English matched.

    The old filter admitted any entity sharing ONE query term, and the query is
    the whole plan text — so "a", "the" and "any" were query terms. The
    connector computed a relevance score and then ignored it.
    """
    catalog = {
        "items": [
            {
                "kind": "Template",
                "metadata": {
                    "name": "nixos-module",
                    "title": "NixOS Module",
                    "description": "Scaffold a NixOS module for any host.",
                    "namespace": "default",
                    "uid": "u-9",
                },
                "spec": {"type": "template"},
            },
        ]
    }
    conn = BackstageConnector(base_url="https://bs.example.com", http=_FakeHttp(catalog))
    titles = [r.title for r in conn.search(_SLUG_PLAN_TEXT, limit=10)]
    assert "NixOS Module" not in titles


def test_backstage_ignores_an_entity_sharing_exactly_one_significant_word() -> None:
    """`component/tfactory` shares "service" with the plan and nothing else.

    Stopword filtering alone does not remove this one — "service" is a real
    word that genuinely appears in both. One shared topic word out of a whole
    plan is still a coincidence, which is what the hit floor is for.
    """
    catalog = {
        "items": [
            {
                "kind": "Component",
                "metadata": {
                    "name": "tfactory",
                    "title": "TFactory",
                    "description": "The testing factory service.",
                    "namespace": "default",
                    "uid": "u-11",
                },
                "spec": {"type": "service"},
            },
        ]
    }
    conn = BackstageConnector(base_url="https://bs.example.com", http=_FakeHttp(catalog))
    assert [r.title for r in conn.search(_SLUG_PLAN_TEXT, limit=10)] == []


def test_backstage_still_returns_a_genuinely_matching_entity() -> None:
    """The floor must not delete the useful case, only the accidental one."""
    catalog = {
        "items": [
            {
                "kind": "Template",
                "metadata": {
                    "name": "python-fastapi-service",
                    "title": "Python FastAPI Service",
                    "description": "Scaffold a Python FastAPI service with a REST endpoint.",
                    "namespace": "default",
                    "tags": ["python", "fastapi"],
                    "uid": "u-10",
                },
                "spec": {"type": "service"},
            },
        ]
    }
    conn = BackstageConnector(base_url="https://bs.example.com", http=_FakeHttp(catalog))
    titles = [r.title for r in conn.search(_SLUG_PLAN_TEXT, limit=10)]
    assert "Python FastAPI Service" in titles


def test_backstage_narrow_query_still_needs_only_one_hit() -> None:
    """A deliberately one-term query has one term to match; the cap allows it."""
    conn = BackstageConnector(base_url="https://bs.example.com", http=_FakeHttp(_CATALOG))
    assert [r.title for r in conn.search("payments")] == ["Payments API"]
