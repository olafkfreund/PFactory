"""analysis declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "analysis",
    reason=(
        "Scans a target project on request (framework detection, risk classification, test "
        "discovery) and returns the findings. Its only caches are the _cache dicts on each "
        "CIDiscovery, RiskClassifier and TestDiscovery instance, which live and die with the "
        "caller's object; nothing is module-level."
    ),
)
