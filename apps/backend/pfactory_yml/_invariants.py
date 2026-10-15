"""pfactory_yml declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "pfactory_yml",
    reason=(
        "Pydantic schema and parser for a target repository's .pfactory.yml. Validation when a file"
        " is loaded is already the check, and the service keeps no parsed config between calls."
    ),
)
