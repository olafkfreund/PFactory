"""Spec-kit artifact ingest (RFC-0015 §3.2 — PFactory #214).

GitHub's ``spec-kit`` (Spec-Driven Development) produces a ``.specify/``
workspace whose primary artifacts are a feature ``spec.md``, a ``plan.md``, a
``tasks.md``, and a project ``memory/constitution.md``. RFC-0015 positions
Factory as spec-kit's "missing back half": a spec-kit user hands the workspace
to PFactory and gets the governed engine (code → verify → CI-gated merge →
observe) spec-kit lacks.

This module maps a spec-kit workspace onto the pipeline's working artifacts:

* ``spec.md``      → :class:`~plan.models.NormalizedPlan` (title + description +
  acceptance criteria from the Requirements / Acceptance Criteria / User Stories
  sections),
* ``plan.md``      → the technical spec, folded into the epic body/summary,
* ``tasks.md``     → one :class:`~plan.decompose.models.ChildIssue` per task,
* ``constitution`` → ``epic_context.constitution`` (via
  :mod:`plan.emit.constitution`), captured on the plan as ``constitution_md``.

Layout discovery is forgiving: a ``.specify/`` root, a ``specs/<feature>/``
directory, or a flat directory containing the files all work, and **every file
is optional** — a workspace with only a ``spec.md`` still ingests (it just has
no explicit tasks; decomposition then falls back to one child per criterion).
Degrades cleanly: a missing spec falls back to the directory/feature name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from plan.decompose.models import ChildIssue, EpicPlan
from plan.models import NormalizedPlan
from spec_sources import AcceptanceCriterion, NormalizedSpec, SpecFormat, parse_markdown

# ── candidate file locations (first hit wins) ───────────────────────────────
_SPEC_NAMES = ("spec.md", "specification.md")
_PLAN_NAMES = ("plan.md", "technical-plan.md", "design.md")
_TASKS_NAMES = ("tasks.md", "todo.md")
_CONSTITUTION_NAMES = ("constitution.md",)

# Markdown helpers (shared shapes with spec_sources, kept local to avoid coupling).
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*\S)\s*$")
# A task line: "- [ ] T1: do the thing", "1. do the thing", "- do the thing".
_TASK_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s*(?:\[[ xX]\]\s*)?(.*\S)\s*$")
_TASK_ID = re.compile(r"^\s*(T\d+|TASK[-\s]?\d+)\s*[:.\-]\s*(.*\S)\s*$", re.IGNORECASE)
_REQ_HEADINGS = ("requirement", "acceptance", "user stories", "user story", "criteria")
_TASK_HEADINGS = ("task", "implementation", "todo", "work breakdown")


class SpecKitError(ValueError):
    """Raised when a path is not a usable spec-kit workspace."""


@dataclass(frozen=True)
class SpecKitWorkspace:
    """Resolved spec-kit artifact paths (any may be ``None``)."""

    root: Path
    feature: str
    spec: Path | None
    plan: Path | None
    tasks: Path | None
    constitution: Path | None


# ── discovery ───────────────────────────────────────────────────────────────


def _first_existing(directory: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


_FEATURE_ARTIFACTS = _SPEC_NAMES + _PLAN_NAMES + _TASKS_NAMES


def _feature_dir(root: Path) -> tuple[Path, str]:
    """Locate the feature directory holding the per-feature artifacts + its name.

    Order: ``<root>`` itself, then ``<root>/specs/<feature>`` (the spec-kit
    convention; the most recently modified feature dir wins when several exist),
    then ``<root>/specs``. A feature dir is recognised by *any* per-feature
    artifact (spec / plan / tasks), so a spec-less workspace still resolves.
    """
    if _first_existing(root, _FEATURE_ARTIFACTS) is not None:
        return root, root.name

    specs = root / "specs"
    if specs.is_dir():
        feature_dirs = [
            d
            for d in specs.iterdir()
            if d.is_dir() and _first_existing(d, _FEATURE_ARTIFACTS) is not None
        ]
        if feature_dirs:
            chosen = max(feature_dirs, key=lambda d: d.stat().st_mtime)
            return chosen, chosen.name
        if _first_existing(specs, _FEATURE_ARTIFACTS) is not None:
            return specs, root.name

    # No per-feature artifact anywhere — still return root so a
    # constitution-only workspace resolves; the name falls back to the dir name.
    return root, root.name


def discover_workspace(path: str | Path) -> SpecKitWorkspace:
    """Resolve a spec-kit workspace from ``path``.

    ``path`` may be a ``.specify/`` root, a repo root containing one, a
    ``specs/<feature>`` directory, or any directory holding the artifacts.
    Raises :class:`SpecKitError` only when *none* of the four artifacts exist.
    """
    root = Path(path)
    if not root.is_dir():
        raise SpecKitError(f"not a directory: {root}")

    # A repo that nests the workspace under .specify/ — descend into it.
    specify = root / ".specify"
    if specify.is_dir():
        root = specify

    feature_dir, feature = _feature_dir(root)

    spec = _first_existing(feature_dir, _SPEC_NAMES)
    plan = _first_existing(feature_dir, _PLAN_NAMES)
    tasks = _first_existing(feature_dir, _TASKS_NAMES)

    # The constitution lives in spec-kit's project ``memory/`` dir, not per
    # feature — search the workspace root and a couple of conventional spots.
    constitution = (
        _first_existing(root / "memory", _CONSTITUTION_NAMES)
        or _first_existing(root, _CONSTITUTION_NAMES)
        or _first_existing(feature_dir, _CONSTITUTION_NAMES)
        or _first_existing(root / ".factory", _CONSTITUTION_NAMES)
    )

    if not any((spec, plan, tasks, constitution)):
        raise SpecKitError(
            f"no spec-kit artifacts (spec.md / plan.md / tasks.md / constitution.md) under {root}"
        )

    return SpecKitWorkspace(
        root=root,
        feature=feature,
        spec=spec,
        plan=plan,
        tasks=tasks,
        constitution=constitution,
    )


# ── parsing ─────────────────────────────────────────────────────────────────


def _read(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _first_paragraph(text: str) -> str:
    """The first non-heading, non-empty block of prose — the spec's summary."""
    out: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            if out:
                break
            continue
        if s.startswith("#") or s.startswith(("-", "*", "+", ">")):
            if out:
                break
            continue
        out.append(s)
    return " ".join(out).strip()


