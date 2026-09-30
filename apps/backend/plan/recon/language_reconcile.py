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

# A weak token resolves under exactly one of three rules (#827). #822's first
# attempt at this was a prefix list plus a ten-word function-word denylist, and
# both leaked: "responded in swift succession" and "a swift and reliable api" each
# HALTed a valid plan on the hard language-reconciled gate.
#
# A  a STRONG prefix -- evidence on its own, at any casing.
_STRONG_PREFIX = (
    r"(?:written\s+in|rewritten\s+in|rewrite\s+in|ported\s+to|"
    r"migrate[ds]?\s+to|implemented\s+in)\s+"
)
# B  a BARE prefix, which proves nothing by itself, so the token must also be
#    capitalised in the ORIGINAL text: "write it in Go" resolves, "in swift
#    succession" does not. Case RAISES confidence here; it never gates tier 1,
#    because briefs arrive lowercased (issue bodies, pasted logs) and on a hard
#    gate a false negative -- missing a real mismatch -- is the worse error.
_BARE_PREFIX = r"(?:in|using|with)\s+"
# C  the token followed by a noun that makes it a language.
_LANG_NOUN = (
    r"(?:service|module|package|binary|app|application|code|codebase|version|"
    r"program|library|sdk|backend|api|microservice|project)"
)
# A qualifier may sit between the token and the noun, but only if it LOOKS like a
# proper noun, acronym or version -- it must carry an uppercase letter or a digit.
# A shape allowlist, not a word denylist: "and", "live" and "reliable" walked
# through the denylist, and English cannot be enumerated.
_QUALIFIER = r"(?:\s+[\w.+#-]*[A-Z0-9][\w.+#-]*){0,2}"

# Tokens that are ordinary English words as well as language names. Rule C requires
# these Capitalised-but-NOT-ALL-CAPS, which is what separates "A Swift SPM library"
# from "A SWIFT MT103 service" -- SWIFT being the interbank network, a real shape in
# this product's payments briefs.
_ENGLISH_WORD_TOKENS = frozenset({"go", "swift", "flask", "cargo", "django", "maven"})

# Tier 2: tokens with an everyday meaning of their own. #397 only half-fixed this
# class -- word boundaries stopped matching *inside* words, but not words that are
# ordinary English on their own, so "users can go to the next screen" read as Go.
# `cargo`, `flask`, `django` and `maven` live here rather than in the tool tier for
# exactly that reason: "track cargo across the fleet" is not a Rust plan (#827).
_WEAK_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
    ("go", ("go",)),
    ("swift", ("swift",)),
    ("typescript", ("ts",)),
    ("javascript", ("js",)),
    ("rust", ("rs", "cargo")),
    ("python", ("uv", "flask", "django")),
    ("java", ("maven",)),
]

# Tier 3: build tools and ecosystems, reached only when tiers 1 and 2 say nothing.
# Only tokens with no everyday English meaning belong here -- this tier has no
# context requirement, so anything ambiguous placed in it fires on prose (#827).
_TOOL_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
    ("rust", ("tokio", "actix")),
    ("python", ("pytest", "fastapi")),
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


# The spec text keeps its original casing (rules B and C read it), so every pattern
# that should ignore case says so explicitly. A missing `re.I` here is a silent
# false negative, which on this gate is the worse failure -- hence the table covers
# each tier's tokens in both casings.
_NAME_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (lang, re.compile("|".join(boundary(n) for n in needles), re.I))
    for lang, needles in _LANGUAGE_NAMES
]


def _weak_alternatives(needle: str) -> list[str]:
    """Rules A, B and C for one weak token (#827); see the constants above."""
    escaped = re.escape(needle)
    alternatives = [
        # A -- a strong prefix is evidence at any casing, so the token is folded in.
        rf"(?i:{_STRONG_PREFIX}{escaped})\b",
        # B -- a bare prefix plus a capitalised token.
        rf"(?i:{_BARE_PREFIX})(?:{needle.capitalize()}|{needle.upper()})\b",
    ]
    if needle in _ENGLISH_WORD_TOKENS:
        # C -- Capitalised, and NOT all-caps: "Swift SPM library" yes, "SWIFT
        # MT103 service" no.
        alternatives.append(
            rf"\b{needle.capitalize()}\b(?![A-Z]){_QUALIFIER}\s+(?i:{_LANG_NOUN})\b"
        )
    else:
        alternatives.append(rf"(?i:\b{escaped}\b){_QUALIFIER}\s+(?i:{_LANG_NOUN})\b")
    return alternatives


_WEAK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        lang,
        re.compile("|".join(alt for n in needles for alt in _weak_alternatives(n))),
    )
    for lang, needles in _WEAK_SIGNALS
]

_TOOL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (lang, re.compile("|".join(boundary(n) for n in needles), re.I))
    for lang, needles in _TOOL_SIGNALS
]

_SHARED_TOOL_PATTERN = re.compile("|".join(boundary(n) for n in _SHARED_TOOLS), re.I)


def detect_spec_language_signal(plan: NormalizedPlan) -> tuple[str | None, str | None]:
    """Return ``(language, matched token)``, or ``(None, None)`` if unstated.

    The token is what makes a language conflict diagnosable: the failure names
    only the detected language, so an author who never mentioned Rust has no way
    to see that the word "untrusted" is what produced it (#397).

    Consults three tiers in order of how much a match actually proves (#801):
    an unambiguous language name, then a weak English word that only counts as
    evidence under rules A/B/C above, then a build tool/ecosystem. A token several
    languages share (``gradle``, ``android``) is a real ambiguity, not a guess --
    it resolves to ``(None, None)`` so #585's conflict gate stays honest.

    The text is NOT lowercased (#827): rules B and C read original casing, since a
    capitalised "Go" is evidence where "go" is a verb. Tier 1 never requires case --
    briefs arrive lowercased from issue bodies and pasted logs, and on a hard gate
    failing to catch a real mismatch is worse than reporting a spurious one. The
    returned token is lowered so the author-facing evidence string is unchanged.
    """
    text = " ".join(
        [
            plan.title,
            plan.description,
            *(c.text for c in plan.criteria),
            plan.raw_text or "",
        ]
    )

    for lang, pattern in _NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            return lang, match.group(0).lower()

    for lang, pattern in _WEAK_PATTERNS:
        match = pattern.search(text)
        if match:
            return lang, match.group(0).strip().lower()

    for lang, pattern in _TOOL_PATTERNS:
        match = pattern.search(text)
        if match:
            return lang, match.group(0).lower()

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
