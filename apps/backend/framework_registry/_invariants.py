"""framework_registry: the shipped frameworks directory loads and is not empty.

``load_registry`` raises on a bad descriptor, but an empty or relocated
``frameworks/`` directory loads "successfully" with nothing in it.
"""

# ruff: noqa: PLC0415

from __future__ import annotations

from collections.abc import Iterator

from factory_invariants import registry


def _frameworks_load() -> Iterator[str]:
    from framework_registry import loader

    # A raised error propagates: check_all() reports a raising check as a violation.
    if not loader.load_registry():
        yield f"no framework descriptors found under {loader._DEFAULT_FRAMEWORKS_DIR}"


registry.register("framework_registry.loader", _frameworks_load)
