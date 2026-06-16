"""RFC-0007 (#84): attach the discovered ``access`` block to the task contract.

Bridges the pure classifier (:mod:`plan.access_discovery`) to the emitted
contract. Config-duck-typed (like ``auth_tagging``): any object exposing a
``targets`` list of ``.pfactory.yml`` target specs works, so this is testable
without constructing a full ``PFactoryConfig``. No-op when there is no config or
no targets — the contract then omits the block entirely (meaning: the task needs
no external/authenticated resource).
"""

from __future__ import annotations

from typing import Any

from plan.access_discovery import discover_access


def _targets_as_dicts(config: Any) -> list[dict]:
    """Normalize a config's targets to plain dicts for the pure classifier.

    Accepts a ``PFactoryConfig``-like object (``.targets``) or a plain dict (the
    snapshotted ``context/pfactory_yml.json``, ``{"targets": [...]}``).
    """
    if isinstance(config, dict):
        targets = config.get("targets") or []
    else:
        targets = getattr(config, "targets", None) or []
    out: list[dict] = []
    for t in targets:
        if hasattr(t, "model_dump"):
            out.append(t.model_dump(exclude_none=True))
        elif isinstance(t, dict):
            out.append(t)
    return out


def attach_access(contract: dict, config: Any | None, spec_text: str = "") -> dict:
    """Set ``contract['access']`` from ``.pfactory.yml`` targets (RFC-0007).

    No-op (block omitted) when ``config`` is None or has no targets. Mutates and
    returns ``contract``.
    """
    if config is None:
        return contract
    block = discover_access(_targets_as_dicts(config), spec_text or "")
    if block is not None:
        contract["access"] = block
    return contract
