"""Registry for module-owned runtime invariants (Factory#818 pilot).

WHY RUNTIME AND NOT CI. A scheduled gate can stop firing and nobody learns:
Factory#693 records two that failed 21 consecutive times unnoticed, and
Factory#816 found four more that had never once succeeded. An assertion that
throws inside a running process cannot be silently dark in the same way -- it
either fires or the relation it watches still holds.

WHAT THIS MODULE OWNS, AND NOTHING ELSE: configuration, registration
uniqueness, attribution of a failure to its owning module, and the error type.
It imports no product module and contains none of their checks. That is
deliberate -- a central diagnostics module that imports product vocabularies
becomes the file every package has to edit, which is how the last god-file
started.

WHAT A CALLER GETS. ``check_all()`` returns the list of violations rather than
raising, so a caller decides whether a violation is fatal. ``verify_all()``
raises on the first one. Neither runs unless something calls it: importing a
product module must not change runtime behaviour, so registration is inert
until verification is asked for.

DEFAULT OFF IN PRODUCTION. ``enabled`` defaults to False. An invariant that
throws in a running pod is a new failure mode, and this is a pilot -- it earns
production by being observed to catch something first, not by being switched on
because it exists.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

__all__ = [
    "NO_RUNTIME_INVARIANT",
    "InvariantError",
    "InvariantRegistry",
    "Violation",
    "registry",
]

# Sentinel a module returns to declare, in writing, that it owns no runtime
# relation worth watching. It is not the same as registering nothing: a module
# that registers nothing is a GAP, and the ownership check fails on it.
NO_RUNTIME_INVARIANT = object()

# A declared-empty reason must say something specific about the module. These
# are the words that mean "nobody thought about it yet", and they are rejected
# so the empty form stays an architectural conclusion rather than a placeholder.
_EMPTY_REASON_MIN_WORDS = 6
_PLACEHOLDER = re.compile(r"^\s*(todo|tbd|n/?a|none|placeholder|grandfathered)\b", re.I)


class InvariantError(AssertionError):
    """A module-owned runtime invariant was violated.

    Subclasses AssertionError so an existing `except AssertionError` in a test
    harness still catches it, while `except InvariantError` can single it out.
    """

    def __init__(self, module: str, message: str) -> None:
        super().__init__(f"invariant violated by {module!r}: {message}")
        self.module = module
        self.detail = message


@dataclass(frozen=True)
class Violation:
    """One failed check, attributed to the module that registered it."""

    module: str
    detail: str

    def __str__(self) -> str:
        return f"{self.module}: {self.detail}"


@dataclass
class _Registration:
    module: str
    check: Callable[[], Iterator[str]] | None
    reason: str | None


@dataclass
class InvariantRegistry:
    """Holds one registration per module and runs them on request.

    Selection mirrors the shape DeepSeek Harness uses (allowlist admits, then
    blocklist excludes), so a deployment can enable one module's checks without
    enabling all of them.
    """

    enabled: bool = False
    allowlist: list[str] = field(default_factory=list)
    blocklist: list[str] = field(default_factory=list)
    _registrations: dict[str, _Registration] = field(default_factory=dict)

    def register(
        self,
        module: str,
        check: Callable[[], Iterator[str]] | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        """Register one module's contribution. Registering twice is an error.

        Pass ``check`` for a real invariant, or ``reason`` for the declared-empty
        form. Passing both, or neither, is a programming error rather than a
        runtime finding -- it means the declaration itself is malformed.
        """
        if (check is None) == (reason is None):
            raise ValueError(
                f"{module}: pass exactly one of check= (a real invariant) or "
                "reason= (why this module owns no runtime relation)"
            )
        if module in self._registrations:
            raise ValueError(
                f"{module}: already registered — two declarations claim the same "
                "module name, so one of them would silently never run"
            )
        if reason is not None and (
            _PLACEHOLDER.match(reason) or len(reason.split()) < _EMPTY_REASON_MIN_WORDS
        ):
            raise ValueError(
                f"{module}: the declared-empty reason must say what this module "
                f"owns and why no relation is observable; got {reason!r}"
            )
        self._registrations[module] = _Registration(module, check, reason)

    def registered(self) -> frozenset[str]:
        """Every module name that has declared, in either form."""
        return frozenset(self._registrations)

    def executable(self) -> frozenset[str]:
        """Modules that registered a real check, as opposed to a written reason."""
        return frozenset(m for m, r in self._registrations.items() if r.check is not None)

    def reason_for(self, module: str) -> str | None:
        """The declared-empty reason for a module, or None if it has a real check."""
        reg = self._registrations.get(module)
        return reg.reason if reg else None

    def _selected(self, module: str) -> bool:
        if not self.enabled:
            return False
        if self.allowlist and not any(re.search(p, module) for p in self.allowlist):
            return False
        return not any(re.search(p, module) for p in self.blocklist)

    def check_all(self) -> list[Violation]:
        """Run every selected check and collect violations. Never raises on a finding.

        A check that itself raises is reported as a violation rather than
        propagating: a broken check must not be indistinguishable from a clean
        run, and it must not take down the caller either.
        """
        out: list[Violation] = []
        for name, reg in sorted(self._registrations.items()):
            if reg.check is None or not self._selected(name):
                continue
            try:
                out.extend(Violation(name, d) for d in reg.check())
            except Exception as exc:  # noqa: BLE001 - a broken check is a finding
                out.append(Violation(name, f"the check itself raised {type(exc).__name__}: {exc}"))
        return out

    def verify_all(self) -> None:
        """Raise InvariantError on the first violation. For startup and tests."""
        for v in self.check_all():
            raise InvariantError(v.module, v.detail)


def _from_env() -> InvariantRegistry:
    """Build the process registry. Off unless FACTORY_INVARIANTS=1."""
    return InvariantRegistry(
        enabled=os.environ.get("FACTORY_INVARIANTS", "").strip() in {"1", "true", "yes"},
        allowlist=[p for p in os.environ.get("FACTORY_INVARIANTS_ONLY", "").split(",") if p],
        blocklist=[p for p in os.environ.get("FACTORY_INVARIANTS_SKIP", "").split(",") if p],
    )


registry = _from_env()
