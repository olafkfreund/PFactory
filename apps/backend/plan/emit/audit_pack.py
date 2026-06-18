"""EU AI Act audit-pack export (MVP, #122).

Assembles the evidence PFactory *already* holds for one plan — the honoured
source document, the review-gate findings with citations, the human approval
record, the signed Task Contract, the completion/correlation timeline, and the
TFactory verdict if present — into a single **self-contained** pack, and
cross-references each artifact to the EU AI Act obligation headings it is
relevant to.

IMPORTANT — this is a *descriptive evidence bundle*, not a compliance
attestation. The article/heading references are navigational labels indicating
where an artifact *may* be relevant under the EU AI Act; they are explicitly not
a statement that the system or this plan "complies". Every rendered pack carries
:data:`DISCLAIMER`. (Out of scope for the MVP: full legal mapping, GDPR/SOC2
packs, per-tenant batching — see #122.)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from plan.service import PlanSession

SCHEMA_VERSION = "audit-pack/v0"

DISCLAIMER = (
    "This audit pack is a descriptive evidence bundle assembled from PFactory's "
    "planning record. The EU AI Act article/heading references are navigational "
    "labels indicating where each artifact may be relevant; they are NOT an "
    "assertion of EU AI Act conformity for the system, model, or plan. "
    "Conformity determinations require a qualified legal / conformity assessment. "
    "PFactory makes no conformity claim."
)

# EU AI Act heading labels used as cross-reference tags (descriptive only).
_TECH_DOC = "Technical documentation (Art. 11 / Annex IV)"
_RECORD_KEEPING = "Record-keeping & logging (Art. 12)"
_HUMAN_OVERSIGHT = "Human oversight (Art. 14)"
_RISK_MGMT = "Risk management (Art. 9)"


class AuditArtifact(BaseModel):
    """One piece of evidence in the pack + the EU AI Act headings it maps to."""

    key: str
    title: str
    present: bool
    eu_ai_act_headings: list[str] = Field(default_factory=list)
    summary: str = ""
    # The actual evidence, inlined so the pack is self-contained (AC2).
    content: Any = None


class AuditPack(BaseModel):
    """A self-contained, descriptive evidence bundle for one plan (#122)."""

    schema_version: str = SCHEMA_VERSION
    plan_id: str
    plan_title: str = ""
    correlation_key: str | None = None
    issue_number: int | None = None
    generated_at: str = ""
    disclaimer: str = DISCLAIMER
    artifacts: list[AuditArtifact] = Field(default_factory=list)

    def cross_reference(self) -> list[dict[str, Any]]:
        """The MVP artifact → EU AI Act heading cross-reference table."""
        return [
            {
                "artifact": a.title,
                "present": a.present,
                "eu_ai_act_headings": a.eu_ai_act_headings,
            }
            for a in self.artifacts
        ]


def _source_document(session: PlanSession) -> AuditArtifact:
    plan = session.plan
    return AuditArtifact(
        key="source_document",
        title="Honoured source document",
        present=True,
        eu_ai_act_headings=[_TECH_DOC],
        summary=f"The plan as ingested: {plan.title!r} ({len(plan.criteria)} stated "
        "acceptance criteria).",
        content={
            "title": plan.title,
            "description": plan.description,
            "target_kind": plan.target_kind,
            "plan_type": plan.plan_type,
            "acceptance_criteria": [
                {"id": c.id, "text": c.text} for c in plan.criteria
            ],
            "content_hash": plan.content_hash,
            "raw_text": plan.raw_text,
        },
    )


def _review_findings(session: PlanSession) -> AuditArtifact:
    review = session.review
    present = review is not None
    lenses: list[dict[str, Any]] = []
    if present:
        for ls in review.lenses:
            lenses.append({
                "lens": ls.lens,
                "score": ls.score,
                "findings": [
                    {
                        "title": f.title,
                        "detail": f.detail,
                        "severity": f.severity,
                        "source": f.source,
                        "blocking": f.blocking,
                        "citations": [
                            {"title": c.title, "uri": c.uri, "why": c.why}
                            for c in f.citations
                        ],
                    }
                    for f in ls.findings
                ],
            })
    return AuditArtifact(
        key="review_findings",
        title="Review-gate findings & citations",
        present=present,
        eu_ai_act_headings=[_RISK_MGMT, _TECH_DOC],
        summary=(
            f"Multi-lens review: aggregate {review.aggregate_score}, "
            f"gates_passed={review.gates_passed}."
            if present
            else "No review record on this plan."
        ),
        content={
            "aggregate_score": review.aggregate_score,
            "threshold": review.threshold,
            "gates_passed": review.gates_passed,
            "lenses": lenses,
        }
        if present
        else None,
    )


def _human_approval(session: PlanSession) -> AuditArtifact:
    review = session.review
    appr = review.human_approval if review is not None else None
    present = bool(appr and appr.approved)
    return AuditArtifact(
        key="human_approval",
        title="Human approval record",
        present=present,
        eu_ai_act_headings=[_HUMAN_OVERSIGHT, _RECORD_KEEPING],
        summary=(
            f"Approved by {appr.approved_by} at {appr.approved_at} "
            f"(valid={appr.valid}, bound to plan hash)."
            if present
            else "No valid human approval recorded."
        ),
        content={
            "approved": appr.approved,
            "approved_by": appr.approved_by,
            "approved_at": appr.approved_at,
            "plan_hash": appr.plan_hash,
            "valid": appr.valid,
            "review_count": appr.review_count,
        }
        if appr is not None
        else None,
    )


def _task_contract(session: PlanSession) -> AuditArtifact:
    result = session.contract_result or {}
    contract = result.get("contract") if isinstance(result, dict) else None
    present = bool(contract)
    return AuditArtifact(
        key="task_contract",
        title="Signed Task Contract (RFC-0002)",
        present=present,
        eu_ai_act_headings=[_TECH_DOC, _RECORD_KEEPING],
        summary=(
            "HMAC-signed Task Contract emitted to AIFactory."
            if present
            else "No Task Contract emitted for this plan."
        ),
        content=result if present else None,
    )


def _completion_timeline(session: PlanSession) -> AuditArtifact:
    has_chain = bool(session.correlation_key or session.emitted_issue_number)
    return AuditArtifact(
        key="completion_timeline",
        title="Completion & correlation timeline",
        present=has_chain,
        eu_ai_act_headings=[_RECORD_KEEPING],
        summary=(
            f"Correlation key {session.correlation_key!r}; "
            f"epic issue #{session.emitted_issue_number}."
            if has_chain
            else "Plan has not been emitted — no correlation chain yet."
        ),
        content={
            "correlation_key": session.correlation_key,
            "issue_number": session.emitted_issue_number,
            "aifactory_task_id": session.aifactory_task_id,
            "status": session.status,
            "audit_trail": list(session.access_audit or []),
        }
        if has_chain
        else None,
    )


def _tfactory_verdict(session: PlanSession) -> AuditArtifact:
    # TFactory's verdict is produced downstream; PFactory carries it only if a
    # handback recorded it on the session. Absent is honest, not an error.
    verdict = getattr(session, "tfactory_verdict", None)
    present = verdict is not None
    return AuditArtifact(
        key="tfactory_verdict",
        title="TFactory verification verdict",
        present=present,
        eu_ai_act_headings=[_TECH_DOC, _RISK_MGMT],
        summary=(
            "Independent TFactory verification verdict attached."
            if present
            else "No TFactory verdict on this plan record (verified downstream in "
            "TFactory)."
        ),
        content=verdict,
    )


def build_audit_pack(session: PlanSession, *, now: str | None = None) -> AuditPack:
    """Assemble the EU AI Act audit pack for one plan session (#122).

    ``now`` is injectable for deterministic tests; defaults to the current UTC
    timestamp. The pack inlines every artifact's content so a reviewer needs no
    access to PFactory's systems to read it (AC2).
    """
    generated = now or datetime.now(timezone.utc).isoformat()
    return AuditPack(
        plan_id=session.plan.plan_id,
        plan_title=session.plan.title,
        correlation_key=session.correlation_key,
        issue_number=session.emitted_issue_number,
        generated_at=generated,
        artifacts=[
            _source_document(session),
            _review_findings(session),
            _human_approval(session),
            _task_contract(session),
            _completion_timeline(session),
            _tfactory_verdict(session),
        ],
    )


def render_markdown(pack: AuditPack) -> str:
    """Render a self-contained Markdown audit pack (the human-readable export)."""
    lines: list[str] = []
    lines.append(f"# EU AI Act Audit Pack — {pack.plan_title or pack.plan_id}")
    lines.append("")
    lines.append(f"> {pack.disclaimer}")
    lines.append("")
    lines.append(f"- Plan id: `{pack.plan_id}`")
    if pack.correlation_key:
        lines.append(f"- Correlation key: `{pack.correlation_key}`")
    if pack.issue_number:
        lines.append(f"- Epic issue: #{pack.issue_number}")
    lines.append(f"- Generated: {pack.generated_at}")
    lines.append(f"- Schema: `{pack.schema_version}`")
    lines.append("")

    lines.append("## Cross-reference")
    lines.append("")
    lines.append("| Artifact | Present | EU AI Act headings (descriptive) |")
    lines.append("| --- | --- | --- |")
    for a in pack.artifacts:
        present = "yes" if a.present else "no"
        headings = "; ".join(a.eu_ai_act_headings) or "—"
        lines.append(f"| {a.title} | {present} | {headings} |")
    lines.append("")

    lines.append("## Evidence")
    lines.append("")
    for a in pack.artifacts:
        lines.append(f"### {a.title}")
        lines.append("")
        lines.append(f"_{a.summary}_" if a.summary else "_(no summary)_")
        lines.append("")
        if a.present and a.content is not None:
            import json

            lines.append("```json")
            lines.append(json.dumps(a.content, indent=2, default=str))
            lines.append("```")
        else:
            lines.append("Not present in this plan's record.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
