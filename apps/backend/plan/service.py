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

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

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


def _knowledge_connector_kwargs(name: str, wiki_root: str | None) -> dict[str, object]:
    """Build a knowledge connector's constructor kwargs from the environment.

    The connectors accept ``base_url`` / ``token`` (etc.) but read nothing
    themselves, so without this the enrichment loop ran them unconfigured and
    they returned nothing. Each source has its own ``PFACTORY_<SOURCE>_*`` vars;
    empty/unset values are dropped so an unconfigured connector simply reports
    ``available() is False`` and degrades to an empty result.
    """
    env = os.environ.get

    def _kw(**pairs: str | None) -> dict[str, object]:
        return {k: v for k, v in pairs.items() if v}

    if name == "backstage":
        return _kw(base_url=env("PFACTORY_BACKSTAGE_URL"),
                   token=env("PFACTORY_BACKSTAGE_TOKEN"))
    if name == "confluence":
        return _kw(base_url=env("PFACTORY_CONFLUENCE_URL"),
                   token=env("PFACTORY_CONFLUENCE_TOKEN"),
                   email=env("PFACTORY_CONFLUENCE_EMAIL"))
    if name == "gitbook":
        return _kw(token=env("PFACTORY_GITBOOK_TOKEN"),
                   space_id=env("PFACTORY_GITBOOK_SPACE_ID"))
    if name == "notion":
        return _kw(token=env("PFACTORY_NOTION_TOKEN"))
    if name == "git-markdown":
        return _kw(root=wiki_root)
    return {}


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
    selected_category: str = ""  # category the user chose at intake (#E)
    selected_template: str = ""  # template the user chose — its policy IS enforced (#E)
    suggested_template: str = ""  # best keyword match — informational only
    emit_result: dict | None = None
    docs_result: list[dict] | None = None  # docs emit per-target results (P1)
    contract_result: dict | None = None  # RFC-0002 signed Task Contract v2 emit (#65)
    # RFC-0007 (#86): human-verified access curation. `access_approvals` maps a
    # resource -> approval record (applied at the next emit); `access_audit` is the
    # append-only RFC-0001a curation trail (refs only, never secrets).
    access_approvals: dict = Field(default_factory=dict)
    access_audit: list = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # PARR correlation chain (#47): pfactory.session_id → issue# → aifactory.task_id.
    # `correlation_key` is the shared key (the emitted GitHub issue #, with a
    # synthetic `pf-<session_id>` fallback when no issue exists yet — e.g. a
    # rejected plan). The two ids below are the upstream/downstream links.
    correlation_key: str | None = None
    emitted_issue_number: int | None = None  # upstream link — the emitted epic issue#
    aifactory_task_id: str | None = None  # downstream link — the handed-off task

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


logger = logging.getLogger(__name__)


