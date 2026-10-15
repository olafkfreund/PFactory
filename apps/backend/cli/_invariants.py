"""cli declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "cli",
    reason=(
        "Argument parsing and command routing for the operator command line (run.py and python -m "
        "cli). It runs only in a terminal process, never inside the web service, so it has no "
        "runtime relation there."
    ),
)
