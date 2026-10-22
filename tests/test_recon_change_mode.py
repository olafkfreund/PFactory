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
    # ── #827: the rules that replaced the prefix list + function-word denylist ──
    #
    # Those two leaked, and each of these nine HALTed a valid plan on the hard
    # language-reconciled gate (five with conflict=True). A bare "in"/"using"/"with"
    # required nothing after the token, and the denylist let "and", "live" and
    # "reliable" through. Found by a review of #822, not by #822's own 49 cases.
    #
    # rule B: a bare prefix now needs the token capitalised, so prose does not pass
    ("The team responded in swift succession.", None),
    # "UV light" is how anyone writes it; the lowercase spelling made this row
    # pass for the wrong reason, which a reviewer caught (#827).
    ("The enclosure is tested in UV light for 500 hours.", None),
    ("Sales dropped in go-to-market velocity.", None),
    ("In go we have a saying about naming.", None),
    # rule C: a qualifier must carry an uppercase letter or a digit
    ("A swift and reliable api for partners.", None),
    ("We go live with backend changes on Friday.", None),
    ("The handler must go and fetch application state.", None),
    ("We go GDPR compliant service-wide.", None),
    # tier 2 now holds the English-word tool tokens, so prose no longer resolves
    ("Sterilise the flask before each run.", None),
    ("Track cargo across the fleet.", None),
    ("Django Reinhardt playlist feature.", None),
    # I wrote this row expecting None, and the expectation was wrong: "cargo
    # build" is the Rust build command, so a brief saying it IS naming Rust.
    # Corrected rather than worked around -- it is a row from this same
    # unmerged change, not shipped behaviour (#827).
    ("The cargo build must be reproducible.", "rust"),
    ("The maven of our team wrote it.", None),
    ("Please go and check the flask on the bench.", None),
    # ...while their genuine uses still resolve, via rule B or C
    ("A Django app for the admin console.", "python"),
    ("Built with Django and Postgres.", "python"),
    ("A Flask API for the webhook receiver.", "python"),
    # NB: no bare "rust" in this one -- it has to be decided by the cargo token
    # itself, or it proves nothing about the tier move.
    ("A Cargo package for the parser.", "rust"),
    ("Rewritten in Cargo workspaces.", "rust"),
    ("A Maven module for the shared DTOs.", "java"),
    # rule C requires Capitalised-but-not-ALL-CAPS: SWIFT is the interbank network,
    # which is an ordinary shape in this product's payments briefs
    ("SWIFT payment api for cross-border transfers.", None),
    ("Send the SWIFT message before cut-off.", None),
    ("A SWIFT MT103 service.", None),
    ("A swift KYC api for onboarding.", None),
    # an acronym qualifier must not smuggle an English "go" past rule C
    ("Users go 2FA app enrolment.", None),
    # rule B, genuine: capitalised after a bare prefix
    ("Using Go conventions for naming.", "go"),
    ("Go live on Friday.", None),
    ("Go to settings.", None),
    ("Go to the api docs.", None),
    # ── #827 round two: what an independent review constructed and this missed ──
    #
    # Every row below resolved a language before the fix; the first ten HALTed a
    # valid plan. They are here because my own 33 rows could not see them -- the
    # cases were all shaped by the same assumptions as the rules.
    #
    # rule B accepted the ALL-CAPS form, so acronyms walked in -- including SWIFT,
    # the exact case rule C was built to exclude
    ("Payments are settled in SWIFT format before cut-off.", None),
    ("Reconcile the ledger with SWIFT confirmations nightly.", None),
    # no left boundary meant any word ENDING in "in" donated the prefix
    ("We begin GO week on Monday.", None),
    ("Within Swift boundaries, the retry budget is 3.", None),
    ("The check-in Go/No-Go meeting is Friday.", None),
    ("Store the plugin UV export under /assets.", None),
    # ...which made every non-English brief a minefield: German ein/kein/sein
    ("Das ist ein Swift Modul fuer die Bank.", None),
    # two-letter tokens are dense English acronyms, so a following noun is too
    # weak to tell Reed-Solomon from Rust
    ("Add an RS code for erasure repair.", None),
    ("The RS module is Reed-Solomon.", None),
    # ...while the genuine two-letter uses still resolve, via rules A and B
    ("Write it in TS.", "typescript"),
    ("Ported to JS for the browser build.", "javascript"),
    # The four REGRESSIONS this change first introduced and then fixed: moving the
    # ecosystem tokens into the case-gated tier lost genuine lowercase uses that the
    # pre-#827 code resolved. Rule C no longer requires capitalisation for them --
    # the following noun carries it, which the prose rows above still prove.
    ("Use cargo to build it.", "rust"),
    ("Run the flask app under gunicorn.", "python"),
    ("Add a maven profile for the release.", "java"),
    ("The django settings module needs splitting.", "python"),
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


def test_the_derived_signal_union_still_canonicalises_every_token() -> None:
    """Moving a token between tiers must not change what it canonicalises to (#827).

    `_LANGUAGE_SIGNALS` is derived as the per-language union of the three tiers, and
    `migration_classifier` builds its token->language `_CANON` map from it. #801's
    deviation 7 is this check missing: four tokens moved and the behaviour change went
    unnoticed until a reviewer read the consumer.

    What this guards, precisely, because the first version of this docstring claimed
    more than the test delivers (#827 review): it catches a token claimed by two
    languages, a canonical name shadowed by an earlier needle, and a token moved
    BETWEEN languages. It cannot catch a token moved between tiers *within* one
    language -- which is what #827 did -- because the union is per-language and
    identical either way. That case is covered by the behaviour rows above instead.
    """
    from plan.detect import migration_classifier
    from plan.recon.language_reconcile import _LANGUAGE_SIGNALS

    for language, needles in _LANGUAGE_SIGNALS:
        assert migration_classifier._CANON.get(language) == language, (
            f"the canonical name {language!r} must map to itself"
        )
        for needle in needles:
            if " " in needle:
                continue  # _CANON skips multi-word needles by design
            assert migration_classifier._CANON.get(needle) == language, (
                f"{needle!r} is a {language} signal but _CANON maps it to "
                f"{migration_classifier._CANON.get(needle)!r}"
            )
