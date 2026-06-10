"""Plan → docs emit (P1): pure render + repo target + gated orchestrator.

Backend-only, no network. Builds a real processed PlanSession via PlanService so
the render exercises the actual plan/epic/review models.
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

from plan.emit.docs import emit_docs, is_enabled, render_plan_docs  # noqa: E402
from plan.emit.docs.bundle import DocBundle, TargetResult  # noqa: E402
from plan.emit.docs.targets.repo import RepoDocsTarget  # noqa: E402
from plan.service import PlanService  # noqa: E402

_PLAN = """# Refund API
Add a REST endpoint to the payments service with auth + a Kubernetes deploy.
## Acceptance Criteria
- User can request a refund through the API
- Refunds are written to the audit log
- The endpoint requires a valid JWT
"""


def _processed_session():
    svc = PlanService()
    s = svc.ingest_text(_PLAN, title="Refund API", category="software")
    svc.process(s.session_id)
    return svc.get(s.session_id)


# ── render ──────────────────────────────────────────────────────────────


def test_render_is_deterministic_and_has_sections():
    s = _processed_session()
    a = render_plan_docs(s)
    b = render_plan_docs(s)
    assert isinstance(a, DocBundle)
    assert a.markdown == b.markdown  # pure → byte-identical
    assert "# Refund API" in a.markdown
    assert "## Acceptance criteria" in a.markdown
    assert "## Governance" in a.markdown
    assert "generated_by: pfactory" in a.markdown  # feedback-loop marker


def test_registry_entry_keyed_by_correlation_and_has_deps():
    s = _processed_session()
    bundle = render_plan_docs(s)
    e = bundle.registry_entry
    assert e["correlation_key"] == bundle.correlation_key
    assert e["plan_id"] == s.plan.plan_id
    assert e["generated_by"] == "pfactory"
    assert isinstance(e["dependencies"], list)


# ── repo target ─────────────────────────────────────────────────────────


def test_repo_target_writes_page_registry_index(tmp_path):
    s = _processed_session()
    bundle = render_plan_docs(s)
    res = RepoDocsTarget(tmp_path, updated_at="2026-06-10T00:00:00Z").publish(bundle)
    assert res.status == "written"
    assert (tmp_path / f"{bundle.slug}.md").exists()
    assert (tmp_path / "index.md").exists()
    reg = json.loads((tmp_path / "registry.json").read_text())
    assert bundle.correlation_key in reg["plans"]
    assert reg["plans"][bundle.correlation_key]["updated_at"] == "2026-06-10T00:00:00Z"


def test_repo_target_is_idempotent(tmp_path):
    s = _processed_session()
    bundle = render_plan_docs(s)
    t = RepoDocsTarget(tmp_path)
    t.publish(bundle)
    t.publish(bundle)  # re-publish must not duplicate the registry row
    reg = json.loads((tmp_path / "registry.json").read_text())
    assert len(reg["plans"]) == 1


def test_repo_target_always_available(tmp_path):
    assert RepoDocsTarget(tmp_path).available() is True


# ── orchestrator (gating + best-effort) ─────────────────────────────────


def test_is_enabled_default_off(monkeypatch):
    monkeypatch.delenv("PFACTORY_DOCS_EMIT", raising=False)
    assert is_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "on", "YES"])
def test_is_enabled_truthy(val, monkeypatch):
    monkeypatch.setenv("PFACTORY_DOCS_EMIT", val)
    assert is_enabled() is True


def test_emit_docs_publishes_to_repo(tmp_path):
    s = _processed_session()
    results = emit_docs(s, root=tmp_path)
    assert results[0]["target"] == "repo"
    assert results[0]["status"] == "written"
    assert (tmp_path / "index.md").exists()


def test_emit_docs_isolates_a_failing_target(tmp_path):
    s = _processed_session()

    class _Boom:
        name = "boom"

        def available(self):
            return True

        def publish(self, bundle) -> TargetResult:
            raise RuntimeError("kaboom")

    results = emit_docs(s, targets=[_Boom()])
    # Never raises; records the failure.
    assert results[0]["target"] == "boom"
    assert results[0]["status"] == "error"


def test_emit_docs_skips_unavailable_target():
    s = _processed_session()

    class _Off:
        name = "off"

        def available(self):
            return False

        def publish(self, bundle):  # pragma: no cover - should not be called
            raise AssertionError("must not publish when unavailable")

    results = emit_docs(s, targets=[_Off()])
    assert results[0]["status"] == "skipped"
