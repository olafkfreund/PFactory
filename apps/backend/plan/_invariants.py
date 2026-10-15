"""plan: every built-in infra adapter and knowledge connector stays discoverable.

``discover_builtins`` imports each adapter module inside a bare ``except`` and
skips it on failure, so a broken adapter silently disappears from the catalogue.
"""

# ruff: noqa: PLC0415

from __future__ import annotations

from collections.abc import Iterator

from factory_invariants import registry

_EXPECTED = (
    "infra:aws",
    "infra:azure",
    "infra:gcp",
    "infra:kubernetes",
    "infra:openshift",
    "knowledge:backstage",
    "knowledge:confluence",
    "knowledge:git-markdown",
    "knowledge:gitbook",
    "knowledge:notion",
)


def _builtins_discoverable() -> Iterator[str]:
    from plan.registry.loader import discover_builtins

    found = {e.id for e in discover_builtins()}
    for missing in _EXPECTED:
        if missing not in found:
            yield (
                f"{missing} is not discovered: discover_builtins swallows import errors, "
                "so a broken adapter just disappears from the catalogue"
            )


registry.register("plan.registry", _builtins_discoverable)
