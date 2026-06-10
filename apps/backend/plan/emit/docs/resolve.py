"""Cross-factory lookup: resolve a plan by ``correlation_key`` (the memory read).

Other factories (AIFactory/TFactory/CFactory) ask "what's the plan + dependencies
behind epic #N?" against the registry this emit writes. The registry source is
pluggable — a local dir (the repo target / PVC) or a fetched ``registry.json``
blob (GitHub raw / Backstage) — so the resolver is decoupled from where docs land.

This is the durable/browsable complement to the live MCP planning-context tools
(``pfactory_get_*``): same key, two surfaces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .targets import registry as reg


class PlanDocsResolver:
    """Resolve plans from a registry blob (inject the text for any source)."""

    def __init__(self, registry_text: str | None) -> None:
        self._plans = reg.parse_registry(registry_text)

    @classmethod
    def from_dir(cls, root: str | Path) -> PlanDocsResolver:
        """Build from a local ``<root>/registry.json`` (repo target / PVC)."""
        path = Path(root) / reg.REGISTRY_FILE
        return cls(path.read_text() if path.exists() else None)

    def resolve(self, correlation_key: str) -> dict[str, Any] | None:
        """Return the registry entry for a key (doc_file, dependencies, epic…)."""
        return self._plans.get(correlation_key)

    def dependencies(self, correlation_key: str) -> list[str]:
        entry = self.resolve(correlation_key)
        return list(entry.get("dependencies", [])) if entry else []

    def all(self) -> dict[str, dict]:
        return dict(self._plans)
