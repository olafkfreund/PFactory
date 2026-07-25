"""RFC-0019 section 3.4 — the ``/.well-known/agent-skills/index.json`` manifest.

Three things must hold, and the first is the one that silently failed before:

1. **It validates against the fleet contract.** The canonical schema lives in the
   Factory hub (``apis/agent-skills-manifest.schema.json``). A copy is vendored
   at ``tests/contracts/`` so the check runs offline, mirroring how this repo
   already vendors the RFC-0002 task-contract schema. The first version of this
   endpoint passed a suite of hand-written shape assertions while producing a
   document with 23 validation errors — hand-asserting a subset of a contract is
   not the same as checking it, so ``jsonschema`` (already an ``apps/backend``
   dependency) does the real thing here.

2. **It is readable with no credentials.** CI runs the suite under
   ``APP_DISABLE_AUTH=true``, which short-circuits ``TokenAuthMiddleware``
   entirely — so an anonymous ``TestClient`` request proves nothing. The auth
   tests below drive ``TokenAuthMiddleware.dispatch`` with ``DISABLE_AUTH=False``,
   the way ``tests/test_api_key_auth.py`` does.

3. **Its claims are true.** The MCP entries must track the live tool catalogue,
   and every statically-listed skill package must still exist on disk.

Vendored schema provenance: Factory ``1158433`` ("feat(rfc-0019): agent-skills
manifest contract + fleet aggregate schema (#302) (#355)"). Re-vendor when the
hub's copy changes.

Requires FastAPI (web-server venv); skipped where it is absent.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
jsonschema = pytest.importorskip("jsonschema")

_ROOT = Path(__file__).parent.parent
_BACKEND = _ROOT / "apps" / "backend"
_WEBSERVER = _ROOT / "apps" / "web-server"
for _p in (str(_BACKEND), str(_WEBSERVER)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from server import auth as auth_mod  # noqa: E402
from server.routes import mcp_rpc, well_known  # noqa: E402

MANIFEST_PATH = well_known.MANIFEST_PATH
SCHEMA_PATH = Path(__file__).parent / "contracts" / "agent-skills-manifest.schema.json"
SKILLS_DIR = _ROOT / ".claude" / "skills"

# The contract's `service_identity.name` / `skill.name` pattern.
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


@pytest.fixture()
def client() -> TestClient:
    """A minimal app carrying only the well-known router.

    Deliberately not ``server.main.create_app()``: that pulls the database and
    every optional integration into a test whose subject is one read-only route.
    Version/description are set here because the route reads them off
    ``request.app``.
    """
    app = FastAPI(title="PFactory Web API", description="Test description", version="9.9.9")
    app.include_router(well_known.router)
    return TestClient(app)


@pytest.fixture()
def manifest(client: TestClient) -> dict:
    resp = client.get(MANIFEST_PATH)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body: dict = resp.json()
    return body


# ---------------------------------------------------------------------------
# The contract itself
# ---------------------------------------------------------------------------


def test_manifest_validates_against_the_fleet_contract(manifest: dict) -> None:
    """The whole document, checked against the hub schema. The regression gate."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    errors = sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path))

    assert not errors, "manifest violates the agent-skills contract:\n" + "\n".join(
        f"  {'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors
    )


