"""analysis declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "analysis",
    reason=(
        "Scans a target project on request (framework detection, risk classification, test "
        "discovery) and returns the findings to its caller. It keeps no state in the service, so "
        "nothing holds between calls for a check to read."
    ),
)
