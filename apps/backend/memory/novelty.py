"""Exact-repeat filtering for memory writers."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def drop_repeats(candidates: list[Any], seen: set[str], label: str) -> list[Any]:
    """Return a new list without repeated strings; add kept stripped strings to seen."""
    kept: list[Any] = []
    # shortcut: exact-only; use embedder similarity when the pod has one (RFC-0021).
    for item in candidates:
        if isinstance(item, str) and item.strip():
            stripped = item.strip()
            if stripped in seen:
                logger.info("memory novelty gate: rejected %r in %s", item, label)
                continue
            seen.add(stripped)
        kept.append(item)
    return kept
