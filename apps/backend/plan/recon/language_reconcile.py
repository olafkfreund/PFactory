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

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plan.models import NormalizedPlan
    from plan.recon.models import RepoMap

# Intended-language signals in spec prose. Ordered so the first hit wins; values
# are the canonical language name (matching project/stack_detector output).
_LANGUAGE_SIGNALS: list[tuple[str, tuple[str, ...]]] = [
    ("rust", ("rust", "cargo", " rs ", "tokio", "actix")),
    ("go", ("golang", " go ", "goroutine", "go.mod")),
    ("typescript", ("typescript", " ts ", "deno", "node.js", "nodejs")),
    ("javascript", ("javascript", " js ", "express.js")),
    ("python", ("python", "pytest", "fastapi", "django", "flask", "uv ")),
    ("java", ("java", "spring boot", "maven", "gradle")),
    ("csharp", ("c#", ".net", "dotnet", "asp.net")),
    ("ruby", ("ruby", "rails")),
    ("php", ("php", "laravel", "symfony")),
    ("kotlin", ("kotlin",)),
    ("swift", ("swift",)),
    ("cpp", ("c++", "cmake")),
]


def detect_spec_language(plan: NormalizedPlan) -> str | None:
    """Best-effort: the language the spec text *asks for*, or None if unstated.

    Pads the text with spaces so single-token signals like ``" go "`` match at
    string boundaries.
    """
    text = (
        " "
        + " ".join(
            [
                plan.title,
                plan.description,
                *(c.text for c in plan.criteria),
                plan.raw_text or "",
            ]
        ).lower()
        + " "
    )
    for lang, needles in _LANGUAGE_SIGNALS:
        if any(n in text for n in needles):
            return lang
    return None


@dataclass
class LanguageReconcile:
    """Outcome of reconciling spec intent with the repo's language."""

    resolved_language: str | None
    spec_language: str | None
    repo_language: str | None
    conflict: bool
    reason: str = ""


def reconcile_language(
    plan: NormalizedPlan, repo_map: RepoMap | None, change_mode: str
) -> LanguageReconcile:
    """Decide the language to plan in, flagging an unintended mismatch (#585)."""
    spec_lang = detect_spec_language(plan)
    repo_langs = list(repo_map.languages) if (repo_map and repo_map.available) else []
    repo_lang = repo_langs[0] if repo_langs else None

    # Greenfield / no repo grounding: the spec's intent is all we have.
    if not repo_lang:
        return LanguageReconcile(
            resolved_language=spec_lang,
            spec_language=spec_lang,
            repo_language=None,
            conflict=False,
        )

    # Migration: the difference is the whole point — target is the spec language.
    if change_mode == "migration":
        return LanguageReconcile(
            resolved_language=spec_lang or repo_lang,
            spec_language=spec_lang,
            repo_language=repo_lang,
            conflict=False,
            reason="migration: target language differs from source by design",
        )

    # No spec intent, or it matches the repo → use the grounded repo language.
    if spec_lang is None or spec_lang in repo_langs:
        return LanguageReconcile(
            resolved_language=repo_lang,
            spec_language=spec_lang,
            repo_language=repo_lang,
            conflict=False,
        )

    # Spec wants a different language and this is not a migration → conflict (#585).
    return LanguageReconcile(
        resolved_language=repo_lang,
        spec_language=spec_lang,
        repo_language=repo_lang,
        conflict=True,
        reason=(
            f"spec asks for {spec_lang!r} but the repo is {repo_lang!r}; "
            "not a migration. Correct the spec, re-classify as a migration, or waive."
        ),
    )
