"""CLI: approve a test-target access requirement (RFC-0007 / #86 PR-g).

Thin client over ``POST /api/plan/sessions/{session_id}/access/approve`` (PR-f).
Run emit-contract (dry-run) first to discover the requirements, then:

    python -m plan.access_cli <session_id> --resource web \
        --approved-by olaf --scope "staging account, read-only"

The request builder + result formatter are pure (unit-tested); the HTTP call is
injected so ``main`` is testable without a running server. Base URL from
``PFACTORY_API_URL`` (default http://localhost:3114); bearer token from
``APP_API_TOKEN`` / ``PFACTORY_MCP_SECRET`` when set.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request


def build_request(
    base_url: str,
    session_id: str,
    *,
    resource: str,
    approved_by: str,
    scope: str,
    approved_at: str | None = None,
    token: str | None = None,
) -> tuple[str, dict, dict]:
    """Pure: (url, json_body, headers) for the approve-access call."""
    url = f"{base_url.rstrip('/')}/api/plan/sessions/{session_id}/access/approve"
    body = {"resource": resource, "approved_by": approved_by, "scope": scope}
    if approved_at:
        body["approved_at"] = approved_at
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return url, body, headers


def format_result(result: dict) -> str:
    """Pure: a one-line human summary of the route's response."""
    resource = result.get("resource", "?")
    if result.get("ok"):
        return f"approved: {resource} -> {result.get('state', 'curated')}"
    return f"NOT approved: {resource} — {result.get('reason', '(no reason given)')}"


def _post(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str] | None = None, *, post=None) -> int:
    p = argparse.ArgumentParser(
        prog="pfactory-approve-access",
        description="Approve a test-target access requirement (RFC-0007 #86).",
    )
    p.add_argument("session_id")
    p.add_argument("--resource", required=True, help="the access resource to approve")
    p.add_argument("--approved-by", required=True, help="who is approving")
    p.add_argument("--scope", required=True, help="what is being granted")
    p.add_argument("--approved-at", default=None)
    p.add_argument(
        "--base-url",
        default=os.environ.get("PFACTORY_API_URL", "http://localhost:3114"),
    )
    args = p.parse_args(argv)

    token = os.environ.get("APP_API_TOKEN") or os.environ.get("PFACTORY_MCP_SECRET")
    url, body, headers = build_request(
        args.base_url,
        args.session_id,
        resource=args.resource,
        approved_by=args.approved_by,
        scope=args.scope,
        approved_at=args.approved_at,
        token=token,
    )
    result = (post or _post)(url, body, headers)
    print(format_result(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
