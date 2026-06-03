"""Tests for the test-planning fields on Subtask — Task 5 (#6), commit 1.

Covers the schema additions:
  - target: str | None        (file:symbol the test exercises)
  - rationale: str | None     (which acceptance criterion this covers)
  - replan_count: int = 0     (bumped per replan; >= 2 → status=stuck)

Sister to ``tests/test_test_plan_lane.py`` (Task 3). Both stay narrow and
focus on the dataclass + JSON contract — Planner integration tests land
later in ``tests/test_planner.py`` once commits 2-6 of Task 5 ship.
"""

from __future__ import annotations

import pytest
from test_plan import Lane, Subtask, SubtaskStatus

# ── Defaults ─────────────────────────────────────────────────────────────


def test_target_defaults_to_none() -> None:
    s = Subtask(id="1", description="x")
    assert s.target is None


def test_rationale_defaults_to_none() -> None:
    s = Subtask(id="1", description="x")
    assert s.rationale is None


def test_replan_count_defaults_to_zero() -> None:
    s = Subtask(id="1", description="x")
    assert s.replan_count == 0
    assert isinstance(s.replan_count, int)


# ── Explicit construction ────────────────────────────────────────────────


def test_accepts_all_three_fields() -> None:
    s = Subtask(
        id="1",
        description="exercise login_user happy path",
        target="apps/auth/login.py::login_user",
        rationale="AC#3: rejects expired tokens",
        replan_count=1,
    )
    assert s.target == "apps/auth/login.py::login_user"
    assert s.rationale == "AC#3: rejects expired tokens"
    assert s.replan_count == 1


def test_fields_coexist_with_existing_lane_field() -> None:
    """target/rationale/replan_count must coexist cleanly with an
    explicit non-default lane. Use BROWSER (v0.2 spine) as the
    non-default — was Lane.SAST in v0.1, replaced in Task 0."""
    s = Subtask(
        id="1", description="x",
        lane=Lane.BROWSER,
        target="foo.py::bar",
        rationale="AC#1",
        replan_count=2,
    )
    assert s.lane == Lane.BROWSER
    assert s.target == "foo.py::bar"


# ── to_dict emission ─────────────────────────────────────────────────────


def test_to_dict_omits_target_when_none() -> None:
    s = Subtask(id="1", description="x")
    assert "target" not in s.to_dict()


def test_to_dict_omits_rationale_when_none() -> None:
    s = Subtask(id="1", description="x")
    assert "rationale" not in s.to_dict()


def test_to_dict_omits_replan_count_when_zero() -> None:
    """Zero is the default; don't bloat plans with redundant keys."""
    s = Subtask(id="1", description="x")
    assert "replan_count" not in s.to_dict()


def test_to_dict_emits_target_when_set() -> None:
    s = Subtask(id="1", description="x", target="foo.py::bar")
    assert s.to_dict()["target"] == "foo.py::bar"


def test_to_dict_emits_rationale_when_set() -> None:
    s = Subtask(id="1", description="x", rationale="AC#7: returns 401 on bad creds")
    assert s.to_dict()["rationale"] == "AC#7: returns 401 on bad creds"


def test_to_dict_emits_replan_count_when_nonzero() -> None:
    s = Subtask(id="1", description="x", replan_count=1)
    assert s.to_dict()["replan_count"] == 1


def test_to_dict_emits_empty_string_rationale() -> None:
    """Empty string is set-but-blank, not None — should still emit."""
    s = Subtask(id="1", description="x", rationale="")
    # rationale is None vs empty string: we emit None=>omit, but "" is set.
    # The check is `is not None`, so "" emits.
    assert s.to_dict()["rationale"] == ""


# ── from_dict tolerance ─────────────────────────────────────────────────


def test_from_dict_legacy_json_round_trips_with_defaults() -> None:
    """Plans authored before Task 5 must still parse."""
    legacy = {"id": "1", "description": "x"}
    s = Subtask.from_dict(legacy)
    assert s.target is None
    assert s.rationale is None
    assert s.replan_count == 0


