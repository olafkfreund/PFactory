"""ConfluenceTarget — upsert a Confluence page per plan (by title).

Pushes the rendered plan into a configured space: search by title → update (bump
version) or create, then apply labels. The HTTP client is injectable (a small
Protocol) so unit tests run with a fake and never touch the network. Markdown is
wrapped in the Confluence ``markdown`` storage macro (refine later — see design
§6c risks). ``publish`` never raises.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

from ..bundle import DocBundle, TargetResult

logger = logging.getLogger(__name__)


class _Resp(Protocol):
    status_code: int

    def json(self) -> Any: ...


class _Client(Protocol):
    def get(self, url: str, *, params: dict | None = ...) -> _Resp: ...
    def post(self, url: str, *, json: dict) -> _Resp: ...
    def put(self, url: str, *, json: dict) -> _Resp: ...


def _storage_body(markdown: str) -> dict:
    macro = (
        '<ac:structured-macro ac:name="markdown">'
        f"<ac:plain-text-body><![CDATA[{markdown}]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )
    return {"storage": {"value": macro, "representation": "storage"}}


class ConfluenceTarget:
    name = "confluence"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        space: str | None = None,
        client: _Client | None = None,
    ) -> None:
        self._base = (base_url or os.environ.get("CONFLUENCE_BASE_URL", "")).rstrip("/")
        self._token = token or os.environ.get("CONFLUENCE_API_TOKEN", "")
        self._space = space or os.environ.get("CONFLUENCE_SPACE", "")
        self._client = client

    def available(self) -> bool:
        return bool(self._base and self._token and self._space)

    def _http(self) -> _Client:
        if self._client is not None:
            return self._client
        import httpx

        return httpx.Client(
            timeout=20.0,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )

    def publish(self, bundle: DocBundle) -> TargetResult:
        try:
            c = self._http()
            api = f"{self._base}/wiki/rest/api/content"
            title = f"Plan: {bundle.title}"

            found = c.get(
                api,
                params={"spaceKey": self._space, "title": title, "expand": "version"},
            )
            results = (
                (found.json() or {}).get("results", [])
                if found.status_code < 400
                else []
            )

            if results:
                page = results[0]
                page_id = page["id"]
                version = int(page.get("version", {}).get("number", 1)) + 1
                c.put(
                    f"{api}/{page_id}",
                    json={
                        "id": page_id,
                        "type": "page",
                        "title": title,
                        "space": {"key": self._space},
                        "version": {"number": version},
                        "body": _storage_body(bundle.markdown),
                    },
                )
                action = "updated"
            else:
                resp = c.post(
                    api,
                    json={
                        "type": "page",
                        "title": title,
                        "space": {"key": self._space},
                        "body": _storage_body(bundle.markdown),
                    },
                )
                page_id = (
                    (resp.json() or {}).get("id", "") if resp.status_code < 400 else ""
                )
                action = "created"

            # Best-effort labels (non-fatal)
            if page_id:
                try:
                    c.post(
                        f"{api}/{page_id}/label",
                        json=[
                            {"prefix": "global", "name": "pfactory"},
                            {
                                "prefix": "global",
                                "name": f"correlation-{bundle.correlation_key}",
                            },
                        ],
                    )
                except Exception:  # noqa: BLE001
                    pass

            return TargetResult(
                target=self.name,
                status="written",
                detail={"action": action, "page_id": page_id, "title": title},
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, never break emit
            logger.warning("ConfluenceTarget failed for %s: %s", bundle.plan_id, exc)
            return TargetResult(
                target=self.name, status="error", detail={"error": str(exc)}
            )
