"""Tests for RFC-0010 Phase 3: change_mode + language reconciliation (#585)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.recon import RepoMap, classify_change_mode  # noqa: E402
from plan.recon.language_reconcile import (  # noqa: E402
    detect_spec_language,
    detect_spec_language_signal,
    reconcile_language,
)
from plan.review.readiness.checks import run_readiness  # noqa: E402


def _plan(title="T", desc="", crits=()) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-x",
        title=title,
        description=desc,
        source_format="markdown",
        criteria=[Criterion(id=f"AC#{i}", text=t) for i, t in enumerate(crits, 1)],
    )


# ── change_mode classifier ──────────────────────────────────────────────


def test_change_mode_greenfield_when_no_repo_map():
    assert classify_change_mode(None) == "greenfield"


def test_change_mode_greenfield_when_unavailable():
    assert classify_change_mode(RepoMap(available=False)) == "greenfield"


def test_change_mode_modify_when_code_present():
    assert (
        classify_change_mode(RepoMap(available=True, languages=["python"])) == "modify"
    )
    assert classify_change_mode(RepoMap(available=True, iac=["terraform"])) == "modify"


def test_change_mode_migration_wins_on_signal():
    rm = RepoMap(available=True, languages=["python"])
    assert classify_change_mode(rm, is_migration=True) == "migration"


# ── spec language detection ─────────────────────────────────────────────


def test_detect_spec_language():
    assert detect_spec_language(_plan(desc="Build a Rust service with cargo")) == "rust"
    assert detect_spec_language(_plan(desc="A FastAPI app")) == "python"
    assert detect_spec_language(_plan(desc="just some prose")) is None
    # "AC#1:" criterion labels must not read as C# (#325)
    assert detect_spec_language(_plan(crits=("AC#1: factorial(0) == 1",))) is None
    assert detect_spec_language(_plan(desc="port it to C# please")) == "csharp"


def test_untrusted_is_not_rust():
    """The reported #397 case: satisfying the security lens broke the language gate.

    A security acceptance criterion naturally says "untrusted", which contained a
    bare "rust" needle. Because the first signal wins, that beat every python
    signal in the same text and hard-failed approval on a Python spec.
    """
    plan = _plan(
        desc="A FastAPI endpoint",
        crits=(
            "AC#1: reject oversized input so an untrusted caller cannot trigger "
            "unbounded computation",
        ),
    )
    assert detect_spec_language(plan) == "python"


def test_language_needles_match_on_word_boundaries():
    """The whole substring class, not just the one needle that was reported."""
    # "java" no longer matches inside "javascript", so signal order stops being
    # the only thing keeping these apart.
    assert detect_spec_language(_plan(desc="a javascript bundler")) == "javascript"
    assert detect_spec_language(_plan(desc="a java service")) == "java"
    # Bare short tokens must not match inside ordinary words.
    assert detect_spec_language(_plan(desc="the meeting is going ahead")) is None
    assert detect_spec_language(_plan(desc="we trust the caller")) is None
    assert detect_spec_language(_plan(desc="a swiftly delivered feature")) is None
    # ...while still matching when genuinely meant, including the punctuated ones.
    assert detect_spec_language(_plan(desc="write it in Go")) == "go"
    assert detect_spec_language(_plan(desc="a C++ library with cmake")) == "cpp"
    assert detect_spec_language(_plan(desc="an ASP.NET service")) == "csharp"


def test_detect_reports_the_token_that_matched():
    """A conflict must be able to name its own evidence (#397)."""
    lang, signal = detect_spec_language_signal(_plan(desc="Build a Rust service"))
    assert (lang, signal) == ("rust", "rust")
    assert detect_spec_language_signal(_plan(desc="just some prose")) == (None, None)


def test_language_conflict_names_the_offending_word():
    """The failure detail and evidence must point at the token, not just the language."""
    plan = _plan(desc="Rewrite the tokio worker")
    rec = reconcile_language(plan, RepoMap(available=True, languages=["python"]), "modify")
    assert rec.conflict
    assert rec.spec_language_signal == "tokio"


# ── language reconciliation (#585) ──────────────────────────────────────


def test_reconcile_no_repo_uses_spec_intent():
    rec = reconcile_language(_plan(desc="rust service"), None, "greenfield")
    assert rec.resolved_language == "rust" and rec.conflict is False


def test_reconcile_match_uses_repo_language():
    rm = RepoMap(available=True, languages=["python"])
    rec = reconcile_language(_plan(desc="a fastapi app"), rm, "modify")
    assert rec.resolved_language == "python" and rec.conflict is False


def test_reconcile_conflict_when_spec_differs_and_not_migration():
    rm = RepoMap(available=True, languages=["python"])
    rec = reconcile_language(_plan(desc="rewrite in rust with cargo"), rm, "modify")
    assert rec.conflict is True
    assert rec.spec_language == "rust" and rec.repo_language == "python"


def test_reconcile_migration_is_not_a_conflict():
    rm = RepoMap(available=True, languages=["python"])
    rec = reconcile_language(_plan(desc="port to rust"), rm, "migration")
    assert rec.conflict is False
    assert rec.resolved_language == "rust" and rec.repo_language == "python"


# ── readiness check wiring ──────────────────────────────────────────────


def _epic():
    from plan.decompose.models import ChildIssue, EpicPlan

    return EpicPlan(
        plan_id="001-x",
        epic_title="T",
        summary="s",
        children=[ChildIssue(key="C1", title="c", body="b", kind="feature")],
    )


def _results(plan):
    report = run_readiness(plan, _epic())
    return {r.check_id: r for r in report.results}


def test_language_check_not_applicable_for_greenfield():
    r = _results(_plan(desc="rust"))["language-reconciled"]
    assert r.status == "not_applicable"


def test_language_check_hard_fail_on_conflict():
    plan = _plan(desc="rewrite in rust with cargo").model_copy(
        update={
            "repo_map": RepoMap(available=True, languages=["python"]),
            "change_mode": "modify",
        }
    )
    r = _results(plan)["language-reconciled"]
    assert r.status == "fail" and r.hard is True and r.is_hard_failure()


def test_language_check_passes_for_migration():
    plan = _plan(desc="port to rust").model_copy(
        update={
            "repo_map": RepoMap(available=True, languages=["python"]),
            "change_mode": "migration",
        }
    )
    r = _results(plan)["language-reconciled"]
    assert r.status == "pass"
