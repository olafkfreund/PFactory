"""RFC-0019 section 3.4 — the ``/.well-known/agent-skills/index.json`` manifest.

Two things must hold, and the second is the one that is easy to fake:

1. The manifest is valid JSON of the expected shape, and every field is
   *derived* from the code it describes (skills from ``.claude/skills/``, MCP
   tools from ``mcp_rpc.MCP_TOOLS``) rather than hand-written.
2. It is readable with **no** credentials. CI runs the suite with
   ``APP_DISABLE_AUTH=true``, which short-circuits ``TokenAuthMiddleware``
   entirely — so a plain anonymous ``TestClient`` request proves nothing. The
   auth tests below drive ``TokenAuthMiddleware.dispatch`` directly with
   ``DISABLE_AUTH=False``, the same way ``tests/test_api_key_auth.py`` does.

Requires FastAPI (web-server venv); skipped where it is absent.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

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


@pytest.fixture()
def client() -> TestClient:
    """A minimal app carrying only the well-known router.

    Deliberately not ``server.main.create_app()``: that pulls the database,
    the skills service and every optional integration into a test whose whole
    subject is one read-only route. Version/description are set here because
    the route reads them off ``request.app``.
    """
    app = FastAPI(title="PFactory Web API", description="Test description", version="9.9.9")
    app.include_router(well_known.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_manifest_is_json_with_the_expected_shape(client: TestClient) -> None:
    """200 + JSON carrying identity, skills, the MCP endpoint and the OpenAPI URL."""
    resp = client.get(MANIFEST_PATH)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")

    body = resp.json()
    assert set(body) >= {"schema_version", "service", "skills", "mcp", "openapi_url"}

    assert body["service"]["name"] == "pfactory"
    assert body["service"]["version"] == "9.9.9"

    assert body["openapi_url"] == "/openapi.json"
    assert body["mcp"]["endpoint"] == "/mcp"
    assert body["mcp"]["protocol_version"] == mcp_rpc.PROTOCOL_VERSION

    assert isinstance(body["skills"], list)
    for skill in body["skills"]:
        assert set(skill) == {"name", "description", "when_to_use", "allowed_tools"}
        assert skill["name"] and skill["description"]


def test_mcp_tools_are_derived_from_the_live_catalog(client: TestClient) -> None:
    """The advertised tools must BE ``MCP_TOOLS`` — not a copy that can drift."""
    tools = client.get(MANIFEST_PATH).json()["mcp"]["tools"]

    assert [t["name"] for t in tools] == [t["name"] for t in mcp_rpc.MCP_TOOLS]
    assert [t["description"] for t in tools] == [t["description"] for t in mcp_rpc.MCP_TOOLS]


def test_skills_are_derived_from_the_skills_directory(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    """Point the skills dir at a fixture bundle; the manifest must reflect it."""
    bundle = tmp_path / "demo-skill"
    bundle.mkdir()
    (bundle / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "description: A fixture skill used to prove the manifest reads the directory.\n"
        "when_to_use: When the test runs.\n"
        "allowed-tools:\n"
        "  - mcp__pfactory__plan_ingest\n"
        "---\n\nBody.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PFACTORY_SKILLS_DIR", str(tmp_path))

    skills = client.get(MANIFEST_PATH).json()["skills"]

    assert skills == [
        {
            "name": "demo-skill",
            "description": "A fixture skill used to prove the manifest reads the directory.",
            "when_to_use": "When the test runs.",
            "allowed_tools": ["mcp__pfactory__plan_ingest"],
        }
    ]


def test_missing_skills_directory_still_serves_a_manifest(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    """Fail-safe: no skill bundles shipped -> empty list, not a 500."""
    monkeypatch.setenv("PFACTORY_SKILLS_DIR", str(tmp_path / "does-not-exist"))

    resp = client.get(MANIFEST_PATH)

    assert resp.status_code == 200
    assert resp.json()["skills"] == []


def test_manifest_leaks_no_credentials(client: TestClient) -> None:
    """Public metadata only — no secrets and no absolute (host-disclosing) URLs."""
    raw = client.get(MANIFEST_PATH).text

    for marker in ("secret", "token", "password", "api_key", "Bearer"):
        assert marker.lower() not in raw.lower(), f"manifest exposes {marker!r}"
    # URLs stay relative so the manifest never discloses an internal hostname.
    assert "http://" not in raw and "https://" not in raw


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
