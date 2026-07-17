"""Multi-tenancy: tenant-scoped specs/plans/sessions (#308, factory-gitops#13).

Covers the whole seam end to end:
  - tenant resolution (X-Tenant-Id header, "default" fallback, the
    PFACTORY_MULTI_TENANT flag — mirroring the CFactory pattern);
  - tenant stamped on session creation (ingest-text + the #306 from-issue
    endpoint) and mirrored onto the durable job_states row;
  - the Alembic migration's NOT NULL DEFAULT 'default' backfill;
  - tenant-filtered list/detail reads with the flag on, unchanged behaviour
    with the flag off;
  - the OPTIONAL additive ``provenance.tenant_id`` on the AIFactory contract.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))
_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("fastapi")
pytest.importorskip("pydantic")
pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.emit.contract_emit import assemble_contract  # noqa: E402
from plan.service import PlanService  # noqa: E402
from server.jobstore import JobStateStore  # noqa: E402
from server.jobstore.models import JobState  # noqa: E402
from server.routes import plan_pipeline as pp  # noqa: E402
from server.tenancy import DEFAULT_TENANT, multi_tenant_enabled, resolve_tenant  # noqa: E402

_PLAN = """# Widget service
A FastAPI service tested with pytest.
## Acceptance Criteria
- exposes a widget API
"""

_ISSUE_PAYLOAD = {
    "repo": "olafkfreund/AIFactory",
    "provider": "github",
    "issue_number": 306,
    "title": "Widget service",
    "body": _PLAN,
    "labels": ["factory:hard"],
    "autonomy_tier": "hard",
}


class _Request:
    """Header-carrying Request stand-in for direct route calls."""

    def __init__(self, headers: dict | None = None) -> None:
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def _no_project_tracking(monkeypatch) -> None:
    """Stub the tracked-project side effect (it writes through to the DB)."""
    from server.routes import projects  # noqa: PLC0415 — deferred like the route itself

    monkeypatch.setattr(projects, "ensure_tracked_project", lambda _repo: None)


@pytest.fixture()
def service(monkeypatch) -> PlanService:
    """Route the module-level SERVICE at a fresh in-memory PlanService."""
    svc = PlanService()
    monkeypatch.setattr(pp, "SERVICE", svc)
    return svc


@pytest.fixture()
def multi_tenant(monkeypatch):
    monkeypatch.setenv("PFACTORY_MULTI_TENANT", "true")


# ── tenant resolution (the CFactory-pattern helper) ─────────────────────────


def test_flag_off_resolves_default_even_with_header(monkeypatch) -> None:
    monkeypatch.delenv("PFACTORY_MULTI_TENANT", raising=False)
    assert multi_tenant_enabled() is False
    assert resolve_tenant(_Request({"X-Tenant-Id": "acme"})) == DEFAULT_TENANT


@pytest.mark.usefixtures("multi_tenant")
def test_flag_on_resolves_header_with_default_fallback() -> None:
    assert multi_tenant_enabled() is True
    assert resolve_tenant(_Request({"X-Tenant-Id": "acme"})) == "acme"
    assert resolve_tenant(_Request({"X-Tenant-Id": "  "})) == DEFAULT_TENANT
    assert resolve_tenant(_Request()) == DEFAULT_TENANT
    assert resolve_tenant(None) == DEFAULT_TENANT


# ── stamp on session creation ───────────────────────────────────────────────


def test_ingest_text_stamps_default_tenant(service: PlanService) -> None:
    session = service.ingest_text(_PLAN, title="Widget")
    assert session.tenant_id == DEFAULT_TENANT
    assert session.summary()["tenant_id"] == DEFAULT_TENANT


@pytest.mark.usefixtures("multi_tenant")
def test_ingest_text_route_stamps_resolved_tenant(service) -> None:
    body = pp.IngestTextBody(text=_PLAN, title="Widget")
    out = asyncio.run(pp.ingest_text(body, _Request({"X-Tenant-Id": "acme"})))
    assert service.get(out["session_id"]).tenant_id == "acme"
    assert out["tenant_id"] == "acme"


@pytest.mark.usefixtures("multi_tenant")
def test_from_issue_route_stamps_resolved_tenant(service) -> None:
    """The #306 from-issue endpoint stamps the tenant like every other intake."""
    body = pp.FromIssueBody(**_ISSUE_PAYLOAD)
    out = asyncio.run(pp.ingest_from_issue(body, _Request({"X-Tenant-Id": "acme"})))
    assert service.get(out["session_id"]).tenant_id == "acme"


def test_flag_off_ignores_the_header_on_intake(service, monkeypatch) -> None:
    monkeypatch.delenv("PFACTORY_MULTI_TENANT", raising=False)
    body = pp.IngestTextBody(text=_PLAN, title="Widget")
    out = asyncio.run(pp.ingest_text(body, _Request({"X-Tenant-Id": "acme"})))
    assert service.get(out["session_id"]).tenant_id == DEFAULT_TENANT


# ── tenant-filtered reads ───────────────────────────────────────────────────


def _two_tenants(service: PlanService) -> tuple[str, str]:
    a = service.ingest_text(_PLAN, title="A", tenant_id="acme")
    b = service.ingest_text(_PLAN, title="B", tenant_id="globex")
    return a.session_id, b.session_id


