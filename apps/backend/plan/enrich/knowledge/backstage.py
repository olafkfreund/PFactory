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

import re
from typing import Any, Protocol, cast

from plan.enrich.knowledge.base import (
    KnowledgeConnector,
    KnowledgeKind,
    KnowledgeRef,
    _HttpResponse,
    register_connector,
)

_DEFAULT_LIMIT = 10

# The search query is the WHOLE plan text, so the terms it splits into decide
# what counts as a catalog match. Words this common carry no signal — an entity
# whose description contains "the" is not a golden path for this plan (#386).
_STOPWORDS = frozenset(
    {
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "must",
        "not",
        "of",
        "on",
        "or",
        "should",
        "so",
        "than",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "to",
        "use",
        "used",
        "uses",
        "using",
        "was",
        "were",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "would",
        "your",
    }
)
# Below this length a token is punctuation or an article, not a topic.
_MIN_TERM_LEN = 2
# ponytail: two significant shared terms, not a tuned float. The score here is
# hits/len(terms), which shrinks as the plan gets longer, so any fixed float
# threshold would silently admit more on short plans and less on long ones. Move
# to a score floor only if a real catalogue shows two-term coincidences.
_MIN_HITS = 2


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
        """Return the injected client, or lazily build a real one (#257)."""
        return cast(_HttpClient, self._lazy_http_client())

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
    def _significant(query: str) -> set[str]:
        """Query terms that can carry a match.

        The query is the whole plan text, so splitting it raw makes "a", "the",
        "and" and "to" query terms — and an entity whose description contains
        ANY of them scored a hit under the old ``hits == 0`` filter. That is how
        `nixos-module` and `component/tfactory` came to be cited on a
        pure-Python slug service (#386): not because they matched it, but
        because English did.
        """
        # `.` and `-` are kept INSIDE a token (`url-safe`, `go.mod`) but stripped
        # from its edges — otherwise "service." and "service" are two terms, and
        # an entity mentioning "service" once scores two hits against them.
        return {
            stripped
            for w in re.split(r"[^a-z0-9_.-]+", query.lower())
            if (stripped := w.strip("._-"))
            and len(stripped) > _MIN_TERM_LEN
            and stripped not in _STOPWORDS
        }

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
        terms = self._significant(query)
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
            # One shared word out of a whole plan is a coincidence, not a match.
            # The connector already COMPUTES a relevance score here and then
            # admitted every entity regardless of it; a citation that says "the
            # plan should follow it" has to clear some bar, and this is the bar
            # the code was already in a position to apply (#386).
            #
            # Capped at the number of terms so a deliberately narrow query still
            # works: `search("payments")` has one term to match and needs one
            # hit, while a 60-term plan needs two.
            if terms and hits < min(_MIN_HITS, len(terms)):
                continue
            score = min(1.0, hits / len(terms)) if terms else 0.0
            scored.append((hits, self._to_ref(entity, score)))

        scored.sort(key=lambda pair: (-pair[0], pair[1].title))
        return [ref for _, ref in scored[:limit]]
