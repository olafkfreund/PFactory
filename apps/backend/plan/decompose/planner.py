"""Decompose-stage planner (issue #6).

Turns a :class:`~plan.models.NormalizedPlan` into an
:class:`~plan.decompose.models.EpicPlan` — one epic plus the child issues that
implement it. Two paths share the same entry point:

* a deterministic **heuristic** decomposer (the tested default), which maps each
  acceptance criterion to a ``feature`` child and, when the plan type's stages
  call for it, appends ``testing`` / ``cicd`` children that depend on the
  feature work; and
* an **LLM seam** (:func:`decompose_with_llm`) that prompts a model for an
  ``EpicPlan`` as JSON and parses it tolerantly, falling back to the heuristic
  on any failure. No real model is called here and no SDK is imported, so this
  module stays importable everywhere.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from plan import plan_types
from plan.decompose.models import ChildIssue, EpicPlan
from plan.models import NormalizedPlan
from plan.plan_types import PlanTypeDescriptor

# ── LLM seam ───────────────────────────────────────────────────────────────


@runtime_checkable
class Decomposer(Protocol):
    """Minimal LLM contract: turn a prompt into a completion string.

    Any object exposing ``complete(prompt) -> str`` satisfies this; we keep the
    surface tiny so a real adapter (or a test fake) can be dropped in without an
    SDK dependency.
    """

    def complete(self, prompt: str) -> str:  # pragma: no cover - protocol only
        ...


def _llm_complete(llm: object, prompt: str) -> str:
    """Call an llm object tolerantly, accepting a few common method names."""
    for attr in ("complete", "generate", "__call__"):
        fn = getattr(llm, attr, None)
        if callable(fn):
            return str(fn(prompt))
    raise TypeError("llm object has no complete/generate/__call__ method")


# ── shared helpers ─────────────────────────────────────────────────────────


def _title_from_text(text: str, *, max_len: int = 80) -> str:
    """Derive a concise issue title from a (possibly long) criterion/description."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    first = first.lstrip("-*0123456789. ").strip() or text.strip()
    if len(first) > max_len:
        first = first[: max_len - 1].rstrip() + "…"
    return first or "Untitled"


def _kind_label(kind: str) -> str:
    """Per-kind label used downstream (e.g. ``kind:feature``)."""
    return f"kind:{kind}"


def _labels(descriptor: PlanTypeDescriptor, kind: str) -> list[str]:
    """Labels for a child: the plan-type name plus a per-kind label."""
    return [f"plan-type:{descriptor.name}", _kind_label(kind)]


def _epic_body(plan: NormalizedPlan, children: list[ChildIssue]) -> str:
    """Epic body = plan description + a short generated overview of the breakdown."""
    feature_count = sum(1 for c in children if c.kind == "feature")
    extra = [c.kind for c in children if c.kind != "feature"]
    overview_bits = [f"{feature_count} feature issue(s)"]
    overview_bits.extend(f"a {k} issue" for k in extra)
    overview = "This epic is decomposed into " + ", ".join(overview_bits) + "."
    description = plan.description.strip()
    return f"{description}\n\n{overview}" if description else overview


# ── heuristic decomposer ───────────────────────────────────────────────────


def heuristic_decompose(
    plan: NormalizedPlan, descriptor: PlanTypeDescriptor
) -> EpicPlan:
    """Deterministically map a plan to an epic + child issues.

    One ``feature`` child per acceptance criterion (keys ``C1``..``Cn``); if the
    plan has no criteria, a single feature child is synthesized from the
    title/description. When the descriptor's stages enable testing and/or CI/CD,
    matching children are appended that depend on every feature child.
    """
    children: list[ChildIssue] = []

    if plan.criteria:
        for i, crit in enumerate(plan.criteria, 1):
            children.append(
                ChildIssue(
                    key=f"C{i}",
                    title=_title_from_text(crit.text),
                    body=crit.text,
                    kind="feature",
                    labels=_labels(descriptor, "feature"),
                    complexity="standard",
                    acceptance_criteria=[crit.text],
                )
            )
    else:
        children.append(
            ChildIssue(
                key="C1",
                title=_title_from_text(plan.title),
                body=plan.description or plan.title,
                kind="feature",
                labels=_labels(descriptor, "feature"),
                complexity="standard",
                acceptance_criteria=[plan.description or plan.title],
            )
        )

    feature_keys = [c.key for c in children]
    next_index = len(children) + 1

    if descriptor.stages.synthesize_testing:
        children.append(
            ChildIssue(
                key=f"C{next_index}",
                title=f"Set up testing for {plan.title}",
                body=f"Add automated tests covering the features of '{plan.title}'.",
                kind="testing",
                labels=_labels(descriptor, "testing"),
                depends_on=list(feature_keys),
                complexity="standard",
            )
        )
        next_index += 1

    if descriptor.stages.synthesize_cicd:
        children.append(
            ChildIssue(
                key=f"C{next_index}",
                title=f"Set up CI/CD for {plan.title}",
                body=f"Add a CI/CD pipeline that builds and ships '{plan.title}'.",
                kind="cicd",
                labels=_labels(descriptor, "cicd"),
                depends_on=list(feature_keys),
                complexity="standard",
            )
        )
        next_index += 1

    return EpicPlan(
        plan_id=plan.plan_id,
        epic_title=plan.title,
        epic_body=_epic_body(plan, children),
        children=children,
        summary=f"{len(children)} child issue(s) under epic '{plan.title}'.",
    )


