"""PlanService — orchestrates the full pipeline behind the portal/API (#20).

A thin, dependency-light application service the web-server (and the MCP tool)
call to drive a plan through every stage and hold the working state between
HTTP requests. Pure Python + in-memory store, so it is fully unit-testable
without FastAPI. The web route layer (``server/routes/plan_pipeline.py``) is a
thin wrapper over a module-level :data:`SERVICE` singleton.

Flow:  ingest → process (detect → plan-type → decompose → synthesize → gates)
       → approve/reject → emit (dry-run by default).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from plan.decompose.models import EpicPlan
from plan.decompose.planner import decompose
from plan.detect.target_classifier import apply as detect_apply
from plan.detect.target_classifier import classify_plan
from plan.ingest.channels import ingest_bytes, ingest_text
from plan.models import NormalizedPlan
from plan.plan_types import apply as plan_type_apply
from plan.plan_types import select_for
from plan.review.approval import approve as approve_review
from plan.review.approval import reject as reject_review
from plan.review.gates import run_gates
from plan.review.models import PlanReview
from plan.synthesize.models import SynthesizedArtifact
from plan.synthesize.run import synthesize
from pydantic import BaseModel, Field

SessionStatus = str  # ingested | processed | approved | rejected | emitted


class PlanSession(BaseModel):
    """All working state for one plan as it moves through the pipeline."""

    session_id: str
    status: SessionStatus = "ingested"
    plan: NormalizedPlan
    epic: EpicPlan | None = None
    artifacts: list[SynthesizedArtifact] = Field(default_factory=list)
    review: PlanReview | None = None
    emit_result: dict | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "title": self.plan.title,
            "status": self.status,
            "target_kind": self.plan.target_kind,
            "plan_type": self.plan.plan_type,
            "children": len(self.epic.children) if self.epic else 0,
            "gates_passed": self.review.gates_passed if self.review else None,
            "created_at": self.created_at,
        }


class PlanServiceError(RuntimeError):
    """Raised for invalid session ids or out-of-order stage calls."""


class PlanService:
    """In-memory orchestrator for plan sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, PlanSession] = {}

    # ── ingest ─────────────────────────────────────────────────────────

    def _store(self, plan: NormalizedPlan) -> PlanSession:
        session = PlanSession(session_id=plan.plan_id, plan=plan)
        self._sessions[session.session_id] = session
        return session

    def ingest_text(self, text: str, *, title: str | None = None,
                    channel: str = "portal") -> PlanSession:
        plan = ingest_text(text, source_channel=channel, title=title,
                           seq=self._next_seq())
        return self._store(plan)

    def ingest_bytes(self, data: bytes, *, filename: str, title: str | None = None,
                     channel: str = "portal") -> PlanSession:
        plan = ingest_bytes(data, filename=filename, source_channel=channel,
                            title=title, seq=self._next_seq())
        return self._store(plan)

    def _next_seq(self) -> int:
        return len(self._sessions) + 1

    # ── query ──────────────────────────────────────────────────────────

    def list_sessions(self) -> list[dict]:
        return [s.summary() for s in self._sessions.values()]

    def get(self, session_id: str) -> PlanSession:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise PlanServiceError(f"unknown session '{session_id}'") from None

    # ── process (the pipeline core) ────────────────────────────────────

    def process(self, session_id: str, *, external_runner=None) -> PlanSession:
        """Detect → plan-type → enrich → decompose → synthesize → review gates."""
        session = self.get(session_id)
        plan = self._enrich(plan_type_apply(detect_apply(session.plan)))
        descriptor = select_for(plan)
        epic = decompose(plan, descriptor=descriptor)
        artifacts = synthesize(plan, epic, descriptor=descriptor)
        review = run_gates(plan, epic, external_runner=external_runner)

        session.plan = plan
        session.epic = epic
        session.artifacts = artifacts
        session.review = review
        session.status = "processed"
        return session

    def _enrich(self, plan: NormalizedPlan) -> NormalizedPlan:
        """Attach live infra context from the adapters named in
        ``PFACTORY_ENRICH_ADAPTERS`` (comma-separated, e.g. ``aws``).

        Off by default (empty env). Each adapter's ``to_enrichment()`` is
        read-only and never raises, so a failed/absent environment just yields
        an ``available: false`` finding.
        """
        text = " ".join(
            [plan.title, plan.description, *(c.text for c in plan.criteria), plan.raw_text or ""]
        )
        low = text.lower()
        enrichment = plan.enrichment.model_copy(deep=True)

        # ── infra adapters (probe AWS / k8s / …) ───────────────────────
        adapters = [
            n.strip()
            for n in os.environ.get("PFACTORY_ENRICH_ADAPTERS", "").split(",")
            if n.strip()
        ]
        if adapters:
            # Only probe cloud/cluster infra when the plan actually targets it.
            cloud_adapters = {"aws", "azure", "gcp", "kubernetes", "openshift"}
            cloud_keywords = (
                "aws", "eks", "ecs", "lambda", "s3", "rds", "kubernetes", "k8s",
                "openshift", "azure", "aks", "gcp", "gke", "cloud", "helm",
                "terraform", "redis", "deploy", "ingress", "microservice",
            )
            cloud_relevant = any(k in low for k in cloud_keywords) or (
                plan.plan_type in ("infra-change", "data-pipeline")
            )
            adapters = [n for n in adapters if n not in cloud_adapters or cloud_relevant]
        if adapters:
            from plan.enrich.base import get_adapter

            for mod in ("kubernetes", "openshift", "azure", "aws", "gcp"):
                try:
                    __import__(f"plan.enrich.adapters.{mod}")
                except Exception:
                    pass
            # Replace prior snapshots so a re-process doesn't multiply findings.
            infra = [
                e for e in enrichment.infra
                if not (isinstance(e, dict) and e.get("adapter") in adapters)
            ]
            for name in adapters:
                try:
                    infra.append(get_adapter(name).to_enrichment())
                except Exception as exc:
                    infra.append({"adapter": name, "available": False, "error": str(exc)})
            enrichment = enrichment.model_copy(update={"infra": infra})

        # ── knowledge connectors (review wiki / search best practices) ──
        connectors = [
            n.strip()
            for n in os.environ.get("PFACTORY_ENRICH_CONNECTORS", "").split(",")
            if n.strip()
        ]
        if connectors:
            from plan.enrich.knowledge.base import get_connector

            for mod in ("git_markdown", "backstage", "confluence", "gitbook",
                        "notion", "best_practices"):
                try:
                    __import__(f"plan.enrich.knowledge.{mod}")
                except Exception:
                    pass
            wiki_root = os.environ.get("PFACTORY_WIKI_ROOT")
            knowledge = [
                k for k in enrichment.knowledge
                if not (isinstance(k, dict) and k.get("connector") in connectors)
            ]
            for name in connectors:
                try:
                    kw = {"root": wiki_root} if (name == "git-markdown" and wiki_root) else {}
                    knowledge.extend(get_connector(name, **kw).to_enrichment(text, limit=8))
                except Exception:
                    continue
            enrichment = enrichment.model_copy(update={"knowledge": knowledge})

        return plan.model_copy(update={"enrichment": enrichment})

    # ── approval ───────────────────────────────────────────────────────

    def approve(self, session_id: str, *, approver: str,
                feedback: str | None = None) -> PlanSession:
        session = self.get(session_id)
        if session.review is None:
            raise PlanServiceError("process the plan before approving")
        approve_review(session.review, session.plan, approver=approver, feedback=feedback)
        session.status = "approved"
        return session

    def reject(self, session_id: str, *, approver: str, feedback: str) -> PlanSession:
        session = self.get(session_id)
        if session.review is None:
            raise PlanServiceError("process the plan before rejecting")
        reject_review(session.review, session.plan, approver=approver, feedback=feedback)
        session.status = "rejected"
        return session

    # ── emit ───────────────────────────────────────────────────────────

    def emit(self, session_id: str, *, repo: str, dry_run: bool = True) -> PlanSession:
        from plan.emit.github_emitter import emit_to_github

        session = self.get(session_id)
        if session.epic is None:
            raise PlanServiceError("process the plan before emitting")
        result = emit_to_github(session.epic, repo=repo, review=session.review,
                                dry_run=dry_run)
        session.emit_result = result.model_dump()
        if not dry_run and not result.errors:
            session.status = "emitted"
        return session

    def classify_preview(self, session_id: str) -> dict:
        """Lightweight classification preview (no full pipeline run)."""
        session = self.get(session_id)
        return classify_plan(session.plan).__dict__


# Module-level singleton the route layer + MCP tool share.
SERVICE = PlanService()
