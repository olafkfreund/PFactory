"""Attach the registry's matching `kind: skill` rows to the contract (#687).

The plan registry has carried a skill lane since it existed, and the rows name
the skill files this repo ships (`skills/engineering/*.md`). Nothing read them:
PFactory raised an obligation — the compliance lens cites a retention duty, the
mobile requirements demand an OS floor — and handed AIFactory a contract that
said what must be true without mentioning that a written skill for exactly that
work was sitting in the repo.

:func:`attach_skills` closes that: it derives what the contract *needs* from
signals already on it, matches those needs against each enabled row's declared
``capabilities``, and records the matches in ``epic_context.skills`` beside
``house_standards`` and ``constitution``.

Conservative by construction. A wrongly attached skill points a coder at
irrelevant guidance; a missed one is the behaviour before this module existed.
So needs come from declared, closed vocabularies (the compliance lens's data
classes, the plan type) — never from scanning prose — and a signal is mapped
only when it can mean nothing else.
"""

from __future__ import annotations

from typing import Any

_SOURCE = "pfactory:plan-registry"

# Data classes that mean "this plan handles people's data". `store-distribution`
# is deliberately absent: it is about app stores, which a web plan can mention,
# and mapping it would pull the mobile skill into a non-mobile build.
_PERSONAL_DATA_CLASSES = frozenset(
    {"account", "location", "personal-profile", "profile", "profiling", "user-contact"}
)

# The plan types whose work a skill row speaks to, by plan_type -> need.
_PLAN_TYPE_NEEDS = {"mobile-app": "mobile"}


def derive_needs(contract: dict[str, Any], plan: Any = None) -> set[str]:
    """What this contract needs help with, as capability tokens.

    Reads signals that are already decided by the time the contract is
    assembled: the compliance block (attached just before this runs) and the
    plan type. Each mapping is one row here, so adding a signal later is a row
    and a test rather than a rewrite.
    """
    needs: set[str] = set()

    compliance = contract.get("compliance")
    if isinstance(compliance, dict):
        if compliance.get("obligations"):
            needs.add("compliance-block")
        classes = compliance.get("data_classes") or []
        if isinstance(classes, list) and _PERSONAL_DATA_CLASSES.intersection(classes):
            needs.add("privacy")

    plan_type = getattr(plan, "plan_type", None) if plan is not None else None
    need = _PLAN_TYPE_NEEDS.get(str(plan_type or ""))
    if need:
        needs.add(need)

    return needs


def _matching_skills(needs: set[str]) -> list[dict[str, str]]:
    """Enabled skill rows whose capabilities intersect ``needs``, in lane order."""
    from plan.registry import (  # noqa: PLC0415 - lazy: keep the registry out of the emit import graph
        load_registry,
    )

    matches = []
    for entry in load_registry().enabled("skill"):
        if not needs.intersection(entry.capabilities or []):
            continue
        matches.append(
            {
                "id": entry.id,
                "title": entry.title,
                "path": str((entry.config or {}).get("path", "")),
            }
        )
    return matches


def _catalogue_present() -> bool:
    """True when the registry catalogue directory is actually there.

    The loader degrades a missing directory to an empty list, which would make
    ``available: true`` mean "we read nothing" (review on #760).
    """
    from plan.registry import loader  # noqa: PLC0415 - lazy, as _matching_skills is

    return bool(loader._CATALOGUE_DIR.is_dir())


def build_skills_block(contract: dict[str, Any], plan: Any = None) -> dict[str, Any]:
    """Build the ``epic_context.skills`` block.

    ``available`` says the catalogue was read, NOT that anything matched — so
    "we looked and no skill applies" (``available: true``, empty ``skills``,
    ``matched_on`` showing what was looked for) stays distinguishable from "the
    catalogue could not be read" (``available: false``). The key being absent
    entirely means the contract predates this feature.
    """
    needs = derive_needs(contract, plan)
    try:
        # `_load_catalogue` returns [] for a MISSING directory rather than
        # raising, so "no catalogue" and "catalogue with no match" both arrive
        # here as an empty list — the two states this block promises to keep
        # apart (review on #760). Check the source before claiming we looked.
        if not _catalogue_present():
            return {
                "available": False,
                "source": _SOURCE,
                "matched_on": sorted(needs),
                "skills": [],
            }
        skills = _matching_skills(needs)
    except Exception:  # noqa: BLE001 — an unreadable catalogue must not fail an emit
        return {
            "available": False,
            "source": _SOURCE,
            "matched_on": sorted(needs),
            "skills": [],
        }
    return {
        "available": True,
        "source": _SOURCE,
        "matched_on": sorted(needs),
        "skills": skills,
    }


def attach_skills(contract: dict[str, Any], plan: Any = None) -> dict[str, Any]:
    """Attach the skills block to ``epic_context`` in place (#687).

    Mirrors :func:`plan.emit.constitution.attach_constitution`: additive,
    returns the contract for composability, and **never raises** — a skill
    lookup must not be able to break an emit.
    """
    try:
        epic_context = contract.get("epic_context")
        if not isinstance(epic_context, dict):
            epic_context = {}
            contract["epic_context"] = epic_context
        epic_context["skills"] = build_skills_block(contract, plan)
        return contract
    except Exception:  # noqa: BLE001 — best-effort, like every other attach helper
        return contract
