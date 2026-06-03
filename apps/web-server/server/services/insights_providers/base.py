"""
Base provider strategy and shared types for multi-provider insights chat.
"""

import abc
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProviderModel:
    """A model available from a provider."""
    id: str
    label: str


@dataclass
class ProviderInfo:
    """Detection result for a single provider."""
    provider: str
    available: bool
    display_name: str
    icon: str
    auth_method: str | None = None
    models: list[ProviderModel] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "available": self.available,
            "displayName": self.display_name,
            "icon": self.icon,
            "authMethod": self.auth_method,
            "models": [{"id": m.id, "label": m.label} for m in self.models],
        }


class ProviderStrategy(abc.ABC):
    """Abstract base class for insights chat providers."""

    @abc.abstractmethod
    async def detect(self) -> ProviderInfo:
        """Detect whether this provider is available and return its info."""
        ...

    @abc.abstractmethod
    async def send_message(
        self,
        project_path: Path,
        project_id: str,
        message: str,
        model: str | None,
        model_config: dict | None,
        conversation_history: list[dict] | None,
    ) -> str:
        """Send a message and stream the response via WebSocket events.

        Must broadcast insights:chunk events with types:
        text, tool_start, tool_end, done, error.

        Returns the full accumulated response text for persistence.
        """
        ...