# ── LLM-backed decomposer ──────────────────────────────────────────────────


def build_decompose_prompt(
    plan: NormalizedPlan, descriptor: PlanTypeDescriptor
) -> str:
    """Build an instruction asking the model for an ``EpicPlan`` as JSON.

    The schema described mirrors :class:`EpicPlan` / :class:`ChildIssue` exactly,
    so a compliant completion parses straight back into the contract.
    """
    criteria_lines = (
        "\n".join(f"- {c.id}: {c.text}" for c in plan.criteria)
        or "- (no explicit acceptance criteria provided)"
    )
    stages = descriptor.stages
    stage_notes = []
    if stages.synthesize_testing:
        stage_notes.append("include one 'testing' child depending on all features")
    if stages.synthesize_cicd:
        stage_notes.append("include one 'cicd' child depending on all features")
    stage_text = "; ".join(stage_notes) or "no extra testing/cicd children needed"

    return f"""\
You are the Decompose stage of a planning factory. Break the plan below into an
epic and its child issues, then return ONLY a single JSON object (no prose,
no markdown fences) matching this schema:

{{
  "plan_id": "{plan.plan_id}",
  "epic_title": "string",
  "epic_body": "string",
  "summary": "string",
  "children": [
    {{
      "key": "C1",                      // stable, unique within this plan
      "title": "string",
      "body": "string",
      "kind": "feature|task|testing|cicd|docs|infra|research|chore",
      "labels": ["string"],
      "depends_on": ["C-key", ...],     // other child keys only
      "complexity": "simple|standard|complex",
      "acceptance_criteria": ["string"]
    }}
  ]
}}

Rules:
- Create one 'feature' child per acceptance criterion.
- {stage_text}.
- Dependencies must reference existing child keys only; no cycles, no self-deps.
- Plan type: {descriptor.name}.

Plan title: {plan.title}
Plan description: {plan.description}
Acceptance criteria:
{criteria_lines}
"""


def _extract_first_json_object(text: str) -> dict:
    """Extract and parse the first balanced ``{...}`` JSON object from text.

    Tolerant of surrounding prose or markdown fences a model might emit.
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in completion")
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unbalanced JSON object in completion")


def decompose_with_llm(
    plan: NormalizedPlan, descriptor: PlanTypeDescriptor, llm: object
) -> EpicPlan:
    """Prompt ``llm`` for an ``EpicPlan`` JSON and parse it.

    Falls back to :func:`heuristic_decompose` on any prompt, parse, or
    validation failure so the stage always yields a usable EpicPlan.
    """
    try:
        prompt = build_decompose_prompt(plan, descriptor)
        completion = _llm_complete(llm, prompt)
        data = _extract_first_json_object(completion)
        data.setdefault("plan_id", plan.plan_id)
        epic = EpicPlan(**data)
        if not epic.children:
            raise ValueError("llm produced an EpicPlan with no children")
        return epic
    except Exception:
        return heuristic_decompose(plan, descriptor)


# ── entry point ────────────────────────────────────────────────────────────


def decompose(
    plan: NormalizedPlan,
    *,
    descriptor: PlanTypeDescriptor | None = None,
    llm: object | None = None,
) -> EpicPlan:
    """Decompose a :class:`NormalizedPlan` into an :class:`EpicPlan`.

    Selects the plan-type descriptor when one is not supplied, then routes to the
    LLM seam if an ``llm`` object is given or to the deterministic heuristic
    otherwise.
    """
    if descriptor is None:
        descriptor = plan_types.select_for(plan)
    if llm is not None:
        return decompose_with_llm(plan, descriptor, llm)
    return heuristic_decompose(plan, descriptor)
