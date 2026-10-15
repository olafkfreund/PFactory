"""security: every command that needs validation has a validator.

``project.VALIDATED_COMMANDS`` is maintained apart from ``security.hooks``'s
``VALIDATORS``; a command in the first but not the second is allowed through.
"""

# ruff: noqa: PLC0415

from __future__ import annotations

from collections.abc import Iterator

from factory_invariants import registry


def _validators_cover_commands() -> Iterator[str]:
    from project import VALIDATED_COMMANDS
    from security import hooks

    for cmd in sorted(VALIDATED_COMMANDS):
        if cmd not in hooks.VALIDATORS:
            yield f"{cmd!r} has no validator: the hook would allow it unchecked"


registry.register("security.hooks", _validators_cover_commands)
