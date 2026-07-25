"""RFC-0019 section 3.4 — public capability discovery.

Serves ``GET /.well-known/agent-skills/index.json``: the manifest an external
agent hits *first*, listing PFactory's installable skills, its MCP endpoint and
its OpenAPI URL, so the factory's capabilities are self-describing rather than
tribal knowledge.

**Derived from live code, never hand-maintained.** Every field is read from the
thing it describes, so the manifest cannot drift from reality:

- ``skills``      — ``.claude/skills/`` via :func:`routes.pfactory_skills.collect_skills`,
                    the same catalogue the portal serves at ``GET /api/pfactory/skills``.
- ``mcp.tools``   — :data:`routes.mcp_rpc.MCP_TOOLS`, the live JSON-RPC tool
                    catalogue behind ``POST /mcp``.
- ``service``     — ``request.app`` version/description, which ``main.create_app()``
                    reads from ``apps/backend/__init__.py`` (the release source of truth).

**Unauthenticated by design.** Agents enumerate capabilities before they hold a
token, so this path is exempt from :class:`server.auth.TokenAuthMiddleware`
(listed in its ``PUBLIC_PATHS``). It is therefore restricted to public metadata
that is already readable on the unauthenticated ``/openapi.json``: no tokens, no
credentials, no user or project data. URLs are emitted **relative** so the
manifest never discloses an internal hostname or the deployment's ingress
topology — the client resolves them against the origin it already reached.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from . import mcp_rpc
from .pfactory_skills import collect_skills

router = APIRouter(tags=["Well-Known"])

# RFC 8615 well-known URI for the RFC-0019 manifest. Kept as a module constant
# so server.auth can assert the exact public path in a test rather than
# duplicating the literal.
MANIFEST_PATH = "/.well-known/agent-skills/index.json"

# Bumped only on a breaking change to the manifest's own shape.
SCHEMA_VERSION = "1"


@router.get(MANIFEST_PATH, summary="Agent capability manifest (RFC-0019)")
async def agent_skills_index(request: Request) -> dict[str, Any]:
    """Return this service's agent-skills manifest.

    Fail-safe: a missing or unreadable ``.claude/skills/`` yields an empty
    ``skills`` list (see :func:`collect_skills`) rather than a 500 — a container
    image built without the skill bundles must still be discoverable.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "service": {
            "name": mcp_rpc.SERVER_NAME,
            "version": request.app.version,
            "description": request.app.description,
        },
        "skills": collect_skills(),
        "mcp": {
            "endpoint": "/mcp",
            "transport": "http",
            "protocol_version": mcp_rpc.PROTOCOL_VERSION,
            "tools": [
                {"name": tool["name"], "description": tool["description"]}
                for tool in mcp_rpc.MCP_TOOLS
            ],
        },
        "openapi_url": "/openapi.json",
    }