def parse_spec(text: str, *, feature: str) -> NormalizedSpec:
    """Map a spec-kit ``spec.md`` to a :class:`~spec_sources.NormalizedSpec`.

    Reuses the markdown AC parser (bullets under a Requirements / Acceptance /
    User Stories heading, or ``AC#N:`` lines). When no criteria parse, returns a
    criteria-less spec rather than raising — a spec that is pure prose still
    flows (decomposition then synthesizes a single feature).
    """
    try:
        spec = parse_markdown(text, title=None)
        criteria = spec.criteria
        title = spec.title
    except Exception:  # noqa: BLE001 — a spec with no parseable ACs is criteria-less, not fatal
        criteria = ()
        title = ""

    if not title or title == "Untitled spec":
        title = _spec_title(text) or feature
    else:
        # Strip a leading "Feature Specification:" / "Spec:" label spec-kit emits.
        title = _strip_spec_label(title)
    description = _first_paragraph(text)
    return NormalizedSpec(
        title=title,
        description=description,
        criteria=tuple(criteria),
        source_format=SpecFormat.MARKDOWN,
    )


def _strip_spec_label(title: str) -> str:
    """Drop a leading "Feature Specification:" / "Spec:" label from a title."""
    return re.sub(r"(?i)^(feature\s+)?spec(ification)?\s*[:.\-]\s*", "", title).strip() or title


def _spec_title(text: str) -> str:
    for ln in text.splitlines():
        m = _HEADING.match(ln)
        if m:
            return _strip_spec_label(m.group(1))
    return ""


def _section_lines(text: str, heading_words: tuple[str, ...]) -> list[str]:
    """Collect the body lines under any heading matching ``heading_words``."""
    out: list[str] = []
    in_section = False
    for ln in text.splitlines():
        h = _HEADING.match(ln)
        if h:
            in_section = any(w in h.group(1).lower() for w in heading_words)
            continue
        if in_section and ln.strip():
            out.append(ln)
    return out


def parse_tasks(text: str) -> list[tuple[str, str]]:
    """Map a spec-kit ``tasks.md`` to ``[(task_id, title)]`` entries.

    Prefers list items under a Tasks / Implementation heading; falls back to any
    list item in the doc. A leading ``T1:`` / ``TASK-1:`` id is captured when
    present, else a stable ``T{n}`` id is synthesized.
    """
    candidate = _section_lines(text, _TASK_HEADINGS)
    if not candidate:
        candidate = text.splitlines()

    tasks: list[tuple[str, str]] = []
    for ln in candidate:
        m = _TASK_LINE.match(ln)
        if not m:
            continue
        item = m.group(1).strip()
        idm = _TASK_ID.match(item)
        if idm:
            tasks.append((_normalize_task_id(idm.group(1)), idm.group(2).strip()))
        else:
            tasks.append(("", item))

    # Assign stable T{n} ids where one was not given, keeping any explicit ids.
    numbered: list[tuple[str, str]] = []
    for i, (tid, title) in enumerate(tasks, start=1):
        numbered.append((tid or f"T{i}", title))
    return numbered