@pytest.mark.usefixtures("multi_tenant")
def test_list_is_filtered_when_the_flag_is_on(service) -> None:
    sid_a, _sid_b = _two_tenants(service)
    out = asyncio.run(pp.list_sessions(_Request({"X-Tenant-Id": "acme"})))
    assert [s["session_id"] for s in out["sessions"]] == [sid_a]


def test_list_is_unchanged_when_the_flag_is_off(service, monkeypatch) -> None:
    monkeypatch.delenv("PFACTORY_MULTI_TENANT", raising=False)
    sid_a, sid_b = _two_tenants(service)
    out = asyncio.run(pp.list_sessions(_Request({"X-Tenant-Id": "acme"})))
    assert {s["session_id"] for s in out["sessions"]} == {sid_a, sid_b}


@pytest.mark.usefixtures("multi_tenant")
def test_detail_cross_tenant_is_a_404(service) -> None:
    _sid_a, sid_b = _two_tenants(service)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(pp.get_session(sid_b, _Request({"X-Tenant-Id": "acme"})))
    assert exc.value.status_code == 404
    # ...while the owning tenant still reads it.
    out = asyncio.run(pp.get_session(sid_b, _Request({"X-Tenant-Id": "globex"})))
    assert out["session_id"] == sid_b


def test_detail_is_unchanged_when_the_flag_is_off(service, monkeypatch) -> None:
    monkeypatch.delenv("PFACTORY_MULTI_TENANT", raising=False)
    _sid_a, sid_b = _two_tenants(service)
    out = asyncio.run(pp.get_session(sid_b, _Request({"X-Tenant-Id": "acme"})))
    assert out["session_id"] == sid_b


# ── durable job_states row ──────────────────────────────────────────────────


@pytest.fixture
def sqlite_store(tmp_path: Path):
    """A JobStateStore on a fresh file-based sqlite schema (see #217 tests)."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'jobstate.db'}"

    async def _create_schema() -> None:
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.run_sync(JobState.metadata.create_all)
        await eng.dispose()

    asyncio.run(_create_schema())
    store = JobStateStore(database_url=url, max_concurrent=4)
    yield store
    store.close()


def test_jobstore_row_defaults_to_the_default_tenant(sqlite_store) -> None:
    sqlite_store.upsert("plan-1", service_status="ingested")
    assert sqlite_store.get("plan-1")["tenant_id"] == DEFAULT_TENANT


def test_jobstore_upsert_stamps_the_tenant(sqlite_store) -> None:
    sqlite_store.upsert("plan-2", service_status="ingested", tenant_id="acme")
    assert sqlite_store.get("plan-2")["tenant_id"] == "acme"


def test_mirror_carries_the_session_tenant_to_the_store(sqlite_store) -> None:
    svc = PlanService(job_store=sqlite_store)
    session = svc.ingest_text(_PLAN, title="Widget", tenant_id="acme")
    assert sqlite_store.get(session.session_id)["tenant_id"] == "acme"


# ── migration: default + backfill ───────────────────────────────────────────


def test_migration_backfills_existing_rows_to_default(tmp_path: Path) -> None:
    """Rows created before the tenant migration land as tenant 'default'."""
    # Deferred: tests.postgres.helpers only resolves when pytest runs from the
    # repo root (the CI invocation); keep this test importable elsewhere.
    from tests.postgres.helpers import alembic_available, run_alembic  # noqa: PLC0415

    if not alembic_available():
        pytest.skip("alembic not importable")
    db = tmp_path / "mig.db"
    env = {"DATABASE_URL": f"sqlite+aiosqlite:///{db}"}

    # Schema as it was BEFORE the tenant migration.
    pre = run_alembic(["upgrade", "f6c9d3a5b2e8"], env=env)
    assert pre.returncode == 0, pre.stderr[-2000:]

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO job_states (schema_version, job_id, service, kind,"
            " lifecycle_state, attempt, created_at, updated_at) VALUES"
            " ('1','plan-old','pfactory','plan','done',1,datetime('now'),datetime('now'))"
        )

    post = run_alembic(["upgrade", "head"], env=env)
    assert post.returncode == 0, post.stderr[-2000:]

    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT job_id, tenant_id FROM job_states").fetchall()
    assert rows == [("plan-old", "default")]


# ── AIFactory propagation (optional additive provenance) ────────────────────


def _plan_and_epic(service: PlanService):
    session = service.ingest_text(_PLAN, title="Widget", tenant_id="acme")
    epic = EpicPlan(
        plan_id=session.plan.plan_id,
        epic_title="Widget service",
        children=[ChildIssue(key="C1", title="API", kind="feature")],
    )
    return session, epic


def test_contract_provenance_carries_a_non_default_tenant(service) -> None:
    session, epic = _plan_and_epic(service)
    contract = assemble_contract(session.plan, epic, tenant_id="acme")
    assert contract["provenance"]["tenant_id"] == "acme"


def test_contract_provenance_omits_the_default_tenant(service) -> None:
    """Single-tenant contracts stay byte-identical (backward compatible)."""
    session, epic = _plan_and_epic(service)
    for tenant in (DEFAULT_TENANT, None):
        contract = assemble_contract(session.plan, epic, tenant_id=tenant)
        assert "tenant_id" not in contract["provenance"]


def test_emit_contract_threads_the_session_tenant(service) -> None:
    session, epic = _plan_and_epic(service)
    session.epic = epic
    out = service.emit_contract(session.session_id, repo="acme/widget", dry_run=True)
    assert out.contract_result["contract"]["provenance"]["tenant_id"] == "acme"
