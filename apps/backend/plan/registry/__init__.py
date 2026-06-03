"""Declarative registry of pluggable units (MCP/skills/agents/templates)."""

from plan.registry.loader import discover_builtins, load_registry
from plan.registry.models import Registry, RegistryEntry

__all__ = ["Registry", "RegistryEntry", "discover_builtins", "load_registry"]
