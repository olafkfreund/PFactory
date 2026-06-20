"""GitBook knowledge connector — search a GitBook space (#12).

Queries the GitBook API space search
(``GET {base_url}/v1/spaces/{space_id}/search?query=...``) and maps result pages
to :class:`KnowledgeRef` objects (``kind="doc"``) so the planner can cite the
org's published documentation.

The HTTP library is lazy-imported inside methods, and an ``http`` client may be
injected so tests need no network and no ``requests``/``httpx`` installed.

Read-only: the connector only reads/searches content; it never writes back.
"""

from __future__ import annotations

from typing import Any, Protocol

from plan.enrich.knowledge.base import (
    KnowledgeConnector,
    KnowledgeRef,
    register_connector,
)

_DEFAULT_LIMIT = 10
_SNIPPET_LEN = 200
_DEFAULT_BASE_URL = "https://api.gitbook.com"


class _HttpResponse(Protocol):
    """Minimal response shape the connector relies on."""

    status_code: int

    def json(self) -> Any: ...

    def raise_for_status(self) -> None: ...


class _HttpClient(Protocol):
    """Minimal HTTP client shape (e.g. ``requests`` or ``httpx``)."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        params: dict[str, Any] | None = ...,
    ) -> _HttpResponse: ...


@register_connector
class GitBookConnector(KnowledgeConnector):
    """Map GitBook space-search pages to knowledge refs (read-only)."""

    name = "gitbook"

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        token: str | None = None,
        space_id: str | None = None,
        http: _HttpClient | None = None,
        **opts: object,
    ) -> None:
        """Create the connector.

        Args:
            base_url: GitBook API base URL. Defaults to ``https://api.gitbook.com``.
            token: API token (required — :meth:`available` is False without it).
            space_id: Optional space to scope the search to.
            http: Optional injected HTTP client (with ``.get``) for tests.
            **opts: Forwarded to the base connector.
        """
        super().__init__(**opts)
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self.token = token
        self.space_id = space_id
        self._http = http

    def available(self) -> bool:
        """True if an API token is configured."""
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
        """Auth/accept headers for search requests."""
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        """GET ``url`` with ``params`` and return parsed JSON."""
        resp = self._client().get(url, headers=self._headers(), params=params)
        raise_for_status = getattr(resp, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        else:  # pragma: no cover - clients without raise_for_status
            status = getattr(resp, "status_code", 200)
            if status >= 400:
                raise RuntimeError(f"gitbook search returned HTTP {status}")
        return resp.json()

    def _search_url(self) -> str:
        """Return the space-scoped or global search endpoint."""
        if self.space_id:
            return f"{self.base_url}/v1/spaces/{self.space_id}/search"
        return f"{self.base_url}/v1/search"

    @staticmethod
    def _snippet(item: dict[str, Any]) -> str:
        """Extract a short snippet from common GitBook result fields."""
        for key in ("excerpt", "snippet", "description", "body"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:_SNIPPET_LEN]
        sections = item.get("sections")
        if isinstance(sections, list):
            for section in sections:
                if isinstance(section, dict):
                    body = section.get("body") or section.get("snippet")
                    if isinstance(body, str) and body.strip():
                        return body.strip()[:_SNIPPET_LEN]
        return ""

    @staticmethod
    def _uri(item: dict[str, Any]) -> str:
        """Resolve the best page URL from a result item."""
        for key in ("url", "href", "path"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
        urls = item.get("urls")
        if isinstance(urls, dict):
            for key in ("app", "public", "self"):
                value = urls.get(key)
                if isinstance(value, str) and value:
                    return value
        return ""

    @staticmethod
    def _matches(item: dict[str, Any], terms: set[str], snippet: str) -> int:
        """Count query-term hits across a result's text fields."""
        haystack = " ".join(str(v) for v in (item.get("title", ""), snippet)).lower()
        return sum(1 for t in terms if t in haystack)

    def _to_ref(self, item: dict[str, Any], score: float) -> KnowledgeRef:
        """Build a KnowledgeRef from a GitBook search result."""
        title = str(item.get("title", "") or "page")
        snippet = self._snippet(item)
        return KnowledgeRef(
            connector=self.name,
            kind="doc",
            title=title,
            uri=self._uri(item),
            snippet=snippet,
            score=round(score, 4),
            metadata={
                "id": item.get("id"),
                "space_id": self.space_id,
            },
        )

    def search(self, query: str, *, limit: int = _DEFAULT_LIMIT) -> list[KnowledgeRef]:
        """Query GitBook space search and map results to knowledge refs."""
        if not self.token:
            return []
        terms = {w for w in query.lower().split() if w}
        params = {"query": query, "limit": limit}
        data = self._get_json(self._search_url(), params)

        # GitBook returns {"items": [...]}; tolerate "results" and bare lists.
        if isinstance(data, dict):
            items = data.get("items")
            if items is None:
                items = data.get("results", [])
        else:
            items = data
        if not isinstance(items, list):
            return []

        scored: list[tuple[int, KnowledgeRef]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            snippet = self._snippet(item)
            hits = self._matches(item, terms, snippet) if terms else 1
            score = min(1.0, hits / len(terms)) if terms else 0.0
            scored.append((hits, self._to_ref(item, score)))

        scored.sort(key=lambda pair: (-pair[0], pair[1].title))
        return [ref for _, ref in scored[:limit]]
