"""Lightweight prompt-injection scan for intake TEXT (Factory#273, #283).

Defense in depth at the fleet's front door: GitHub issue/spec bodies are
untrusted user content, and a body carrying likely injection payloads must not
auto-proceed through the tiered pipeline. This module holds the shared pattern
list (mirrors AIFactory's ``ContentSanitizer.INJECTION_PATTERNS`` so the two
services flag the same payloads) and a single ``scan_text`` entry point.

Deliberately TEXT ONLY: the scan runs over the issue/spec body at
classification time. Repo-content scanning belongs to AIFactory's pre-coder
gate (AIFactory#805) — never walk repositories here.
"""

from __future__ import annotations

import re

__all__ = ["INJECTION_PATTERNS", "scan_text"]

# Patterns that look like prompt-injection attempts. Kept in lockstep with
# AIFactory apps/backend/runners/github/sanitize.py INJECTION_PATTERNS, minus
# the bare ``system:`` pattern — ordinary spec prose ("the system: must …")
# would trip it, and a flag here parks the plan for human review, so the cost
# of that false positive is a stalled intake rather than a stripped substring.
INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # The qualifier group repeats so stacked forms ("ignore all previous
    # instructions") match too — a strict superset of the AIFactory patterns.
    re.compile(r"ignore\s+(?:(?:previous|above|all)\s+)+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+(?:(?:previous|above|all)\s+)+instructions?", re.IGNORECASE),
    re.compile(r"forget\s+(?:(?:previous|above|all)\s+)+instructions?", re.IGNORECASE),
    re.compile(r"new\s+instructions?:", re.IGNORECASE),
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"\[SYSTEM\]", re.IGNORECASE),
    re.compile(r"```system", re.IGNORECASE),
    re.compile(r"IMPORTANT:\s*ignore", re.IGNORECASE),
    re.compile(r"override\s+safety", re.IGNORECASE),
    re.compile(r"bypass\s+restrictions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"pretend\s+you\s+are", re.IGNORECASE),
    re.compile(r"act\s+as\s+if\s+you", re.IGNORECASE),
)


def scan_text(text: str | None) -> list[str]:
    """Return the injection patterns matched in ``text`` (empty = clean).

    Pure and stdlib-only; each hit is reported as the pattern source so the
    verdict reason is auditable without echoing the payload back into prompts.
    """
    if not text:
        return []
    return [p.pattern for p in INJECTION_PATTERNS if p.search(text)]
