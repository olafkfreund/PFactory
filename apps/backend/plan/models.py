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
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from spec_sources import NormalizedSpec

TargetKind = Literal["software", "non-software", "undetermined"]
SourceChannel = Literal["portal", "cli", "mcp", "github_issue", "github_discussion"]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 50) -> str:
    """Lowercase, hyphenate, and trim a title into a URL/branch-safe slug."""
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return (slug[:max_len].rstrip("-") or "plan")


def make_plan_id(seq: int, title: str) -> str:
    """Build a deterministic ``NNN-slug`` plan id (mirrors AIFactory spec ids)."""
    return f"{seq:03d}-{slugify(title)}"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        :class:`spec_sources.NormalizedSpec`, computing id + content hash."""
        plan = cls(
            plan_id=plan_id or make_plan_id(seq, spec.title),
            title=spec.title,
            description=spec.description,
            source_format=spec.source_format.value,
            source_channel=source_channel,
            criteria=[Criterion(id=c.id, text=c.text) for c in spec.criteria],
            raw_text=raw_text,
        )
        return plan.with_hash()
