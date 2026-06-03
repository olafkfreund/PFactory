"""Knowledge connector framework — read-only access to org knowledge sources.

Shared contract for the Git-markdown and Backstage connectors (#11) and future
Confluence / GitBook / Notion adapters. A connector answers: *is this source
configured?* (:meth:`available`) and *what's relevant to this plan?*
(:meth:`search`). Results are :class:`KnowledgeRef` objects appended to
``NormalizedPlan.enrichment.knowledge`` so the planner and review gates can cite
the org's policies, best practices, and golden paths.

Read-only: connectors only read/search; they never write back to the source.
"""

from __future__ import annotations

import abc
from typing import Literal

from pydantic import BaseModel, Field

KnowledgeKind = Literal["policy", "doc", "adr", "template", "catalog", "best-practice"]


class KnowledgeRef(BaseModel):
    """A reference to one relevant piece of org knowledge."""

    connector: str
    kind: KnowledgeKind = "doc"
    title: str
    uri: str = ""
    snippet: str = ""
    score: float = 0.0  # relevance 0–1 (connector-defined)
    metadata: dict = Field(default_factory=dict)


class KnowledgeConnector(abc.ABC):
    """Base class for a read-only knowledge connector."""

    name: str = "base"

    def __init__(self, **options: object) -> None:
        self.options = options

    @abc.abstractmethod
    def available(self) -> bool:
        """True if the source is configured (path/URL/token present)."""

    @abc.abstractmethod
    def search(self, query: str, *, limit: int = 10) -> list[KnowledgeRef]:
        """Return knowledge refs relevant to ``query`` (read-only)."""

    def fetch(self, uri: str) -> str:  # pragma: no cover - optional override
        """Optionally fetch the full text of a ref. Default: not supported."""
        raise NotImplementedError(f"{self.name} does not support fetch()")

    def to_enrichment(self, query: str, *, limit: int = 10) -> list[dict]:
        """Search and return JSON-able dicts for ``enrichment.knowledge``.

        Never raises: failures degrade to an empty list.
        """
        try:
            if not self.available():
                return []
            return [ref.model_dump() for ref in self.search(query, limit=limit)]
        except Exception:  # enrichment must never break the pipeline
            return []


# ── registry ───────────────────────────────────────────────────────────

_REGISTRY: dict[str, type[KnowledgeConnector]] = {}


def register_connector(cls: type[KnowledgeConnector]) -> type[KnowledgeConnector]:
    _REGISTRY[cls.name] = cls
    return cls


def available_connectors() -> list[str]:
    return sorted(_REGISTRY)


def get_connector(name: str, **kwargs: object) -> KnowledgeConnector:
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown knowledge connector '{name}'; registered: {available_connectors()}"
        ) from None
    return cls(**kwargs)
