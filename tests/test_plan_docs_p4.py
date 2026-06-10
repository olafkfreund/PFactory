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
    PlanDocsResolver,
    connections_to_targets,
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
