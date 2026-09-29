"""Cross-replica ownership of running work (#805).

Agent tasks, insights replies, changelog generation and PR reviews each keep
their process or asyncio task in a per-process dict. That dict is still where
the run lives; this module adds a shared lease row next to it, so that any
replica can answer "is it running?", refuse a duplicate start, and ask the
owner to stop it.

The pattern is the #758 emit lease: one conditional statement on the database
clock, an owner string naming the pod, and a TTL that the owner renews on a
heartbeat. A pod that dies stops claiming its runs within one TTL.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import os
import socket
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, ParamSpec, TypeVar

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from factory_common.logsafe import sanitize_log
from server.background.tasks import spawn
from server.database.engine import async_session_factory
from server.database.models import RunLease

logger = logging.getLogger(__name__)

# The pod (hostname) and the process within it; module-level so tests can
# stand in for another replica.
OWNER = f"{socket.gethostname()}:{os.getpid()}"

_TTL_DEFAULT = 60.0
_HEARTBEAT_DEFAULT = 15.0

# One watcher per (kind, key) in this process.
_WATCHERS: dict[tuple[str, str], asyncio.Task[None]] = {}


_P = ParamSpec("_P")
_T = TypeVar("_T")


def _fail_open(
    fallback: Callable[[], _T],
) -> Callable[[Callable[_P, Coroutine[Any, Any, _T]]], Callable[_P, Coroutine[Any, Any, _T]]]:
    """A database error degrades to ``fallback`` with an ERROR, never a crash.

    The same rule as the heartbeat: a lease-table outage must not stop work
    from starting or finishing. It is logged loudly because, with more than
    one replica, the cross-replica guard is off for that call.
    """

    def deco(fn: Callable[_P, Coroutine[Any, Any, _T]]) -> Callable[_P, Coroutine[Any, Any, _T]]:
        @functools.wraps(fn)
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
            try:
                return await fn(*args, **kwargs)
            except Exception:
                logger.exception(
                    "[RunLease] %s failed; continuing without the cross-replica lease",
                    fn.__name__,
                )
                return fallback()

        return wrapper

    return deco


class RunAlreadyActiveError(ValueError):
    """Another run holds the lease for this key, on this or another replica."""


def _seconds(env: str, default: float) -> float:
    try:
        value = float(os.environ.get(env, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


def ttl_seconds() -> float:
    return _seconds("PFACTORY_RUN_LEASE_TTL_SECONDS", _TTL_DEFAULT)


def heartbeat_seconds() -> float:
    """The renew interval, capped at a third of the TTL so a live run never lapses."""
    return min(
        _seconds("PFACTORY_RUN_LEASE_HEARTBEAT_SECONDS", _HEARTBEAT_DEFAULT), ttl_seconds() / 3
    )


def _is_postgres(session: AsyncSession) -> bool:
    return bool(session.get_bind().dialect.name == "postgresql")


def _now(session: AsyncSession) -> Any:
    if _is_postgres(session):
        return func.now()
    # SQLite (dev, tests): millisecond text timestamps that compare in order.
    return func.strftime("%Y-%m-%d %H:%M:%f", "now")


def _until(session: AsyncSession, ttl: float) -> Any:
    if _is_postgres(session):
        return func.now() + func.make_interval(0, 0, 0, 0, 0, 0, ttl)
    return func.strftime("%Y-%m-%d %H:%M:%f", "now", f"+{ttl} seconds")


@_fail_open(lambda: True)
async def acquire(kind: str, key: str, *, steal: bool = False, owner: str | None = None) -> bool:
    """Take the lease for ``(kind, key)``; True when this owner now holds it.

    Succeeds on a free key, an expired lease, or one this owner already holds
    (a same-pod restart; each service's local dict still blocks a real
    duplicate). ``steal`` takes a live lease from another owner, whose watcher
    then stops its run.
    """
    me = owner or OWNER
    async with async_session_factory() as session:
        insert = postgresql.insert if _is_postgres(session) else sqlite.insert
        ins = insert(RunLease).values(
            kind=kind,
            key=key,
            owner=me,
            lease_until=_until(session, ttl_seconds()),
            stop_requested=False,
        )
        free = (RunLease.lease_until < _now(session)) | (RunLease.owner == me)
        upsert = ins.on_conflict_do_update(
            index_elements=[RunLease.kind, RunLease.key],
            set_={
                "owner": me,
                "lease_until": ins.excluded.lease_until,
                "stop_requested": False,
            },
            where=None if steal else free,
        ).returning(RunLease.owner)
        got = (await session.execute(upsert)).scalar_one_or_none()
        await session.commit()
    return bool(got == me)


@_fail_open(lambda: None)
async def release(kind: str, key: str, *, owner: str | None = None) -> None:
    """Drop the lease, but only if this owner still holds it."""
    async with async_session_factory() as session:
        await session.execute(
            delete(RunLease).where(
                RunLease.kind == kind, RunLease.key == key, RunLease.owner == (owner or OWNER)
            )
        )
        await session.commit()


@_fail_open(lambda: False)
async def is_active(kind: str, key: str) -> bool:
    """A live (unexpired) lease exists for ``(kind, key)``, whoever owns it."""
    async with async_session_factory() as session:
        row = await session.execute(
            select(RunLease.owner).where(
                RunLease.kind == kind, RunLease.key == key, RunLease.lease_until > _now(session)
            )
        )
        return row.scalar_one_or_none() is not None


def _no_keys() -> list[str]:
    return []


@_fail_open(_no_keys)
async def active_keys(kind: str) -> list[str]:
    async with async_session_factory() as session:
        rows = await session.execute(
            select(RunLease.key).where(RunLease.kind == kind, RunLease.lease_until > _now(session))
        )
        return sorted(rows.scalars())


@_fail_open(lambda: False)
async def request_stop(kind: str, key: str) -> bool:
    """Ask the owner of a live lease to stop its run; False when none is live."""
    async with async_session_factory() as session:
        row = await session.execute(
            update(RunLease)
            .where(RunLease.kind == kind, RunLease.key == key, RunLease.lease_until > _now(session))
            .values(stop_requested=True)
            .returning(RunLease.owner)
        )
        owner = row.scalar_one_or_none()
        await session.commit()
    if owner is not None:
        logger.info(
            "[RunLease] stop requested for %s %s (owner %s)",
            sanitize_log(kind),
            sanitize_log(key),
            sanitize_log(owner),
        )
    return owner is not None


async def stop_and_wait(kind: str, key: str, wait_seconds: float | None = None) -> bool:
    """Request a stop and wait for the lease to go; True once it is free."""
    await request_stop(kind, key)
    deadline = time.monotonic() + (
        wait_seconds if wait_seconds is not None else heartbeat_seconds() + 5
    )
    while time.monotonic() < deadline:
        if not await is_active(kind, key):
            return True
        await asyncio.sleep(min(1.0, heartbeat_seconds()))
    return not await is_active(kind, key)


async def _renew(kind: str, key: str, owner: str) -> bool | None:
    """Extend ``owner``'s lease; its ``stop_requested``, or None if it was lost."""
    async with async_session_factory() as session:
        row = await session.execute(
            update(RunLease)
            .where(RunLease.kind == kind, RunLease.key == key, RunLease.owner == owner)
            .values(lease_until=_until(session, ttl_seconds()))
            .returning(RunLease.stop_requested)
        )
        got = row.scalar_one_or_none()
        await session.commit()
    return None if got is None else bool(got)


