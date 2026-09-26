"""The MCP task-contract tool answers with the contract it builds, with a store (#779).

With the shared session store, ``PlanService.emit_contract`` loads a fresh copy
of the session, builds the contract onto that copy and saves it. The tool used
to read ``contract_result`` from the copy it held before the call, which never
changes, so it answered "task contract could not be built" every time.

Migrations run on SQLite, so the test uses its own tmp SQLite file and runs in
every CI job, not only the Postgres one.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from alembic import command  # noqa: E402

from plan import service as svc  # noqa: E402
from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.review.models import LensScore, PlanReview  # noqa: E402
from server.database import engine as engine_mod  # noqa: E402
from server.jobstore import PlanSessionStore  # noqa: E402
from server.routes import mcp_rpc  # noqa: E402

_PLAN = """# Widget service
A FastAPI service tested with pytest.
## Acceptance Criteria
- exposes a widget API
"""


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[svc.PlanService]:
    """SERVICE backed by a migrated tmp SQLite store."""
    url = f"sqlite+aiosqlite:///{tmp_path}/pf.db"
    # Both: alembic's env.py reads the environment, engine.py its import-time copy.
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(engine_mod, "DATABASE_URL", url)
    command.upgrade(engine_mod._alembic_config(), "head")  # type: ignore[no-untyped-call]
    store = PlanSessionStore(database_url=url)
    service = svc.PlanService(persist=False, session_store=store)
    monkeypatch.setattr(svc, "SERVICE", service, raising=False)
    yield service
    store.close()


def _processed_session(service: svc.PlanService) -> str:
    """A session with a governed epic and a passing review, saved to the store."""
    session = service.ingest_text(_PLAN, title="Widget service")
    session.epic = EpicPlan(
        plan_id=session.plan.plan_id,
        epic_title="Widget service",
        children=[
            ChildIssue(
                key="C1",
                title="API",
                kind="feature",
                acceptance_criteria=["exposes a widget API"],
            ),
        ],
    )
    session.review = PlanReview(
        plan_id=session.plan.plan_id,
        lenses=[LensScore(lens="architecture", score=0.95)],
        aggregate_score=0.95,
        gates_passed=True,
    )
    service._save(session)
    sid: str = session.session_id
    return sid


def test_task_contract_tool_returns_the_contract_it_built(service: svc.PlanService) -> None:
    sid = _processed_session(service)

    out = mcp_rpc._tool_get_task_contract({"session_id": sid})

    assert out["ok"] and out["dry_run"]
    assert out["contract"]["contract_version"] == "2"
    stored = service.get(sid).contract_result
    assert stored is not None and stored["contract"] == out["contract"]


def test_read_tools_find_a_session_another_replica_wrote(service: svc.PlanService) -> None:
    """The MCP read tools read through the store, not this pod's cache (#779).

    A second PlanService on the same store is another replica: its session is
    in the table but was never in this SERVICE's per-process dict.
    """
    other = svc.PlanService(persist=False, session_store=service._session_store)
    sid = _processed_session(other)
    session = other.get(sid)
    session.emitted_issue_number = 4242
    other._save(session)
    assert sid not in service._sessions

    by_id = mcp_rpc._tool_get_decomposition({"session_id": sid})
    by_issue = mcp_rpc._tool_get_decomposition({"issue_number": 4242})

    assert by_id["epic_title"] == by_issue["epic_title"] == "Widget service"
