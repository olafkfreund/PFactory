"""Tests for the Decompose-stage planner (issue #6)."""

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

from plan.decompose.planner import (  # noqa: E402
    build_decompose_prompt,
    decompose,
    decompose_with_llm,
)
from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.plan_types import select_for  # noqa: E402


def _plan(title="Add API", desc="Add a REST API endpoint.", kind="software", criteria=None):
    return NormalizedPlan(
        plan_id="001-add-api",
        title=title,
        description=desc,
        source_format="markdown",
        target_kind=kind,
        criteria=[
            Criterion(id=f"AC#{i}", text=t) for i, t in enumerate(criteria or [], 1)
        ],
    )


def test_software_plan_decomposes_with_testing_and_cicd():
    plan = _plan(criteria=[
        "Users can create a record via POST /records.",
        "Users can list records via GET /records.",
        "Records are validated before persistence.",
    ])
    epic = decompose(plan)

    assert epic.plan_id == plan.plan_id
    features = [c for c in epic.children if c.kind == "feature"]
    testing = [c for c in epic.children if c.kind == "testing"]
    cicd = [c for c in epic.children if c.kind == "cicd"]

    assert len(features) == 3
    assert len(testing) == 1
    assert len(cicd) == 1
    assert epic.validate_dependencies() == []

    feature_keys = {c.key for c in features}
    assert set(testing[0].depends_on) == feature_keys
    assert set(cicd[0].depends_on) == feature_keys

    # acceptance criteria propagate onto the feature children
    assert features[0].acceptance_criteria == [plan.criteria[0].text]


def test_software_plan_uses_software_service_descriptor():
    plan = _plan(criteria=["Add an endpoint."])
    assert select_for(plan).name == "software-service"


def test_non_software_plan_has_no_testing_or_cicd():
    plan = _plan(
        title="Hiring campaign",
        desc="Plan a hiring campaign.",
        kind="non-software",
        criteria=["Draft a job description.", "Post to three boards."],
    )
    epic = decompose(plan)

    assert select_for(plan).name == "generic-deliverable"
    features = [c for c in epic.children if c.kind == "feature"]
    assert len(features) == 2
    assert all(c.kind not in ("testing", "cicd") for c in epic.children)
    assert epic.validate_dependencies() == []


def test_plan_with_zero_criteria_gets_one_feature_child():
    plan = _plan(
        title="Spruce up the README",
        desc="Improve the project README.",
        kind="non-software",
        criteria=[],
    )
    epic = decompose(plan)

    features = [c for c in epic.children if c.kind == "feature"]
    assert len(features) == 1
    assert epic.validate_dependencies() == []


class _FakeLLM:
    """Returns a fixed completion string regardless of prompt."""

    def __init__(self, completion: str):
        self._completion = completion

    def complete(self, prompt: str) -> str:
        return self._completion


def test_decompose_with_llm_parses_valid_json():
    payload = {
        "plan_id": "001-add-api",
        "epic_title": "Add API",
        "epic_body": "Body",
        "summary": "Two features",
        "children": [
            {
                "key": "C1",
                "title": "Create endpoint",
                "kind": "feature",
                "acceptance_criteria": ["POST /records works"],
            },
            {
                "key": "C2",
                "title": "Test endpoint",
                "kind": "testing",
                "depends_on": ["C1"],
            },
        ],
    }
    # Wrap in prose + a fence to exercise tolerant extraction.
    completion = "Here is the plan:\n```json\n" + json.dumps(payload) + "\n```\nDone."
    plan = _plan(criteria=["x"])
    descriptor = select_for(plan)

    epic = decompose_with_llm(plan, descriptor, _FakeLLM(completion))

    assert epic.plan_id == "001-add-api"
    assert {c.key for c in epic.children} == {"C1", "C2"}
    assert epic.child("C2").depends_on == ["C1"]
    assert epic.validate_dependencies() == []


def test_decompose_with_llm_falls_back_on_garbage():
    plan = _plan(criteria=["Add an endpoint.", "Validate input."])
    descriptor = select_for(plan)

    epic = decompose_with_llm(plan, descriptor, _FakeLLM("not json at all <<>>"))

    # Fell back to the heuristic: non-empty, sane EpicPlan from the criteria.
    assert epic.children
    assert len([c for c in epic.children if c.kind == "feature"]) == 2
    assert epic.validate_dependencies() == []
    # The fallback is recorded (gap #6) instead of being silent.
    assert epic.decompose_method == "llm_fallback"
    assert epic.decompose_errors  # carries the swallowed error string


def test_decompose_records_method_on_each_path():
    plan = _plan(criteria=["Add an endpoint."])

    # Heuristic path (no llm) → "heuristic".
    assert decompose(plan).decompose_method == "heuristic"

    # LLM success path → "llm".
    payload = {
        "epic_title": "From LLM",
        "children": [{"key": "C1", "title": "Only child", "kind": "feature"}],
    }
    llm_epic = decompose(plan, llm=_FakeLLM(json.dumps(payload)))
    assert llm_epic.decompose_method == "llm"
    assert llm_epic.decompose_errors == []


def test_decompose_routes_to_llm_when_provided():
    payload = {
        "epic_title": "From LLM",
        "children": [{"key": "C1", "title": "Only child", "kind": "feature"}],
    }
    plan = _plan(criteria=["a", "b", "c"])
    epic = decompose(plan, llm=_FakeLLM(json.dumps(payload)))

    # The LLM result (1 child) wins over the heuristic (would be 3+).
    assert epic.epic_title == "From LLM"
    assert len(epic.children) == 1


def test_build_decompose_prompt_mentions_schema_and_stages():
    plan = _plan(criteria=["Add an endpoint."])
    descriptor = select_for(plan)
    prompt = build_decompose_prompt(plan, descriptor)

    assert "JSON" in prompt
    assert "children" in prompt
    assert plan.plan_id in prompt
    assert "testing" in prompt  # software-service enables testing stage
