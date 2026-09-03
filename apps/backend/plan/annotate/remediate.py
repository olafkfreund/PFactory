"""Draft a concrete replacement for a suggestion, so it can be APPLIED (#701).

Until now a "suggested edit" was the review finding's title re-anchored to a
line: it told you what was wrong and never what to write. Nothing could be
accepted, because there was no text to accept.

This module drafts that text. It is deliberately **deterministic**: every draft
below is a curated remediation for a finding this repo's own lenses raise, keyed
off the finding's citation or its title shape. A generated draft would vary run
to run, and these land in compliance documents.

Two rules the drafts follow:

* **A draft is a starting point, never an answer.** Where the correct value is a
  business decision — a minimum age, a cost centre, a retention period — the
  draft leaves an explicit ``<...>`` placeholder rather than inventing a
  plausible number. An invented number would read as decided and pass the gate
  that exists to notice it is not.
* **No draft is better than a fake one.** A finding with no curated remediation
  gets ``mode="manual"`` and an empty ``replacement``; the UI says so. Emitting
  a "TODO: address X" stub would satisfy the text-matching checks while fixing
  nothing, which is the failure mode this whole feature exists to avoid.
"""

from __future__ import annotations

import re

from .models import ApplyMode, SuggestedEdit

__all__ = ["draft_replacement", "with_replacements"]

# ── the curated remediations ──────────────────────────────────────────────

_LAWFUL_BASIS = """## Data protection — lawful basis and purpose limitation

Personal data in this service is processed on the lawful basis of
**<lawful basis: e.g. consent / contract / legitimate interests>** (UK GDPR
Art. 6(1)). It is collected for the specified, explicit purpose of
**<purpose: e.g. matching users with compatible friends>** and is not further
processed in a manner incompatible with that purpose (Art. 5(1)(b)).

Where legitimate interests is relied on, the balancing test is recorded in
**<link to the LIA>**. Where consent is relied on, it is freely given,
specific, informed and withdrawable by the same number of steps as it was
given."""

_LOCATION_HANDLING = """## Data protection — location data

Location is collected at **<precision: e.g. town/city or postcode-district>**
granularity and never at GPS precision. The value stored is derived on the
server; the precise value the device reports is discarded at the point of
derivation and is never persisted, logged, or returned in any client-facing
response.

Other users see only **<what is shown: e.g. an approximate distance band>**.
Location is retained for **<retention period>** and is deleted with the account.
Users can change or remove their location without deleting their profile."""

_PROFILING_TRANSPARENCY = """## Data protection — automated matching and profiling

Users are told, before their profile becomes discoverable, that their
interests, activities and approximate location are used to rank and suggest
other users, and what the main criteria are (UK GDPR Art. 13(2)(f)).

This ranking does not produce legal or similarly significant effects. No
decision with such effect is made about a user solely by automated means. Users
can see why a suggestion was made and can opt out of being suggested to
others."""

# A citation URI/title fragment → the section that answers it. Matching on the
# citation rather than the title keeps this working when a lens rewords itself.
_BY_CITATION: tuple[tuple[str, str], ...] = (
    ("art. 5", _LAWFUL_BASIS),
    ("art. 6", _LOCATION_HANDLING),
    ("art. 13", _PROFILING_TRANSPARENCY),
)

# Title-shape fallbacks, for findings that arrive without a citation.
_BY_TITLE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"lawful basis|purpose limitation", re.I), _LAWFUL_BASIS),
    (re.compile(r"location data", re.I), _LOCATION_HANDLING),
    (re.compile(r"profiling|automated (matching|decision)", re.I), _PROFILING_TRANSPARENCY),
)

_MISSING_TAG = re.compile(r"missing required tag '([^']+)'", re.I)
_AMBIGUOUS_AC = re.compile(r"ambiguous.*?\b(AC#\d+)", re.I)


def _tag_draft(tag: str) -> tuple[str, ApplyMode]:
    """A ``key: value`` line, which is exactly what the tag check looks for.

    ``templates/loader.py`` collects tags by scanning the plan text for
    ``key:``/``key=``, so this is not an approximation of the fix — it is the
    fix. The value is left as a placeholder because only the owner knows it.
    """
    return f"{tag}: <{tag}>", "append_tag"


def draft_replacement(edit: SuggestedEdit) -> tuple[str, ApplyMode]:
    """Return ``(replacement, mode)`` for one suggestion.

    ``("", "manual")`` when nothing can be drafted honestly — the caller shows
    that as "no automatic draft" rather than inventing one.
    """
    title = edit.suggestion or ""

    tag = _MISSING_TAG.search(title)
    if tag:
        return _tag_draft(tag.group(1))

    ac = _AMBIGUOUS_AC.search(title)
    if ac:
        # The criterion is replaced wholesale, so the draft has to carry the
        # measurable threshold the finding says is missing — as a placeholder,
        # since the number itself is the business decision being flagged.
        return (
            f"{edit.original_excerpt.strip() or ac.group(1)} "
            "— measurable form: the system rejects values outside "
            "<explicit threshold, e.g. 18-120> and shows <the exact message>.",
            "replace_criterion",
        )

    citation_text = ""
    if edit.citation is not None:
        citation_text = f"{edit.citation.title or ''} {edit.citation.uri or ''}".lower()
    for needle, section in _BY_CITATION:
        if needle in citation_text:
            return section, "insert_section"

    for pattern, section in _BY_TITLE:
        if pattern.search(title):
            return section, "insert_section"

    return "", "manual"


def with_replacements(edits: list[SuggestedEdit]) -> list[SuggestedEdit]:
    """Return copies of *edits* carrying a drafted ``replacement`` + ``mode``."""
    out: list[SuggestedEdit] = []
    for edit in edits:
        replacement, mode = draft_replacement(edit)
        out.append(edit.model_copy(update={"replacement": replacement, "mode": mode}))
    return out
