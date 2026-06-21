"""Render spec-kit-shaped spec.md / plan.md / tasks.md from a Task Contract.

RFC-0015 §3.3 — the human-readable mirror of the machine contract. This is the
inverse of the §3.2 ingest (:mod:`plan.ingest.speckit`): given an emitted
RFC-0002 Task Contract, produce the three canonical Markdown documents a
spec-kit user (and the CFactory cockpit) can read directly.

It is also the **durable fix for the cockpit "Overview wall-of-text" bug class**:
instead of stringifying nested contract structures at a human, the cockpit
renders these canonical, sectioned Markdown artifacts.

Pure + dependency-light: every function takes a contract ``dict`` and returns a
Markdown ``str``. Defensive reads throughout, so a partial/older contract still
renders (missing sections are simply omitted). Never raises.
"""

from __future__ import annotations

from typing import Any

# The three canonical document names (match spec-kit's artifact names).
SPEC_DOC = "spec.md"
PLAN_DOC = "plan.md"
TASKS_DOC = "tasks.md"

_GENERATED_BY = "pfactory"


def _g(contract: Any, key: str, default: Any = None) -> Any:
    """Defensive dict get — tolerates a non-dict contract."""
    if isinstance(contract, dict):
        return contract.get(key, default)
    return default


def _feature(contract: dict[str, Any]) -> str:
    return str(_g(contract, "feature") or _g(contract, "title") or "Untitled plan")


def _front_matter(contract: dict[str, Any], doc: str) -> str:
    prov = _g(contract, "provenance") or {}
    plan_id = prov.get("plan_id") if isinstance(prov, dict) else None
    ck = _g(contract, "correlation_key")
    lines = ["---", f"title: {_feature(contract)}", f"doc: {doc}"]
    if plan_id:
        lines.append(f"plan_id: {plan_id}")
    if ck is not None:
        lines.append(f"correlation_key: {ck}")
    lines.append(f"generated_by: {_GENERATED_BY}")
    lines.append("---\n")
    return "\n".join(lines)


def _acceptance_criteria(contract: dict[str, Any]) -> list[str]:
    """The contract's global acceptance criteria (``final_acceptance``)."""
    fa = _g(contract, "final_acceptance")
    if isinstance(fa, list):
        return [str(x) for x in fa if str(x).strip()]
    return []