def test_vendored_schema_is_the_service_variant_we_target() -> None:
    """Guard the vendored copy itself: a wrong file would make the gate vacuous."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert "service" in schema["properties"]["kind"]["enum"]
    # If these move, the manifest must be revisited rather than the test relaxed.
    assert schema["$defs"]["service_body"]["required"] == [
        "service",
        "openapi_url",
        "mcp",
        "skills",
    ]
    assert schema["$defs"]["skill"]["required"] == ["name", "description", "invocation"]


def test_envelope_and_identity(manifest: dict) -> None:
    """Spot-check the fields an agent keys off, beyond bare schema validity."""
    assert manifest["schema_version"] == "1"
    assert manifest["kind"] == "service"

    service = manifest["service"]
    assert service["name"] == "pfactory"
    assert NAME_PATTERN.match(service["name"])
    assert service["version"] == "9.9.9"
    # PFactory is the `prepare` stage of PARR.
    assert service["role"] == "prepare"

    assert manifest["openapi_url"] == "/openapi.json"
    # POST /mcp is JSON-RPC over HTTP, so it must not claim stdio or SSE.
    assert manifest["mcp"]["transport"] == "streamable-http"
    assert manifest["mcp"]["endpoint"] == "/mcp"
    # The manifest asserts its own no-auth guarantee rather than leaving a
    # consumer to infer it.
    assert manifest["auth"]["manifest"] == "none"


# ---------------------------------------------------------------------------
# The claims are true
# ---------------------------------------------------------------------------


def test_mcp_skills_track_the_live_tool_catalog(manifest: dict) -> None:
    """Derived, not restated — the manifest cannot drift from the MCP server."""
    advertised = {
        s["invocation"]["tool"] for s in manifest["skills"] if s["invocation"]["kind"] == "mcp_tool"
    }
    actual = {tool["name"] for tool in mcp_rpc.MCP_TOOLS}

    assert advertised == actual, (
        "the manifest's mcp_tool skills must be exactly the server's tool "
        f"catalog; missing={actual - advertised} extra={advertised - actual}"
    )


def test_every_advertised_skill_package_exists_on_disk(manifest: dict) -> None:
    """The static package list is the anti-drift counterpart to not scanning.

    ``.claude/`` is dockerignored, so the manifest cannot glob it at runtime (see
    the route's module docstring). This test does the globbing instead, at a time
    when the repo IS on disk, so a renamed or deleted package fails CI rather
    than shipping a 404 to an agent.
    """
    if not SKILLS_DIR.is_dir():
        pytest.skip(".claude/skills/ not present in this checkout")

    for skill in manifest["skills"]:
        if skill["invocation"]["kind"] != "slash_command":
            continue

        install = skill["install"]
        skill_md = _ROOT / install["path"]
        assert skill_md.is_file(), f"{skill['name']}: {install['path']} does not exist"
        assert install["path"] == f".claude/skills/{skill['name']}/SKILL.md"
        # The slash command an agent runs is the package's own name.
        assert skill["invocation"]["command"] == f"/{skill['name']}"


def test_skill_packages_are_curated_not_a_directory_dump(manifest: dict) -> None:
    """The contract wants genuine PFactory capabilities, not every bundle on disk.

    ``.claude/skills/`` also holds imported third-party bundles (Backstage
    migrations) that describe the agent's authoring environment rather than
    anything this service implements. Advertising those would be a public,
    machine-readable claim PFactory cannot honour — the same defect #349 fixed.
    """
    advertised = {
        s["name"] for s in manifest["skills"] if s["invocation"]["kind"] == "slash_command"
    }

    assert advertised == {"handover-to-pfactory", "pfactory-watch", "cloud-discover"}
    for imported in ("mui-to-bui-migration", "app-frontend-system-migration"):
        assert imported not in advertised, f"{imported} is not a PFactory capability"


def test_skill_ids_are_unique(manifest: dict) -> None:
    names = [s["name"] for s in manifest["skills"]]
    assert len(names) == len(set(names)), f"duplicate skill ids: {names}"


def test_manifest_leaks_nothing_sensitive(manifest: dict) -> None:
    """Public metadata only — no credentials, no internal hostnames."""
    blob = json.dumps(manifest).lower()

    for marker in ("secret", "password", "api_token", "bearer ", "acw_", "localhost"):
        assert marker not in blob, f"manifest leaks {marker!r}"
    # Only the two public GitHub URLs are absolute; nothing points at a host we
    # would then have to keep honest across local / dev / hosted.
    for url in (manifest["openapi_url"], manifest["mcp"]["endpoint"]):
        assert url.startswith("/"), f"{url} should be origin-relative"


# ---------------------------------------------------------------------------
# Unauthenticated reachability (with auth actually ENABLED)
# ---------------------------------------------------------------------------


def _anonymous_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],  # no Authorization header, no cookie
            "query_string": b"",
        }
    )


# Not credentials — placeholders the anonymous requests below can never match.
# Bound to a name (rather than inlined at a sensitive kwarg) so ruff's S105/S106
# stay quiet, mirroring tests/test_api_key_auth.py.
_DUMMY = "unused"


def _settings_with_auth_enabled() -> SimpleNamespace:
    return SimpleNamespace(
        DISABLE_AUTH=False,
        API_TOKEN=_DUMMY,
        JWT_SECRET=_DUMMY,
        JWT_ALGORITHM="HS256",
    )


@pytest.mark.asyncio
async def test_manifest_is_reachable_without_authentication(monkeypatch) -> None:
    """With auth ENABLED and no token, the manifest must still reach the route."""
    monkeypatch.setattr(auth_mod, "get_settings", _settings_with_auth_enabled)

    middleware = auth_mod.TokenAuthMiddleware(app=None)
    reached = {}

    async def call_next(_request):
        reached["downstream"] = True
        return SimpleNamespace(status_code=200)

    resp = await middleware.dispatch(_anonymous_request(MANIFEST_PATH), call_next)

    assert reached.get("downstream"), "anonymous manifest request was blocked by auth"
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_still_guards_the_api_surface(monkeypatch) -> None:
    """Control: the exemption is scoped to the manifest, not a blanket bypass."""
    monkeypatch.setattr(auth_mod, "get_settings", _settings_with_auth_enabled)

    middleware = auth_mod.TokenAuthMiddleware(app=None)
    reached = {}

    async def call_next(_request):
        reached["downstream"] = True
        return SimpleNamespace(status_code=200)

    resp = await middleware.dispatch(_anonymous_request("/api/projects"), call_next)

    assert not reached.get("downstream"), "anonymous /api request must not reach the route"
    assert resp.status_code == 401


def test_public_path_matches_the_route_path() -> None:
    """The auth exemption must name the exact path the router serves."""
    assert MANIFEST_PATH in auth_mod.TokenAuthMiddleware.PUBLIC_PATHS
