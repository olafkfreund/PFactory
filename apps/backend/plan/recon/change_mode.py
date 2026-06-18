"""Classify a plan as greenfield / modify / migration (RFC-0010, Phase 3).

The verdict is grounded in the :class:`~plan.recon.models.RepoMap` (what
reconnaissance actually found), not in spec-text keywords. It is distinct from
the contract's ``workflow_type`` (the work-shape): ``change_mode`` is the recon
verdict that tells AIFactory whether to scaffold fresh, edit existing code, or
rewrite from one language to another.

* ``greenfield`` — no readable repo (recon unavailable) or an effectively empty
  repo: new code from scratch.
* ``modify``     — existing code/IaC is present and the spec reconciles with it
  (Scenario A). The repo's language wins.
* ``migration``  — a directional rewrite was requested (set by the Phase 5
  migration classifier); the only case a language change is legitimate.
"""

from __future__ import annotations

from typing import Literal

from plan.recon.models import RepoMap

ChangeMode = Literal["greenfield", "modify", "migration"]


def classify_change_mode(
    repo_map: RepoMap | None, *, is_migration: bool = False
) -> ChangeMode:
    """Return the change mode for a plan grounded in ``repo_map``.

    ``is_migration`` is the signal from the directional migration classifier
    (Phase 5); when true it wins, because a rewrite is the one case where the
    spec's target language legitimately differs from the repo's.
    """
    if is_migration:
        return "migration"
    if repo_map and repo_map.available and (repo_map.languages or repo_map.iac):
        return "modify"
    return "greenfield"
