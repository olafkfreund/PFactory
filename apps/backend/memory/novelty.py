"""Exact-repeat filtering for memory writers (RFC-0021 Phase 1)."""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def repeat_key(item: Any) -> str | None:
    """Comparison key: stripped text, or canonical JSON for a dict entry."""
    if isinstance(item, str):
        return item.strip() or None
    if isinstance(item, dict):
        # Round trip first: stringifies keys as storage does, so mixed-type keys sort.
        try:
            return json.dumps(json.loads(json.dumps(item, default=str)), sort_keys=True)
        except (TypeError, ValueError):
            return None
    return None


def drop_repeats(candidates: Any, seen: set[str], label: str) -> Any:
    """Return a new list without repeats of seen or earlier items; add kept keys.

    A value that is not a list is returned unchanged, as it was stored before.
    """
    if not isinstance(candidates, list):
        return candidates
    kept: list[Any] = []
    # shortcut: exact-only; use embedder similarity when the pod has one (RFC-0021).
    for item in candidates:
        key = repeat_key(item)
        if key is not None:
            if key in seen:
                logger.info("memory novelty gate: rejected %r in %s", item, label)
                continue
            seen.add(key)
        kept.append(item)
    return kept
