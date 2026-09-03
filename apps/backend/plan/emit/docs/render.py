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

# The signature format's own version. Bump when a field's MEANING changes, so a
# consumer reading an old file is not silently misinterpreting it.
SIGNATURE_SCHEMA = 1


def _run_signature(
    session: PlanSession, *, correlation_key: str, pfactory_version: str
) -> dict[str, Any]:
    """The provenance record for one planner run (#700).

    Answers, from a checkout and with no server: which planner run produced this
    page, from exactly which text, under which template, and what the review
    made of it. ``content_hash`` is the join key — re-running the planner over
    an unchanged plan reproduces it, so a changed hash is the signal that the
    document on disk is stale.

    Pure, like the rest of this module: every value is read off the session, a
    module constant, or an injected argument. No clock — ``created_at`` is the
    session's own stamp, so two renders of the same session agree byte-for-byte,
    and ``pfactory_version`` is passed in for the same reason ``updated_at`` is.
    """
    plan = session.plan
    review = session.review
    lenses: dict[str, Any] = {}
    findings_by_severity: dict[str, int] = {}
    if review is not None:
        for lens in review.lenses:
            lenses[lens.lens] = lens.score
            for finding in lens.findings:
                sev = str(finding.severity)
                findings_by_severity[sev] = findings_by_severity.get(sev, 0) + 1

    annotation = session.annotation
    return {
        "signature_schema": SIGNATURE_SCHEMA,
        "generated_by": GENERATED_BY,
        "pfactory_version": pfactory_version or "unknown",
        # ── identity ────────────────────────────────────────────────
        "session_id": session.session_id,
        "plan_id": plan.plan_id,
        "correlation_key": correlation_key,
        "tenant_id": session.tenant_id,
        "created_at": session.created_at,
        # ── what was planned ────────────────────────────────────────
        "title": plan.title,
        "content_hash": plan.content_hash,
        "target_kind": plan.target_kind,
        "plan_type": plan.plan_type,
        "source_format": plan.source_format,
        "source_channel": plan.source_channel,
        "original_filename": session.original_filename,
        "selected_category": session.selected_category,
        "selected_template": session.selected_template,
        "criteria_count": len(plan.criteria),
        "repo": session.repo,
        "base_ref": session.base_ref,
        # ── what the run produced ───────────────────────────────────
        "status": session.status,
        "board_state": session.board_state(),
        "children": len(session.epic.children) if session.epic else 0,
        "suggestions": len(annotation.suggestions) if annotation else 0,
        # ── the verdict ─────────────────────────────────────────────
        "gates_passed": review.gates_passed if review else None,
        "aggregate_score": review.aggregate_score if review else None,
        "lens_scores": lenses,
        "findings_by_severity": findings_by_severity,
        "injection_scan": (session.injection_scan or {}).get("verdict", "not-run"),
        # Zero tokens means the deterministic path ran — NOT that nothing ran.
        "tokens": session.usage.model_dump() if session.usage else {},
        "emitted_issue_number": session.emitted_issue_number,
    }


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


def _routing_verdict(session: PlanSession) -> dict[str, str] | None:
    """The RFC-0014 effort-free verdict for the doc, read defensively off the
    emitted contract's ``execution.routing``. Returns ``None`` when no contract
    has been emitted yet (dry-run a contract first to populate it)."""
    contract = (session.contract_result or {}).get("contract")
    if not isinstance(contract, dict):
        return None
    routing = (
        (contract.get("execution") or {}) if isinstance(contract.get("execution"), dict) else {}
    ).get("routing")
    if not isinstance(routing, dict):
        return None
    autonomy = routing.get("autonomy")
    autonomy = autonomy if isinstance(autonomy, dict) else {}
    out = {
        "difficulty": str(routing.get("difficulty", "")),
        "risk": str(routing.get("risk", "")),
        "autonomy": str(autonomy.get("verdict", "")),
        "reason": str(autonomy.get("reason", "")),
    }
    if not (out["difficulty"] or out["risk"] or out["autonomy"]):
        return None
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
                parts.append(f"- **{child.key}** — {child.title}\n")

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
        # RFC-0014: render the effort-free difficulty/risk/autonomy verdict (from
        # the emitted contract's execution.routing) in place of dev-day sizing.
        verdict = _routing_verdict(session)
        if verdict:
            parts.append("\n" + _h(2, "Difficulty, risk & autonomy"))
            parts.append(f"- **Difficulty:** {verdict['difficulty']}\n")
            parts.append(f"- **Risk:** {verdict['risk']}\n")
            parts.append(
                f"- **Autonomy:** {verdict['autonomy']}"
                + (f" — {verdict['reason']}" if verdict.get("reason") else "")
                + "\n"
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


def render_plan_docs(session: PlanSession, *, pfactory_version: str = "unknown") -> DocBundle:
    """Render the plan session into a :class:`DocBundle` (pure).

    ``pfactory_version`` is injected by the caller (see ``updated_at`` in the
    module docstring for why): resolving it means reading a file, and a
    plausible-looking wrong version would defeat the field's only purpose.
    Callers that cannot determine it should leave the default, which records the
    honest "unknown".
    """
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
        # Enough of the verdict to scan the index without opening each run file.
        "status": session.status,
        "gates_passed": session.review.gates_passed if session.review else None,
        "signature_file": f"{slug}.run.json",
    }

    return DocBundle(
        plan_id=plan.plan_id,
        slug=slug,
        title=plan.title,
        correlation_key=ck,
        content_hash=plan.content_hash,
        markdown=markdown,
        registry_entry=registry_entry,
        run_signature=_run_signature(
            session, correlation_key=ck, pfactory_version=pfactory_version
        ),
    )
