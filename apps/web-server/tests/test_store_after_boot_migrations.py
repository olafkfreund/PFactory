"""A pod that migrates on boot uses the shared session store without a restart (#774).

The route modules are imported while the app is built, and that import
constructs ``SERVICE``, so it resolved its session store before the lifespan
hook ran ``init_db()``. On the first boot after a store migration the table was
missing at that moment: the store fell back to per-process sessions for the
life of the pod, and with ``PFACTORY_REQUIRE_SHARED_STORE=1`` the pod crashed at
import instead of refusing after migrating.

Migrations run on SQLite, so each test uses its own tmp SQLite file.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from alembic import command  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from plan import service as svc  # noqa: E402
from server.config import get_settings  # noqa: E402
from server.database import engine as engine_mod  # noqa: E402
from server.main import create_app  # noqa: E402

_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice.
## Acceptance Criteria
- User can request a refund through the API
- The endpoint requires a valid JWT
"""

_ATTACHED = "attached to the shared store after boot migrations (#774)"


class _ListHandler(logging.Handler):
    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.append(record)


@contextmanager
def captured() -> Iterator[list[logging.LogRecord]]:
    """This module's records, whatever create_app() does to the root logger.

    Not ``caplog``: create_app() reconfigures the root logger, which caplog
    reads through (see test_tracing.captured for the same reasoning).
    """
    records: list[logging.LogRecord] = []
    handler = _ListHandler(records)
    was_disabled, was_level = svc.logger.disabled, svc.logger.level
    svc.logger.disabled = False
    svc.logger.setLevel(logging.INFO)
    svc.logger.addHandler(handler)
    try:
        yield records
    finally:
        svc.logger.removeHandler(handler)
        svc.logger.disabled = was_disabled
        svc.logger.setLevel(was_level)


@pytest.fixture
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """An empty SQLite DB, with the service's module state reset around it."""
    url = f"sqlite+aiosqlite:///{tmp_path}/pf.db"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("PFACTORY_PLAN_PERSIST", "1")
    monkeypatch.setenv("PFACTORY_PLAN_STORE_DIR", str(tmp_path / "sessions"))
    monkeypatch.delenv("PFACTORY_REPLICA_COUNT", raising=False)
    monkeypatch.delenv("PFACTORY_REQUIRE_SHARED_STORE", raising=False)
    stores: dict[str, object] = {}
    monkeypatch.setattr(svc, "_SESSION_STORE_CACHE", stores)
    monkeypatch.setattr(svc, "_SESSION_STORE_UNAVAILABLE", set())
    monkeypatch.setattr(svc, "_JOB_STORE_CACHE", {})
    monkeypatch.setattr(svc, "_DEFER_REPLICA_GUARD", False, raising=False)
    # setitem then delitem: the undo then removes the SERVICE a test builds.
    module_dict: dict[str, object] = vars(svc)
    monkeypatch.setitem(module_dict, "SERVICE", None)
    monkeypatch.delitem(module_dict, "SERVICE")
    # engine.py snapshots DATABASE_URL at import; point the migrations here.
    monkeypatch.setattr(engine_mod, "DATABASE_URL", url)
    yield url
    for store in stores.values():
        store.close()  # type: ignore[attr-defined]


def _service() -> svc.PlanService:
    """The lazily built SERVICE singleton, typed (PEP 562 __getattr__ is untyped)."""
    service = getattr(svc, "SERVICE")  # noqa: B009 - resolved by the module __getattr__
    assert isinstance(service, svc.PlanService)
    return service


def _migrate() -> None:
    command.upgrade(engine_mod._alembic_config(), "head")  # type: ignore[no-untyped-call]


def _seed_on_disk_session() -> str:
    """A session written to the PVC by a pod that had no store."""
    legacy = svc.PlanService(persist=True, session_store=None)
    return legacy.ingest_text(_PLAN, title="Refund API").session_id


def test_attach_after_migrating_uses_the_store_and_imports(db_url: str) -> None:
    sid = _seed_on_disk_session()
    service = _service()  # built before the table exists, as at app import
    assert service._session_store is None
    assert db_url in svc._SESSION_STORE_UNAVAILABLE

    _migrate()

    with captured() as records:
        assert svc.attach_session_store_after_migrations() is True
    store = service._session_store
    assert store is not None
    assert sid in store.session_ids(), "the on-disk session was not imported"
    assert any(_ATTACHED in r.getMessage() for r in records)

    assert svc.attach_session_store_after_migrations() is True
    assert service._session_store is store, "a second call replaced the store"


@pytest.mark.usefixtures("db_url")
def test_the_replica_guard_waits_for_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PFACTORY_REPLICA_COUNT", "2")
    monkeypatch.setenv("PFACTORY_REQUIRE_SHARED_STORE", "1")
    svc.defer_replica_guard()

    service = _service()  # no table yet: must not raise at import
    assert service._session_store is None

    with pytest.raises(RuntimeError, match="PER-PROCESS"):
        svc.attach_session_store_after_migrations()

    _migrate()
    assert svc.attach_session_store_after_migrations() is True
    assert service._session_store is not None


def test_app_boot_attaches_the_store_after_its_own_migrations(
    db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "MIGRATIONS_AUTO_APPLY", True)
    monkeypatch.setattr(settings, "PROJECTS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "BACKEND_PATH", str(tmp_path / "no-backend"))
    monkeypatch.setattr(settings, "DISABLE_AUTH", False)
    monkeypatch.setattr(settings, "LIVENESS_SWEEP_ENABLED", False)
    monkeypatch.setattr(engine_mod, "engine", create_async_engine(db_url))

    with captured() as records:
        app = create_app()
        # Built by the route import, unless an earlier test already imported
        # the routes; either way it exists before the lifespan migrates.
        service = _service()
        assert service._session_store is None
        with TestClient(app):
            store = service._session_store

    assert store is not None, "the pod kept per-process sessions after migrating"
    assert any(_ATTACHED in r.getMessage() for r in records)