def _subtasks(contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten ``phases[].subtasks[]`` in phase order."""
    out: list[dict[str, Any]] = []
    for phase in _g(contract, "phases") or []:
        if not isinstance(phase, dict):
            continue
        for st in phase.get("subtasks") or []:
            if isinstance(st, dict):
                out.append(st)
    return out


def _constitution(contract: dict[str, Any]) -> dict[str, Any] | None:
    ec = _g(contract, "epic_context")
    if isinstance(ec, dict):
        c = ec.get("constitution")
        if isinstance(c, dict) and c.get("available"):
            return c
    return None


# ── spec.md ──────────────────────────────────────────────────────────────────


def render_spec_md(contract: dict[str, Any], *, description: str = "") -> str:
    """Render the feature spec: title, overview, and acceptance criteria."""
    parts = [_front_matter(contract, SPEC_DOC), f"# {_feature(contract)}\n"]

    if description.strip():
        parts.append(description.strip() + "\n")

    workflow = _g(contract, "workflow_type")
    if workflow:
        parts.append(f"> Workflow: **{workflow}**\n")

    acs = _acceptance_criteria(contract)
    parts.append("## Acceptance Criteria\n")
    if acs:
        parts.append("\n".join(f"- {ac}" for ac in acs) + "\n")
    else:
        parts.append("_No explicit acceptance criteria recorded._\n")

    constitution = _constitution(contract)
    if constitution:
        parts.append(_constitution_section(constitution))

    return "\n".join(parts).rstrip() + "\n"


def _constitution_section(constitution: dict[str, Any]) -> str:
    lines = ["## Governing principles (constitution)\n"]
    src = constitution.get("source")
    if src:
        lines.append(f"_Source: `{src}`_\n")
    for p in constitution.get("principles") or []:
        if not isinstance(p, dict):
            continue
        mark = " **[enforced]**" if p.get("enforceable") else ""
        pid = str(p.get("id", "")).strip()
        text = str(p.get("text", "")).strip()
        if text:
            lines.append(f"- **{pid}**{mark}: {text}")
    return "\n".join(lines) + "\n"


# ── plan.md ──────────────────────────────────────────────────────────────────


def render_plan_md(contract: dict[str, Any], *, technical_notes: str = "") -> str:
    """Render the technical plan: services, phases (with parallelism), routing."""
    parts = [_front_matter(contract, PLAN_DOC), f"# Plan — {_feature(contract)}\n"]

    services = _g(contract, "services_involved")
    if isinstance(services, list) and services:
        parts.append("## Services\n")
        parts.append("\n".join(f"- `{s}`" for s in services) + "\n")

    if technical_notes.strip():
        parts.append("## Technical approach\n")
        parts.append(technical_notes.strip() + "\n")

    phases = _g(contract, "phases") or []
    if phases:
        parts.append("## Phases\n")
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            num = phase.get("phase", "?")
            name = phase.get("name") or phase.get("type") or f"phase {num}"
            ptype = phase.get("type", "")
            parallel = phase.get("parallel_safe")
            tag = " · parallel-safe" if parallel else ""
            type_tag = f" ({ptype})" if ptype else ""
            parts.append(f"### Phase {num}: {name}{type_tag}{tag}")
            deps = phase.get("depends_on") or []
            if deps:
                parts.append(f"_Depends on: {', '.join(str(d) for d in deps)}_")
            for st in phase.get("subtasks") or []:
                if isinstance(st, dict):
                    parts.append(f"- `{st.get('id', '?')}` — {_subtask_title(st)}")
            parts.append("")

    routing = _routing(contract)
    if routing:
        parts.append("## Difficulty, risk & autonomy\n")
        parts.append(f"- **Difficulty:** {routing.get('difficulty', '?')}")
        parts.append(f"- **Risk:** {routing.get('risk', '?')}")
        verdict = routing.get("autonomy", "")
        reason = routing.get("reason", "")
        parts.append(f"- **Autonomy:** {verdict}" + (f" — {reason}" if reason else ""))
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _routing(contract: dict[str, Any]) -> dict[str, str] | None:
    execution = _g(contract, "execution")
    routing = execution.get("routing") if isinstance(execution, dict) else None
    if not isinstance(routing, dict):
        return None
    autonomy = routing.get("autonomy") if isinstance(routing.get("autonomy"), dict) else {}
    out = {
        "difficulty": str(routing.get("difficulty", "")),
        "risk": str(routing.get("risk", "")),
        "autonomy": str(autonomy.get("verdict", "")),
        "reason": str(autonomy.get("reason", "")),
    }
    if not (out["difficulty"] or out["risk"] or out["autonomy"]):
        return None
    return out


# ── tasks.md ─────────────────────────────────────────────────────────────────


def _subtask_title(st: dict[str, Any]) -> str:
    """A one-line title for a subtask (first line of its description)."""
    desc = str(st.get("description") or st.get("title") or st.get("id") or "task")
    return desc.splitlines()[0].strip() if desc.strip() else str(st.get("id", "task"))


def render_tasks_md(contract: dict[str, Any]) -> str:
    """Render the task checklist: one ``- [ ]`` item per subtask, with deps + ACs."""
    parts = [_front_matter(contract, TASKS_DOC), f"# Tasks — {_feature(contract)}\n"]

    subtasks = _subtasks(contract)
    if not subtasks:
        parts.append("_No tasks decomposed yet._\n")
        return "\n".join(parts).rstrip() + "\n"

    for st in subtasks:
        sid = st.get("id", "?")
        parts.append(f"- [ ] **{sid}** — {_subtask_title(st)}")
        deps = st.get("depends_on") or []
        if deps:
            parts.append(f"  - depends on: {', '.join(str(d) for d in deps)}")
        complexity = st.get("complexity")
        if complexity:
            parts.append(f"  - complexity: {complexity}")
        for ac in st.get("acceptance_criteria") or []:
            if str(ac).strip():
                parts.append(f"  - AC: {ac}")
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


# ── bundle ───────────────────────────────────────────────────────────────────


def render_speckit_bundle(
    contract: dict[str, Any],
    *,
    description: str = "",
    technical_notes: str = "",
) -> dict[str, str]:
    """Render all three canonical documents, keyed by filename.

    ``description`` (the spec overview) and ``technical_notes`` (the design
    rationale) are not carried on the plan block of the contract, so callers that
    have them (the plan session) may pass them in; both default to empty and the
    docs still render. Never raises.
    """
    try:
        return {
            SPEC_DOC: render_spec_md(contract, description=description),
            PLAN_DOC: render_plan_md(contract, technical_notes=technical_notes),
            TASKS_DOC: render_tasks_md(contract),
        }
    except Exception:  # noqa: BLE001 — the mirror is best-effort; never break emit
        return {}
