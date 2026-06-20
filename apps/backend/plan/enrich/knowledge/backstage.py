"""Backstage knowledge connector — query the software catalog (#11).

Reads the Backstage catalog (``GET {base_url}/api/catalog/entities``) and maps
entities to :class:`KnowledgeRef` objects so the planner can cite the org's
golden paths, owned components, and software templates. Template entities map to
``kind="template"``; everything else to ``kind="catalog"``.

The HTTP library is lazy-imported inside methods, and an ``http`` client may be
injected so tests need no network and no ``requests``/``httpx`` installed.

Read-only: the connector only reads/searches the catalog; it never writes back.
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


class _HttpResponse(Protocol):
    """Minimal response shape the connector relies on."""

    status_code: int

    def json(self) -> Any: ...

    def raise_for_status(self) -> None: ...


class _HttpClient(Protocol):
    """Minimal HTTP client shape (e.g. ``requests`` or ``httpx``)."""

    def get(self, url: str, *, headers: dict[str, str] | None = ...) -> _HttpResponse: ...


@register_connector
class BackstageConnector(KnowledgeConnector):
    """Map Backstage catalog entities to knowledge refs (read-only)."""

    name = "backstage"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        http: _HttpClient | None = None,
        **opts: object,
    ) -> None:
        """Create the connector.

        Args:
            base_url: Backstage backend base URL (e.g. ``https://backstage``).
            token: Optional bearer token for protected catalogs.
            http: Optional injected HTTP client (with ``.get``) for tests.
            **opts: Forwarded to the base connector.
        """
        super().__init__(**opts)
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        self._http = http

    def available(self) -> bool:
        """True if a base URL is configured (token is optional)."""
        return bool(self.base_url)

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
        """Auth/accept headers for catalog requests."""
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get_json(self, url: str) -> Any:
        """GET ``url`` and return parsed JSON, honouring status checks."""
        resp = self._client().get(url, headers=self._headers())
        raise_for_status = getattr(resp, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        else:  # pragma: no cover - clients without raise_for_status
            status = getattr(resp, "status_code", 200)
            if status >= 400:
                raise RuntimeError(f"backstage catalog returned HTTP {status}")
        return resp.json()

    @staticmethod
    def _entity_kind(entity: dict[str, Any]) -> KnowledgeKind:
        """Map a catalog entity's kind to a KnowledgeKind."""
        return "template" if str(entity.get("kind", "")).lower() == "template" else "catalog"

    @staticmethod
    def _matches(entity: dict[str, Any], terms: set[str]) -> int:
        """Count query-term hits across an entity's text fields."""
        meta = entity.get("metadata", {}) or {}
        spec = entity.get("spec", {}) or {}
        haystack = " ".join(
            str(v)
            for v in (
                entity.get("kind", ""),
                meta.get("name", ""),
                meta.get("title", ""),
                meta.get("description", ""),
                " ".join(meta.get("tags", []) or []),
                spec.get("type", ""),
                spec.get("owner", ""),
            )
        ).lower()
        return sum(1 for t in terms if t in haystack)

    def _to_ref(self, entity: dict[str, Any], score: float) -> KnowledgeRef:
        """Build a KnowledgeRef from a catalog entity."""
        meta = entity.get("metadata", {}) or {}
        name = str(meta.get("name", "") or "entity")
        kind = str(entity.get("kind", "Component"))
        ekind = str(meta.get("namespace", "default"))
        uid = meta.get("uid")
        path = f"catalog/{ekind}/{kind.lower()}/{name}"
        uri = f"{self.base_url}/{path}" if self.base_url else path
        return KnowledgeRef(
            connector=self.name,
            kind=self._entity_kind(entity),
            title=str(meta.get("title") or name),
            uri=uri,
            snippet=str(meta.get("description", "") or "")[:200],
            score=round(score, 4),
            metadata={
                "entity_kind": kind,
                "namespace": ekind,
                "name": name,
                "uid": uid,
                "tags": meta.get("tags", []) or [],
            },
        )

    def search(self, query: str, *, limit: int = _DEFAULT_LIMIT) -> list[KnowledgeRef]:
        """Query the catalog and map matching entities to knowledge refs."""
        if not self.base_url:
            return []
        terms = {w for w in query.lower().split() if w}
        data = self._get_json(f"{self.base_url}/api/catalog/entities")

        # Backstage may return a bare list or {"items": [...]}.
        if isinstance(data, dict):
            entities = data.get("items", [])
        else:
            entities = data
        if not isinstance(entities, list):
            return []

        scored: list[tuple[int, KnowledgeRef]] = []
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            hits = self._matches(entity, terms) if terms else 1
            if terms and hits == 0:
                continue
            score = min(1.0, hits / len(terms)) if terms else 0.0
            scored.append((hits, self._to_ref(entity, score)))

        scored.sort(key=lambda pair: (-pair[0], pair[1].title))
        return [ref for _, ref in scored[:limit]]
