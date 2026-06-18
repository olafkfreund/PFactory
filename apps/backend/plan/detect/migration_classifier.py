"""Detect a language-migration request (RFC-0010, Phase 5).

A migration is a *directional* rewrite — "rewrite the payments module **from**
Python **to** Rust". This is what distinguishes a legitimate migration from the
#585 conflict (spec language != repo language): the directional verb + an
explicit/implicit source that matches the repo's actual language.

When it fires, the planner records ``change_mode=migration`` and both
``source_language`` / ``target_language``, instead of HALTing on a language
mismatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from plan.recon.language_reconcile import _LANGUAGE_SIGNALS, detect_spec_language

if TYPE_CHECKING:
    from plan.models import NormalizedPlan
    from plan.recon.models import RepoMap

_DIRECTIONAL = re.compile(
    r"\b(re-?writ\w*|re-?implement\w*|port\w*|migrat\w*|convert\w*|translat\w*)\b"
)


@dataclass
class MigrationSignal:
    """Outcome of migration detection."""

    is_migration: bool
    source_language: str | None = None
    target_language: str | None = None


def _plan_text(plan: NormalizedPlan) -> str:
    return (
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


def _lang_hits(text: str) -> list[tuple[int, str]]:
    """(position, language) for every language signal present, earliest first."""
    hits: list[tuple[int, str]] = []
    for lang, needles in _LANGUAGE_SIGNALS:
        positions = [text.find(n) for n in needles if text.find(n) >= 0]
        if positions:
            hits.append((min(positions), lang))
    return sorted(hits)


def _nearest_after(
    text: str, markers: tuple[str, ...], hits: list[tuple[int, str]]
) -> str | None:
    """The first language appearing after any of ``markers`` (e.g. 'from', 'to')."""
    for marker in markers:
        mi = text.find(marker)
        if mi < 0:
            continue
        after = [lang for pos, lang in hits if pos > mi]
        if after:
            return after[0]
    return None


def classify_migration(
    plan: NormalizedPlan, repo_map: RepoMap | None
) -> MigrationSignal:
    """Detect a directional language rewrite grounded in the repo's language."""
    text = _plan_text(plan)
    if not _DIRECTIONAL.search(text):
        return MigrationSignal(False)

    hits = _lang_hits(text)
    target = _nearest_after(
        text, (" to ", " into ", " in "), hits
    ) or detect_spec_language(plan)
    source = _nearest_after(text, (" from ",), hits)
    if (
        source is None
        and repo_map is not None
        and repo_map.available
        and repo_map.languages
    ):
        source = repo_map.languages[0]

    if not (source and target and source != target):
        return MigrationSignal(False)
    return MigrationSignal(True, source_language=source, target_language=target)
