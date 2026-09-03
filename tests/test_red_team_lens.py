"""Unit tests for the adversarial Red Team review lens (RFC-0015 §4 D1 — #216).

Covers: the extension-registry gating (inert until enabled), each adversarial
check (missing / ambiguous / contradictory ACs, infeasible constraints,
unstated security scope, wrong-language mismatch), the RFC-0014 risk-threshold
blocking behaviour, and inclusion in default_lenses only when enabled.

Run: apps/backend/.venv/bin/pytest tests/test_red_team_lens.py
"""

from __future__ import annotations

import json

import pytest

from plan.decompose.models import EpicPlan
from plan.models import Criterion, NormalizedPlan
from plan.recon.models import RepoMap
from plan.review import extension_registry
from plan.review.lenses.base import default_lenses
from plan.review.lenses.red_team import RedTeamLens


@pytest.fixture(autouse=True)
def _enable_red_team(monkeypatch):
    """Enable the gated lens via the operator env override for these tests."""
    monkeypatch.setenv("PFACTORY_RED_TEAM_REVIEW", "1")
    extension_registry.reset_cache()
    yield
    extension_registry.reset_cache()


def _plan(*, criteria=None, title="Build a service", description="", target="software", **kw):
    return NormalizedPlan(
        plan_id="001-x",
        title=title,
        description=description,
        source_format="markdown",
        target_kind=target,
        criteria=[Criterion(id=f"AC#{i}", text=t) for i, t in enumerate(criteria or [], 1)],
        **kw,
    )


def _epic() -> EpicPlan:
    return EpicPlan(plan_id="001-x", epic_title="x", children=[])


def _run(plan):
    return RedTeamLens().evaluate(plan, _epic())


# ── gating ────────────────────────────────────────────────────────────────────


def _registry_with_red_team_disabled(tmp_path):
    """A registry file declaring red-team-review gated OFF.

    The vendored registry now ships red-team-review enabled, and a nonexistent
    PFACTORY_EXTENSION_REGISTRY override falls back to it — so simulating the
    disabled state needs a real registry that says enabled: false.
    """
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "extensions": [
                    {
                        "name": "red-team-review",
                        "category": "review",
                        "effect": "read-only",
                        "enabled": False,
                        "owner_service": "pfactory",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def test_inert_when_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv("PFACTORY_RED_TEAM_REVIEW", raising=False)
    monkeypatch.setenv("PFACTORY_EXTENSION_REGISTRY", _registry_with_red_team_disabled(tmp_path))
    extension_registry.reset_cache()
    score = _run(_plan(criteria=[]))  # would normally flag "missing ACs"
    assert score.score == 1.0
    assert score.findings == []
    assert score.blocking is False


def test_active_when_enabled():
    score = _run(_plan(criteria=[]))
    assert score.blocking is True  # missing ACs is a high-risk blocker


# ── adversarial checks ────────────────────────────────────────────────────────


def test_missing_acs_blocks():
    score = _run(_plan(criteria=[], title="A networked API service"))
    titles = [f.title for f in score.findings]
    assert any("No acceptance criteria" in t for t in titles)
    assert score.blocking is True


def test_ambiguous_ac_is_medium_not_blocking():
    score = _run(_plan(criteria=["The system should be fast and user-friendly"]))
    amb = [f for f in score.findings if "Ambiguous" in f.title]
    assert amb and amb[0].severity == "medium"
    assert amb[0].blocking is False


def test_contradictory_acs_block():
    score = _run(_plan(criteria=["The endpoint must be public", "The endpoint must be private"]))
    assert any("Contradictory" in f.title for f in score.findings)
    assert score.blocking is True


def test_infeasible_constraint_blocks():
    score = _run(_plan(criteria=["The service guarantees 100% uptime"]))
    assert any("Infeasible" in f.title for f in score.findings)
    assert score.blocking is True


def test_unstated_security_scope_flagged():
    score = _run(_plan(criteria=["Expose a REST API endpoint over https"]))
    sec = [f for f in score.findings if "security" in f.title.lower()]
    assert sec and sec[0].severity == "medium"


def test_security_scope_satisfied_when_auth_present():
    score = _run(_plan(criteria=["Expose a REST API behind OAuth2 login"]))
    assert not any("Unstated security" in f.title for f in score.findings)


def test_language_mismatch_blocks():
    rm = RepoMap(available=True, repo="o/r", languages=["python"], commit="abc")
    plan = _plan(
        title="Rewrite the parser in Rust",
        description="Port the module to Rust",
        criteria=["Parser handles all inputs"],
        repo_map=rm,
        change_mode="modify",
    )
    score = _run(plan)
    assert any("Wrong-language" in f.title for f in score.findings)
    assert score.blocking is True


def test_language_mismatch_skipped_for_migration():
    rm = RepoMap(available=True, repo="o/r", languages=["python"], commit="abc")
    plan = _plan(
        title="Rewrite the parser in Rust",
        criteria=["Parser handles all inputs"],
        repo_map=rm,
        change_mode="migration",
    )
    score = _run(plan)
    assert not any("Wrong-language" in f.title for f in score.findings)


def test_clean_spec_passes():
    score = _run(
        _plan(
            title="Add login",
            criteria=[
                "Users authenticate with email and password via OAuth2",
                "A failed login returns HTTP 401 within 200ms",
            ],
        )
    )
    assert score.score == 1.0
    assert score.blocking is False
    assert any("no spec-breaking gaps" in f.title for f in score.findings)


# ── threshold override ─────────────────────────────────────────────────────────


def test_risk_threshold_override_makes_medium_block(monkeypatch):
    monkeypatch.setenv("PFACTORY_RED_TEAM_RISK_THRESHOLD", "medium")
    score = _run(_plan(criteria=["The system should be fast"]))
    amb = [f for f in score.findings if "Ambiguous" in f.title]
    assert amb and amb[0].blocking is True  # medium now meets the threshold


# ── default_lenses inclusion ───────────────────────────────────────────────────


def test_red_team_in_default_lenses_when_enabled():
    assert any(getattr(lens, "name", "") == "red-team" for lens in default_lenses())


def test_red_team_absent_from_default_lenses_when_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv("PFACTORY_RED_TEAM_REVIEW", raising=False)
    monkeypatch.setenv("PFACTORY_EXTENSION_REGISTRY", _registry_with_red_team_disabled(tmp_path))
    extension_registry.reset_cache()
    assert not any(getattr(lens, "name", "") == "red-team" for lens in default_lenses())
