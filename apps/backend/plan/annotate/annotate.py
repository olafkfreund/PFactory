"""Turn review findings into anchored, cited suggestions + an improved draft (Phase D)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from plan.annotate.models import AnnotationResult, SuggestedEdit

if TYPE_CHECKING:
    from plan.models import NormalizedPlan
    from plan.review.models import Finding, PlanReview

# Words too generic to anchor on.
_STOP = {
    "the", "a", "an", "is", "are", "no", "not", "to", "of", "in", "on", "and",
    "or", "for", "with", "plan", "issue", "child", "has", "have", "should",
    "criteria", "criterion", "this", "that", "its", "be", "needs",
}


def _change_proposing(finding: Finding) -> bool:
    """A finding proposes a change when it's more than an info note."""
    return finding.severity != "info" or bool(finding.blocking)


def _anchor_for(finding: Finding, lines: list[str]) -> tuple[str, int, str]:
    """Find the best line in the original to attach this finding to.

    Returns (anchor_excerpt, 1-based line number, original_excerpt). Falls back
    to document-level (line 0) when no distinctive token matches.
    """
    # Split on punctuation so compound tokens like "rds:CreateDBInstance" yield
    # anchorable words ("rds", "CreateDBInstance") that match the source text.
    tokens = [
        t.lower()
        for t in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", f"{finding.title} {finding.detail}")
        if t.lower() not in _STOP
    ]
    # Prefer the most distinctive (longest) tokens first.
    for tok in sorted(set(tokens), key=len, reverse=True):
        for i, line in enumerate(lines):
            if tok in line.lower():
                excerpt = line.strip()[:160]
                return excerpt, i + 1, excerpt
    return "", 0, ""


def annotate_plan(plan: NormalizedPlan, review: PlanReview | None) -> AnnotationResult:
    """Build anchored, cited suggestions + an improved draft for a plan.

    The original ``raw_text`` is preserved verbatim; the improved draft appends a
    clearly-marked, cited suggestions section beneath it.
    """
    original = plan.raw_text or plan.description or ""
    lines = original.splitlines()
    suggestions: list[SuggestedEdit] = []
    change_log: list[dict] = []

    findings: list[Finding] = []
    if review is not None:
        for lens in review.lenses:
            findings.extend(lens.findings)

    for f in findings:
        if not _change_proposing(f):
            continue
        anchor, line_no, excerpt = _anchor_for(f, lines)
        citation = f.citations[0] if f.citations else None
        why = (citation.why if citation and citation.why else f.detail) or f.title
        suggestions.append(
            SuggestedEdit(
                anchor=anchor,
                anchor_line=line_no,
                original_excerpt=excerpt,
                suggestion=f.title,
                why=why,
                severity=f.severity,
                source=f.source,
                citation=citation,
            )
        )
        change_log.append(
            {
                "suggestion": f.title,
                "source": f.source,
                "anchor_line": line_no,
                "citation_uri": citation.uri if citation else "",
            }
        )

    improved = _render_improved(original, suggestions)
    return AnnotationResult(
        suggestions=suggestions,
        improved_markdown=improved,
        change_log=change_log,
        original_preserved=True,
    )


def _render_improved(original: str, suggestions: list[SuggestedEdit]) -> str:
    """Original verbatim + a cited 'suggested edits' section (never in-place)."""
    if not suggestions:
        return original
    out = [original.rstrip(), "", "---", "", "## PFactory review — suggested edits", ""]
    out.append(
        "_The original above is unchanged. PFactory suggests the edits below; "
        "accept, reject, or adopt the improved version. Each cites why._",
    )
    out.append("")
    for i, s in enumerate(suggestions, 1):
        where = f"line {s.anchor_line}" if s.anchor_line else "whole document"
        out.append(f"### {i}. {s.suggestion}  `[{s.severity}]`")
        out.append(f"- **Where:** {where}" + (f" — “{s.original_excerpt}”" if s.original_excerpt else ""))
        out.append(f"- **Why:** {s.why}")
        if s.citation and (s.citation.uri or s.citation.title):
            label = s.citation.title or s.citation.source or s.citation.uri
            out.append(f"- **Source:** [{label}]({s.citation.uri})" if s.citation.uri else f"- **Source:** {label}")
        out.append("")
    return "\n".join(out)
