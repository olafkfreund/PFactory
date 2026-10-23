"""Shared task log mirror; the local task_logs.json remains the writer's copy."""

import logging
import os
from typing import Any

from sqlalchemy import delete as sql_delete, func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from factory_common.logsafe import sanitize_log
from server.database.engine import async_session_factory, engine
from server.database.models import TaskLogEntry, TaskLogPhase

logger = logging.getLogger(__name__)

LogOp = tuple[str, str, dict[str, Any]]


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


async def delete(task_id: str) -> None:
    try:
        async with async_session_factory() as session:
            await session.execute(sql_delete(TaskLogEntry).where(TaskLogEntry.task_id == task_id))
            await session.execute(sql_delete(TaskLogPhase).where(TaskLogPhase.task_id == task_id))
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
            await session.commit()
    except Exception:
        logger.exception("[TaskLogStore] prune failed")
