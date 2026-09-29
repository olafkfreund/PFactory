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


# ── language signal resolves by strength, not list order (#801) ─────────

# (prose, expected_language) — from spec/2026-09-29-801-language-signal-ambiguity.md
# "Measured" section, one row per case quoted there, commented with the tier
# that decides it.
_STRENGTH_CASES: list[tuple[str, str | None]] = [
    # the reported defect: tier 1 (name "kotlin") resolves before tier 3 ever
    # sees "gradle".
    ("Kotlin Android app, Gradle build.", "kotlin"),
    # the two worse cases the intent found: tier 2 (weak "go"/"swift") requires
    # a language context that ordinary prose does not have.
    ("Users can go to the next screen and confirm.", None),
    ("The system must give a swift response under load.", None),
    # existing suite assertions, tier 1 (unambiguous names)
    ("Build a Rust service with cargo", "rust"),
    ("port it to C# please", "csharp"),
    ("a javascript bundler", "javascript"),
    ("a java service", "java"),
    ("a C++ library with cmake", "cpp"),
    ("an ASP.NET service", "csharp"),
    # existing suite assertions, tier 2 (weak signal, in a language context)
    ("write it in Go", "go"),
    # existing suite assertions, tier 3 (tool/ecosystem)
    ("A FastAPI app", "python"),
    # existing suite assertions that must stay None: no tier matches
    ("the meeting is going ahead", None),
    ("we trust the caller", None),
    ("a swiftly delivered feature", None),
    ("just some prose", None),
    # adversarial cases: tier 2 context absent, or punctuation breaks it
    ("The build will go green in CI.", None),
    ("The migration will go to production on Friday.", None),
    ("Sign in; go to settings.", None),
    # adversarial case: shared token, deliberately None (#585)
    ("An Android app built with Gradle.", None),
    # adversarial case: tier 1 (name "kotlin") resolves despite the shared
    # "android" token also being present
    ("An Android app in Kotlin.", "kotlin"),
    # adversarial case: tier 2 (weak "ts", corroborated by "ported to" context)
    ("Ported to TS for type safety.", "typescript"),
    # additional cases supplied by the reviewer from the full 41-case measured
    # set (the spec's prose only quoted 21 of them) -- not present verbatim in
    # the spec/intent text.
    # tier 1 (unambiguous names)
    ("A Kotlin Android app built with Gradle.", "kotlin"),
    ("A Kotlin multiplatform module, Gradle build.", "kotlin"),
    ("A Java Spring Boot service built with Maven.", "java"),
    ("A Java service built with Gradle.", "java"),
    ("Rewritten in Rust for the hot path.", "rust"),
    ("A SwiftUI view for the profile screen.", "swift"),
    ("Use TypeScript for the front end.", "typescript"),
    ("Everything is in Python 3.12.", "python"),
    ("reject oversized input so an untrusted caller cannot trigger unbounded "
     "computation. the service is python.", "python"),
    ("Build a Rust service", "rust"),
    # tier 2 (weak signal, in a language context)
    ("Write the API in Go with a Postgres store.", "go"),
    ("A Go service exposing a gRPC endpoint.", "go"),
    ("An iOS app written in Swift.", "swift"),
    # tier 3 (tool/ecosystem)
    ("The pipeline runs pytest against the FastAPI app.", "python"),
    # None: weak signal without a language context
    ("Approvals go through a review queue.", None),
    ("Let the operator go back to the previous step.", None),
    ("Reduce p99 latency; responses must be swift.", None),
    # None: shared token (#585)
    ("A Scala service built with Gradle.", None),
    ("A Groovy script in a Gradle build.", None),
    # None: boundary() still refuses a substring match (#397)
    ("Untrusted input must be rejected at the boundary.", None),
    ("AC#1: factorial(0) == 1", None),
    # tier 2: qualifier-gap cases -- a qualifier may sit between the weak token
    # and the context noun, but a function word ("to", "the", ...) still blocks it
    ("A Swift SPM library for the iOS app matching logic.", "swift"),
    ("Swift iOS app; the marketing frontend pages show live data.", "swift"),
    ("A Go HTTP service behind the gateway.", "go"),
    ("A Go 1.22 module for the parser.", "go"),
    ("Users go to the api docs page.", None),
    ("Approvals go to the backend queue.", None),
    ("Operators go into the application menu.", None),
]


@pytest.mark.parametrize("prose,expected", _STRENGTH_CASES)
def test_the_spec_language_resolves_by_strength_not_list_order(prose, expected):
    assert detect_spec_language(_plan(desc=prose)) == expected


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
