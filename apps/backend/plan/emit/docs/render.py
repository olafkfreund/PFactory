"""Render a plan session into a deterministic Markdown doc + registry entry.

Pure: no network, no clock, no filesystem — so it unit-tests in isolation and
produces byte-identical output for identical input (idempotency hinges on this).
The ``updated_at`` stamp is injected by the caller (the target), not read here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .bundle import DocBundle

if TYPE_CHECKING:  # avoid import cycles / heavy imports at module load
    from plan.service import PlanSession

# Marks every emission so the enrichment connectors can skip PFactory's own
# output (the feedback-loop guard from the design §6/§7).
GENERATED_BY = "pfactory"


def _h(level: int, text: str) -> str:
    return f"{'#' * level} {text}\n"


def _correlation_key(session: PlanSession) -> str:
    return session.correlation_key or f"pf-{session.session_id}"


def _dependencies(session: PlanSession) -> list[str]:
    """Dependency refs derived from the enrichment bundle (infra + knowledge).

    These are what the registry exposes as the cross-factory dependency list.
    Open dicts, so we read defensively.
    """
    deps: list[str] = []
    enr = session.plan.enrichment
    for item in enr.infra or []:
        if isinstance(item, dict):
            ref = item.get("adapter") or item.get("name")
            if ref:
                deps.append(f"infra:{ref}")
    for item in enr.knowledge or []:
        if isinstance(item, dict):
            ref = item.get("uri") or item.get("title") or item.get("connector")
            if ref:
                deps.append(str(ref))
    # Stable order, de-duplicated.
    seen: set[str] = set()
    out: list[str] = []
    for d in deps:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _render_markdown(session: PlanSession, deps: list[str]) -> str:
    plan = session.plan
    epic = session.epic
    review = session.review
    ck = _correlation_key(session)

    parts: list[str] = []
    # Front matter (TechDocs/Backstage-friendly; also human-readable)
    parts.append("---\n")
    parts.append(f"title: {plan.title}\n")
    parts.append(f"correlation_key: {ck}\n")
    parts.append(f"plan_id: {plan.plan_id}\n")
    parts.append(f"generated_by: {GENERATED_BY}\n")
    parts.append("---\n\n")

    parts.append(_h(1, plan.title))
    if plan.description.strip():
        parts.append(f"\n{plan.description.strip()}\n")

    # Provenance
    parts.append("\n" + _h(2, "Provenance"))
    parts.append(f"- **Plan id:** `{plan.plan_id}`\n")
    parts.append(f"- **Correlation key:** `{ck}`\n")
    if session.emitted_issue_number:
        parts.append(f"- **Epic issue:** #{session.emitted_issue_number}\n")
    parts.append(f"- **Target kind:** {plan.target_kind}\n")
    if plan.plan_type:
        parts.append(f"- **Plan type:** {plan.plan_type}\n")
    parts.append(f"- **Source channel:** {plan.source_channel}\n")
    parts.append(f"- **Content hash:** `{plan.content_hash}`\n")

    # Acceptance criteria
    if plan.criteria:
        parts.append("\n" + _h(2, "Acceptance criteria"))
        for c in plan.criteria:
            parts.append(f"- **{c.id}** — {c.text}\n")

    # Decomposition
    if epic is not None:
        parts.append("\n" + _h(2, "Decomposition"))
        if epic.summary.strip():
            parts.append(f"\n{epic.summary.strip()}\n")
        if epic.children:
            parts.append("\n")
            for child in epic.children:
                sp = ""
                if child.effort_estimate and child.effort_estimate.story_points:
                    sp = f" _( {child.effort_estimate.story_points} pts )_"
                parts.append(f"- **{child.key}** — {child.title}{sp}\n")

        # Feasibility
        if epic.cost_estimate and epic.cost_estimate.lines:
            ce = epic.cost_estimate
            parts.append("\n" + _h(2, "Estimated cost"))
            parts.append(
                f"\n**~{ce.monthly_usd:.0f} {ce.currency}/mo**"
                + (f" (source: {ce.source})" if ce.source else "")
                + "\n\n"
            )
            parts.append("| Resource | Qty | Monthly | Detail |\n")
            parts.append("|---|---:|---:|---|\n")
            for ln in ce.lines:
                parts.append(
                    f"| `{ln.resource}` | {ln.quantity:g} | "
                    f"{ln.monthly_usd:.2f} {ce.currency} | {ln.detail} |\n"
                )
        if epic.effort_estimate and epic.effort_estimate.story_points:
            ee = epic.effort_estimate
            parts.append("\n" + _h(2, "Effort"))
            parts.append(
                f"- **{ee.story_points} story points** "
                f"(~{ee.duration_days_low:g}–{ee.duration_days_high:g} days)\n"
            )
        if epic.access_requirements:
            parts.append("\n" + _h(2, "Access requirements"))
            for ar in epic.access_requirements:
                bits = " · ".join(b for b in [ar.provider, ar.action, ar.resource, ar.region] if b)
                parts.append(f"- {bits}{(' — ' + ar.detail) if ar.detail else ''}\n")

    # Governance / review
    if review is not None:
        parts.append("\n" + _h(2, "Governance"))
        status = "✅ passed" if review.gates_passed else "⚠️ not passed"
        parts.append(
            f"- **Review gates:** {status} "
            f"(score {review.aggregate_score:.2f} / threshold {review.threshold:.2f})\n"
        )
        ha = review.human_approval
        if ha and ha.approved:
            who = f" by {ha.approved_by}" if ha.approved_by else ""
            parts.append(f"- **Human approval:** approved{who}\n")
        blocking = [
            f for lens in (review.lenses or []) for f in (lens.findings or []) if f.blocking
        ]
        if blocking:
            parts.append("\n**Blocking findings:**\n")
            for f in blocking:
                parts.append(f"- _{f.source}_ — {f.title}\n")

    # Dependencies & context
    if deps:
        parts.append("\n" + _h(2, "Dependencies & context"))
        for d in deps:
            parts.append(f"- {d}\n")

    return "".join(parts)


def render_plan_docs(session: PlanSession) -> DocBundle:
    """Render the plan session into a :class:`DocBundle` (pure)."""
    plan = session.plan
    ck = _correlation_key(session)
    deps = _dependencies(session)
    slug = plan.plan_id  # already a stable, slugified id ("006-fastapi-...")
    markdown = _render_markdown(session, deps)

    registry_entry: dict[str, Any] = {
        "correlation_key": ck,
        "plan_id": plan.plan_id,
        "title": plan.title,
        "epic": session.emitted_issue_number,
        "target_kind": plan.target_kind,
        "plan_type": plan.plan_type,
        "doc_file": f"{slug}.md",
        "dependencies": deps,
        "content_hash": plan.content_hash,
        "generated_by": GENERATED_BY,
    }

    return DocBundle(
        plan_id=plan.plan_id,
        slug=slug,
        title=plan.title,
        correlation_key=ck,
        content_hash=plan.content_hash,
        markdown=markdown,
        registry_entry=registry_entry,
    )
