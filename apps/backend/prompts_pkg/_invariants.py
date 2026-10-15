"""prompts_pkg declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "prompts_pkg",
    reason=(
        "Renders prompt text from templates and project context on each call. A prompt is a string "
        "handed to the model, and nothing is kept between calls for a check to read."
    ),
)
