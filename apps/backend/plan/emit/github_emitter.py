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

import time
from typing import TYPE_CHECKING, Any, Callable, Protocol

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from plan.decompose.models import ChildIssue, EpicPlan
    from plan.models import NormalizedPlan
    from plan.review.models import PlanReview


# Substrings that mark a *transient* GitHub failure worth retrying — secondary
# rate limits / abuse detection / throttling (#119). Matched case-insensitively
# against the GhRunnerError message, which carries gh's stderr. A genuine error
# (bad label, auth, validation) won't match, so it fails fast without retrying.
_RETRYABLE_MARKERS = (
    "rate limit",
    "secondary rate",
    "abuse detection",
    "retry after",
    "submitted too quickly",
    "exceeded a secondary",
    "http 403",
    "http 429",
)


def _is_retryable(exc: Exception) -> bool:
    """True when ``exc`` looks like a transient GitHub rate-limit/throttle."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _RETRYABLE_MARKERS)


def _create_issue_resilient(
    gh: GhRunner,
    title: str,
    body: str,
    labels: list[str],
    *,
    attempts: int = 4,
    sleep_fn: Callable[[float], None] = time.sleep,
    backoff_base: float = 2.0,
) -> int:
    """Create one issue, retrying transient rate-limit failures with backoff.

    Retries only on a :func:`_is_retryable` error (secondary rate limit / abuse
    detection / 403 throttle), sleeping ``backoff_base ** attempt`` seconds
    between tries. A non-retryable error (or the final attempt) re-raises so the
    caller can record it and move on — see :func:`emit_to_github` (#119).
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return gh.create_issue(title, body, labels)
        except Exception as exc:  # noqa: BLE001 — classify then re-raise
            last = exc
            if not _is_retryable(exc) or attempt == attempts - 1:
                raise
            sleep_fn(backoff_base ** attempt)
    raise last  # pragma: no cover - loop always returns or raises above


def _dedup(labels: list[str]) -> list[str]:
    """De-duplicate labels, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for label in labels:
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


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
    # Non-fatal issues (e.g. best-effort sub-issue linking). Unlike ``errors``,
    # warnings do NOT block the caller's "emit succeeded" decision — the epic +
    # children were still created.
    warnings: list[str] = Field(default_factory=list)


def _epic_payload(
    epic: EpicPlan, *, extra_labels: list[str] | None, meta_block: str = ""
) -> dict[str, Any]:
    """Build the GitHub issue payload for the epic, with a child checklist."""
    lines = [epic.epic_body.rstrip(), ""] if epic.epic_body.strip() else []
    if epic.children:
        lines.append("### Child issues")
        for child in epic.children:
            lines.append(f"- [ ] {child.key}: {child.title}")
    body = "\n".join(lines).strip()
    if meta_block:
        body = f"{body}\n\n{meta_block}".strip()
    labels = _dedup(["epic", *(extra_labels or [])])
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
    meta_block: str = "",
) -> dict[str, Any]:
    """Build the GitHub issue payload for one child issue."""
    labels = _dedup([*child.labels, *(extra_labels or [])])
    body = _child_body(child, epic_number=epic_number)
    if meta_block:
        body = f"{body}\n\n{meta_block}".strip()
    return {
        "key": child.key,
        "title": child.title,
        "body": body,
        "labels": labels,
        "kind": child.kind,
        "depends_on": list(child.depends_on),
    }


def emit_to_github(
    epic: EpicPlan,
    *,
    repo: str,
    review: PlanReview | None = None,
    plan: NormalizedPlan | None = None,
    gh: GhRunner | None = None,
    dry_run: bool = True,
    extra_labels: list[str] | None = None,
    meta_block: str = "",
    existing_epic_number: int | None = None,
    existing_child_numbers: dict[str, int] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
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
        existing_epic_number: When set, reuse this epic instead of creating a new
            one — idempotent recovery after a partial emit, so a retry never
            duplicates the epic (#119).
        existing_child_numbers: ``{child.key: issue#}`` already created in a prior
            attempt; those children are reused (not re-created), and only the
            missing ones are created (#119).
        sleep_fn: Injected sleep seam for the retry backoff (tests pass a no-op).

    Behaviour:
        * Live emit refuses an ungoverned plan: if ``review`` is provided and
          not ready, returns an :class:`EmitResult` with an error and creates
          nothing.
        * Dry-run fills :attr:`EmitResult.planned` (epic first, then children).
        * Live emit reuses any ``existing_epic_number``/``existing_child_numbers``
          (idempotent), creates the rest with retry-on-rate-limit, CONTINUES past
          a single child failure, then links the newly-created children as
          sub-issues — recording numbers in :attr:`EmitResult.child_numbers`.
    """
    if not dry_run and review is not None and not review.ready_to_emit(plan):
        return EmitResult(
            dry_run=False,
            errors=[
                "refusing to emit an ungoverned plan: review is not ready_to_emit "
                "(gates/approval/readiness not satisfied)"
            ],
        )

    epic_pl = _epic_payload(epic, extra_labels=extra_labels, meta_block=meta_block)

    if dry_run:
        planned: list[dict[str, Any]] = [epic_pl]
        planned.extend(
            _child_payload(c, epic_number=None, extra_labels=extra_labels,
                           meta_block=meta_block)
            for c in epic.children
        )
        return EmitResult(dry_run=True, planned=planned)

    if gh is None:
        return EmitResult(
            dry_run=False,
            errors=["live emit requested but no `gh` runner was injected"],
        )

    errors: list[str] = []

    # Idempotency (#119): reuse an epic already created for this session rather
    # than creating a duplicate. The caller (PlanService.emit) passes the prior
    # epic number whenever a previous attempt created it — even one that then
    # failed mid-children — so a retry recovers instead of spawning epic #N+1.
    if existing_epic_number is not None:
        epic_number = existing_epic_number
    else:
        try:
            epic_number = _create_issue_resilient(
                gh, epic_pl["title"], epic_pl["body"], list(epic_pl["labels"]),
                sleep_fn=sleep_fn,
            )
        except Exception as exc:  # noqa: BLE001
            # No epic means nothing to attach children to — fail the whole emit
            # (and create nothing else), leaving a clean retry.
            return EmitResult(
                dry_run=False,
                errors=[f"failed to create epic issue: {exc}"],
            )

    # Resilient + idempotent child creation (#119): reuse children already made
    # in a prior attempt (keyed by child.key), retry transient rate-limit errors
    # with backoff, and CONTINUE past a single child failure so one throttled
    # child can't strand the rest. A failed child is recorded in ``errors`` and
    # re-attempted on the next emit (the epic + good children are reused).
    reused = dict(existing_child_numbers or {})
    child_numbers: dict[str, int] = {}
    newly_created: list[int] = []
    for child in epic.children:
        if child.key in reused:
            child_numbers[child.key] = reused[child.key]
            continue
        pl = _child_payload(child, epic_number=epic_number, extra_labels=extra_labels,
                            meta_block=meta_block)
        try:
            number = _create_issue_resilient(
                gh, pl["title"], pl["body"], list(pl["labels"]), sleep_fn=sleep_fn,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"failed to create child issue {child.key!r}: {exc}")
            continue
        child_numbers[child.key] = number
        newly_created.append(number)

    # Link only the children we created THIS call as sub-issues (best-effort);
    # reused children were already linked by the attempt that created them.
    warnings: list[str] = []
    link = getattr(gh, "link_sub_issue", None)
    if callable(link):
        for number in newly_created:
            try:
                link(epic_number, number)
            except Exception as exc:  # pragma: no cover - defensive
                # Linking is best-effort: the issues exist regardless, so this is
                # a warning, not a fatal error that should block the emit.
                warnings.append(f"failed to link sub-issue #{number}: {exc}")

    return EmitResult(
        dry_run=False,
        epic_number=epic_number,
        child_numbers=child_numbers,
        errors=errors,
        warnings=warnings,
    )
