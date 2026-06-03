"""Declarative registry of pluggable units (issue #25).

PFactory is extended by *data, not forks*: MCP servers, skills, agents, templates,
infra adapters, and knowledge connectors are declared as :class:`RegistryEntry`
records (in YAML catalogue files and/or ``.pfactory.yml``). The pipeline asks the
:class:`Registry` what's enabled and what capabilities each unit offers.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EntryKind = Literal[
    "mcp",
    "skill",
    "agent",
    "template",
    "infra-adapter",
    "knowledge-connector",
    "provider",
]


class RegistryEntry(BaseModel):
    """One pluggable unit in the registry."""

    id: str
    kind: EntryKind
    title: str = ""
    version: str = "0.1.0"
    capabilities: list[str] = Field(default_factory=list)
    enabled: bool = True
    config: dict = Field(default_factory=dict)


class Registry(BaseModel):
    """A catalogue of registry entries with simple query helpers."""

    entries: list[RegistryEntry] = Field(default_factory=list)

    def get(self, entry_id: str) -> RegistryEntry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    def by_kind(self, kind: EntryKind) -> list[RegistryEntry]:
        return [e for e in self.entries if e.kind == kind]

    def enabled(self, kind: EntryKind | None = None) -> list[RegistryEntry]:
        items = self.entries if kind is None else self.by_kind(kind)
        return [e for e in items if e.enabled]

    def is_enabled(self, entry_id: str) -> bool:
        e = self.get(entry_id)
        return bool(e and e.enabled)

    def with_capability(self, capability: str) -> list[RegistryEntry]:
        return [e for e in self.entries if e.enabled and capability in e.capabilities]

    def upsert(self, entry: RegistryEntry) -> Registry:
        """Add ``entry`` or replace an existing one with the same id."""
        self.entries = [e for e in self.entries if e.id != entry.id] + [entry]
        return self
