"""Tests for the validated handshake read-back in emit_contract (issue #81).

Covers read-back success, read-back mismatch → retry → ok:false with mismatches,
read-back unavailable (no get / flag off) → create-confirmed + warning, and that
dry-run is unchanged. The handshake flag is toggled via env / monkeypatch.
"""

from __future__ import annotations

import pytest
from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_emit import emit_contract
from plan.models import Criterion, NormalizedPlan
from plan.review.models import LensScore, PlanReview

HANDSHAKE_ENV = "PFACTORY_AIFACTORY_HANDSHAKE"


def _plan(**kw) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-widget", title="Widget", source_format="markdown",
        description="fastapi service tested with pytest",
        criteria=[Criterion(id="AC#1", text="exposes an API")], **kw,
    ).with_hash()


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="001-widget", epic_title="Widget",
        children=[
            ChildIssue(key="C1", title="Scaffold", kind="infra"),
            ChildIssue(key="C2", title="API", kind="feature", depends_on=["C1"],
                       acceptance_criteria=["exposes an API"]),
        ],
    )


def _review() -> PlanReview:
    return PlanReview(
        plan_id="001-widget",
        lenses=[LensScore(lens="architecture", score=0.95)],
        aggregate_score=0.95, gates_passed=True,
    )


class FakeHttpReadback:
    """Fake HTTP client with both post + get for read-back verification.

    ``post`` returns a created task id; ``get`` returns the task body produced by
    ``task_factory(call_index)`` so a test can make the first read-back mismatch
    and a later one match.
    """

    def __init__(self, task_factory) -> None:
        self.calls: list[dict] = []
        self.gets: list[str] = []
        self._task_factory = task_factory
        self._post_count = 0

    def post(self, url, *, params, json):
        self.calls.append({"url": url, "params": params, "json": json})
        self._post_count += 1
        return {"taskId": f"t-{self._post_count}", "status": "accepted"}

    def get(self, url):
        self.gets.append(url)
        return self._task_factory(len(self.gets) - 1)


class FakeHttpNoGet:
    """Fake HTTP client with only post (read-back unavailable)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url, *, params, json):
        self.calls.append({"url": url, "params": params, "json": json})
        return {"taskId": "t-123", "status": "accepted"}


def _matching_task() -> dict:
    return {
        "title": "Widget",
        "metadata": {"complexity": "standard", "requireReviewBeforeCoding": True},
    }


# ── read-back success ────────────────────────────────────────────────────────


def test_readback_success(monkeypatch) -> None:
    monkeypatch.setenv(HANDSHAKE_ENV, "1")
    http = FakeHttpReadback(lambda i: _matching_task())
    result = emit_contract(
        _plan(), _epic(), _review(), base_url="http://ai:3101",
        project_id="p1", http=http, key="secret",
        approval_timestamp="2026-06-06T00:00:00Z", dry_run=False,
    )
    assert result["ok"] is True
    assert result["verified"] is True
    assert result["fallback"] is False
    assert result["attempts"] == 1
    # correlation persisted spec/task/project
    assert result["correlation"] == {
        "spec_id": "001-widget", "task_id": "t-1", "project_id": "p1",
    }
    # read-back hit the task endpoint
    assert http.gets == ["http://ai:3101/api/tasks/t-1"]


# ── read-back mismatch → retry then ok:false ─────────────────────────────────


def test_readback_mismatch_retries_then_fails(monkeypatch) -> None:
    monkeypatch.setenv(HANDSHAKE_ENV, "true")
    # every read-back returns a wrong title → never matches
    http = FakeHttpReadback(lambda i: {"title": "Wrong", "metadata": {"complexity": "x"}})
    result = emit_contract(
        _plan(), _epic(), _review(), base_url="http://ai:3101",
        project_id="p1", http=http, dry_run=False,
    )
    assert result["ok"] is False
    assert result["verified"] is False
    assert result["mismatches"]
    assert any("title" in m for m in result["mismatches"])
    # default max_retries=2 → 1 initial + 2 retries = 3 posts
    assert len(http.calls) == 3
    assert result["attempts"] == 3


def test_readback_mismatch_then_recovers(monkeypatch) -> None:
    monkeypatch.setenv(HANDSHAKE_ENV, "1")
    # first read-back wrong, second correct → retry succeeds
    http = FakeHttpReadback(
        lambda i: _matching_task() if i >= 1 else {"title": "Wrong", "metadata": {}}
    )
    result = emit_contract(
        _plan(), _epic(), _review(), base_url="http://ai:3101",
        project_id="p1", http=http, dry_run=False,
    )
    assert result["ok"] is True
    assert result["verified"] is True
    assert result["attempts"] == 2
    assert len(http.calls) == 2


def test_readback_missing_complexity_is_mismatch(monkeypatch) -> None:
    monkeypatch.setenv(HANDSHAKE_ENV, "1")
    http = FakeHttpReadback(lambda i: {"title": "Widget", "metadata": {}})
    result = emit_contract(
        _plan(), _epic(), _review(), base_url="http://ai:3101",
        project_id="p1", http=http, dry_run=False, max_retries=0,
    )
    assert result["ok"] is False
    assert any("complexity" in m for m in result["mismatches"])
    assert len(http.calls) == 1  # max_retries=0 → single attempt


# ── read-back unavailable → create-confirmed + warning ───────────────────────


def test_readback_unavailable_no_get(monkeypatch) -> None:
    monkeypatch.setenv(HANDSHAKE_ENV, "1")  # flag on, but client has no get
    http = FakeHttpNoGet()
    result = emit_contract(
        _plan(), _epic(), _review(), base_url="http://ai:3101",
        project_id="p1", http=http, dry_run=False,
    )
    assert result["ok"] is True
    assert result["verified"] is False
    assert result["warnings"] == ["read-back unavailable"]
    assert result["correlation"]["task_id"] == "t-123"
    assert len(http.calls) == 1


def test_readback_disabled_by_flag(monkeypatch) -> None:
    monkeypatch.delenv(HANDSHAKE_ENV, raising=False)  # flag off
    http = FakeHttpReadback(lambda i: _matching_task())
    result = emit_contract(
        _plan(), _epic(), _review(), base_url="http://ai:3101",
        project_id="p1", http=http, dry_run=False,
    )
    assert result["ok"] is True
    assert result["verified"] is False
    assert result["warnings"] == ["read-back unavailable"]
    # flag off → never read back even though get exists
    assert http.gets == []


# ── dry-run unchanged ────────────────────────────────────────────────────────


def test_dry_run_unchanged_with_handshake_on(monkeypatch) -> None:
    monkeypatch.setenv(HANDSHAKE_ENV, "1")
    http = FakeHttpReadback(lambda i: _matching_task())
    result = emit_contract(
        _plan(), _epic(), _review(), base_url="http://ai:3101",
        project_id="p1", http=http, dry_run=True,
    )
    assert result["ok"] and result["dry_run"]
    assert "correlation" not in result
    assert "verified" not in result
    assert http.calls == []  # nothing posted
    assert http.gets == []


def test_explicit_spec_id_overrides_plan_id(monkeypatch) -> None:
    monkeypatch.setenv(HANDSHAKE_ENV, "1")
    http = FakeHttpReadback(lambda i: _matching_task())
    result = emit_contract(
        _plan(), _epic(), _review(), base_url="http://ai:3101",
        project_id="p1", http=http, spec_id="custom-spec", dry_run=False,
    )
    assert result["correlation"]["spec_id"] == "custom-spec"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
