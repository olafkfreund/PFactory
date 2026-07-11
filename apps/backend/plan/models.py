"""Core data contracts for the PFactory planning pipeline (issue #3).

:class:`NormalizedPlan` is the spine of the pipeline — every stage after Ingest
reads and enriches it. It is a superset of :class:`spec_sources.NormalizedSpec`
(the text-ingestion result), adding the fields the planning factory needs:
a stable ``plan_id``, the source channel, the detected target kind / plan type,
the live-context ``enrichment`` bundle, and a ``content_hash`` used by the
human-approval gate to invalidate sign-off when the plan changes.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from plan.recon.models import RepoMap
from spec_sources import NormalizedSpec

TargetKind = Literal["software", "non-software", "undetermined"]
SourceChannel = Literal["portal", "cli", "mcp", "github_issue", "github_discussion", "agent"]
# Factory#273 (untrusted-content provenance): issue/spec-derived text is
# untrusted user content; operator-authored specs arriving through the
# authenticated API / CLI / MCP surfaces are trusted.
ContentTrust = Literal["untrusted_user_content", "trusted"]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 50) -> str:
    """Lowercase, hyphenate, and trim a title into a URL/branch-safe slug."""
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "plan"


def make_plan_id(seq: int, title: str) -> str:
    """Build a deterministic ``NNN-slug`` plan id (mirrors AIFactory spec ids)."""
    return f"{seq:03d}-{slugify(title)}"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class Criterion(BaseModel):
    """One acceptance criterion with a stable ``AC#N`` id."""

    id: str
    text: str


class Enrichment(BaseModel):
    """Live organizational context attached during the Enrich stage.

    ``infra`` and ``knowledge`` hold structured findings produced by the infra
    adapters (issues #8–#10) and knowledge connectors (issue #11). Kept as open
    dicts here so this contract doesn't depend on those stages; each stage
    documents its own finding shape.
    """

    infra: list[dict[str, Any]] = Field(default_factory=list)
    knowledge: list[dict[str, Any]] = Field(default_factory=list)


class NormalizedPlan(BaseModel):
    """A source-neutral, enrichable plan — the pipeline's working artifact."""

    plan_id: str
    title: str
    description: str = ""
    source_format: str
    source_channel: SourceChannel = "cli"
    criteria: list[Criterion] = Field(default_factory=list)
    target_kind: TargetKind = "undetermined"
    plan_type: str | None = None
    enrichment: Enrichment = Field(default_factory=Enrichment)
    # RFC-0010: what reconnaissance statically observed in the target repo. None
    # until the recon stage runs (or when no target repo is given → greenfield).
    # Excluded from canonical_content() here; Phase 4 folds a stable digest of it
    # into the approval hash so sign-off invalidates when the repo drifts.
    repo_map: RepoMap | None = None
    # RFC-0010: the reconnaissance verdict — greenfield | modify | migration.
    # None until the recon stage runs. Excluded from canonical_content() here;
    # Phase 4 folds it into the approval-hash digest.
    change_mode: str | None = None
    # RFC-0010 migration (Phase 5): the languages being ported from/to, and the
    # migration metadata (golden-corpus manifest + tfactory.equivalence block)
    # the downstream factories consume. None outside a migration.
    source_language: str | None = None
    target_language: str | None = None
    migration: dict[str, Any] | None = None
    # RFC-0011: the label-driven difficulty tier (low|medium|hard) carried from
    # intake (factory:low|medium|hard) through the pipeline so emit can override
    # the execution/review_tier/tfactory blocks per the tier routing table. None
    # until classified; excluded from canonical_content() (a routing knob, not
    # plan substance — changing it must not invalidate a human approval).
    autonomy_tier: str | None = None
    # RFC-0015 §3.1: the per-project constitution markdown captured from the
    # target repo's ``.factory/constitution.md`` during reconnaissance (read-only
    # checkout). Consumed by the decompose prompt + readiness check + the emitted
    # ``epic_context.constitution`` block. None until recon reads it (or when the
    # repo carries no constitution → today's behaviour). Excluded from
    # canonical_content(): the constitution is governance grounding, not plan
    # substance, so refreshing it must not invalidate a human approval.
    constitution_md: str | None = None
    # Factory#273: the trust marking for the plan's source text, stamped at the
    # ingest seams and carried into the contract's ``provenance.content_trust``.
    # None on plans predating the field (back-compat: absent => unmarked).
    # Excluded from canonical_content(): a provenance marker, not plan substance.
    content_trust: ContentTrust | None = None
    raw_text: str | None = None
    content_hash: str = ""
    ingested_at: str = Field(default_factory=_utcnow_iso)

    # ── hashing (approval invalidation) ────────────────────────────────

    def canonical_content(self) -> str:
        """The content the approval hash is computed over.

        Deliberately excludes volatile / non-semantic fields (``ingested_at``,
        ``enrichment``, ``content_hash``) so re-running enrichment doesn't
        invalidate a human approval, but editing the plan's substance does.
        """
        parts = [
            self.title.strip(),
            self.description.strip(),
            self.target_kind,
            self.plan_type or "",
            *[f"{c.id}:{c.text.strip()}" for c in self.criteria],
        ]
        # RFC-0010: fold a STABLE digest of the reconnaissance grounding into the
        # approval hash so sign-off invalidates when the repo drifts, yet stays
        # idempotent on the same commit. Uses only stable facts (baseline commit +
        # change_mode + primary language) — the file footprint is a pure function
        # of the commit, so the commit already captures it. Greenfield plans (no
        # repo_map) add nothing, preserving the historical hash.
        if self.repo_map is not None and self.repo_map.available:
            primary = self.repo_map.languages[0] if self.repo_map.languages else ""
            parts.append(f"recon:{self.repo_map.commit or ''}:{self.change_mode or ''}:{primary}")
        return "\n".join(parts)

    def compute_hash(self) -> str:
        return hashlib.md5(self.canonical_content().encode("utf-8")).hexdigest()

    def with_hash(self) -> NormalizedPlan:
        """Return a copy with ``content_hash`` set to the current content hash."""
        return self.model_copy(update={"content_hash": self.compute_hash()})

    def hash_matches(self) -> bool:
        """True if the stored ``content_hash`` still reflects the content."""
        return bool(self.content_hash) and self.content_hash == self.compute_hash()

    # ── construction ───────────────────────────────────────────────────

    @classmethod
    def from_spec(
        cls,
        spec: NormalizedSpec,
        *,
        seq: int = 1,
        plan_id: str | None = None,
        source_channel: SourceChannel = "cli",
        raw_text: str | None = None,
    ) -> NormalizedPlan:
        """Build a :class:`NormalizedPlan` from an ingested
        :class:`spec_sources.NormalizedSpec`, computing id + content hash.

        ``content_trust`` is derived from the channel (Factory#273): GitHub
        issue/discussion bodies are ``untrusted_user_content``; text arriving on
        the authenticated operator surfaces (portal/cli/mcp/agent) is
        ``trusted``. Ingest paths whose text is repo-authored despite the
        channel (spec-kit workspaces) override it via ``model_copy``.
        """
        content_trust: ContentTrust = (
            "untrusted_user_content"
            if source_channel in ("github_issue", "github_discussion")
            else "trusted"
        )
        plan = cls(
            plan_id=plan_id or make_plan_id(seq, spec.title),
            title=spec.title,
            description=spec.description,
            source_format=spec.source_format.value,
            source_channel=source_channel,
            criteria=[Criterion(id=c.id, text=c.text) for c in spec.criteria],
            raw_text=raw_text,
            content_trust=content_trust,
        )
        return plan.with_hash()
