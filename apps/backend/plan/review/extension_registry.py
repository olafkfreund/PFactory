"""Read the declarative Factory extension registry (RFC-0015 §4 D3).

The hub ships ``apis/extension-registry.json`` — a manifest describing every
Factory stage, gate, connector, and runtime with ``category`` + ``effect`` tags
and an operator-gating ``enabled`` flag. RFC-0015 §4 D1 lists the adversarial
``red-team-review`` lens there, **gated off** until the PFactory lens ships; this
module is how PFactory asks the manifest "is this extension turned on?".

Resolution is best-effort and never raises. An extension is **enabled** when:

1. the env override ``PFACTORY_<NAME>`` is truthy (operator opt-in, the
   RFC-0014 gated-runtime pattern), OR
2. the registry's entry for it has ``enabled: true``.

The registry is located from (first found wins): ``PFACTORY_EXTENSION_REGISTRY``,
a vendored copy under this package, or a sibling ``Factory/apis/`` hub checkout.
A missing/unreadable registry degrades to "disabled" — so a gated extension stays
off unless explicitly opted in, which is the safe default.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_TRUTHY = {"1", "true", "yes", "on"}

# Where the manifest may live, in priority order. The vendored copy keeps
# PFactory self-contained; the sibling hub path supports a monorepo checkout.
_VENDORED = Path(__file__).resolve().parents[1] / "emit" / "contracts" / "extension-registry.json"
_SIBLING_HUB = Path(__file__).resolve().parents[5] / "Factory" / "apis" / "extension-registry.json"


def _env_flag(name: str) -> str:
    """The env-override variable name for an extension, e.g. red-team-review →
    ``PFACTORY_RED_TEAM_REVIEW``."""
    slug = name.strip().upper().replace("-", "_").replace(" ", "_")
    return f"PFACTORY_{slug}"


def _registry_path() -> Path | None:
    override = os.environ.get("PFACTORY_EXTENSION_REGISTRY", "").strip()
    if override and Path(override).is_file():
        return Path(override)
    if _VENDORED.is_file():
        return _VENDORED
    if _SIBLING_HUB.is_file():
        return _SIBLING_HUB
    return None


@lru_cache(maxsize=1)
def _load() -> list[dict[str, Any]]:
    """Load the registry's ``extensions`` list. Best-effort; ``[]`` on any miss."""
    path = _registry_path()
    if path is None:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    exts = data.get("extensions") if isinstance(data, dict) else data
    if not isinstance(exts, list):
        return []
    return [e for e in exts if isinstance(e, dict)]


def get_extension(name: str) -> dict[str, Any] | None:
    """Return the registry entry for ``name``, or ``None`` when absent."""
    for e in _load():
        if e.get("name") == name:
            return e
    return None


def is_enabled(name: str) -> bool:
    """True when extension ``name`` is enabled by env override or the registry.

    The env override always wins (operator opt-in for a registry-gated
    extension); otherwise the registry's ``enabled`` flag decides. A
    missing/unreadable registry with no override ⇒ disabled (safe default).
    """
    if os.environ.get(_env_flag(name), "").strip().lower() in _TRUTHY:
        return True
    entry = get_extension(name)
    return bool(entry and entry.get("enabled") is True)


def reset_cache() -> None:
    """Drop the cached registry (tests that swap PFACTORY_EXTENSION_REGISTRY)."""
    _load.cache_clear()
