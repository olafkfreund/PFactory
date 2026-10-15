"""ui declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "ui",
    reason=(
        "Terminal output helpers (colours, icons, boxes, spinners, menus) for the command line. "
        "They detect terminal capabilities per process and print, and nothing they do is observable"
        " in the web service."
    ),
)
