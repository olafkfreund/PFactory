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

from plan.annotate import AnnotationResult, annotate_plan
from plan.completion import correlation_key_for, notify_completion
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
from plan.usage import PlanUsage
from pydantic import BaseModel, Field

# Lifecycle status. The first five are persisted stages; `processing`/`reviewing`
# are transient sub-states set during process() so the board shows live progress.
SessionStatus = str
# ingested | processing | reviewing | processed | approved | rejected | emitted

# Task-board column ids (shared with the frontend kanban — see constants/task.ts).
# "backlog" is rendered as "Plans ready".
BoardColumn = str  # backlog | in_progress | ai_review | human_review | done


def board_state(status: str, review: PlanReview | None) -> BoardColumn:
    """Project a session's (status, review) onto a kanban column (#5).

    plans-ready (backlog) → in-progress (processing) → AI review (gates running)
    → human review (AI done: awaiting sign-off, blocked, or needs edit) →
    done (approved by AI *and* human, or already emitted).
    """
    if status in ("approved", "emitted"):
        return "done"
    if status == "rejected":
        return "human_review"  # needs attention / edit
    if status == "ingested":
        return "backlog"
    if status == "processing":
        return "in_progress"
    if status == "reviewing":
        return "ai_review"
    if status == "processed":
        # AI review is complete; a human must approve a clean plan or fix a
        # blocked one — either way it awaits a person.
        return "human_review"
    return "backlog"


