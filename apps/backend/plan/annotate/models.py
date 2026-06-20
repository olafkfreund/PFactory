"""Data contracts for document annotation (Phase D)."""

from __future__ import annotations

from plan.review.models import Citation, Severity
from pydantic import BaseModel, Field


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


class AnnotationResult(BaseModel):
    """The full annotation of a plan document."""

    suggestions: list[SuggestedEdit] = Field(default_factory=list)
    improved_markdown: str = ""  # original verbatim + cited suggestions section
    change_log: list[dict] = Field(default_factory=list)
    original_preserved: bool = True
