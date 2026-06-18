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

from plan.recon.language_reconcile import _LANGUAGE_SIGNALS

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


# Map a captured language token → canonical language. Built from the reconcile
# signal map: the canonical names themselves (go, rust, python, ...) plus the
# single-word needles (golang→go, cargo→rust, c#→csharp, ...). Multi-word needles
# (e.g. " go ") are skipped — the canonical name already covers the bare token.
_CANON: dict[str, str] = {}
for _lang, _needles in _LANGUAGE_SIGNALS:
    _CANON[_lang] = _lang
    for _needle in _needles:
        _t = _needle.strip()
        if _t and " " not in _t:
            _CANON.setdefault(_t, _lang)

_LANG_TOKEN = r"[a-z0-9+#.]+"
# Parse the directional clause as a unit so distractor mentions elsewhere (e.g.
# "the Rust refund" in an acceptance criterion, or "c#" inside "AC#1") can't
# misroute source/target.
_FROM_TO = re.compile(rf"\bfrom\s+({_LANG_TOKEN})\s+(?:in)?to\s+({_LANG_TOKEN})")
_TO_ONLY = re.compile(
    r"\b(?:re-?writ\w*|re-?implement\w*|port\w*|migrat\w*|convert\w*|translat\w*)\b"
    rf"[^.]*?\b(?:into|in|to)\s+({_LANG_TOKEN})"
)


def _canon(token: str | None) -> str | None:
    if not token:
        return None
    # Strip trailing sentence punctuation ("rust." -> "rust") without harming
    # language tokens like ".net", "c++", or "c#".
    return _CANON.get(token.strip().lower().rstrip(".,;:!?)"))


def classify_migration(
    plan: NormalizedPlan, repo_map: RepoMap | None
) -> MigrationSignal:
    """Detect a directional language rewrite grounded in the repo's language."""
    text = _plan_text(plan)
    if not _DIRECTIONAL.search(text):
        return MigrationSignal(False)

    source = target = None
    # "rewrite X from <L1> to <L2>" — the unambiguous form.
    m = _FROM_TO.search(text)
    if m:
        source, target = _canon(m.group(1)), _canon(m.group(2))
    # "rewrite X in/to <L2>" — target only; source comes from the repo. Target
    # must come from the directional clause; we deliberately do NOT fall back to a
    # loose keyword scan, so a passing mention ("a rust-style retry") can't be
    # mistaken for a migration target.
    if target is None:
        m2 = _TO_ONLY.search(text)
        target = _canon(m2.group(1)) if m2 else None
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
