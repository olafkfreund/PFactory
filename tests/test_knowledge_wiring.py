"""Tests for knowledge-connector env wiring + cited best-practices findings.

The connectors were built + unit-tested but never wired: the enrichment loop ran
them with no base_url/token, so a real Backstage/Confluence never got reached, and
surfaced refs were only counted, never cited. These lock in the fix.
"""

from __future__ import annotations

from plan.decompose.models import EpicPlan
from plan.models import Enrichment, NormalizedPlan
from plan.review.lenses.best_practices import BestPracticesLens
from plan.service import _knowledge_connector_kwargs

# ── env wiring ──────────────────────────────────────────────────────────


def test_backstage_kwargs_from_env(monkeypatch) -> None:
    monkeypatch.setenv("PFACTORY_BACKSTAGE_URL", "https://backstage.example.com")
    monkeypatch.setenv("PFACTORY_BACKSTAGE_TOKEN", "tok123")
    kw = _knowledge_connector_kwargs("backstage", None)
    assert kw == {"base_url": "https://backstage.example.com", "token": "tok123"}


def test_backstage_token_optional(monkeypatch) -> None:
    monkeypatch.setenv("PFACTORY_BACKSTAGE_URL", "https://bs")
    monkeypatch.delenv("PFACTORY_BACKSTAGE_TOKEN", raising=False)
    # empty/unset values are dropped — no token key, just base_url
    assert _knowledge_connector_kwargs("backstage", None) == {"base_url": "https://bs"}


def test_confluence_kwargs_from_env(monkeypatch) -> None:
    monkeypatch.setenv("PFACTORY_CONFLUENCE_URL", "https://acme.atlassian.net")
    monkeypatch.setenv("PFACTORY_CONFLUENCE_TOKEN", "pat")
    monkeypatch.setenv("PFACTORY_CONFLUENCE_EMAIL", "me@acme.com")
    kw = _knowledge_connector_kwargs("confluence", None)
    assert kw == {
        "base_url": "https://acme.atlassian.net",
        "token": "pat",
        "email": "me@acme.com",
    }


def test_gitbook_and_notion_kwargs(monkeypatch) -> None:
    monkeypatch.setenv("PFACTORY_GITBOOK_TOKEN", "gb")
    monkeypatch.setenv("PFACTORY_GITBOOK_SPACE_ID", "space-1")
    monkeypatch.setenv("PFACTORY_NOTION_TOKEN", "nt")
    assert _knowledge_connector_kwargs("gitbook", None) == {"token": "gb", "space_id": "space-1"}
    assert _knowledge_connector_kwargs("notion", None) == {"token": "nt"}


def test_git_markdown_uses_wiki_root(monkeypatch) -> None:
    assert _knowledge_connector_kwargs("git-markdown", "/repo/docs") == {"root": "/repo/docs"}
    assert _knowledge_connector_kwargs("git-markdown", None) == {}


def test_best_practices_and_unknown_take_no_kwargs(monkeypatch) -> None:
    monkeypatch.delenv("PFACTORY_BACKSTAGE_URL", raising=False)
    assert _knowledge_connector_kwargs("best-practices", None) == {}
    assert _knowledge_connector_kwargs("backstage", None) == {}  # nothing configured


# ── cited best-practices finding ────────────────────────────────────────


def _plan_with_knowledge(refs: list[dict]) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-x", title="Add a service", source_format="markdown",
        enrichment=Enrichment(knowledge=refs),
    ).with_hash()


def test_lens_cites_surfaced_knowledge_refs() -> None:
    refs = [
        {"connector": "backstage", "title": "payments-api", "uri": "https://bs/catalog/payments-api", "score": 0.9},
        {"connector": "confluence", "title": "Data Retention Policy", "uri": "https://wiki/policy", "score": 0.7},
    ]
    epic = EpicPlan(plan_id="001-x", epic_title="x", children=[])
    score = BestPracticesLens().evaluate(_plan_with_knowledge(refs), epic)
    gp = next(f for f in score.findings if f.title == "Golden-path guidance available")
    # the surfaced refs are CITED (title + uri + source), not just counted
    assert len(gp.citations) == 2
    titles = {c.title for c in gp.citations}
    assert "payments-api" in titles and "Data Retention Policy" in titles
    assert any(c.uri == "https://bs/catalog/payments-api" for c in gp.citations)
    assert any(c.source == "knowledge:backstage" for c in gp.citations)
    # detail names the top sources + the source count
    assert "payments-api" in gp.detail and "2 source(s)" in gp.detail


def test_lens_no_knowledge_no_golden_path_finding() -> None:
    epic = EpicPlan(plan_id="001-x", epic_title="x", children=[])
    score = BestPracticesLens().evaluate(_plan_with_knowledge([]), epic)
    assert not any(f.title == "Golden-path guidance available" for f in score.findings)


# ── PFactory#386: infra guidance belongs only on a plan with infrastructure ──


def _slug_plan() -> NormalizedPlan:
    """Plan 044 from the issue: a pure-Python FastAPI service, no infrastructure.

    Its own cost estimate reads `source: no-resources`, and its spec explicitly
    puts a database out of scope — which is precisely the sentence the
    best-practices catalogue matched "database" inside.
    """
    return NormalizedPlan(
        plan_id="044-url-safe-slug-service-python-fastapi",
        title="URL-safe slug service",
        description=(
            "A pure-Python FastAPI service that converts arbitrary text into a "
            "URL-safe slug. Exposes a single REST API endpoint. Out of scope: "
            "authentication, persistence, rate limiting, a database, and any "
            "frontend."
        ),
        source_format="markdown",
        target_kind="software",
    ).with_hash()


def _eks_plan() -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="050-eks-platform",
        title="Provision the EKS platform",
        description="Stand up an EKS cluster with RDS Postgres and multi-AZ failover.",
        source_format="markdown",
        target_kind="software",
    ).with_hash()


def _knowledge_connectors(plan: NormalizedPlan, monkeypatch) -> set[str]:
    """Which connectors the enrich stage actually reaches for this plan."""
    from plan.service import PlanService

    monkeypatch.setenv("PFACTORY_ENRICH_CONNECTORS", "best-practices")
    monkeypatch.setenv("PFACTORY_ENRICH_ADAPTERS", "")
    enriched = PlanService._enrich(None, plan)  # does not use self
    return {k.get("connector") for k in enriched.enrichment.knowledge if isinstance(k, dict)}


def test_no_infra_citations_on_a_plan_with_no_infrastructure(monkeypatch) -> None:
    """The reported defect: AWS RDS Multi-AZ and EKS networking on a slug service.

    They were matched out of the plan's own OUT-OF-SCOPE sentence ("a database")
    and out of the phrase "REST API endpoint". The `why` string asserts "the
    plan should follow it", so a reader has no way to tell this from a real one.
    """
    assert _knowledge_connectors(_slug_plan(), monkeypatch) == set()


def test_infra_citations_still_surface_on_a_plan_with_infrastructure(monkeypatch) -> None:
    """Scoping must not delete the feature — the useful case is the whole point."""
    assert "best-practices" in _knowledge_connectors(_eks_plan(), monkeypatch)
