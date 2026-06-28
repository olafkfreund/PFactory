"""Small shared helpers for the cloud infra adapters.

Kept deliberately dependency-free so every adapter module can import it without
risking an import cycle.
"""

from __future__ import annotations


def parse_k8s_version(version: str | None) -> tuple[int, int] | None:
    """Parse a Kubernetes version string to ``(major, minor)``.

    Handles the common shapes — ``"1.27"``, ``"1.27.3"``, ``"v1.27"``,
    ``"1.27.3-gke.100"`` — and returns ``None`` when the value is missing or
    unparsable. ``str(version)`` keeps it robust to non-string inputs.
    """
    if not version:
        return None
    parts = str(version).lstrip("v").split(".")
    try:
        return (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        return None
