"""Shared task log mirror; the local task_logs.json remains the writer's copy."""

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Any

from sqlalchemy import delete as sql_delete, func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from factory_common.logsafe import sanitize_log
from server.background.tasks import spawn
from server.database.engine import async_session_factory, engine
from server.database.models import TaskBoardState, TaskLogEntry, TaskLogPhase

logger = logging.getLogger(__name__)

LogOp = tuple[str, str, dict[str, Any]]
_board_pending: dict[str, tuple[str, Callable[[], dict[str, Any]]]] = {}
_board_flushers: set[str] = set()


def enabled() -> bool:
    return engine.dialect.name != "sqlite"


def retention_days() -> int:
    try:
        days = int(os.environ.get("PFACTORY_TASK_LOG_RETENTION_DAYS", "90"))
    except ValueError:
        return 90
    return days if days >= 0 else 90


def _is_postgres(session: AsyncSession) -> bool:
    return session.get_bind().dialect.name == "postgresql"


def _cutoff(session: AsyncSession, days: int) -> Any:
    if _is_postgres(session):
        return func.now() - func.make_interval(0, 0, 0, days)
    return func.strftime("%Y-%m-%d %H:%M:%f", "now", f"-{days} days")


async def write(task_id: str, ops: list[LogOp]) -> None:
    """Apply a writer's queued operations in order in one transaction."""
    try:
        async with async_session_factory() as session:
            insert = postgresql.insert if _is_postgres(session) else sqlite.insert
            for kind, phase, data in ops:
                if kind == "entry":
                    session.add(TaskLogEntry(task_id=task_id, phase=phase, entry=data))
                else:
                    stmt = insert(TaskLogPhase).values(
                        task_id=task_id, phase=phase, **data, updated_at=func.now()
                    )
                    await session.execute(
                        stmt.on_conflict_do_update(
                            index_elements=[TaskLogPhase.task_id, TaskLogPhase.phase],
                            set_={**data, "updated_at": func.now()},
                        )
                    )
            await session.commit()
    except Exception:
        logger.exception(
            "[TaskLogStore] write failed for task %s; dropping batch", sanitize_log(task_id)
        )


async def load(task_id: str) -> dict[str, Any] | None:
    """Return the file-shaped document, or None if no database rows exist."""
    try:
        async with async_session_factory() as session:
            phases = (
                (await session.execute(select(TaskLogPhase).where(TaskLogPhase.task_id == task_id)))
                .scalars()
                .all()
            )
            entries = (
                (
                    await session.execute(
                        select(TaskLogEntry)
                        .where(TaskLogEntry.task_id == task_id)
                        .order_by(TaskLogEntry.id)
                    )
                )
                .scalars()
                .all()
            )
        if not phases and not entries:
            return None
        result: dict[str, Any] = {
            "spec_id": task_id.split(":", 1)[-1],
            "phases": {
                phase: {
                    "phase": phase,
                    "status": "pending",
                    "started_at": None,
                    "completed_at": None,
                    "entries": [],
                }
                for phase in ("planning", "coding", "validation")
            },
        }
        times = []
        for row in phases:
            result["phases"][row.phase].update(
                status=row.status, started_at=row.started_at, completed_at=row.completed_at
            )
            times.append(row.updated_at)
        for entry_row in entries:
            result["phases"][entry_row.phase]["entries"].append(entry_row.entry)
            times.append(entry_row.created_at)
        result["created_at"] = min(times).isoformat()
        result["updated_at"] = max(times).isoformat()
        return result
    except Exception:
        logger.exception(
            "[TaskLogStore] load failed for task %s; using local file", sanitize_log(task_id)
        )
        return None


async def save_board(task_id: str, project_id: str, record: dict[str, Any]) -> None:
    try:
        async with async_session_factory() as session:
            insert = postgresql.insert if _is_postgres(session) else sqlite.insert
            stmt = insert(TaskBoardState).values(
                task_id=task_id, project_id=project_id, record=record, updated_at=func.now()
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[TaskBoardState.task_id],
                    set_={"project_id": project_id, "record": record, "updated_at": func.now()},
                )
            )
            await session.commit()
    except Exception:
        logger.exception("[TaskLogStore] board save failed for %s", sanitize_log(task_id))


async def load_boards(project_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not project_ids:
        return {}
    try:
        async with async_session_factory() as session:
            stmt = select(TaskBoardState).where(TaskBoardState.project_id.in_(project_ids))
            rows = (await session.execute(stmt)).scalars().all()
            return {row.task_id: row.record for row in rows}
    except Exception:
        logger.exception("[TaskLogStore] board load failed")
        return {}


async def load_board(task_id: str) -> dict[str, Any] | None:
    try:
        async with async_session_factory() as session:
            row = await session.get(TaskBoardState, task_id)
            return row.record if row else None
    except Exception:
        logger.exception("[TaskLogStore] board load failed for %s", sanitize_log(task_id))
        return None


def schedule_board(task_id: str, project_id: str, build: Callable[[], dict[str, Any]]) -> None:
    """Keep one writer per task and build the latest snapshot at flush time."""
    if not enabled():
        return
    _board_pending[task_id] = (project_id, build)
    if task_id in _board_flushers:
        return
    _board_flushers.add(task_id)
    spawn(_flush_board(task_id))


async def _flush_board(task_id: str) -> None:
    try:
        await asyncio.sleep(0)
        while task_id in _board_pending:
            project_id, build = _board_pending.pop(task_id)
            try:
                record = build()
                await save_board(task_id, project_id, record)
            except Exception:
                logger.exception("[TaskLogStore] board build failed for %s", sanitize_log(task_id))
    finally:
        _board_flushers.discard(task_id)


async def delete(task_id: str) -> None:
    try:
        async with async_session_factory() as session:
            await session.execute(sql_delete(TaskLogEntry).where(TaskLogEntry.task_id == task_id))
            await session.execute(sql_delete(TaskLogPhase).where(TaskLogPhase.task_id == task_id))
            await session.execute(
                sql_delete(TaskBoardState).where(TaskBoardState.task_id == task_id)
            )
            await session.commit()
    except Exception:
        logger.exception("[TaskLogStore] delete failed for task %s", sanitize_log(task_id))


async def prune() -> None:
    days = retention_days()
    if days == 0:
        return
    try:
        async with async_session_factory() as session:
            cutoff = _cutoff(session, days)
            await session.execute(sql_delete(TaskLogEntry).where(TaskLogEntry.created_at < cutoff))
            await session.execute(sql_delete(TaskLogPhase).where(TaskLogPhase.updated_at < cutoff))
            await session.execute(
                sql_delete(TaskBoardState).where(TaskBoardState.updated_at < cutoff)
            )
            await session.commit()
    except Exception:
        logger.exception("[TaskLogStore] prune failed")
