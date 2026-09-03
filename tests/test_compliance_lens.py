"""Unit tests for the compliance review lens.

The recurring defect this suite guards against is the pass-shaped empty
measurement: a lens that scores 1.0 on a spec with no retention policy is a
broken lens, so these tests assert the FINDING, not the absence of a crash —
and the run_gates integration test proves the lens actually runs when wired
(a lens that never runs and one that finds nothing must not look identical:
the emitted contract distinguishes them via ``compliance.available``, tested
in test_compliance_contract.py).

Run: apps/backend/.venv/bin/pytest tests/test_compliance_lens.py
"""

from __future__ import annotations

from plan.decompose.models import EpicPlan
from plan.models import Criterion, NormalizedPlan
from plan.review import extension_registry
from plan.review.gates import run_gates
from plan.review.lenses.base import default_lenses
from plan.review.lenses.compliance import (
    ComplianceLens,
    declared_jurisdictions,
    processes_personal_data,
)

SOCIAL_SPEC = (
    "MyFriends is a native iOS and Android app for finding and connecting "
    "with people nearby who are open to making new friends. Users create a "
    "profile with photos and interests, see suggested friends via a matching "
    "algorithm, and chat with their matches. Distributed via the App Store "
    "and Play Store."
)


def _plan(*, title="MyFriends mobile app", description=SOCIAL_SPEC, criteria=None, **kw):
    return NormalizedPlan(
        plan_id="001-x",
        title=title,
        description=description,
        source_format="markdown",
        target_kind="software",
        criteria=[Criterion(id=f"AC#{i}", text=t) for i, t in enumerate(criteria or [], 1)],
        **kw,
    )


def _epic() -> EpicPlan:
    return EpicPlan(plan_id="001-x", epic_title="x", children=[])


def _run(plan):
    return ComplianceLens().evaluate(plan, _epic())


def _titles(score):
    return [f.title for f in score.findings]


# ── the signal → finding table, asserted per row ──────────────────────────


def test_social_spec_raises_every_expected_finding() -> None:
    """The full consumer-social spec trips all eight signal rows."""
    score = _run(_plan())
    titles = _titles(score)
    expected = [
        "Lawful basis and purpose limitation not stated",
        "Location data handling not specified",
        "Automated matching without profiling transparency",
        "User-to-user contact without trust and safety controls",
        "No retention or deletion policy stated",
        "Store distribution without in-app account deletion",
        "No age assurance stated",
        "No target jurisdiction stated - applicable law cannot be determined",
    ]
    for title in expected:
        assert title in titles, f"missing finding: {title}"
    assert score.blocking
    assert score.score < 0.75  # cannot clear the gate threshold


def test_no_retention_policy_must_not_score_one() -> None:
    """A lens that scores 1.0 on a spec with no retention policy is broken."""
    plan = _plan(description="Users create an account with a personal profile.")
    score = _run(plan)
    assert score.score < 1.0
    assert "No retention or deletion policy stated" in _titles(score)


def test_missing_jurisdiction_and_age_are_blocking_on_social() -> None:
    score = _run(_plan())
    by_title = {f.title: f for f in score.findings}
    juris = by_title["No target jurisdiction stated - applicable law cannot be determined"]
    age = by_title["No age assurance stated"]
    assert juris.blocking and juris.severity == "high"
    assert age.blocking and age.severity == "high"
    # The rest of the table is medium/low, never blocking.
    for f in score.findings:
        if f.title not in (juris.title, age.title):
            assert f.severity in ("medium", "low")
            assert not f.blocking


def test_age_gap_is_not_blocking_without_user_contact() -> None:
    """Age assurance is high+blocking only on a social (user-to-user) app."""
    plan = _plan(description="A photo backup service where users store personal photos.")
    score = _run(plan)
    age = next(f for f in score.findings if f.title == "No age assurance stated")
    assert not age.blocking
    assert age.severity == "medium"


def test_every_change_requesting_finding_carries_a_resolvable_citation() -> None:
    score = _run(_plan())
    assert score.findings
    for f in score.findings:
        assert f.citations, f"finding without citations: {f.title}"
        assert any(c.uri.startswith("https://") for c in f.citations), (
            f"no resolvable https uri on {f.title}"
        )
        for c in f.citations:
            assert c.uri, f"citation with empty uri on {f.title}"
            assert c.why, f"citation without a why on {f.title}"


def test_findings_word_the_not_legal_advice_scope() -> None:
    score = _run(_plan())
    for f in score.findings:
        assert "not legal advice" in f.detail, f.title


# ── addressed obligations suppress their findings ─────────────────────────


def test_fully_addressed_spec_is_clean() -> None:
    addressed = (
        "Users create a profile (lawful basis: consent; purpose limitation "
        "stated). Location uses explicit consent with coarse precision "
        "minimisation. The matching algorithm's profiling is disclosed with "
        "automated processing transparency. Users can block and report users; "
        "moderation is in scope. Age verification gates sign-up at 16+. Data "
        "retention is 12 months with erasure on request and in-app account "
        "deletion. Distributed in the App Store.\n\n"
        "## Jurisdictions\nUK, EU, and US-California (U.S.).\n"
    )
    score = _run(_plan(description=addressed))
    assert score.findings == []
    assert score.score == 1.0
    assert not score.blocking


def test_non_personal_plan_is_clean_and_not_flagged() -> None:
    plan = _plan(
        title="Rotate the TLS certificates",
        description="Replace the expiring certs on the ingress controllers.",
        criteria=["New certs are served", "Old certs are revoked"],
    )
    score = _run(plan)
    assert score.findings == []
    assert score.score == 1.0


# ── shared helpers ────────────────────────────────────────────────────────


