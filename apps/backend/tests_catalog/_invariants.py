"""tests_catalog declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "tests_catalog",
    reason=(
        "Loads and atomically saves the per-repository .pfactory/tests-catalog.json, validating its"
        " schema on load. The catalog lives in the target repo, not in the service, so the service "
        "holds no copy to check."
    ),
)