def _normalize_task_id(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return f"T{digits}" if digits else raw.strip().upper()


# ── public ingest ───────────────────────────────────────────────────────────


def _spec_to_plan(spec: NormalizedSpec, *, raw_text: str) -> NormalizedPlan:
    return NormalizedPlan.from_spec(
        spec,
        seq=1,
        source_channel="cli",
        raw_text=raw_text,
    ).model_copy(update={"source_format": "spec-kit", "target_kind": "software"})


def _epic_from_tasks(
    plan: NormalizedPlan,
    tasks: list[tuple[str, str]],
    *,
    plan_md: str | None,
) -> EpicPlan:
    """Build an :class:`EpicPlan` from spec-kit tasks (one child per task).

    The ``plan.md`` technical content is folded into the epic body so the
    governed pipeline carries spec-kit's design intent. When there are no tasks,
    returns an epic with no children and lets the normal decomposer fill them in
    (one feature per acceptance criterion) downstream.
    """
    children: list[ChildIssue] = []
    for i, (tid, title) in enumerate(tasks, start=1):
        children.append(
            ChildIssue(
                key=f"C{i}",
                title=title[:120],
                body=title,
                kind="task",
                labels=["plan-type:software", "source:spec-kit", f"speckit:{tid}"],
                complexity="standard",
            )
        )

    epic_body_parts = [plan.description or plan.title]
    if plan_md and plan_md.strip():
        epic_body_parts.append("\n## Technical plan (from spec-kit plan.md)\n")
        epic_body_parts.append(plan_md.strip())
    summary = (
        f"{len(children)} task(s) ingested from a spec-kit workspace"
        if children
        else "spec-kit workspace (no explicit tasks; decompose from criteria)"
    )
    return EpicPlan(
        plan_id=plan.plan_id,
        epic_title=plan.title,
        epic_body="\n".join(epic_body_parts).strip(),
        children=children,
        summary=summary,
    )


def ingest_speckit(
    path: str | Path,
) -> tuple[NormalizedPlan, EpicPlan, dict]:
    """Ingest a spec-kit workspace into ``(NormalizedPlan, EpicPlan, constitution)``.

    * ``NormalizedPlan`` — title/description/criteria from ``spec.md``, with the
      project constitution captured on ``constitution_md`` and
      ``source_format="spec-kit"``.
    * ``EpicPlan`` — one child per ``tasks.md`` task (or no children, so the
      normal decomposer runs), with ``plan.md`` folded into the epic body.
    * ``constitution`` — the ``epic_context.constitution`` block built from
      ``memory/constitution.md`` (``available: false`` when absent).

    Raises :class:`SpecKitError` only when the path holds no spec-kit artifacts.
    """
    ws = discover_workspace(path)

    spec_text = _read(ws.spec)
    plan_md = _read(ws.plan)
    tasks_text = _read(ws.tasks)
    constitution_text = _read(ws.constitution)

    if spec_text is not None:
        spec = parse_spec(spec_text, feature=ws.feature)
        raw_text = spec_text
    else:
        # No spec.md — synthesize a minimal spec from the feature name so the
        # plan/tasks/constitution still flow through the pipeline.
        spec = NormalizedSpec(
            title=ws.feature,
            description="",
            criteria=(),
            source_format=SpecFormat.MARKDOWN,
        )
        raw_text = plan_md or tasks_text or ""

    plan = _spec_to_plan(spec, raw_text=raw_text)
    if constitution_text is not None:
        plan = plan.model_copy(update={"constitution_md": constitution_text})

    tasks = parse_tasks(tasks_text) if tasks_text else []
    epic = _epic_from_tasks(plan, tasks, plan_md=plan_md)

    # Build the constitution block via the shared (RFC-0015 §3.1) builder, so
    # spec-kit ingest and the recon path produce an identical contract block.
    from plan.emit.constitution import build_constitution_block  # noqa: PLC0415

    constitution = build_constitution_block(
        constitution_text, source="spec-kit:memory/constitution.md"
    )

    return plan, epic, constitution


def acceptance_criteria(spec: NormalizedSpec) -> tuple[AcceptanceCriterion, ...]:
    """Convenience accessor used by callers/tests for the parsed ACs."""
    return spec.criteria