def _persist_enabled() -> bool:
    """Whether plan sessions are persisted to disk.

    Opt-in via ``PFACTORY_PLAN_PERSIST`` so unit tests (which construct bare
    ``PlanService()`` instances) stay hermetic by default — no disk reads/writes,
    identical to the historical in-memory behaviour. Production deployments set
    it to survive pod restarts (the in-memory store was wiped on every restart).
    """
    return os.environ.get("PFACTORY_PLAN_PERSIST", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _default_store_dir() -> Path:
    """Directory that holds one ``<session_id>.json`` per plan session.

    Defaults under ``~/.pfactory`` — which the deployment mounts on a
    PersistentVolumeClaim, so sessions survive restarts. Override with
    ``PFACTORY_PLAN_STORE_DIR``.
    """
    override = os.environ.get("PFACTORY_PLAN_STORE_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".pfactory" / "plan-sessions"


def _workspaces_dir() -> Path:
    """Base of the per-spec workspace context snapshots (RFC-0007 / #84).

    ``<base>/workspaces/{project_id}/specs/{spec_id}/context/`` holds the
    snapshotted ``pfactory_yml.json`` + ``aifactory_spec.md`` (see
    ``workspaces.snapshotter``). Override the base with ``PFACTORY_WORKSPACES_DIR``.
    """
    override = os.environ.get("PFACTORY_WORKSPACES_DIR", "").strip()
    base = Path(override) if override else Path.home() / ".pfactory" / "workspaces"
    return base


def _load_access_inputs(project_id: str, spec_id: str) -> tuple[dict | None, str]:
    """Best-effort: load the snapshotted .pfactory.yml + spec for access discovery.

    Returns ``(config_dict_or_None, spec_text)``. Never raises: a missing snapshot
    (the common case for plans with no declared targets) yields ``(None, "")`` so
    the contract simply omits the RFC-0007 ``access`` block.
    """
    try:
        ctx = _workspaces_dir() / str(project_id) / "specs" / str(spec_id) / "context"
        config: dict | None = None
        pj = ctx / "pfactory_yml.json"
        if pj.is_file():
            loaded = json.loads(pj.read_text(encoding="utf-8"))
            config = loaded if isinstance(loaded, dict) else None
        spec_text = ""
        sm = ctx / "aifactory_spec.md"
        if sm.is_file():
            spec_text = sm.read_text(encoding="utf-8", errors="replace")
        return config, spec_text
    except Exception:  # noqa: BLE001 - access discovery must never break emit
        return None, ""


class PlanService:
    """Orchestrator for plan sessions, with optional disk-backed persistence.

    The store is in-memory for speed/testability; when ``PFACTORY_PLAN_PERSIST``
    is set, every mutation is mirrored to a JSON file under the store dir and the
    set is reloaded on startup, so plans survive pod restarts.
    """

    def __init__(
        self, *, store_dir: Path | None = None, persist: bool | None = None
    ) -> None:
        self._sessions: dict[str, PlanSession] = {}
        self._persist = _persist_enabled() if persist is None else persist
        self._store_dir = store_dir or _default_store_dir()
        if self._persist:
            self._load_all()

    # ── persistence (opt-in via PFACTORY_PLAN_PERSIST) ──────────────────

    def _load_all(self) -> None:
        """Repopulate ``_sessions`` from ``<store_dir>/*.json`` on startup.

        Best-effort: a missing dir yields an empty store; an unreadable or
        schema-incompatible file is skipped (logged), never fatal.
        """
        try:
            self._store_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # noqa: BLE001 — disk unavailable → stay in-memory
            logger.warning("plan store dir unavailable (%s); running in-memory", exc)
            self._persist = False
            return
        for path in sorted(self._store_dir.glob("*.json")):
            try:
                session = PlanSession.model_validate_json(path.read_text())
                self._sessions[session.session_id] = session
            except Exception as exc:  # noqa: BLE001 — skip corrupt/old payloads
                logger.warning(
                    "skipping unreadable plan session %s: %s", path.name, exc
                )
        if self._sessions:
            logger.info("loaded %d persisted plan session(s)", len(self._sessions))

    def _save(self, session: PlanSession) -> None:
        """Mirror one session to disk atomically (temp file + rename).

        Never raises — persistence is best-effort telemetry of state, not part
        of the request's success contract.
        """
        if not self._persist:
            return
        try:
            self._store_dir.mkdir(parents=True, exist_ok=True)
            dest = self._store_dir / f"{session.session_id}.json"
            tmp = dest.with_suffix(".json.tmp")
            tmp.write_text(session.model_dump_json())
            tmp.replace(dest)
        except Exception as exc:  # noqa: BLE001 — disk hiccup must not break a run
            logger.warning(
                "failed to persist plan session %s: %s", session.session_id, exc
            )

    # ── ingest ─────────────────────────────────────────────────────────

    def _store(self, plan: NormalizedPlan) -> PlanSession:
        session = PlanSession(session_id=plan.plan_id, plan=plan)
        self._sessions[session.session_id] = session
        self._save(session)
        return session

    def ingest_text(
        self,
        text: str,
        *,
        title: str | None = None,
        channel: str = "portal",
        category: str = "",
        template: str = "",
    ) -> PlanSession:
        plan = ingest_text(
            text, source_channel=channel, title=title, seq=self._next_seq()
        )
        session = self._store(plan)
        session.selected_category = category
        session.selected_template = template
        self._save(session)
        return session

    def ingest_bytes(
        self,
        data: bytes,
        *,
        filename: str,
        title: str | None = None,
        channel: str = "portal",
        category: str = "",
        template: str = "",
    ) -> PlanSession:
        plan = ingest_bytes(
            data,
            filename=filename,
            source_channel=channel,
            title=title,
            seq=self._next_seq(),
        )
        session = self._store(plan)
        session.original_filename = filename  # preserve for honouring the doc (#D)
        session.selected_category = category
        session.selected_template = template
        self._save(session)
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

    def process(
        self, session_id: str, *, external_runner=None, llm=None
    ) -> PlanSession:
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
        self._save(session)
        return session

    def _enrich(self, plan: NormalizedPlan) -> NormalizedPlan:
        """Attach live infra context from the adapters named in
        ``PFACTORY_ENRICH_ADAPTERS`` (comma-separated, e.g. ``aws``).

        Off by default (empty env). Each adapter's ``to_enrichment()`` is
        read-only and never raises, so a failed/absent environment just yields
        an ``available: false`` finding.
        """
        text = " ".join(
            [
                plan.title,
                plan.description,
                *(c.text for c in plan.criteria),
                plan.raw_text or "",
            ]
        )
        enrichment = plan.enrichment.model_copy(deep=True)

        # ── infra adapters (probe AWS / k8s / …) ───────────────────────
        adapters = [
            n.strip()
            for n in os.environ.get("PFACTORY_ENRICH_ADAPTERS", "").split(",")
            if n.strip()
        ]
        if adapters:
            # Only probe cloud/cluster infra when the plan actually targets it.
            # Shared heuristic so the readiness `enrichment-integrity` check and
            # this stage always agree on cloud-relevance.
            from plan.enrich.relevance import is_cloud_relevant

            cloud_adapters = {"aws", "azure", "gcp", "kubernetes", "openshift"}
            cloud_relevant = is_cloud_relevant(plan)
            adapters = [
                n for n in adapters if n not in cloud_adapters or cloud_relevant
            ]
        if adapters:
            from plan.enrich.base import get_adapter

            for mod in ("kubernetes", "openshift", "azure", "aws", "gcp"):
                try:
                    __import__(f"plan.enrich.adapters.{mod}")
                except Exception:
                    pass
            # Replace prior snapshots so a re-process doesn't multiply findings.
            infra = [
                e
                for e in enrichment.infra
                if not (isinstance(e, dict) and e.get("adapter") in adapters)
            ]
            for name in adapters:
                try:
                    infra.append(get_adapter(name).to_enrichment())
                except Exception as exc:
                    infra.append(
                        {"adapter": name, "available": False, "error": str(exc)}
                    )
            enrichment = enrichment.model_copy(update={"infra": infra})

        # ── knowledge connectors (review wiki / search best practices) ──
        connectors = [
            n.strip()
            for n in os.environ.get("PFACTORY_ENRICH_CONNECTORS", "").split(",")
            if n.strip()
        ]
        if connectors:
            from plan.enrich.knowledge.base import get_connector

            for mod in (
                "git_markdown",
                "backstage",
                "confluence",
                "gitbook",
                "notion",
                "best_practices",
            ):
                try:
                    __import__(f"plan.enrich.knowledge.{mod}")
                except Exception:
                    pass
            wiki_root = os.environ.get("PFACTORY_WIKI_ROOT")
            knowledge = [
                k
                for k in enrichment.knowledge
                if not (isinstance(k, dict) and k.get("connector") in connectors)
            ]
            for name in connectors:
                try:
                    kw = _knowledge_connector_kwargs(name, wiki_root)
                    knowledge.extend(
                        get_connector(name, **kw).to_enrichment(text, limit=8)
                    )
                except Exception:
                    continue
            enrichment = enrichment.model_copy(update={"knowledge": knowledge})

        return plan.model_copy(update={"enrichment": enrichment})

    # ── approval ───────────────────────────────────────────────────────

    def approve(
        self, session_id: str, *, approver: str, feedback: str | None = None
    ) -> PlanSession:
        session = self.get(session_id)
        if session.review is None:
            raise PlanServiceError("process the plan before approving")
        approve_review(
            session.review, session.plan, approver=approver, feedback=feedback
        )
        session.status = "approved"
        self._save(session)
        return session

    def waive(
        self, session_id: str, *, check_ids: list[str], reason: str, waived_by: str
    ) -> PlanSession:
        """Record a human waiver of one or more hard readiness failures (#77).

        Mirrors :meth:`approve`'s shape: requires the plan to have been processed
        (so a readiness report exists). Lets :class:`WaiverError` propagate — the
        route maps it to 400.
        """
        from plan.review.readiness.waiver import waive as waive_review

        session = self.get(session_id)
        if session.review is None:
            raise PlanServiceError("process the plan before waiving")
        waive_review(
            session.review,
            session.plan,
            check_ids=check_ids,
            reason=reason,
            waived_by=waived_by,
        )
        self._save(session)
        return session

    def reject(self, session_id: str, *, approver: str, feedback: str) -> PlanSession:
        session = self.get(session_id)
        if session.review is None:
            raise PlanServiceError("process the plan before rejecting")
        reject_review(
            session.review, session.plan, approver=approver, feedback=feedback
        )
        session.status = "rejected"
        # Terminal too: emit the completion event with a synthetic key (no issue#).
        session.correlation_key = correlation_key_for(session)
        notify_completion(session)
        self._save(session)
        return session

    # ── emit ───────────────────────────────────────────────────────────

    def emit(
        self,
        session_id: str,
        *,
        repo: str,
        dry_run: bool = True,
        gh=None,
        docs_connections: list[dict] | None = None,
        docs_selected: list[str] | None = None,
    ) -> PlanSession:
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
        result = emit_to_github(
            session.epic,
            repo=repo,
            review=session.review,
            plan=session.plan,
            dry_run=dry_run,
            extra_labels=labels,
            meta_block=meta,
            gh=gh,
        )
        session.emit_result = result.model_dump()
        if not dry_run and not result.errors:
            session.status = "emitted"
            # Persist the upstream correlation id (the emitted epic issue#) and the
            # shared key, then emit the terminal completion event (#47).
            session.emitted_issue_number = result.epic_number
            session.correlation_key = correlation_key_for(session)
            notify_completion(session)
            # Documentation emit (P1) — gated + best-effort. Default OFF, never
            # raises, so it cannot affect the GitHub emit / completion above.
            try:
                from plan.emit.docs import emit_docs, is_enabled

                if is_enabled():
                    session.docs_result = emit_docs(
                        session,
                        repo=repo,
                        connections=docs_connections,
                        selected=docs_selected,
                    )
            except Exception:  # noqa: BLE001 — docs must never break emit
                logger.warning("plan docs emit failed", exc_info=True)
        self._save(session)
        return session

    def emit_contract(
        self,
        session_id: str,
        *,
        repo: str | None = None,
        project_id: str | None = None,
        dry_run: bool = True,
        http=None,
        base_url: str | None = None,
        key: str | None = None,
    ) -> PlanSession:
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
        # RFC-0007 (#84): discover the access block from the snapshotted
        # .pfactory.yml for this spec. Best-effort — when no snapshot exists the
        # block is simply omitted (the task declares no external resource).
        access_config, access_spec_text = _load_access_inputs(pid, session.plan.plan_id)
        result = _emit_contract(
            session.plan,
            session.epic,
            session.review,
            base_url=base,
            project_id=pid,
            http=http,
            key=key,
            repo=repo,
            correlation_key=corr,
            config=access_config,
            spec_text=access_spec_text,
            approvals=session.access_approvals or None,
            dry_run=dry_run,
        )
        session.contract_result = result
        if result.get("ok") and not dry_run:
            resp = (
                result.get("response")
                if isinstance(result.get("response"), dict)
                else {}
            )
            task_id = (resp or {}).get("taskId") or (resp or {}).get("task_id")
            if task_id:
                session.aifactory_task_id = str(task_id)
            session.status = "emitted"
            session.correlation_key = corr
            notify_completion(session)
        self._save(session)
        return session

    def approve_access(
        self,
        session_id: str,
        resource: str,
        *,
        approved_by: str,
        scope: str,
        approved_at: str | None = None,
        ref_exists=None,
    ) -> dict:
        """Record a human-verified access approval for one resource (RFC-0007 #86).

        The resource must appear in the last emitted contract's ``access`` block
        (run ``emit_contract`` dry-run first to discover requirements). Runs the
        curation gate: a non-D requirement whose credential is present (probed,
        never resolved into the open) is curated, the approval is stored for the
        next emit to apply, and an RFC-0001a audit record is appended. Returns
        ``{ok, resource, state?, audit?, reason?}``. Never stores/logs a secret.
        """
        from pfactory_secrets.probe import probe_ref_exists
        from plan.access_discovery import curate_requirement

        session = self.get(session_id)
        block = ((session.contract_result or {}).get("contract") or {}).get(
            "access"
        ) or {}
        req = next(
            (
                r
                for r in (block.get("requirements") or [])
                if r.get("resource") == resource
            ),
            None,
        )
        if req is None:
            raise PlanServiceError(
                f"resource '{resource}' not in the contract access block; emit a "
                "dry-run contract first to discover access requirements"
            )
        approval = {
            "approved_by": approved_by,
            "scope": scope,
            "approved_at": approved_at or datetime.now(timezone.utc).isoformat(),
        }
        probe = ref_exists or probe_ref_exists

        def liveness(r) -> bool:  # credential must be present to curate at approval
            return probe(r.get("credential_ref")) is True

        _curated, audit = curate_requirement(
            req, approval=approval, liveness_check=liveness
        )
        if audit is None:
            return {
                "ok": False,
                "resource": resource,
                "reason": "cannot curate now: class D (un-automatable), or the "
                "credential is not present/verifiable at approval time",
            }
        session.access_approvals[resource] = approval
        session.access_audit.append(audit)
        self._save(session)
        return {"ok": True, "resource": resource, "state": "curated", "audit": audit}

    def classify_preview(self, session_id: str) -> dict:
        """Lightweight classification preview (no full pipeline run)."""
        session = self.get(session_id)
        return classify_plan(session.plan).__dict__


# Module-level singleton the route layer + MCP tool share.
SERVICE = PlanService()
