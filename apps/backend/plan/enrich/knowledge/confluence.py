"""Confluence knowledge connector — search a Confluence wiki (#12).

Queries Confluence Cloud/Server content search (CQL ``text ~ "query"``) and maps
pages to :class:`KnowledgeRef` objects so the planner can cite the org's wiki
pages and policies. Pages whose title/space hints at a policy map to
``kind="policy"``; everything else to ``kind="doc"``.

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
    register_connector,
)

_DEFAULT_LIMIT = 10
_SNIPPET_LEN = 200


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
class ConfluenceConnector(KnowledgeConnector):
    """Map Confluence content-search pages to knowledge refs (read-only)."""

    name = "confluence"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        email: str | None = None,
        http: _HttpClient | None = None,
        **opts: object,
    ) -> None:
        """Create the connector.

        Args:
            base_url: Confluence base URL (e.g. ``https://acme.atlassian.net``).
            token: Optional API token / PAT for protected wikis.
            email: Optional account email (Confluence Cloud basic auth).
            http: Optional injected HTTP client (with ``.get``) for tests.
            **opts: Forwarded to the base connector.
        """
        super().__init__(**opts)
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        self.email = email
        self._http = http

    def available(self) -> bool:
        """True if a base URL is configured (token is optional)."""
        return bool(self.base_url)

    def _client(self) -> _HttpClient:
        """Return the injected client, or lazily build a real one."""
        if self._http is not None:
            return self._http
        try:
            import requests
        except ImportError:  # pragma: no cover - fall back to httpx
            import httpx

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
                raise RuntimeError(f"confluence search returned HTTP {status}")
        return resp.json()

    @staticmethod
    def _infer_kind(page: dict[str, Any]) -> KnowledgeKind:
        """Map a page to a KnowledgeKind, flagging policy pages."""
        space = page.get("space", {}) or {}
        haystack = " ".join(
            str(v)
            for v in (
                page.get("title", ""),
                space.get("name", ""),
                space.get("key", ""),
            )
        ).lower()
        return "policy" if "policy" in haystack else "doc"

    @staticmethod
    def _snippet(page: dict[str, Any]) -> str:
        """Extract a short snippet from excerpt or body fields."""
        excerpt = page.get("excerpt")
        if isinstance(excerpt, str) and excerpt.strip():
            return excerpt.strip()[:_SNIPPET_LEN]
        body = page.get("body", {}) or {}
        for view in ("view", "storage", "excerptView"):
            section = body.get(view, {}) or {}
            value = section.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()[:_SNIPPET_LEN]
        return ""

    @staticmethod
    def _matches(page: dict[str, Any], terms: set[str], snippet: str) -> int:
        """Count query-term hits across a page's text fields."""
        space = page.get("space", {}) or {}
        haystack = " ".join(
            str(v)
            for v in (
                page.get("title", ""),
                space.get("name", ""),
                space.get("key", ""),
                snippet,
            )
        ).lower()
        return sum(1 for t in terms if t in haystack)

    def _to_ref(self, page: dict[str, Any], score: float) -> KnowledgeRef:
        """Build a KnowledgeRef from a Confluence content-search page."""
        title = str(page.get("title", "") or "page")
        snippet = self._snippet(page)
        links = page.get("_links", {}) or {}
        webui = links.get("webui") or links.get("self") or ""
        if webui and self.base_url and webui.startswith("/"):
            uri = f"{self.base_url}{webui}"
        else:
            uri = str(webui)
        space = page.get("space", {}) or {}
        return KnowledgeRef(
            connector=self.name,
            kind=self._infer_kind(page),
            title=title,
            uri=uri,
            snippet=snippet,
            score=round(score, 4),
            metadata={
                "id": page.get("id"),
                "type": page.get("type"),
                "space": space.get("key"),
            },
        )

    def search(self, query: str, *, limit: int = _DEFAULT_LIMIT) -> list[KnowledgeRef]:
        """Query Confluence content search and map pages to knowledge refs."""
        if not self.base_url:
            return []
        terms = {w for w in query.lower().split() if w}
        cql = f'text ~ "{query}"'
        params = {"cql": cql, "limit": limit}
        url = f"{self.base_url}/wiki/rest/api/content/search"
        data = self._get_json(url, params)

        # Confluence returns {"results": [...]}; tolerate a bare list too.
        if isinstance(data, dict):
            pages = data.get("results", [])
        else:
            pages = data
        if not isinstance(pages, list):
            return []

        scored: list[tuple[int, KnowledgeRef]] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            snippet = self._snippet(page)
            hits = self._matches(page, terms, snippet) if terms else 1
            score = min(1.0, hits / len(terms)) if terms else 0.0
            scored.append((hits, self._to_ref(page, score)))

        scored.sort(key=lambda pair: (-pair[0], pair[1].title))
        return [ref for _, ref in scored[:limit]]
