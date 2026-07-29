"""Tests for the Emit stage — GitHub issues (#18) + AIFactory handoff (#19)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.emit import (  # noqa: E402
    EmitResult,
    build_requirements,
    emit_to_github,
    handover_labels,
    trigger_api,
    write_requirements,
)
from plan.emit.gh_runner import GhRunnerError  # noqa: E402
from plan.models import NormalizedPlan  # noqa: E402
from plan.review.models import HumanApproval, LensScore, PlanReview  # noqa: E402

# ── fixtures ────────────────────────────────────────────────────────────


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="001-widget",
        epic_title="Build the widget",
        epic_body="Top-level epic for the widget feature.",
        children=[
            ChildIssue(
                key="C1",
                title="Scaffold widget module",
                body="Create the module skeleton.",
                kind="feature",
                labels=["area:core"],
                complexity="simple",
                acceptance_criteria=["module imports", "exposes Widget class"],
            ),
            ChildIssue(
                key="C2",
                title="Wire widget config",
                body="Load config from file.",
                kind="task",
                labels=["area:config"],
                depends_on=["C1"],
                complexity="standard",
                acceptance_criteria=["reads YAML"],
            ),
            ChildIssue(
                key="C3",
                title="Test the widget",
                body="Add unit tests.",
                kind="testing",
                labels=["area:tests"],
                depends_on=["C1", "C2"],
                complexity="complex",
                acceptance_criteria=["coverage >= 90%"],
            ),
        ],
        summary="Widget epic with 3 children.",
    )


def _plan() -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-widget",
        title="Build the widget",
        description="A widget that does widget things.",
        source_format="markdown",
    )


class FakeGh:
    """Records create_issue / link_sub_issue calls; returns incrementing ids."""

    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.links: list[tuple[int, int]] = []
        self._next = 100

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        self._next += 1
        self.created.append({"title": title, "body": body, "labels": labels})
        return self._next

    def link_sub_issue(self, parent: int, child: int) -> None:
        self.links.append((parent, child))


class FakeHttp:
    """Records POST calls; returns a stub response."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, *, params: dict, json: object) -> dict:
        self.calls.append({"url": url, "params": params, "json": json})
        return {"status": 200, "taskId": "task-1"}


# ── emit_to_github ──────────────────────────────────────────────────────


def test_emit_dry_run_plans_without_calling_gh() -> None:
    gh = FakeGh()
    result = emit_to_github(_epic(), repo="acme/widget", gh=gh, dry_run=True)

    assert isinstance(result, EmitResult)
    assert result.dry_run is True
    # epic + 3 child payloads
    assert len(result.planned) == 4
    assert result.planned[0]["title"] == "Build the widget"
    child_keys = [p["key"] for p in result.planned[1:]]
    assert child_keys == ["C1", "C2", "C3"]
    # no gh calls in dry-run
    assert gh.created == []
    assert gh.links == []
    assert result.epic_number is None


def test_emit_live_creates_epic_children_and_links() -> None:
    gh = FakeGh()
    result = emit_to_github(_epic(), repo="acme/widget", gh=gh, dry_run=False)

    assert result.dry_run is False
    assert result.errors == []
    # epic created first, then 3 children
    assert result.epic_number == 101
    assert result.child_numbers == {"C1": 102, "C2": 103, "C3": 104}
    assert len(gh.created) == 4
    # children link back to the epic
    assert gh.links == [(101, 102), (101, 103), (101, 104)]
    # child body references the epic and carries acceptance criteria
    c1_body = gh.created[1]["body"]
    assert "Part of #101" in c1_body
    assert "module imports" in c1_body


def test_emit_refuses_ungoverned_plan() -> None:
    gh = FakeGh()
    review = PlanReview(
        plan_id="001-widget",
        lenses=[LensScore(lens="architecture", score=0.5)],
        human_approval=HumanApproval(approved=False, valid=False),
    )
    assert review.ready_to_emit() is False

    result = emit_to_github(
        _epic(), repo="acme/widget", review=review, gh=gh, dry_run=False
    )

    assert result.errors  # non-empty
    assert gh.created == []  # FakeGh NOT called
    assert gh.links == []
    assert result.epic_number is None


# ── #119: idempotent + rate-limit-resilient emit ─────────────────────────


class FlakyGh:
    """A gh runner that fails on a schedule, to exercise the resilience paths.

    - ``fail_times``: the first N ``create_issue`` calls raise a *retryable*
      (secondary-rate-limit) error before any succeed.
    - ``always_fail_titles``: any issue with one of these titles always raises a
      *non-retryable* error (exercises continue-past-failure).
    """

    def __init__(
        self,
        *,
        fail_times: int = 0,
        always_fail_titles: tuple[str, ...] = (),
    ) -> None:
        self.created: list[dict[str, object]] = []
        self.links: list[tuple[int, int]] = []
        self._next = 100
        self._remaining = fail_times
        self._always_fail = always_fail_titles

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        if title in self._always_fail:
            raise GhRunnerError("gh issue create failed: validation: bad label")
        if self._remaining > 0:
            self._remaining -= 1
            raise GhRunnerError(
                "gh issue create failed: You have exceeded a secondary rate "
                "limit. Please retry after 60 seconds."
            )
        self._next += 1
        self.created.append({"title": title, "labels": labels})
        return self._next

    def link_sub_issue(self, parent: int, child: int) -> None:
        self.links.append((parent, child))


