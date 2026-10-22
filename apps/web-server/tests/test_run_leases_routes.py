"""#805: the routes see, guard and stop a run that another replica owns.

Each test plants a lease owned by "the other pod" and drives this pod's
routes. This pod's in-memory registries are empty, which is exactly the view
a replica that did not start the run has.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
# routes/changelog imports client_errors, which lives in apps/backend.
for _p in (_WEB_SERVER, _WEB_SERVER.parent / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from server.database.models import Base, RunLease  # noqa: E402
from server.routes import (  # noqa: E402
    changelog as changelog_routes,
    execution as execution_routes,
    github as github_routes,
    insights as insights_routes,
)
from server.services import run_leases  # noqa: E402

OTHER_POD = "pod-b:1"
PROJECT = "proj"
TASK_ID = f"{PROJECT}:001-demo"


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[async_sessionmaker]:  # type: ignore[type-arg]
    nonce = secrets.token_hex(8)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:lease805r-{nonce}?mode=memory&cache=shared&uri=true"
    )

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(run_leases, "async_session_factory", session_local)
    monkeypatch.setenv("PFACTORY_RUN_LEASE_TTL_SECONDS", "60")
    monkeypatch.setenv("PFACTORY_RUN_LEASE_HEARTBEAT_SECONDS", "0.3")
    yield session_local
    asyncio.run(engine.dispose())


def _held_by_other_pod(monkeypatch: pytest.MonkeyPatch, kind: str, key: str) -> None:
    monkeypatch.setattr(run_leases, "OWNER", OTHER_POD)
    assert asyncio.run(run_leases.acquire(kind, key)) is True
    monkeypatch.setattr(run_leases, "OWNER", "pod-a:1")


def _stop_requested(db: async_sessionmaker, kind: str, key: str) -> bool:  # type: ignore[type-arg]
    async def go() -> bool:
        async with db() as session:
            row = (
                await session.execute(
                    select(RunLease.stop_requested).where(
                        RunLease.kind == kind, RunLease.key == key
                    )
                )
            ).scalar_one()
            return bool(row)

    return asyncio.run(go())


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    spec_dir = tmp_path / ".pfactory" / "specs" / "001-demo"
    spec_dir.mkdir(parents=True)
    (spec_dir / "test_plan.json").write_text(json.dumps({"phases": [], "status": "in_progress"}))
    monkeypatch.setattr(execution_routes, "resolve_project_path", lambda _pid: tmp_path)
    monkeypatch.setattr(github_routes, "_resolve_project_path", lambda _pid: tmp_path)
    monkeypatch.setattr(changelog_routes, "resolve_project_path", lambda _pid: tmp_path)
    monkeypatch.setattr(insights_routes, "_get_project_path", lambda _pid: tmp_path)
    return tmp_path


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(execution_routes.router, prefix="/api/tasks")
    app.include_router(github_routes.project_router, prefix="/api/projects/{projectId}/github")
    app.include_router(changelog_routes.router, prefix="/api/projects/{projectId}/changelog")
    app.include_router(insights_routes.router, prefix="/api/projects/{projectId}/insights")
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.usefixtures("project", "db")
def test_a_task_on_another_pod_is_reported_running(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _held_by_other_pod(monkeypatch, "task", TASK_ID)
    assert TASK_ID in client.get("/api/tasks/running").json()["tasks"]
    assert client.get(f"/api/tasks/{TASK_ID}/status").json()["is_running"] is True
    assert client.get(f"/api/tasks/{TASK_ID}/running").json()["is_running"] is True


@pytest.mark.usefixtures("project", "db")
def test_starting_a_task_another_pod_runs_is_a_conflict(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _held_by_other_pod(monkeypatch, "task", TASK_ID)
    spawned: list[Any] = []

    async def no_spawn(*args: Any, **_kwargs: Any) -> None:
        spawned.append(args)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_spawn)
    r = client.post(f"/api/tasks/{TASK_ID}/start", json={})
    assert r.status_code == 409, r.text
    assert spawned == []


@pytest.mark.usefixtures("project")
def test_stopping_a_task_another_pod_runs_requests_the_stop(
    db: async_sessionmaker,  # type: ignore[type-arg]
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _held_by_other_pod(monkeypatch, "task", TASK_ID)
    r = client.post(f"/api/tasks/{TASK_ID}/stop")
    assert r.status_code == 200, r.text
    assert "requested" in r.json()["message"].lower()
    assert _stop_requested(db, "task", TASK_ID) is True


@pytest.mark.usefixtures("project")
def test_recover_refuses_while_another_pod_still_runs_it(
    db: async_sessionmaker,  # type: ignore[type-arg]
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _held_by_other_pod(monkeypatch, "task", TASK_ID)
    r = client.post(f"/api/tasks/{TASK_ID}/recover", json={"auto_restart": True})
    assert r.status_code == 409, r.text
    assert _stop_requested(db, "task", TASK_ID) is True


@pytest.mark.usefixtures("project")
def test_a_pr_review_on_another_pod_blocks_a_second_and_can_be_cancelled(
    db: async_sessionmaker,  # type: ignore[type-arg]
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _held_by_other_pod(monkeypatch, "pr_review", f"{PROJECT}:7")
    r = client.post(f"/api/projects/{PROJECT}/github/prs/7/review")
    assert r.status_code == 409, r.text
    r = client.post(f"/api/projects/{PROJECT}/github/prs/7/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["requested"] is True
    assert _stop_requested(db, "pr_review", f"{PROJECT}:7") is True


@pytest.mark.usefixtures("project", "db")
def test_a_changelog_on_another_pod_blocks_a_second(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _held_by_other_pod(monkeypatch, "changelog", PROJECT)
    r = client.post(
        f"/api/projects/{PROJECT}/changelog/generate",
        json={
            "sourceMode": "tasks",
            "version": "1.0.0",
            "date": "2026-09-29",
            "format": "simple-list",
            "audience": "technical",
        },
    )
    assert r.json()["success"] is False
    assert "already in progress" in r.json()["error"]


@pytest.mark.usefixtures("project")
def test_stopping_insights_another_pod_runs_requests_the_stop(
    db: async_sessionmaker,  # type: ignore[type-arg]
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _held_by_other_pod(monkeypatch, "insights", PROJECT)
    r = client.post(f"/api/projects/{PROJECT}/insights/stop")
    assert r.status_code == 200, r.text
    assert r.json()["cancelled"] is True
    assert _stop_requested(db, "insights", PROJECT) is True
