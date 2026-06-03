"""Review lenses package (issue #15).

Re-exports the lens protocol + registry. Importing this package does *not*
eagerly import the individual lens modules; :func:`default_lenses` does that
lazily so registration happens on first use.
"""

from __future__ import annotations

from plan.review.lenses.base import (
    Lens,
    default_lenses,
    get_lens,
    register_lens,
)

__all__ = [
    "Lens",
    "default_lenses",
    "get_lens",
    "register_lens",
]