async def _call(stop: Callable[[], Any]) -> None:
    result = stop()
    if inspect.isawaitable(result):
        await result


async def _watch(
    kind: str,
    key: str,
    owner: str,
    alive: Callable[[], bool],
    stop: Callable[[], Awaitable[Any] | Any],
) -> None:
    tick = min(1.0, heartbeat_seconds())
    next_renew = time.monotonic() + heartbeat_seconds()
    stopping = False
    try:
        while True:
            await asyncio.sleep(tick)
            if not alive():
                await release(kind, key, owner=owner)
                return
            if time.monotonic() < next_renew:
                continue
            next_renew = time.monotonic() + heartbeat_seconds()
            try:
                state = await _renew(kind, key, owner)
            except Exception:
                # A DB outage must not kill work -- but it must not be silent:
                # past the TTL another replica may start a duplicate.
                logger.exception(
                    "[RunLease] could not renew the lease for %s %s; the run continues, "
                    "and another replica may start a duplicate once it expires",
                    sanitize_log(kind),
                    sanitize_log(key),
                )
                continue
            if state is None:
                logger.warning(
                    "[RunLease] lost the lease for %s %s (taken over or expired); stopping",
                    sanitize_log(kind),
                    sanitize_log(key),
                )
                await _call(stop)
                return
            if state and not stopping:
                stopping = True
                logger.info(
                    "[RunLease] stop requested by another replica for %s %s",
                    sanitize_log(kind),
                    sanitize_log(key),
                )
                await _call(stop)
    finally:
        _WATCHERS.pop((kind, key), None)


def watch(
    kind: str, key: str, *, alive: Callable[[], bool], stop: Callable[[], Awaitable[Any] | Any]
) -> None:
    """Keep this process's lease on ``(kind, key)`` for as long as ``alive()``.

    ``alive`` tests the service's own dict, so every way a run ends (normal
    exit, failure, a local stop, a failover respawn keeping the key) is seen in
    one place. ``stop`` is the service's own stop, called when another replica
    requests one or the lease is lost.
    """
    existing = _WATCHERS.get((kind, key))
    if existing is not None and not existing.done():
        return
    # The owner is bound now: the watcher renews and releases as the pod that
    # started the run.
    _WATCHERS[(kind, key)] = spawn(
        _watch(kind, key, OWNER, alive, stop), name=f"run-lease:{kind}:{key}"
    )
