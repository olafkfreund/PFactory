"""RFC-0019 section 3.4 — public capability discovery.

Serves ``GET /.well-known/agent-skills/index.json``: the manifest an external
agent hits *first*, listing what PFactory can do, how to reach its MCP server and
where its OpenAPI document lives — so the factory's capabilities are
self-describing rather than tribal knowledge.

Shape: ``apis/agent-skills-manifest.schema.json`` in the Factory hub, the
``kind: "service"`` variant. A vendored copy of that schema is validated against
the live response in ``tests/test_well_known_agent_skills.py``.

Where the skills come from
--------------------------
Two sources, deliberately different in kind:

- **MCP tools** are derived from :data:`routes.mcp_rpc.MCP_TOOLS`, the same
  catalogue ``POST /mcp`` answers ``tools/list`` with, so the manifest cannot
  advertise a tool the server does not serve.
- **Skill packages** are the static list below rather than a scan of
  ``.claude/skills/``. Two reasons, both load-bearing:

  1. ``.claude/`` is in ``.dockerignore``, so a runtime scan advertises ten
     skills in a dev checkout and *zero* from the container image — and the
     contract requires ``minItems: 1``, so the deployed manifest would be
     invalid. The packages install from GitHub, not from the running pod, so
     presence on local disk is the wrong signal entirely.
  2. The contract asks for a curated list: an entry "MUST correspond to a
     capability the service genuinely implements". ``.claude/skills/`` also
     carries imported third-party bundles (Backstage migrations) that describe
     the agent's authoring environment, not anything PFactory serves.

  ``tests/test_well_known_agent_skills.py`` asserts every package named here
  still exists on disk with a matching SKILL.md, so the static list cannot
  drift from the packages it names.

Unauthenticated by design. Agents enumerate capabilities before they hold a
token, so this path is exempt from :class:`server.auth.TokenAuthMiddleware`
(listed in its ``PUBLIC_PATHS``). It carries public metadata only — no tokens, no
credentials, no user, project or planning-session data. URLs are emitted
origin-relative so the manifest never discloses an internal hostname or the
deployment's ingress topology.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from . import mcp_rpc

router = APIRouter(tags=["Well-Known"])

# RFC 8615 well-known URI for the RFC-0019 manifest. Kept as a module constant
# so server.auth can assert the exact public path in a test rather than
# duplicating the literal.
MANIFEST_PATH = "/.well-known/agent-skills/index.json"

# Bumped only on a breaking change to the manifest's own shape.
SCHEMA_VERSION = "1"

# Discriminator required by the fleet contract
# (Factory apis/agent-skills-manifest.schema.json): "service" for a single
# service's manifest, "fleet" for CFactory's aggregate. The schema keys its
# conditional body off this, so omitting it fails validation twice over -
# once for the missing property and again because every other key then reads
# as unevaluated.
MANIFEST_KIND = "service"

REPOSITORY_URL = "https://github.com/olafkfreund/PFactory"
MCP_DOCUMENTATION_URL = "https://github.com/olafkfreund/Factory/blob/main/apis/pfactory.mcp.md"

# PARR stage PFactory owns. The contract's enum maps prepare=PFactory.
SERVICE_ROLE = "prepare"

SERVICE_DESCRIPTION = (
    "The feasibility and governance gate between an AI plan and execution. "
    "PFactory ingests a plan (markdown, Gherkin or EARS), enriches it with "
    "read-only live-cloud context, runs pre-code feasibility (cost, IAM access, "
    "quotas) plus architecture/security/best-practice review gates, requires one "
    "human approval, and emits governed, tagged GitHub epics and child issues "
    "any execution agent can build. It sits downstream of spec-authoring tools "
    "and upstream of any execution agent."
)

# Skill packages shipped under .claude/skills/. Static by design — see the
# module docstring. Descriptions are the packages' own SKILL.md `description`
# front-matter, so the prose an agent reasons over is the prose the package
# ships with.
_SKILL_PACKAGES: list[dict[str, Any]] = [
    {
        "name": "handover-to-pfactory",
        "description": (
            "Hand a plan or request to PFactory for governed planning. Ingests "
            "it (markdown / Gherkin / EARS / pdf / docx, inline or a file), runs "
            "the pipeline (enrich with live cloud + Backstage context, decompose "
            "into an epic + child issues, feasibility, the review lenses, the "
            "hard readiness gate), and returns a cited review a human can "
            "approve. Nothing reaches GitHub or AIFactory until the gates pass "
            "and a human approves."
        ),
        "invocation": {"kind": "slash_command", "command": "/handover-to-pfactory"},
        "install": {
            "source": REPOSITORY_URL,
            "path": ".claude/skills/handover-to-pfactory/SKILL.md",
        },
        "tags": ["planning", "governance", "parr"],
    },
    {
        "name": "pfactory-watch",
        "description": (
            "Poll one PFactory planning session through the pipeline and report "
            "where it stands — board column, gate result, blocking findings, and "
            "the readiness checks that still fail. One check per invocation, so "
            "it can be driven with /loop. Read-only; it never approves, waives, "
            "or emits."
        ),
        "invocation": {"kind": "slash_command", "command": "/pfactory-watch"},
        "install": {
            "source": REPOSITORY_URL,
            "path": ".claude/skills/pfactory-watch/SKILL.md",
        },
        "tags": ["planning", "status", "read-only"],
    },
    {
        "name": "cloud-discover",
        "description": (
            "Run a read-only cloud infrastructure assessment — discover an "
            "AWS/Azure/GCP account's resources, detect misconfigurations with "
            "Prowler (CIS/OCSF), build a service-topology diagram, and emit a "
            "verdict (reject/flag/accept). Follows the access -> discover -> "
            "diagram -> assess flow and writes the report into findings/."
        ),
        "invocation": {"kind": "slash_command", "command": "/cloud-discover"},
        "install": {
            "source": REPOSITORY_URL,
            "path": ".claude/skills/cloud-discover/SKILL.md",
        },
        "tags": ["cloud", "security-posture", "read-only"],
    },
]


def _skill_name_for_tool(tool_name: str) -> str:
    """Turn an MCP tool name into a manifest skill id.

    ``pfactory_get_epic`` -> ``pfactory-get-epic``. The contract constrains skill
    ids to ``^[a-z][a-z0-9-]{1,63}$``, which admits no underscores; the untouched
    tool name still travels in ``invocation.tool``.
    """
    return tool_name.replace("_", "-")


def _mcp_skills() -> list[dict[str, Any]]:
    """One skill entry per MCP tool, derived from the live catalogue."""
    return [
        {
            "name": _skill_name_for_tool(tool["name"]),
            "description": tool["description"],
            "invocation": {"kind": "mcp_tool", "tool": tool["name"]},
            "tags": ["mcp", "planning-context"],
        }
        for tool in mcp_rpc.MCP_TOOLS
    ]


@router.get(MANIFEST_PATH, summary="Agent capability manifest (RFC-0019)")
async def agent_skills_index(request: Request) -> dict[str, Any]:
    """Serve the RFC-0019 section 3.4 service manifest. Public by design."""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": MANIFEST_KIND,
        "service": {
            "name": mcp_rpc.SERVER_NAME,
            "title": "PFactory",
            # create_app() reads app.version from apps/backend/__init__.py, the
            # file bump-version.js updates on release — so this tracks the
            # running version without a second source of truth.
            "version": request.app.version,
            "role": SERVICE_ROLE,
            "description": SERVICE_DESCRIPTION,
            "repository": REPOSITORY_URL,
        },
        "openapi_url": "/openapi.json",
        "mcp": {
            # POST /mcp speaks JSON-RPC 2.0 over HTTP. `streamable-http` is the
            # honest label of the three the contract allows: it is that
            # transport, minus the optional server-push half (no GET/SSE
            # channel, no Mcp-Session-Id lifecycle) which mcp_rpc documents as
            # deliberately out of scope. `http+sse` would be a false claim, and
            # `stdio` describes a different mount entirely.
            "transport": "streamable-http",
            "endpoint": "/mcp",
            "documentation": MCP_DOCUMENTATION_URL,
        },
        "auth": {
            # The contract lets a consumer assert the no-auth guarantee rather
            # than infer it.
            "manifest": "none",
            # Always true: JWT and the legacy shared token arrive as Bearer,
            # personal acw_ keys as api-key. OIDC (oauth2) is opt-in via
            # APP_OIDC_ENABLED, so it is not claimed unconditionally.
            "schemes": ["bearer", "api-key"],
        },
        "skills": [*_SKILL_PACKAGES, *_mcp_skills()],
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