class PlanSession(BaseModel):
    """All working state for one plan as it moves through the pipeline."""

    session_id: str
    status: SessionStatus = "ingested"
    plan: NormalizedPlan
    epic: EpicPlan | None = None
    artifacts: list[SynthesizedArtifact] = Field(default_factory=list)
    review: PlanReview | None = None
    annotation: AnnotationResult | None = None  # honoured doc + suggested edits (#D)
    original_filename: str = ""  # the uploaded document's name, for rendering
    selected_category: str = ""   # category the user chose at intake (#E)
    selected_template: str = ""   # template the user chose — its policy IS enforced (#E)
    suggested_template: str = ""  # best keyword match — informational only
    emit_result: dict | None = None
    contract_result: dict | None = None  # RFC-0002 signed Task Contract v2 emit (#65)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # PARR correlation chain (#47): pfactory.session_id → issue# → aifactory.task_id.
    # `correlation_key` is the shared key (the emitted GitHub issue #, with a
    # synthetic `pf-<session_id>` fallback when no issue exists yet — e.g. a
    # rejected plan). The two ids below are the upstream/downstream links.
    correlation_key: str | None = None
    emitted_issue_number: int | None = None   # upstream link — the emitted epic issue#
    aifactory_task_id: str | None = None       # downstream link — the handed-off task

    # Token usage accumulated across the run's LLM seams (#60). Zero by default —
    # the pipeline is deterministic unless an LLM is supplied — and surfaced as
    # the additive `usage` block on the completion event (CFactory Tokens page).
    usage: PlanUsage = Field(default_factory=PlanUsage)

    def record_usage(self, usage: PlanUsage | None) -> None:
        """Fold an LLM call's usage into the run total (no-op for ``None``)."""
        self.usage.add(usage)

    def board_state(self) -> BoardColumn:
        return board_state(self.status, self.review)

    def summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "title": self.plan.title,
            "status": self.status,
            "board_state": self.board_state(),
            "target_kind": self.plan.target_kind,
            "plan_type": self.plan.plan_type,
            "children": len(self.epic.children) if self.epic else 0,
            "gates_passed": self.review.gates_passed if self.review else None,
            "created_at": self.created_at,
            "correlation_key": self.correlation_key,
            "issue_number": self.emitted_issue_number,
            "aifactory_task_id": self.aifactory_task_id,
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
                    channel: str = "portal", category: str = "",
                    template: str = "") -> PlanSession:
        plan = ingest_text(text, source_channel=channel, title=title,
                           seq=self._next_seq())
        session = self._store(plan)
        session.selected_category = category
        session.selected_template = template
        return session

    def ingest_bytes(self, data: bytes, *, filename: str, title: str | None = None,
                     channel: str = "portal", category: str = "",
                     template: str = "") -> PlanSession:
        plan = ingest_bytes(data, filename=filename, source_channel=channel,
                            title=title, seq=self._next_seq())
        session = self._store(plan)
        session.original_filename = filename  # preserve for honouring the doc (#D)
        session.selected_category = category
        session.selected_template = template
        return session

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

    def process(self, session_id: str, *, external_runner=None, llm=None) -> PlanSession:
        """Detect → plan-type → enrich → decompose → synthesize → review gates.

        When no ``external_runner`` is supplied, the provider-MCP runner is used by
        default so live runs get provider best-practice findings + suggest-install
        advisories (#3). It never raises and adds no score penalty when providers
        are absent, so the default is safe.

        ``llm`` is the optional decomposer seam: when supplied, decomposition runs
        through it and its token usage is recorded on the session (#60). Left
        ``None`` (the default), the pipeline is fully deterministic and usage
        stays at zero.
        """
        if external_runner is None:
            from plan.providers.review_runner import provider_runner

            external_runner = provider_runner
        session = self.get(session_id)
        # Transient sub-states so a background-run session animates on the board:
        # in-progress while we detect/enrich/decompose/synthesize, AI-review while
        # the gates run. (Synchronous callers just see the final "processed".)
        session.status = "processing"
        plan = self._enrich(plan_type_apply(detect_apply(session.plan)))
        descriptor = select_for(plan)
        usage_sink: list[PlanUsage] = []
        epic = decompose(plan, descriptor=descriptor, llm=llm, usage_sink=usage_sink)
        for u in usage_sink:
            session.record_usage(u)
        artifacts = synthesize(plan, epic, descriptor=descriptor)

        # Feasibility (#C): price the proposed shape, estimate effort, verify
        # access. Estimates are attached to the epic; findings are folded into
        # the feasibility lens via a composed external runner.
        from plan.feasibility import assess_feasibility

        feasibility = assess_feasibility(plan, epic)
        epic.cost_estimate = feasibility.cost
        epic.effort_estimate = feasibility.effort
        epic.access_requirements = feasibility.access

        # Template policy (#E). Enforcement is OPT-IN: a template's embedded policy
        # (required tags / allowed regions / IAM / baselines) gates review only when
        # the user explicitly selected it at intake — auto-matching is recorded as a
        # non-gating suggestion (help, never override). Default the category from the
        # selection, else the detected plan-type's category.
        from plan.templates import build_context, load_templates, select_template

        template_findings: list = []
        try:
            suggested = select_template(plan)
            session.suggested_template = suggested.metadata.name if suggested else ""
            if not session.selected_category:
                session.selected_category = descriptor.category
            if session.selected_template:
                tmpl = load_templates().get(session.selected_template)
                if tmpl is not None:
                    template_findings = tmpl.check(build_context(plan))
        except Exception:
            template_findings = []

        def _composed_runner(p, e):
            out = list(external_runner(p, e)) if external_runner else []
            out.extend(feasibility.findings)
            out.extend(template_findings)
            return out

        session.status = "reviewing"
        review = run_gates(plan, epic, external_runner=_composed_runner)

        session.plan = plan
        session.epic = epic
        session.artifacts = artifacts
        session.review = review
        # Honour the document: anchored, cited suggestions + improved draft (#D).
        session.annotation = annotate_plan(plan, review)
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
        # Terminal too: emit the completion event with a synthetic key (no issue#).
        session.correlation_key = correlation_key_for(session)
        notify_completion(session)
        return session

    # ── emit ───────────────────────────────────────────────────────────

    def emit(self, session_id: str, *, repo: str, dry_run: bool = True,
             gh=None) -> PlanSession:
        from plan.emit.github_emitter import emit_to_github
        from plan.emit.labels import pfactory_meta_block, taxonomy_labels

        session = self.get(session_id)
        if session.epic is None:
            raise PlanServiceError("process the plan before emitting")
        # A live emit needs a real `gh` runner (#52). Construct the default CLI
        # runner when none is injected; tests/callers may pass a fake. Dry-run
        # needs no runner — nothing is created.
        if gh is None and not dry_run:
            from plan.emit.gh_runner import GhCliRunner

            gh = GhCliRunner(repo)
        # Apply the taxonomy (#H): pfactory + type/plan-type/priority/sev labels and
        # the machine-readable pfactory:meta block AIFactory/TFactory parse.
        labels = taxonomy_labels(session.plan, session.epic, session.review)
        meta = pfactory_meta_block(session.plan, session.epic, session.review)
        result = emit_to_github(session.epic, repo=repo, review=session.review,
                                dry_run=dry_run, extra_labels=labels, meta_block=meta,
                                gh=gh)
        session.emit_result = result.model_dump()
        if not dry_run and not result.errors:
            session.status = "emitted"
            # Persist the upstream correlation id (the emitted epic issue#) and the
            # shared key, then emit the terminal completion event (#47).
            session.emitted_issue_number = result.epic_number
            session.correlation_key = correlation_key_for(session)
            notify_completion(session)
        return session

    def emit_contract(self, session_id: str, *, repo: str | None = None,
                      project_id: str | None = None, dry_run: bool = True,
                      http=None, base_url: str | None = None,
                      key: str | None = None) -> PlanSession:
        """Emit the RFC-0002 signed Task Contract v2 for a session (#65).

        Assembles the full contract (plan + execution + tfactory + verification),
        validates + signs it, and (unless ``dry_run``) POSTs it to AIFactory's
        skip-planning ``/api/tasks/from-plan`` endpoint. Dry-run by default; the
        result is stored on ``session.contract_result``.
        """
        from plan.emit.contract_emit import emit_contract as _emit_contract

        session = self.get(session_id)
        if session.epic is None:
            raise PlanServiceError("process the plan before emitting a contract")
        base = base_url or os.environ.get(
            "PFACTORY_AIFACTORY_API_URL", "http://localhost:3101"
        )
        pid = project_id or repo or session.plan.plan_id
        corr = session.correlation_key or correlation_key_for(session)
        result = _emit_contract(
            session.plan, session.epic, session.review,
            base_url=base, project_id=pid, http=http, key=key,
            repo=repo, correlation_key=corr, dry_run=dry_run,
        )
        session.contract_result = result
        if result.get("ok") and not dry_run:
            resp = result.get("response") if isinstance(result.get("response"), dict) else {}
            task_id = (resp or {}).get("taskId") or (resp or {}).get("task_id")
            if task_id:
                session.aifactory_task_id = str(task_id)
            session.status = "emitted"
            session.correlation_key = corr
            notify_completion(session)
        return session

    def classify_preview(self, session_id: str) -> dict:
        """Lightweight classification preview (no full pipeline run)."""
        session = self.get(session_id)
        return classify_plan(session.plan).__dict__


# Module-level singleton the route layer + MCP tool share.
SERVICE = PlanService()
