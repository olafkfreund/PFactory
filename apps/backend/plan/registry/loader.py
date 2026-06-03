"""Registry loader (issue #25).

Builds a :class:`Registry` from three sources, later ones overriding earlier:
  1. **Built-ins** — reflected from the live infra-adapter and knowledge-connector
     registries, so the catalogue always mirrors what's actually importable.
  2. **Catalogue YAML** — ``*.yaml`` entry files under ``plan/registry/catalogue/``
     (and any extra dirs passed in) declaring MCP servers, templates, etc.
  3. **Overrides** — an optional ``registry:`` mapping (e.g. from ``.pfactory.yml``)
     toggling ``enabled`` or patching config per entry id.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from plan.registry.models import Registry, RegistryEntry

_CATALOGUE_DIR = Path(__file__).parent / "catalogue"


def discover_builtins() -> list[RegistryEntry]:
    """Reflect the importable infra adapters + knowledge connectors as entries."""
    entries: list[RegistryEntry] = []
    try:
        from plan.enrich import adapters as _adapters  # noqa: F401  (ensure import)
        from plan.enrich.base import available_adapters

        # Import known adapter modules so they self-register.
        for mod in ("kubernetes", "openshift", "azure", "aws", "gcp"):
            try:
                __import__(f"plan.enrich.adapters.{mod}")
            except Exception:
                pass
        for name in available_adapters():
            entries.append(
                RegistryEntry(
                    id=f"infra:{name}", kind="infra-adapter", title=name,
                    capabilities=["enrich", "read-only"],
                )
            )
    except Exception:
        pass
    try:
        from plan.enrich.knowledge.base import available_connectors

        for mod in ("git_markdown", "backstage", "confluence", "gitbook", "notion"):
            try:
                __import__(f"plan.enrich.knowledge.{mod}")
            except Exception:
                pass
        for name in available_connectors():
            entries.append(
                RegistryEntry(
                    id=f"knowledge:{name}", kind="knowledge-connector", title=name,
                    capabilities=["enrich", "read-only"],
                )
            )
    except Exception:
        pass
    return entries


def _load_catalogue(catalogue_dir: Path) -> list[RegistryEntry]:
    entries: list[RegistryEntry] = []
    if not catalogue_dir.is_dir():
        return entries
    for f in sorted(catalogue_dir.glob("*.yaml")):
        data = yaml.safe_load(f.read_text()) or {}
        items = data.get("entries", data if isinstance(data, list) else [data])
        for item in items:
            if item:
                entries.append(RegistryEntry(**item))
    return entries


def load_registry(
    *,
    catalogue_dir: Path | str | None = None,
    extra_dirs: list[Path | str] | None = None,
    overrides: dict | None = None,
    include_builtins: bool = True,
) -> Registry:
    """Assemble the registry from built-ins + catalogue YAML + overrides."""
    registry = Registry()

    if include_builtins:
        for e in discover_builtins():
            registry.upsert(e)

    dirs = [Path(catalogue_dir) if catalogue_dir else _CATALOGUE_DIR]
    dirs += [Path(d) for d in (extra_dirs or [])]
    for d in dirs:
        for e in _load_catalogue(d):
            registry.upsert(e)

    # Apply per-id overrides: {entry_id: {enabled: bool, config: {...}, ...}}
    for entry_id, patch in (overrides or {}).items():
        existing = registry.get(entry_id)
        if existing and isinstance(patch, dict):
            registry.upsert(existing.model_copy(update=patch))

    return registry