def test_emit_retries_transient_rate_limit_then_succeeds() -> None:
    # First two create_issue calls trip a secondary rate limit, then succeed.
    gh = FlakyGh(fail_times=2)
    slept: list[float] = []
    result = emit_to_github(
        _epic(), repo="acme/widget", gh=gh, dry_run=False, sleep_fn=slept.append
    )
    # Epic + all three children eventually created, no errors surfaced.
    assert result.errors == []
    assert result.epic_number is not None
    assert set(result.child_numbers) == {"C1", "C2", "C3"}
    # Backoff actually slept between the transient failures.
    assert len(slept) == 2


def test_emit_reuses_existing_epic_and_children_idempotently() -> None:
    gh = FakeGh()
    result = emit_to_github(
        _epic(),
        repo="acme/widget",
        gh=gh,
        dry_run=False,
        existing_epic_number=42,
        existing_child_numbers={"C1": 7},
    )
    # Epic reused (not created); C1 reused; only C2 + C3 created.
    assert result.epic_number == 42
    assert result.child_numbers["C1"] == 7
    assert [c["title"] for c in gh.created] == ["Wire widget config", "Test the widget"]
    # Only the newly-created children are linked under the reused epic.
    assert gh.links == [
        (42, result.child_numbers["C2"]),
        (42, result.child_numbers["C3"]),
    ]


def test_emit_continues_past_a_failing_child_and_records_it() -> None:
    # C2 ("Wire widget config") always fails with a non-retryable error.
    gh = FlakyGh(always_fail_titles=("Wire widget config",))
    result = emit_to_github(
        _epic(), repo="acme/widget", gh=gh, dry_run=False, sleep_fn=lambda _s: None
    )
    # Epic + C1 + C3 created; C2 recorded as an error but did not abort the rest.
    assert result.epic_number is not None
    assert set(result.child_numbers) == {"C1", "C3"}
    assert len(result.errors) == 1 and "C2" in result.errors[0]
    # Only the children that succeeded are linked.
    assert result.child_numbers["C3"] in {c for _p, c in gh.links}


# ── aifactory handoff ───────────────────────────────────────────────────


def test_build_requirements_shape_and_omitted_none() -> None:
    child = _epic().children[0]
    payload = build_requirements(
        child,
        plan=_plan(),
        github_issue_number=102,
        model="opus",
    )

    assert payload["title"] == "Scaffold widget module"
    assert "module imports" in payload["description"]
    meta = payload["metadata"]
    # complexity defaults to the child's complexity
    assert meta["complexity"] == "simple"
    assert meta["githubIssueNumber"] == 102
    assert meta["model"] == "opus"
    assert meta["requireReviewBeforeCoding"] is True
    # None keys omitted
    assert "thinkingLevel" not in meta
    assert "phaseModels" not in meta
    assert "phaseThinking" not in meta


def test_write_requirements_writes_valid_json(tmp_path: Path) -> None:
    child = _epic().children[0]
    out = write_requirements(child, tmp_path, plan=_plan(), github_issue_number=102)

    assert out == tmp_path / ".aifactory" / "specs" / "001-widget" / "requirements.json"
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["title"] == "Scaffold widget module"
    assert data["metadata"]["complexity"] == "simple"


def test_trigger_api_dry_run_returns_planned_request() -> None:
    payload = build_requirements(_epic().children[0], plan=_plan())
    result = trigger_api(
        payload, base_url="http://aif.local", project_id="proj-1", dry_run=True
    )

    assert result["dry_run"] is True
    req = result["request"]
    assert req["url"] == "http://aif.local/api/tasks/create-and-run"
    assert req["params"]["project_id"] == "proj-1"
    assert req["params"]["title"] == "Scaffold widget module"
    assert req["json"] is payload


def test_trigger_api_live_posts_via_injected_http() -> None:
    http = FakeHttp()
    payload = build_requirements(_epic().children[0], plan=_plan())
    result = trigger_api(
        payload,
        base_url="http://aif.local",
        project_id="proj-1",
        http=http,
        dry_run=False,
    )

    assert result["dry_run"] is False
    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["url"] == "http://aif.local/api/tasks/create-and-run"
    assert call["params"]["project_id"] == "proj-1"
    assert call["json"] is payload
    assert result["response"] == {"status": 200, "taskId": "task-1"}


def test_handover_labels() -> None:
    assert handover_labels() == ["pfactory", "handoff:aifactory"]


# ── the spec body reaches the implementer (#385) ────────────────────────

_SLUG_SPEC = """\
# URL-safe slug service

## Slug rules

- lowercase the input
- transliterate accented characters to ASCII
- truncate to 200 characters BEFORE slugging
- never cut mid-word when a hyphen boundary exists within the last 20 chars

## Acceptance criteria

- GET /healthz returns HTTP 200
- POST /slug returns a slug for a title
"""


def test_emitted_epic_body_carries_the_spec_prose() -> None:
    """End to end: spec text -> ingest -> decompose -> emitted epic body (#385).

    A rule stated in the spec's prose but never restated as an acceptance
    criterion used to vanish entirely, so the implementer only ever saw the
    checklist. The emitted epic body must contain the prose.
    """
    from plan.decompose.planner import decompose  # noqa: PLC0415 - local to this test
    from plan.ingest.channels import ingest_text  # noqa: PLC0415 - local to this test

    plan = ingest_text(_SLUG_SPEC, source_channel="cli")
    epic = decompose(plan)
    result = emit_to_github(epic, repo="acme/demo", dry_run=True)

    epic_body = result.planned[0]["body"]
    for rule in (
        "transliterate accented characters to ASCII",
        "truncate to 200 characters BEFORE slugging",
        "never cut mid-word when a hyphen boundary exists within the last 20 chars",
    ):
        assert rule in epic_body, f"spec rule never reached the implementer: {rule!r}"

    # the generated breakdown is still there
    assert "### Child issues" in epic_body
