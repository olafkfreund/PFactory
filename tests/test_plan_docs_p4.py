"""Plan → docs emit (P4a): cross-factory resolve + Settings→targets bridge.

Backend-only, no network. Exercises the registry resolver (the memory read other
factories use) and the pure connections→targets mapping (the Settings seam).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.emit.docs import (  # noqa: E402
    DocBundle,
    PlanDocsResolver,
    connections_to_targets,
    emit_bundle,
    emit_docs,
    render_plan_docs,
)
from plan.emit.docs.targets.repo import RepoDocsTarget  # noqa: E402
from plan.service import PlanService  # noqa: E402

_PLAN = """# Refund API
A REST endpoint with auth + a Kubernetes deploy.
## Acceptance Criteria
- User can request a refund
"""


def _session():
    svc = PlanService()
    s = svc.ingest_text(_PLAN, title="Refund API", category="software")
    svc.process(s.session_id)
    return svc.get(s.session_id)


# ── resolver (the cross-factory memory read) ────────────────────────────


def test_resolver_round_trips_a_written_plan(tmp_path):
    s = _session()
    bundle = render_plan_docs(s)
    RepoDocsTarget(tmp_path).publish(bundle)  # writes registry.json

    r = PlanDocsResolver.from_dir(tmp_path)
    entry = r.resolve(bundle.correlation_key)
    assert entry is not None
    assert entry["plan_id"] == s.plan.plan_id
    assert entry["doc_file"] == f"{bundle.slug}.md"
    assert isinstance(r.dependencies(bundle.correlation_key), list)


def test_resolver_unknown_key_returns_none(tmp_path):
    r = PlanDocsResolver.from_dir(tmp_path)  # no registry yet
    assert r.resolve("nope") is None
    assert r.dependencies("nope") == []


def test_resolver_from_text():
    text = json.dumps({"plans": {"42": {"plan_id": "x", "dependencies": ["api:foo"]}}})
    r = PlanDocsResolver(text)
    assert r.resolve("42")["plan_id"] == "x"
    assert r.dependencies("42") == ["api:foo"]


# ── connections → targets (the Settings seam) ───────────────────────────


def test_connections_enabled_by_default():
    conns = [
        {"kind": "backstage", "base_url": "https://bs", "enabled_by_default": True},
        {"kind": "confluence", "base_url": "https://x", "api_token": "t",
         "space": "ENG", "enabled_by_default": False},
    ]
    names = [t.name for t in connections_to_targets(conns, repo="o/r")]
    assert names == ["backstage"]  # only the enabled-by-default one


def test_connections_per_plan_selection_overrides_default():
    conns = [
        {"kind": "backstage", "base_url": "https://bs", "enabled_by_default": False},
        {"kind": "confluence", "base_url": "https://x", "api_token": "t",
         "space": "ENG", "enabled_by_default": True},
    ]
    # explicit per-plan selection wins over enabled_by_default
    names = sorted(t.name for t in connections_to_targets(conns, selected=["backstage"]))
    assert names == ["backstage"]


def test_connections_builds_configured_targets_available():
    conns = [
        {"kind": "backstage", "base_url": "https://bs", "enabled_by_default": True},
        {"kind": "confluence", "base_url": "https://x", "api_token": "t",
         "space": "ENG", "enabled_by_default": True},
    ]
    targets = connections_to_targets(conns, repo="o/r")
    assert {t.name for t in targets} == {"backstage", "confluence"}
    assert all(t.available() for t in targets)  # configured => available


# ── emit_docs(connections=...) — the orchestrator always adds the repo doc ──


def test_emit_docs_connections_path_always_writes_repo(tmp_path):
    """With Settings connections, the repo doc is written plus the selected sink."""
    s = _session()
    conns = [
        {"kind": "backstage", "base_url": "https://bs", "enabled_by_default": True},
    ]
    results = emit_docs(s, repo="o/r", root=tmp_path, connections=conns)
    by_target = {r["target"]: r["status"] for r in results}
    # repo always written; backstage attempted (publish or skipped, never absent)
    assert by_target.get("repo") == "written"
    assert "backstage" in by_target
    # the registry/markdown actually landed on disk
    assert (tmp_path / "registry.json").exists()


def test_emit_docs_empty_connections_writes_repo_only(tmp_path):
    """An empty connection set still yields the default repo doc, nothing remote."""
    s = _session()
    results = emit_docs(s, repo="o/r", root=tmp_path, connections=[])
    assert [r["target"] for r in results] == ["repo"]
    assert results[0]["status"] == "written"


# ── emit_bundle — the plan-agnostic core (TFactory reuse seam, §10.5) ────────


def test_emit_bundle_publishes_a_non_plan_bundle(tmp_path):
    """Any producer can hand a hand-built DocBundle to the shared publish loop.

    Models TFactory's render_test_results → emit_bundle path: no PlanSession, no
    plan renderer — just a bundle + a target. The registry trail + resolver work
    identically, so a test-result doc resolves by the same correlation_key.
    """
    bundle = DocBundle(
        plan_id="tfactory-task-42",
        slug="2026-06-10-refund-api-tests",
        title="Refund API — test results",
        correlation_key="pfactory:plan:refund-api",
        content_hash="deadbeef",
        markdown="# Refund API — test results\n\n3 lanes · 12 passed\n",
        registry_entry={
            "plan_id": "tfactory-task-42",
            "correlation_key": "pfactory:plan:refund-api",
            "doc_file": "2026-06-10-refund-api-tests.md",
            "dependencies": ["api:auth"],
            "generated_by": "tfactory",
        },
    )
    results = emit_bundle(bundle, targets=[RepoDocsTarget(tmp_path)])
    assert results == [{"target": "repo", "status": "written", "detail": results[0]["detail"]}]
    assert (tmp_path / "2026-06-10-refund-api-tests.md").exists()

    # Same correlation_key resolves the test-result doc — the shared memory trail.
    r = PlanDocsResolver.from_dir(tmp_path)
    entry = r.resolve("pfactory:plan:refund-api")
    assert entry is not None
    assert entry["generated_by"] == "tfactory"
    assert r.dependencies("pfactory:plan:refund-api") == ["api:auth"]
