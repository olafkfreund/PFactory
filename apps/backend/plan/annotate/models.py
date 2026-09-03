"""Data contracts for document annotation (Phase D)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from plan.review.models import Citation, Severity

# How an accepted suggestion is applied to the plan (#701).
#   insert_section    - append `replacement` to the description as a new section
#   append_tag        - append a `key: value` line the tag check scans for
#   replace_criterion - swap the criterion the suggestion is anchored to
#   manual            - nothing could be drafted honestly; a human writes it
ApplyMode = Literal["insert_section", "append_tag", "replace_criterion", "manual"]


class SuggestedEdit(BaseModel):
    """One suggested change anchored to a span of the original document.

    The original is never mutated — a suggestion *points at* an anchor (a line /
    heading / excerpt) and proposes a change, always with a ``why`` and (when the
    backing finding had one) a :class:`Citation`. ``anchor_line`` is 1-based, or 0
    when the suggestion is document-level.
    """

    anchor: str = ""  # the excerpt/heading the edit attaches to
    anchor_line: int = 0  # 1-based line in the original, 0 = whole document
    original_excerpt: str = ""
    suggestion: str
    why: str = ""
    severity: Severity = "info"
    source: str = ""  # the originating finding's source
    citation: Citation | None = None
    # ── acceptance (#701) ───────────────────────────────────────────────
    # ``id`` is stable within one annotation so the UI can send back exactly
    # which suggestions the human accepted. ``replacement`` is the drafted text;
    # empty with ``mode="manual"`` means no draft could be made honestly, which
    # the UI shows rather than papering over with a stub.
    id: str = ""
    replacement: str = ""
    mode: ApplyMode = "manual"


class AnnotationResult(BaseModel):
    """The full annotation of a plan document."""

    suggestions: list[SuggestedEdit] = Field(default_factory=list)
    improved_markdown: str = ""  # original verbatim + cited suggestions section
    change_log: list[dict] = Field(default_factory=list)
    original_preserved: bool = True
