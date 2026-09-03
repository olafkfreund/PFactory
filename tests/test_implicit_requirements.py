"""Tests for implicit service requirements (RFC-0008 §3.1, #166)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.implicit_requirements import (  # noqa: E402
    MOBILE_IMPLICIT_REQUIREMENTS,
    SERVICE_IMPLICIT_REQUIREMENTS,
    inject_into_epic,
    is_deployable_service,
    is_mobile_app,
    missing_requirements,
    requirement_set,
)
from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.plan_types import select_for  # noqa: E402
from plan.review.lenses.completeness import CompletenessLens  # noqa: E402
from plan.review.readiness.checks import run_readiness  # noqa: E402


def _service_plan() -> NormalizedPlan:
    # target_kind=software + "service" text → select_for picks software-service.
    return NormalizedPlan(
        plan_id="001-svc",
        title="Build a task board service",
        description="A REST API service for a kanban task board.",
        source_format="markdown",
        target_kind="software",
        criteria=[Criterion(id="AC#1", text="A task can be created and listed")],
    ).with_hash()


def _non_service_plan() -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="002-sow",
        title="Quarterly statement of work",
        description="Write the Q3 statement of work document.",
        source_format="markdown",
        target_kind="non-software",
    ).with_hash()


def _epic(children: list[ChildIssue]) -> EpicPlan:
    return EpicPlan(
        plan_id="001-svc", epic_title="Task board", children=children, summary="x"
    )


def _bare_service_epic() -> EpicPlan:
    # A feature child whose only AC is a stated one — no implicit runtime ACs.
    return _epic([
        ChildIssue(
            key="C1",
            title="Task CRUD API",
            kind="feature",
            acceptance_criteria=["A task can be created and listed"],
        ),
        ChildIssue(key="C2", title="Tests", kind="testing"),
    ])


# ── is_deployable_service ────────────────────────────────────────────────


def test_is_deployable_service_true_for_software_service() -> None:
    plan = _service_plan()
    assert is_deployable_service(plan, select_for(plan)) is True


def test_is_deployable_service_false_for_non_service() -> None:
    plan = _non_service_plan()
    assert is_deployable_service(plan, select_for(plan)) is False


# ── inject_into_epic ─────────────────────────────────────────────────────


def test_inject_adds_all_missing_runtime_acs_to_feature_child() -> None:
    plan, epic = _service_plan(), _bare_service_epic()
    injected = inject_into_epic(plan, epic, select_for(plan))
    # all four implicit requirements were absent → all four injected
    assert len(injected) == 4
    feature = epic.children[0]
    acs = " ".join(feature.acceptance_criteria).lower()
    assert "start" in acs and "health" in acs
    assert "depend" in acs and "deploy" in acs
    # the testing child is untouched
    assert epic.children[1].acceptance_criteria == []


def test_inject_is_idempotent() -> None:
    plan, epic = _service_plan(), _bare_service_epic()
    first = inject_into_epic(plan, epic, select_for(plan))
    second = inject_into_epic(plan, epic, select_for(plan))
    assert first and second == []  # nothing left to add the second time
    assert not missing_requirements(epic)


def test_inject_skips_a_requirement_the_user_already_wrote() -> None:
    plan = _service_plan()
    epic = _epic([
        ChildIssue(
            key="C1",
            title="API",
            kind="feature",
            acceptance_criteria=["Exposes a /health endpoint returning 200"],
        )
    ])
    injected = inject_into_epic(plan, epic, select_for(plan))
    # health already covered → only the other three injected
    assert len(injected) == 3
    assert not any("health-check endpoint" in i for i in injected)


def test_inject_noop_for_non_service_plan() -> None:
    plan = _non_service_plan()
    epic = _epic([ChildIssue(key="C1", title="Draft the SOW", kind="task")])
    assert inject_into_epic(plan, epic, select_for(plan)) == []
    assert epic.children[0].acceptance_criteria == []


# ── CompletenessLens ─────────────────────────────────────────────────────


def test_completeness_lens_flags_missing_then_clean_after_inject() -> None:
    lens = CompletenessLens()
    plan, epic = _service_plan(), _bare_service_epic()

    before = lens.evaluate(plan, epic)
    assert before.score < 1.0
    assert any("not covered" in f.title for f in before.findings)

    inject_into_epic(plan, epic, select_for(plan))
    after = lens.evaluate(plan, epic)
    assert after.score == 1.0


def test_completeness_lens_full_score_for_non_service() -> None:
    lens = CompletenessLens()
    plan = _non_service_plan()
    score = lens.evaluate(plan, _epic([ChildIssue(key="C1", title="x", kind="task")]))
    assert score.score == 1.0
    assert score.findings == []


# ── readiness check ──────────────────────────────────────────────────────


def test_readiness_service_requirements_fail_then_pass_after_inject() -> None:
    plan, epic = _service_plan(), _bare_service_epic()
    rep = run_readiness(plan, epic)
    res = rep.result("service-requirements-covered")
    assert res.status == "fail" and res.hard
    assert res.evidence.get("missing_requirements")

    inject_into_epic(plan, epic, select_for(plan))
    rep2 = run_readiness(plan, epic)
    assert rep2.result("service-requirements-covered").status == "pass"


def test_readiness_service_requirements_not_applicable_for_non_service() -> None:
    plan = _non_service_plan()
    epic = _epic([ChildIssue(key="C1", title="Draft", kind="task")])
    rep = run_readiness(plan, epic)
    assert rep.result("service-requirements-covered").status == "not_applicable"


# ── mobile-app implicit requirements ─────────────────────────────────────


def _mobile_plan() -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="003-mob",
        title="MyFriends mobile app",
        description="A native iOS and Android app (Swift / Kotlin) for finding "
        "nearby people open to new friends, via the App Store and Play Store.",
        source_format="markdown",
        target_kind="software",
        criteria=[Criterion(id="AC#1", text="Nearby open-to-friends users are listed")],
    ).with_hash()


def _bare_mobile_epic(plan_id: str = "003-mob") -> EpicPlan:
    return EpicPlan(
        plan_id=plan_id,
        epic_title="MyFriends",
        summary="x",
        children=[
            ChildIssue(
                key="C1",
                title="Nearby list",
                kind="feature",
                acceptance_criteria=["Nearby open-to-friends users are listed"],
            )
        ],
    )


def test_requirement_set_selects_by_plan_type() -> None:
    mobile = _mobile_plan()
    d_mobile = select_for(mobile)
    assert d_mobile.name == "mobile-app"
    assert is_mobile_app(d_mobile) is True
    assert requirement_set(mobile, d_mobile) is MOBILE_IMPLICIT_REQUIREMENTS

    svc = _service_plan()
    assert requirement_set(svc, select_for(svc)) is SERVICE_IMPLICIT_REQUIREMENTS

    non = _non_service_plan()
    assert requirement_set(non, select_for(non)) == []


def test_inject_adds_all_mobile_acs_to_feature_child() -> None:
    plan, epic = _mobile_plan(), _bare_mobile_epic()
    injected = inject_into_epic(plan, epic, select_for(plan))
    assert len(injected) == len(MOBILE_IMPLICIT_REQUIREMENTS) == 10
    acs = " ".join(epic.children[0].acceptance_criteria).lower()
    for token in (
        "store listing", "permission", "offline", "deep links", "crash",
        "minimum supported os", "voiceover", "talkback", "battery",
        "staged rollout", "forced-upgrade",
    ):
        assert token in acs, token
    # and the SERVICE health-check AC was NOT injected into a mobile plan
    assert "health-check endpoint" not in acs


def test_inject_skips_mobile_requirement_the_user_already_wrote() -> None:
    plan = _mobile_plan()
    epic = _bare_mobile_epic()
    epic.children[0].acceptance_criteria.append(
        "The feed works offline from a local cache"
    )
    injected = inject_into_epic(plan, epic, select_for(plan))
    assert len(injected) == len(MOBILE_IMPLICIT_REQUIREMENTS) - 1
    assert not any("usable offline" in i for i in injected)


def test_min_os_and_forced_upgrade_overlap_phrasing() -> None:
    # "We support iOS 16 and above and prompt users on older versions to
    # update" reads as covering BOTH min-os-versions and forced-upgrade. With
    # substring matching it covers forced-upgrade ("versions to update"); the
    # OS floor cannot be detected without a keyword like "support ios", which
    # would also match "supports iOS and Android" — a phrase in essentially
    # every mobile brief — and silently disable min-os injection everywhere.
    # So min-os is still injected here: the cost is a near-duplicate AC, not a
    # false gate failure, because injection runs before the lens and check.
    plan = _mobile_plan()
    epic = _bare_mobile_epic()
    epic.children[0].acceptance_criteria.append(
        "We support iOS 16 and above and prompt users on older versions to update"
    )
    missing = {k for k, _t in missing_requirements(epic, MOBILE_IMPLICIT_REQUIREMENTS)}
    assert "forced-upgrade" not in missing
    assert "min-os-versions" in missing


def test_supports_ios_and_android_does_not_cover_min_os() -> None:
    # Guard against a future keyword like "support ios": naming the platforms
    # is not declaring an OS floor, and must not suppress min-os injection.
    epic = _bare_mobile_epic()
    epic.children[0].acceptance_criteria.append("The app supports iOS and Android.")
    missing = {k for k, _t in missing_requirements(epic, MOBILE_IMPLICIT_REQUIREMENTS)}
    assert "min-os-versions" in missing


def test_mobile_inject_is_idempotent() -> None:
    plan, epic = _mobile_plan(), _bare_mobile_epic()
    first = inject_into_epic(plan, epic, select_for(plan))
    second = inject_into_epic(plan, epic, select_for(plan))
    assert first and second == []
    assert not missing_requirements(epic, MOBILE_IMPLICIT_REQUIREMENTS)


def test_completeness_lens_enforces_mobile_requirements() -> None:
    lens = CompletenessLens()
    plan, epic = _mobile_plan(), _bare_mobile_epic()

    before = lens.evaluate(plan, epic)
    assert before.score < 1.0
    flagged = {f.title for f in before.findings if "not covered" in f.title}
    assert len(flagged) == len(MOBILE_IMPLICIT_REQUIREMENTS)

    inject_into_epic(plan, epic, select_for(plan))
    after = lens.evaluate(plan, epic)
    assert after.score == 1.0


def test_readiness_mobile_requirements_fail_then_pass_after_inject() -> None:
    plan, epic = _mobile_plan(), _bare_mobile_epic()
    rep = run_readiness(plan, epic)
    res = rep.result("service-requirements-covered")
    assert res.status == "fail" and res.hard
    assert "store-listing" in res.evidence.get("missing_requirements", [])

    inject_into_epic(plan, epic, select_for(plan))
    rep2 = run_readiness(plan, epic)
    assert rep2.result("service-requirements-covered").status == "pass"