def test_processes_personal_data_detection() -> None:
    assert processes_personal_data(_plan())
    assert not processes_personal_data(
        _plan(title="Tune the CI cache", description="Speed up the build.", criteria=[])
    )


def test_declared_jurisdictions_names_and_section() -> None:
    plan = _plan(description=SOCIAL_SPEC + "\n\n## Jurisdictions\nUK and the European Union.")
    found = declared_jurisdictions(plan)
    assert "jurisdictions-section" in found
    assert "UK" in found
    assert any(j.lower() == "european union" for j in found)


def test_lowercase_prose_never_counts_as_a_market_acronym() -> None:
    plan = _plan(description="let us build the profile page for eu users")
    assert declared_jurisdictions(plan) == []


# ── constitution grounding: the customer's own enforceable clauses ─────────

# The seven enforceable clauses of the pfactory-friends-demo constitution,
# abridged (each bullet must be one physical line for parse_constitution) but
# keeping the operative wording each clause classifies on.
DEMO_CONSTITUTION = """# Engineering constitution

- **P1 (enforceable):** anything stored about a person states how long it is kept.
- **P2 (enforceable):** a person can delete their account from inside the app.
- **P3 (enforceable):** features reachable by someone under 18 state age assurance.
- **P4 (enforceable):** location only with explicit consent at minimum precision.
- **P5 (enforceable):** person-to-person surfaces ship blocking and reporting.
- **P6 (enforceable):** a plan processing personal data names its markets.
- **P7 (enforceable):** an unrun verification lane is never reported as passed.
"""


def test_enforceable_clause_upgrades_finding_to_blocking() -> None:
    """A silent plan under an enforceable retention clause: blocking, citing P1."""
    constitution = "- **P1 (enforceable):** state how long personal data is kept.\n"
    plan = _plan(
        description="Users create an account with a personal profile.",
        constitution_md=constitution,
    )
    score = _run(plan)
    retention = next(f for f in score.findings if "retention" in f.title.lower())
    assert retention.blocking is True
    assert retention.severity == "high"
    assert "P1" in retention.detail
    assert retention.citations[0].source.startswith("constitution:")
    assert retention.citations[0].uri == ".factory/constitution.md"
    # The generic regulation citation still rides along, resolvable.
    assert any(c.uri.startswith("https://") for c in retention.citations)


def test_addressed_clause_produces_no_finding() -> None:
    """A plan that satisfies the clause is not flagged for it."""
    constitution = "- **P1 (enforceable):** state how long personal data is kept.\n"
    plan = _plan(
        description=(
            "Users create an account with a personal profile. Data retention "
            "is 12 months with erasure on request."
        ),
        constitution_md=constitution,
    )
    score = _run(plan)
    assert not any("retention" in f.title.lower() for f in score.findings)


def test_unmapped_enforceable_clause_is_surfaced_not_dropped() -> None:
    constitution = (
        "- **P7 (enforceable):** a verification lane that could not run is "
        "reported as not run, never as passed.\n"
    )
    plan = _plan(constitution_md=constitution)
    score = _run(plan)
    note = next(f for f in score.findings if "not machine-checked" in f.title)
    assert note.severity == "info"
    assert not note.blocking
    assert "P7" in note.detail


def test_demo_constitution_all_seven_clauses_against_silent_social_plan() -> None:
    """The demo scenario: six clauses produce blocking findings, P7 is surfaced."""
    score = _run(_plan(constitution_md=DEMO_CONSTITUTION))
    by_title = {f.title: f for f in score.findings}
    constitution_backed = [
        "No retention or deletion policy stated",  # P1
        "Store distribution without in-app account deletion",  # P2
        "No age assurance stated",  # P3
        "Location data handling not specified",  # P4
        "User-to-user contact without trust and safety controls",  # P5
        "No target jurisdiction stated - applicable law cannot be determined",  # P6
    ]
    for title in constitution_backed:
        f = by_title[title]
        assert f.blocking, f"clause-backed finding not blocking: {title}"
        assert f.severity == "high"
        assert f.citations[0].source.startswith("constitution:"), title
    note = by_title["Enforceable constitution clauses not machine-checked at plan time"]
    assert "P7" in note.detail
    assert score.blocking
    assert score.score == 0.0


def test_without_constitution_behaviour_is_unchanged() -> None:
    """No constitution: the fixed table alone decides severity/blocking."""
    score = _run(_plan())
    retention = next(f for f in score.findings if "retention" in f.title.lower())
    assert retention.severity == "medium"
    assert not retention.blocking
    assert not any("not machine-checked" in f.title for f in score.findings)


# ── wiring: registry + default lens order + the gate actually runs it ─────


def test_registry_declares_compliance_review_enabled() -> None:
    entry = extension_registry.get_extension("compliance-review")
    assert entry is not None
    assert entry["enabled"] is True
    assert entry["category"] == "review"
    assert entry["effect"] == "read-only"
    assert entry["owner_service"] == "pfactory"


def test_default_lenses_include_compliance() -> None:
    names = [lens.name for lens in default_lenses()]
    assert "compliance" in names


def test_run_gates_actually_runs_the_compliance_lens() -> None:
    """Negative-control companion: the wired pipeline must show the lens ran.

    If the lens were registered but dropped from run_gates, this test — not a
    unit test on the lens object — is the one that goes red.
    """
    review = run_gates(_plan(), _epic())
    compliance = next((ls for ls in review.lenses if ls.lens == "compliance"), None)
    assert compliance is not None, "the compliance lens never ran in run_gates"
    assert compliance.findings, "the social spec must produce findings through the gate"
    assert compliance.has_blocking_finding()
    assert not review.gates_passed
