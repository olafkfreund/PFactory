"""Deterministic ``requires_auth`` tagging for the Planner (#107 task 6).

The Planner emits lane-tagged subtasks; a browser/api subtask that hits a target
whose ``.pfactory.yml`` ``auth`` is a ``ref`` (form login backed by a stored
credential) must carry ``requires_auth=True`` so the Executor injects the
credential and Gen-Functional uses the Playwright ``storageState`` login path
(see #107 task 5). Rather than trust the LLM to set the flag, we derive it
deterministically from the config after the plan is emitted.

Pure + config-duck-typed (a ``PFactoryConfig`` exposing ``lookup_target``), so
this needs no schema import and is trivially unit-testable.
"""

from __future__ import annotations

from pathlib import Path

from agents.agent_infra import iter_subtasks

__all__ = ["apply_requires_auth_from_config", "tag_requires_auth"]


def tag_requires_auth(subtasks, config) -> int:
    """Set ``requires_auth=True`` on subtasks whose target uses ``ref`` auth.

    Args:
        subtasks: Iterable of ``Subtask`` (anything with ``target_name`` +
            ``requires_auth`` attributes).
        config: A parsed ``.pfactory.yml`` (``PFactoryConfig``) with
            ``lookup_target(name)``, or ``None``.

    Returns:
        The number of subtasks newly tagged. Idempotent — already-tagged
        subtasks and those with no/unknown/non-ref target are left untouched.
    """
    if config is None:
        return 0
    lookup = getattr(config, "lookup_target", None)
    if not callable(lookup):
        return 0

    tagged = 0
    for st in subtasks:
        if getattr(st, "requires_auth", False):
            continue
        name = getattr(st, "target_name", None)
        if not name:
            continue
        target = lookup(name)
        auth = getattr(target, "auth", None) if target is not None else None
        if auth is not None and getattr(auth, "type", None) == "ref":
            st.requires_auth = True
            tagged += 1
    return tagged


def apply_requires_auth_from_config(plan, project_dir: Path | str | None) -> int:
    """Load ``<project_dir>/.pfactory.yml`` and tag the plan's subtasks.

    Best-effort: a missing or malformed config tags nothing (never breaks the
    planner). Flattens ``plan.phases[*].subtasks`` and delegates to
    :func:`tag_requires_auth`.

    Returns:
        The number of subtasks newly tagged.
    """
    if project_dir is None:
        return 0
    try:
        from pfactory_yml.parser import load_pfactory_yml

        config = load_pfactory_yml(Path(project_dir))
    except Exception:  # noqa: BLE001 - config errors must not break planning
        return 0
    if config is None:
        return 0
    subtasks = [st for _phase, st in iter_subtasks(plan)]
    return tag_requires_auth(subtasks, config)
