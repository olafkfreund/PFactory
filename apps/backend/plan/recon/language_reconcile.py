"""Reconcile the spec's intended language with the repo's actual one (RFC-0010).

Preserves the issue #585 contract: when a spec asks for language Y but the repo
is language X and this is *not* a migration, the spec language must win or the
run HALTs — never silently produce the repo's language. The HALT is implemented
as the hard ``language-reconciled`` readiness check (see
``plan/review/readiness/checks.py``); this module is the pure decision function.

Rules:

* No conflict — spec language absent, or it is among the repo's languages → use
  the repo's grounded language (the common ``modify`` case).
* Conflict + not migration → ``conflict=True``; the readiness check HALTs.
* Migration → the difference is intended; record both, no conflict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plan.models import NormalizedPlan
    from plan.recon.models import RepoMap

# Intended-language signals in spec prose. Ordered so the first hit wins; values
# are the canonical language name (matching project/stack_detector output).
#
# Written as plain tokens: matching is word-boundary aware (see _SIGNAL_PATTERNS),
# so nothing here needs the space-padding these once carried. That padding was a
# per-needle patch for a whole-class defect -- a bare "rust" still matched inside
# "untrusted", which is the most natural word in a security criterion, so every
# spec that satisfied the security lens then hard-failed the language gate as a
# Rust spec (#397). Boundaries also make the ordering non-load-bearing: "java" no
# longer matches inside "javascript".
_LANGUAGE_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
    ("rust", ("rust", "cargo", "rs", "tokio", "actix")),
    ("go", ("golang", "go", "goroutine", "go.mod")),
    ("typescript", ("typescript", "ts", "deno", "node.js", "nodejs")),
    ("javascript", ("javascript", "js", "express.js")),
    ("python", ("python", "pytest", "fastapi", "django", "flask", "uv")),
    ("java", ("java", "spring boot", "maven", "gradle")),
    ("csharp", ("c#", ".net", "dotnet", "asp.net")),
    ("ruby", ("ruby", "rails")),
    ("php", ("php", "laravel", "symfony")),
    ("kotlin", ("kotlin",)),
    ("swift", ("swift",)),
    ("cpp", ("c++", "cmake")),
]


def boundary(needle: str) -> str:
    r"""Escape ``needle``, anchoring only the ends that are word characters.

    ``\b`` asserts a word/non-word transition, so appending it to a needle that
    already ends in punctuation demands a following word character and the needle
    can never match: ``\bc\+\+\b`` does not match "c++ " (both ``+`` and the space
    are non-word). Anchoring per-end keeps "c#" and "c++" matchable while still
    refusing "AC#1" -- there is no boundary before the ``c`` in "ac#1".
    """
    return (
        (r"\b" if needle[0].isalnum() else "")
        + re.escape(needle)
        + (r"\b" if needle[-1].isalnum() else "")
    )


_SIGNAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (lang, re.compile("|".join(boundary(n) for n in needles)))
    for lang, needles in _LANGUAGE_SIGNALS
]


def detect_spec_language_signal(plan: NormalizedPlan) -> tuple[str | None, str | None]:
    """Return ``(language, matched token)``, or ``(None, None)`` if unstated.

    The token is what makes a language conflict diagnosable: the failure names
    only the detected language, so an author who never mentioned Rust has no way
    to see that the word "untrusted" is what produced it (#397).
    """
    text = " ".join(
        [
            plan.title,
            plan.description,
            *(c.text for c in plan.criteria),
            plan.raw_text or "",
        ]
    ).lower()
    for lang, pattern in _SIGNAL_PATTERNS:
        match = pattern.search(text)
        if match:
            return lang, match.group(0)
    return None, None


def detect_spec_language(plan: NormalizedPlan) -> str | None:
    """Best-effort: the language the spec text *asks for*, or None if unstated."""
    return detect_spec_language_signal(plan)[0]


@dataclass
class LanguageReconcile:
    """Outcome of reconciling spec intent with the repo's language."""

    resolved_language: str | None
    spec_language: str | None
    repo_language: str | None
    conflict: bool
    reason: str = ""
    # The literal token that produced `spec_language`, so a conflict can name its
    # own evidence rather than leaving the author to guess which word did it.
    spec_language_signal: str | None = None


def reconcile_language(
    plan: NormalizedPlan, repo_map: RepoMap | None, change_mode: str
) -> LanguageReconcile:
    """Decide the language to plan in, flagging an unintended mismatch (#585)."""
    spec_lang, spec_signal = detect_spec_language_signal(plan)
    repo_langs = list(repo_map.languages) if (repo_map and repo_map.available) else []
    repo_lang = repo_langs[0] if repo_langs else None

    # Greenfield / no repo grounding: the spec's intent is all we have.
    if not repo_lang:
        return LanguageReconcile(
            resolved_language=spec_lang,
            spec_language=spec_lang,
            spec_language_signal=spec_signal,
            repo_language=None,
            conflict=False,
        )

    # Migration: the difference is the whole point — target is the spec language.
    if change_mode == "migration":
        return LanguageReconcile(
            resolved_language=spec_lang or repo_lang,
            spec_language=spec_lang,
            spec_language_signal=spec_signal,
            repo_language=repo_lang,
            conflict=False,
            reason="migration: target language differs from source by design",
        )

    # No spec intent, or it matches the repo → use the grounded repo language.
    if spec_lang is None or spec_lang in repo_langs:
        return LanguageReconcile(
            resolved_language=repo_lang,
            spec_language=spec_lang,
            spec_language_signal=spec_signal,
            repo_language=repo_lang,
            conflict=False,
        )

    # Spec wants a different language and this is not a migration → conflict (#585).
    return LanguageReconcile(
        resolved_language=repo_lang,
        spec_language=spec_lang,
        spec_language_signal=spec_signal,
        repo_language=repo_lang,
        conflict=True,
        reason=(
            f"spec asks for {spec_lang!r} but the repo is {repo_lang!r}; "
            "not a migration. Correct the spec, re-classify as a migration, or waive."
        ),
    )
