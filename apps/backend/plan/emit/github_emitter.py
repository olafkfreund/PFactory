"""Emit stage — render an :class:`EpicPlan` into GitHub issues (issue #18).

This is the first half of the Emit stage: it turns a governed
:class:`~plan.decompose.models.EpicPlan` into a GitHub *epic* issue plus one
child issue per :class:`~plan.decompose.models.ChildIssue`, wiring the children
as sub-issues of the epic.

The design is **dry-run by default** and **injectable**: nothing touches GitHub
unless ``dry_run=False`` and a concrete ``gh`` runner is supplied. The runner is
a small protocol — ``create_issue(title, body, labels) -> int`` and an optional
``link_sub_issue(parent, child)`` — so tests inject a recording fake and the
real implementation wraps ``gh issue create`` (or the repo's
``runners/github`` provider in :mod:`apps.backend.runners.github`).

A plan is only emitted live when its :class:`~plan.review.models.PlanReview`
reports :meth:`~plan.review.models.PlanReview.ready_to_emit` — we refuse to
create issues for an ungoverned plan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from plan.decompose.models import ChildIssue, EpicPlan
    from plan.review.models import PlanReview


class GhRunner(Protocol):
    """Minimal GitHub runner contract used by :func:`emit_to_github`.

    The real runner wraps ``gh issue create`` / the repo's
    ``runners/github`` provider; tests inject a fake that records calls and
    returns incrementing issue numbers.
    """

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        """Create an issue and return its number."""
        ...

    def link_sub_issue(self, parent: int, child: int) -> None:  # pragma: no cover
        """Optionally link ``child`` as a sub-issue of ``parent``."""
        ...


class EmitResult(BaseModel):
    """Outcome of an emit attempt — dry-run preview or live creation."""

    dry_run: bool
    epic_number: int | None = None
    child_numbers: dict[str, int] = Field(default_factory=dict)
    planned: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def _epic_payload(epic: EpicPlan, *, extra_labels: list[str] | None) -> dict[str, Any]:
    """Build the GitHub issue payload for the epic, with a child checklist."""
    lines = [epic.epic_body.rstrip(), ""] if epic.epic_body.strip() else []
    if epic.children:
        lines.append("### Child issues")
        for child in epic.children:
            lines.append(f"- [ ] {child.key}: {child.title}")
    body = "\n".join(lines).strip()
    labels = ["epic", *(extra_labels or [])]
    return {"title": epic.epic_title, "body": body, "labels": labels, "kind": "epic"}


def _child_body(child: ChildIssue, *, epic_number: int | None) -> str:
    """Compose a child issue body: its body + acceptance criteria + epic ref."""
    parts = [child.body.rstrip()] if child.body.strip() else []
    if child.acceptance_criteria:
        parts.append("### Acceptance criteria")
        parts.extend(f"- [ ] {ac}" for ac in child.acceptance_criteria)
    ref = f"#{epic_number}" if epic_number is not None else "the epic"
    parts.append(f"Part of {ref}")
    return "\n\n".join(p for p in parts if p).strip()


def _child_payload(
    child: ChildIssue,
    *,
    epic_number: int | None,
    extra_labels: list[str] | None,
) -> dict[str, Any]:
    """Build the GitHub issue payload for one child issue."""
    labels = [*child.labels, *(extra_labels or [])]
    return {
        "key": child.key,
        "title": child.title,
        "body": _child_body(child, epic_number=epic_number),
        "labels": labels,
        "kind": child.kind,
        "depends_on": list(child.depends_on),
    }


def emit_to_github(
    epic: EpicPlan,
    *,
    repo: str,
    review: PlanReview | None = None,
    gh: GhRunner | None = None,
    dry_run: bool = True,
    extra_labels: list[str] | None = None,
) -> EmitResult:
    """Emit an :class:`EpicPlan` as a GitHub epic + child issues.

    Args:
        epic: The decomposed plan to render.
        repo: Target ``owner/name`` repository (carried for the real runner).
        review: Governance review; when emitting live it must report
            :meth:`~plan.review.models.PlanReview.ready_to_emit`.
        gh: Injected runner (see :class:`GhRunner`); required for live emit.
        dry_run: When ``True`` (default) no issues are created — the payloads
            that *would* be sent are returned in :attr:`EmitResult.planned`.
        extra_labels: Labels applied to every created issue (e.g. handoff tags).

    Behaviour:
        * Live emit refuses an ungoverned plan: if ``review`` is provided and
          not ready, returns an :class:`EmitResult` with an error and creates
          nothing.
        * Dry-run fills :attr:`EmitResult.planned` (epic first, then children).
        * Live emit creates the epic, then each child, then links the children
          as sub-issues, recording numbers in :attr:`EmitResult.child_numbers`.
    """
    if not dry_run and review is not None and not review.ready_to_emit():
        return EmitResult(
            dry_run=False,
            errors=[
                "refusing to emit an ungoverned plan: "
                "review is not ready_to_emit (gates/approval not satisfied)"
            ],
        )

    epic_pl = _epic_payload(epic, extra_labels=extra_labels)

    if dry_run:
        planned: list[dict[str, Any]] = [epic_pl]
        planned.extend(
            _child_payload(c, epic_number=None, extra_labels=extra_labels)
            for c in epic.children
        )
        return EmitResult(dry_run=True, planned=planned)

    if gh is None:
        return EmitResult(
            dry_run=False,
            errors=["live emit requested but no `gh` runner was injected"],
        )

    errors: list[str] = []
    epic_number = gh.create_issue(
        epic_pl["title"], epic_pl["body"], list(epic_pl["labels"])
    )

    child_numbers: dict[str, int] = {}
    for child in epic.children:
        pl = _child_payload(child, epic_number=epic_number, extra_labels=extra_labels)
        number = gh.create_issue(pl["title"], pl["body"], list(pl["labels"]))
        child_numbers[child.key] = number

    link = getattr(gh, "link_sub_issue", None)
    if callable(link):
        for number in child_numbers.values():
            try:
                link(epic_number, number)
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"failed to link sub-issue #{number}: {exc}")

    return EmitResult(
        dry_run=False,
        epic_number=epic_number,
        child_numbers=child_numbers,
        errors=errors,
    )
