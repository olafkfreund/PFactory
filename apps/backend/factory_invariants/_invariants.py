"""The registry declares its own invariant (Factory#818).

The registry was the one package the ownership checker did not look at, which is
the same gap in miniature: the thing that proves everything else declares had
declared nothing itself.

What it owns that is worth watching is not state -- it is a GUARD. The
declared-empty form only stays an architectural conclusion because ``register()``
refuses placeholder reasons and reasons too short to say anything. Loosen
``_PLACEHOLDER`` or drop ``_EMPTY_REASON_MIN_WORDS`` to zero and every existing
declaration keeps passing, the checker keeps reporting full coverage, and the
next declared-empty entry can be the word "TODO". Nothing else in the toolchain
watches a regex for having stopped matching.
"""

from __future__ import annotations

from collections.abc import Iterator

from factory_invariants import InvariantRegistry, registry

# The words that mean "nobody has thought about this module yet". Fixed here
# rather than read from ``_PLACEHOLDER``: a probe derived from the pattern
# under test adapts to a mutation of it and can no longer see it.
_PLACEHOLDER_WORDS = ("TODO", "tbd", "N/A", "n/a", "none", "placeholder", "grandfathered")

# Long enough to say what a module owns and why nothing is observable. A reason
# this short is a shrug, not a conclusion.
_TOO_SHORT = "no state here"

# A reason of the shape the pattern is FOR. If this stops being accepted the
# guard has become unusable and authors will route around it.
_A_REAL_REASON = (
    "Reads operator configuration into a value object per call and keeps nothing between calls"
)


def _the_declared_empty_guard_still_bites() -> Iterator[str]:
    """A placeholder reason must be refused and a real one accepted.

    Both directions matter. A guard that has stopped rejecting silently turns
    the declared-empty form back into a generated placeholder; a guard that
    rejects everything gets worked around within a week.
    """
    for word in _PLACEHOLDER_WORDS:
        probe = InvariantRegistry()
        try:
            probe.register("m", reason=f"{word} — nobody has looked at what this module owns yet")
        except ValueError:
            continue
        yield (
            f"a declared-empty reason beginning {word!r} was ACCEPTED: the empty form is a "
            "generated placeholder again, and coverage now counts declarations that say nothing"
        )

    probe = InvariantRegistry()
    try:
        probe.register("m", reason=_TOO_SHORT)
    except ValueError:
        pass
    else:
        yield f"the reason {_TOO_SHORT!r} was accepted; the minimum-length floor no longer applies"

    probe = InvariantRegistry()
    try:
        probe.register("m", reason=_A_REAL_REASON)
    except ValueError as exc:
        yield (
            f"a genuine declared-empty reason was REFUSED ({exc}); authors will stop writing them "
            "rather than fight the guard"
        )

    probe = InvariantRegistry()
    probe.register("m", reason=_A_REAL_REASON)
    try:
        probe.register("m", reason=_A_REAL_REASON)
    except ValueError:
        pass
    else:
        yield "a module registered twice was accepted, so one of the two declarations never runs"

    if InvariantRegistry().enabled:
        yield (
            "a freshly constructed registry runs checks by default; an assertion that throws in a "
            "running pod must be opted into, not inherited"
        )


registry.register("factory_invariants.__init__", _the_declared_empty_guard_still_bites)
