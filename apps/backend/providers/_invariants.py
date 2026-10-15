"""providers: every alias and registry entry resolves to a real agentic provider.

``_PROVIDER_ALIASES`` and ``_AGENTIC_REGISTRY`` are hand-maintained side by
side; a stale target or module path fails only when a caller uses that name.
"""

# ruff: noqa: PLC0415

from __future__ import annotations

import importlib.util
from collections.abc import Iterator

from factory_invariants import registry


def _factory_consistent() -> Iterator[str]:
    from providers import factory

    for alias, target in factory._PROVIDER_ALIASES.items():
        if target not in factory._AGENTIC_REGISTRY:
            yield f"alias {alias!r} points at {target!r}, which is not in _AGENTIC_REGISTRY"
    for name, (module, _cls) in factory._AGENTIC_REGISTRY.items():
        if importlib.util.find_spec(module) is None:
            yield f"provider {name!r} names module {module!r}, which cannot be found"


registry.register("providers.factory", _factory_consistent)
