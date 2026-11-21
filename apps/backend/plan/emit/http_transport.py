"""Stdlib HTTP transport for live AIFactory contract handoffs."""

import json as _json
import os
import urllib.parse
import urllib.request


class _UrllibHttp:
    """Stdlib HttpClient for live contract emit (avoids an httpx dependency)."""

    def post(self, url: str, *, params: dict[str, object], json: object) -> object:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {"Content-Type": "application/json"}
        # Authenticate the cross-service handoff. AIFactory's auth middleware
        # requires a bearer token on /api/*; without it the POST 401s and the
        # whole PFactory→AIFactory emit fails. PFactory holds the shared
        # APP_API_TOKEN (factory-secrets); send it.
        token = (
            os.environ.get("PFACTORY_AIFACTORY_API_TOKEN")
            or os.environ.get("APP_API_TOKEN")
            or os.environ.get("PFACTORY_MCP_SECRET")
        )
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(  # noqa: S310 - endpoint is service configuration
            url,
            data=_json.dumps(json).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
        try:
            return _json.loads(body)
        except ValueError:
            return {"raw": body}
