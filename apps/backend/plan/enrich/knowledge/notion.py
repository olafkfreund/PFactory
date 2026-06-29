"""Notion knowledge connector — search a Notion workspace (#12).

Calls the Notion search API (``POST https://api.notion.com/v1/search``) and maps
returned pages and databases to :class:`KnowledgeRef` objects so the planner can
cite the org's docs and templates. Database results map to ``kind="template"``;
pages to ``kind="doc"``.

The HTTP library is lazy-imported inside methods, and an ``http`` client may be
injected so tests need no network and no ``requests``/``httpx`` installed.

Read-only: the connector only reads/searches content; it never writes back.
"""

from __future__ import annotations

from typing import Any, Protocol

from plan.enrich.knowledge.base import (
    KnowledgeConnector,
    KnowledgeKind,
    KnowledgeRef,
    _HttpResponse,
    register_connector,
)

_DEFAULT_LIMIT = 10
_SNIPPET_LEN = 200
_API_URL = "https://api.notion.com/v1/search"
_NOTION_VERSION = "2022-06-28"


class _HttpClient(Protocol):
    """Minimal HTTP client shape (e.g. ``requests`` or ``httpx``)."""

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        json: dict[str, Any] | None = ...,
    ) -> _HttpResponse: ...


@register_connector
class NotionConnector(KnowledgeConnector):
    """Map Notion search results to knowledge refs (read-only)."""

    name = "notion"

    def __init__(
        self,
        *,
        token: str | None = None,
        http: _HttpClient | None = None,
        **opts: object,
    ) -> None:
        """Create the connector.

        Args:
            token: Notion integration token (required — :meth:`available` is
                False without it).
            http: Optional injected HTTP client (with ``.post``) for tests.
            **opts: Forwarded to the base connector.
        """
        super().__init__(**opts)
        self.token = token
        self._http = http

    def available(self) -> bool:
        """True if an integration token is configured."""
        return bool(self.token)

    def _client(self) -> _HttpClient:
        """Return the injected client, or lazily build a real one."""
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

    def _headers(self) -> dict[str, str]:
        """Auth/version/accept headers for search requests."""
        headers = {
            "Accept": "application/json",
            "Notion-Version": _NOTION_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _post_json(self, url: str, payload: dict[str, Any]) -> Any:
        """POST ``payload`` to ``url`` and return parsed JSON."""
        resp = self._client().post(url, headers=self._headers(), json=payload)
        raise_for_status = getattr(resp, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        else:  # pragma: no cover - clients without raise_for_status
            status = getattr(resp, "status_code", 200)
            if status >= 400:
                raise RuntimeError(f"notion search returned HTTP {status}")
        return resp.json()

    @staticmethod
    def _kind(result: dict[str, Any]) -> KnowledgeKind:
        """Database objects map to templates; everything else to docs."""
        return "template" if str(result.get("object", "")).lower() == "database" else "doc"

    @staticmethod
    def _title(result: dict[str, Any]) -> str:
        """Extract a plain-text title from a Notion page or database object."""
        # Databases carry a top-level "title" rich-text array.
        title = NotionConnector._rich_text(result.get("title"))
        if title:
            return title
        # Pages carry the title under a "title"-typed property.
        props = result.get("properties", {}) or {}
        for prop in props.values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                text = NotionConnector._rich_text(prop.get("title"))
                if text:
                    return text
        return "untitled"

    @staticmethod
    def _rich_text(value: Any) -> str:
        """Flatten a Notion rich-text array to plain text."""
        if not isinstance(value, list):
            return ""
        parts: list[str] = []
        for span in value:
            if not isinstance(span, dict):
                continue
            plain = span.get("plain_text")
            if isinstance(plain, str):
                parts.append(plain)
                continue
            text = span.get("text", {}) or {}
            content = text.get("content")
            if isinstance(content, str):
                parts.append(content)
        return "".join(parts).strip()

    @staticmethod
    def _matches(title: str, terms: set[str]) -> int:
        """Count query-term hits in the result's title."""
        low = title.lower()
        return sum(1 for t in terms if t in low)

    def _to_ref(self, result: dict[str, Any], score: float) -> KnowledgeRef:
        """Build a KnowledgeRef from a Notion search result."""
        title = self._title(result)
        return KnowledgeRef(
            connector=self.name,
            kind=self._kind(result),
            title=title,
            uri=str(result.get("url", "") or ""),
            snippet=title[:_SNIPPET_LEN],
            score=round(score, 4),
            metadata={
                "id": result.get("id"),
                "object": result.get("object"),
            },
        )

    def search(self, query: str, *, limit: int = _DEFAULT_LIMIT) -> list[KnowledgeRef]:
        """Query the Notion search API and map results to knowledge refs."""
        if not self.token:
            return []
        terms = {w for w in query.lower().split() if w}
        payload = {"query": query, "page_size": limit}
        data = self._post_json(_API_URL, payload)

        # Notion returns {"results": [...]}; tolerate a bare list too.
        if isinstance(data, dict):
            results = data.get("results", [])
        else:
            results = data
        if not isinstance(results, list):
            return []

        scored: list[tuple[int, KnowledgeRef]] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            title = self._title(result)
            hits = self._matches(title, terms) if terms else 1
            score = min(1.0, hits / len(terms)) if terms else 0.0
            scored.append((hits, self._to_ref(result, score)))

        scored.sort(key=lambda pair: (-pair[0], pair[1].title))
        return [ref for _, ref in scored[:limit]]
