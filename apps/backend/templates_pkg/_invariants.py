"""templates_pkg declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "templates_pkg",
    reason=(
        "A string.Template engine that loads test-file templates from disk and validates each "
        "template's variable set against its metadata on load. That validation is the check, and it"
        " runs on every load."
    ),
)
