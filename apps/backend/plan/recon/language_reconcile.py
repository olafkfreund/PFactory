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

# Intended-language signals in spec prose, split into three tiers consulted in
# order of how much a match actually proves (#801). Within a tier, order does not
# matter because no two entries in a tier share a token; across tiers, order *is*
# the design -- a name beats a weak English word beats a shared build tool.
#
# Written as plain tokens: matching is word-boundary aware (see boundary()), so
# nothing here needs the space-padding these once carried. That padding was a
# per-needle patch for a whole-class defect -- a bare "rust" still matched inside
# "untrusted", which is the most natural word in a security criterion, so every
# spec that satisfied the security lens then hard-failed the language gate as a
# Rust spec (#397).

# Tier 1: tokens that name exactly one language. A match resolves immediately --
# this is what fixes the reported defect (#801): "Kotlin ... Gradle" hits kotlin
# here and never reaches "gradle" in tier 3.
_LANGUAGE_NAMES: list[tuple[str, tuple[str, ...]]] = [
    ("rust", ("rust",)),
    ("go", ("golang", "go.mod", "goroutine", "gofmt")),
    ("typescript", ("typescript", "deno")),
    ("javascript", ("javascript", "express.js", "node.js", "nodejs")),
    ("python", ("python",)),
    ("java", ("java", "spring boot")),
    ("csharp", ("c#", ".net", "dotnet", "asp.net")),
    ("ruby", ("ruby", "rails")),
    ("php", ("php", "laravel", "symfony")),
    ("kotlin", ("kotlin",)),
    ("swift", ("swiftui",)),
    ("cpp", ("c++",)),
]

_CTX_BEFORE = r"(?:written\s+in|rewritten\s+in|ported\s+to|migrate[ds]?\s+to|in|using|with)\s+"
_AFTER_WORDS = (
    r"(?:service|module|package|binary|app|application|code|codebase|"
    r"version|program|library|sdk|backend|api)"
)
# A qualifier may sit between the token and the noun ("Swift SPM library",
# "Go HTTP service"), but a FUNCTION word may not: without that exclusion
# "users go to the api" reads as Go again, which is the false positive this
# tier exists to stop (#801).
_GAP = (
    r"(?:\s+(?!to\b|the\b|a\b|an\b|through\b|into\b|from\b|onto\b|for\b|of\b)"
    r"[\w.+#-]+){0,2}"
)
_CTX_AFTER = rf"{_GAP}\s+{_AFTER_WORDS}"

# Tier 2: whole words with everyday or ambiguous meanings ("go", "swift" ...).
# They only count inside a phrase that makes the subject a language (#397 only
# half-fixed this class: word boundaries stopped matching *inside* words, but not
# ordinary words used on their own -- "users can go to the next screen" still read
# as Go).
_WEAK_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
    ("go", ("go",)),
    ("swift", ("swift",)),
    ("typescript", ("ts",)),
    ("javascript", ("js",)),
    ("rust", ("rs",)),
    ("python", ("uv",)),
]

# Tier 3: build tools and ecosystems, reached only when tiers 1 and 2 say nothing.
_TOOL_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
    ("rust", ("cargo", "tokio", "actix")),
    ("python", ("pytest", "fastapi", "django", "flask")),
    ("java", ("maven",)),
    ("kotlin", ("jetpack compose",)),
    ("cpp", ("cmake",)),
]

# Tokens that legitimately belong to several languages -- these deliberately
# resolve to None rather than guessing or tie-breaking on the repo language.
# `None` already means "unstated" to reconcile_language, which then grounds on
# the repo language with no conflict: the honest answer when the spec genuinely
# has not said. #585 requires that a real conflict HALT rather than resolve
# quietly, so a repo-preferring tie-break here would be a back door around it.
_SHARED_TOOLS: dict[str, tuple[str, ...]] = {
    "gradle": ("java", "kotlin", "scala", "groovy"),
    "android": ("java", "kotlin"),
}

# Back-compat alias for two consumers that still import the pre-#801 flat table
# directly: `plan/detect/migration_classifier.py` (builds its token→language
# `_CANON` map) and `tests/test_synthesize.py`'s #475 drift guard (every
# detectable language has a source extension). Derived, not hand-maintained --
# the per-language union of the three tiers, in `_LANGUAGE_NAMES` order so all
# 12 languages appear. `_SHARED_TOOLS` is deliberately excluded: its tokens are
# ambiguous by construction, and folding `gradle` back into java's token set is
# the exact defect #801 fixed.
_LANGUAGE_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
    (
        lang,
        tuple(
            dict.fromkeys(
                needles + dict(_WEAK_SIGNALS).get(lang, ()) + dict(_TOOL_SIGNALS).get(lang, ())
            )
        ),
    )
    for lang, needles in _LANGUAGE_NAMES
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


_NAME_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (lang, re.compile("|".join(boundary(n) for n in needles))) for lang, needles in _LANGUAGE_NAMES
]

_WEAK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        lang,
        re.compile(
            "|".join(
                rf"{_CTX_BEFORE}{re.escape(n)}\b|\b{re.escape(n)}\b{_CTX_AFTER}\b" for n in needles
            )
        ),
    )
    for lang, needles in _WEAK_SIGNALS
]

_TOOL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (lang, re.compile("|".join(boundary(n) for n in needles))) for lang, needles in _TOOL_SIGNALS
]

_SHARED_TOOL_PATTERN = re.compile("|".join(boundary(n) for n in _SHARED_TOOLS))


def detect_spec_language_signal(plan: NormalizedPlan) -> tuple[str | None, str | None]:
    """Return ``(language, matched token)``, or ``(None, None)`` if unstated.

    The token is what makes a language conflict diagnosable: the failure names
    only the detected language, so an author who never mentioned Rust has no way
    to see that the word "untrusted" is what produced it (#397).

    Consults three tiers in order of how much a match actually proves (#801):
    an unambiguous language name, then a weak English word that only counts in a
    language context, then a build tool/ecosystem. A token several languages
    share (``gradle``, ``android``) is a real ambiguity, not a guess -- it
    resolves to ``(None, None)`` so #585's conflict gate stays honest.
    """
    text = " ".join(
        [
            plan.title,
            plan.description,
            *(c.text for c in plan.criteria),
            plan.raw_text or "",
        ]
    ).lower()

    for lang, pattern in _NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            return lang, match.group(0)

    for lang, pattern in _WEAK_PATTERNS:
        match = pattern.search(text)
        if match:
            return lang, match.group(0).strip()

    for lang, pattern in _TOOL_PATTERNS:
        match = pattern.search(text)
        if match:
            return lang, match.group(0)

    # A token several languages share proves nothing on its own -- resolving it
    # to a guess (or tie-breaking on the repo language) would be a back door
    # around #585's requirement that a real conflict HALT rather than resolve
    # quietly. Deliberately None, not a fall-through accident.
    if _SHARED_TOOL_PATTERN.search(text):
        return None, None

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
