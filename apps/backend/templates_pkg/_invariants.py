"""templates_pkg declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "templates_pkg",
    reason=(
        "A string.Template engine that loads test-file templates from disk and parses their "
        "metadata. Each template's variables are checked against that metadata when it is "
        "instantiated, on every call, and the package keeps no state between calls."
    ),
)