def test_from_dict_parses_target() -> None:
    s = Subtask.from_dict({"id": "1", "description": "x", "target": "a.py::b"})
    assert s.target == "a.py::b"


def test_from_dict_parses_rationale() -> None:
    s = Subtask.from_dict({"id": "1", "description": "x", "rationale": "AC#2"})
    assert s.rationale == "AC#2"


def test_from_dict_parses_replan_count() -> None:
    s = Subtask.from_dict({"id": "1", "description": "x", "replan_count": 2})
    assert s.replan_count == 2


def test_from_dict_coerces_replan_count_to_int() -> None:
    """JSON sometimes round-trips ints as strings; coerce defensively."""
    s = Subtask.from_dict({"id": "1", "description": "x", "replan_count": "3"})
    assert s.replan_count == 3
    assert isinstance(s.replan_count, int)


# ── Full round-trip ──────────────────────────────────────────────────────


@pytest.mark.parametrize("replan_count", [0, 1, 2, 3])
def test_full_round_trip_at_various_replan_counts(replan_count: int) -> None:
    s = Subtask(
        id=f"r{replan_count}",
        description="x",
        target="t.py::f",
        rationale="AC#1",
        replan_count=replan_count,
    )
    again = Subtask.from_dict(s.to_dict())
    assert again.target == s.target
    assert again.rationale == s.rationale
    assert again.replan_count == s.replan_count


def test_round_trip_preserves_lane_alongside_new_fields() -> None:
    """The Task 3 lane field and Task 5 fields don't interfere."""
    s = Subtask(
        id="1", description="x",
        lane=Lane.MUTATION,
        target="t.py::f",
        rationale="AC#1",
        replan_count=1,
    )
    again = Subtask.from_dict(s.to_dict())
    assert again.lane == Lane.MUTATION
    assert again.target == "t.py::f"
    assert again.replan_count == 1


# ── Subtle interaction with status ──────────────────────────────────────


def test_replan_count_does_not_affect_status_directly() -> None:
    """The dataclass doesn't auto-transition to stuck; that's the Planner's job.

    Subtask is a passive data model. The Planner (Task 5 commit 5) is the
    component that bumps replan_count AND flips status to STUCK when the
    count crosses the threshold.
    """
    s = Subtask(id="1", description="x", replan_count=5)
    assert s.status == SubtaskStatus.PENDING  # unchanged
    # Explicitly: the model doesn't enforce stuck-at-2.


# ── Task 5 / #21: Polyglot fields — defaults ────────────────────────────


def test_language_defaults_to_none() -> None:
    s = Subtask(id="1", description="x")
    assert s.language is None


def test_framework_defaults_to_none() -> None:
    s = Subtask(id="1", description="x")
    assert s.framework is None


def test_target_name_defaults_to_none() -> None:
    s = Subtask(id="1", description="x")
    assert s.target_name is None


def test_intent_defaults_to_create() -> None:
    s = Subtask(id="1", description="x")
    assert s.intent == "create"


# ── Explicit construction ────────────────────────────────────────────────


def test_accepts_all_polyglot_fields() -> None:
    s = Subtask(
        id="1",
        description="verify login endpoint",
        language="python",
        framework="pytest",
        target_name="backend-unit",
        intent="update",
    )
    assert s.language == "python"
    assert s.framework == "pytest"
    assert s.target_name == "backend-unit"
    assert s.intent == "update"


def test_polyglot_fields_coexist_with_lane_and_target() -> None:
    s = Subtask(
        id="1", description="x",
        lane=Lane.BROWSER,
        target="tests/e2e/login.spec.ts::loginTest",
        language="typescript",
        framework="playwright",
        intent="create",
    )
    assert s.lane == Lane.BROWSER
    assert s.language == "typescript"
    assert s.framework == "playwright"


# ── to_dict emission ─────────────────────────────────────────────────────


def test_to_dict_omits_language_when_none() -> None:
    s = Subtask(id="1", description="x")
    assert "language" not in s.to_dict()


