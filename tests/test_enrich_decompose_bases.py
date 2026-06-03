"""Tests for the Enrich/Decompose base contracts (#6/#8/#11 foundations)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.enrich.base import (  # noqa: E402
    InfraAdapter,
    InfraSnapshot,
    get_adapter,
    register_adapter,
)
from plan.enrich.knowledge.base import (  # noqa: E402
    KnowledgeConnector,
    KnowledgeRef,
    get_connector,
)

# ── infra adapter framework ────────────────────────────────────────────

def test_infra_adapter_registry_and_graceful_failure():
    @register_adapter
    class _Dummy(InfraAdapter):
        name = "dummy-infra"

        def available(self) -> bool:
            return True

        def discover(self) -> InfraSnapshot:
            return InfraSnapshot(adapter=self.name, target="t", workloads=[{"n": 1}])

    a = get_adapter("dummy-infra", target="t")
    enr = a.to_enrichment()
    assert enr["adapter"] == "dummy-infra"
    assert enr["available"] is True
    assert enr["workloads"] == [{"n": 1}]


def test_infra_to_enrichment_never_raises():
    @register_adapter
    class _Boom(InfraAdapter):
        name = "boom-infra"

        def available(self) -> bool:
            return True

        def discover(self) -> InfraSnapshot:
            raise RuntimeError("kaboom")

    enr = get_adapter("boom-infra").to_enrichment()
    assert enr["available"] is False
    assert "kaboom" in (enr["error"] or "")


def test_unavailable_adapter_reports_not_available():
    @register_adapter
    class _Off(InfraAdapter):
        name = "off-infra"

        def available(self) -> bool:
            return False

        def discover(self) -> InfraSnapshot:  # pragma: no cover - not reached
            return InfraSnapshot(adapter=self.name)

    enr = get_adapter("off-infra").to_enrichment()
    assert enr["available"] is False


# ── knowledge connector framework ──────────────────────────────────────

def test_knowledge_connector_to_enrichment():
    from plan.enrich.knowledge.base import register_connector

    @register_connector
    class _Wiki(KnowledgeConnector):
        name = "dummy-wiki"

        def available(self) -> bool:
            return True

        def search(self, query, *, limit=10):
            return [KnowledgeRef(connector=self.name, kind="policy", title=query)]

    refs = get_connector("dummy-wiki").to_enrichment("data retention")
    assert refs[0]["title"] == "data retention"
    assert refs[0]["kind"] == "policy"


# ── EpicPlan dependency validation ─────────────────────────────────────

def _epic(deps):
    return EpicPlan(
        plan_id="001-x",
        epic_title="Epic",
        children=[ChildIssue(key=k, title=k, depends_on=d) for k, d in deps.items()],
    )


def test_sane_dependencies_ok():
    assert _epic({"C1": [], "C2": ["C1"], "C3": ["C1", "C2"]}).validate_dependencies() == []


def test_dangling_and_self_dependencies_flagged():
    problems = _epic({"C1": ["C9"], "C2": ["C2"]}).validate_dependencies()
    assert any("unknown child 'C9'" in p for p in problems)
    assert any("depends on itself" in p for p in problems)


def test_cycle_detected():
    problems = _epic({"C1": ["C2"], "C2": ["C1"]}).validate_dependencies()
    assert any("cycle" in p for p in problems)
