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
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

KnowledgeKind = Literal["policy", "doc", "adr", "template", "catalog", "best-practice"]


class _HttpResponse(Protocol):
    """Minimal response shape the HTTP-backed connectors rely on.

    Shared by the notion/gitbook/backstage/confluence connectors (#257) — each
    connector's own ``_HttpClient`` Protocol (which differs by verb/params)
    returns this common response shape.
    """

    status_code: int

    def json(self) -> Any: ...

    def raise_for_status(self) -> None: ...


class KnowledgeRef(BaseModel):
    """A reference to one relevant piece of org knowledge."""

    connector: str
    kind: KnowledgeKind = "doc"
    title: str
    uri: str = ""
    snippet: str = ""
    score: float = 0.0  # relevance 0–1 (connector-defined)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeConnector(abc.ABC):
    """Base class for a read-only knowledge connector."""

    name: str = "base"

    # HTTP-backed connectors set these in their __init__; declared here so the
    # shared ``_headers`` / ``_lazy_http_client`` helpers (#257) can reference
    # them on the base type. ``_http`` is a duck-typed client (each connector's
    # ``_HttpClient`` Protocol differs by verb/params) held as ``object``.
    token: str | None = None
    _http: object | None = None

    def __init__(self, **options: object) -> None:
        self.options = options

    def _headers(self) -> dict[str, str]:
        """Auth/accept headers for HTTP requests (#257).

        The common form shared by the gitbook/backstage/confluence connectors:
        ``Accept: application/json`` plus a bearer ``Authorization`` when a token
        is configured. Connectors needing extra headers (e.g. Notion's
        ``Notion-Version``) override this.
        """
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _lazy_http_client(self) -> object:
        """Return the injected HTTP client, or lazily build a real one (#257).

        The identical ``requests`` → ``httpx`` lazy builder shared by the
        HTTP-backed connectors. Each connector's ``_client`` wraps this and casts
        the result to its own ``_HttpClient`` Protocol (which differs by
        verb/params), so the ~10-line body lives here once instead of x4.
        """
        if self._http is not None:
            return self._http
        try:
            import requests  # noqa: PLC0415 - lazy import keeps deps optional
        except ImportError:  # pragma: no cover - fall back to httpx
            import httpx  # noqa: PLC0415

            self._http = httpx.Client()
        else:
            self._http = requests.Session()
        return self._http

    @abc.abstractmethod
    def available(self) -> bool:
        """True if the source is configured (path/URL/token present)."""

    @abc.abstractmethod
    def search(self, query: str, *, limit: int = 10) -> list[KnowledgeRef]:
        """Return knowledge refs relevant to ``query`` (read-only)."""

    def fetch(self, uri: str) -> str:  # pragma: no cover - optional override
        """Optionally fetch the full text of a ref. Default: not supported."""
        raise NotImplementedError(f"{self.name} does not support fetch()")

    def to_enrichment(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
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