def test_to_dict_omits_framework_when_none() -> None:
    s = Subtask(id="1", description="x")
    assert "framework" not in s.to_dict()


def test_to_dict_omits_target_name_when_none() -> None:
    s = Subtask(id="1", description="x")
    assert "target_name" not in s.to_dict()


def test_to_dict_omits_intent_when_create() -> None:
    """Default intent='create' is omitted to keep plans terse."""
    s = Subtask(id="1", description="x", intent="create")
    assert "intent" not in s.to_dict()


def test_to_dict_emits_intent_when_update() -> None:
    s = Subtask(id="1", description="x", intent="update")
    assert s.to_dict()["intent"] == "update"


def test_to_dict_emits_intent_when_skip() -> None:
    s = Subtask(id="1", description="x", intent="skip")
    assert s.to_dict()["intent"] == "skip"


def test_to_dict_emits_language_when_set() -> None:
    s = Subtask(id="1", description="x", language="typescript")
    assert s.to_dict()["language"] == "typescript"


def test_to_dict_emits_framework_when_set() -> None:
    s = Subtask(id="1", description="x", framework="jest")
    assert s.to_dict()["framework"] == "jest"


def test_to_dict_emits_target_name_when_set() -> None:
    s = Subtask(id="1", description="x", target_name="web-staging")
    assert s.to_dict()["target_name"] == "web-staging"


# ── from_dict tolerance ─────────────────────────────────────────────────


def test_from_dict_legacy_plan_gets_polyglot_defaults() -> None:
    """v0.1 plans (no polyglot keys) must parse with sensible defaults."""
    legacy = {"id": "1", "description": "x"}
    s = Subtask.from_dict(legacy)
    assert s.language is None
    assert s.framework is None
    assert s.target_name is None
    assert s.intent == "create"


def test_from_dict_parses_language() -> None:
    s = Subtask.from_dict({"id": "1", "description": "x", "language": "python"})
    assert s.language == "python"


def test_from_dict_parses_framework() -> None:
    s = Subtask.from_dict({"id": "1", "description": "x", "framework": "pytest"})
    assert s.framework == "pytest"


def test_from_dict_parses_target_name() -> None:
    s = Subtask.from_dict({"id": "1", "description": "x", "target_name": "api"})
    assert s.target_name == "api"


@pytest.mark.parametrize("intent", ["create", "update", "skip"])
def test_from_dict_parses_all_intent_values(intent: str) -> None:
    s = Subtask.from_dict({"id": "1", "description": "x", "intent": intent})
    assert s.intent == intent


# ── framework_id() helper ────────────────────────────────────────────────


def test_framework_id_returns_framework_when_set() -> None:
    s = Subtask(id="1", description="x", framework="pytest")
    assert s.framework_id() == "pytest"


def test_framework_id_returns_none_when_not_set() -> None:
    """v0.1-style subtask has no framework — framework_id returns None."""
    s = Subtask(id="1", description="x")
    assert s.framework_id() is None


def test_framework_id_matches_framework_field() -> None:
    for fw in ("pytest", "jest", "playwright"):
        s = Subtask(id="1", description="x", framework=fw)
        assert s.framework_id() == s.framework


# ── Full round-trip for polyglot fields ─────────────────────────────────


@pytest.mark.parametrize(
    "language,framework,target_name,intent",
    [
        ("python", "pytest", None, "create"),
        ("typescript", "jest", "web-unit", "create"),
        ("typescript", "playwright", "web-e2e", "update"),
        ("python", "pytest", "api-unit", "skip"),
        (None, None, None, "create"),          # v0.1 legacy
    ],
)
def test_polyglot_full_round_trip(
    language: str | None,
    framework: str | None,
    target_name: str | None,
    intent: str,
) -> None:
    s = Subtask(
        id="rt",
        description="x",
        language=language,
        framework=framework,
        target_name=target_name,
        intent=intent,
    )
    again = Subtask.from_dict(s.to_dict())
    assert again.language == s.language
    assert again.framework == s.framework
    assert again.target_name == s.target_name
    assert again.intent == s.intent
