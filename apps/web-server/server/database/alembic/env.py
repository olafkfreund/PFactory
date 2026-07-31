"""Alembic migration environment.

Honors ``DATABASE_URL`` env var (falls back to the SQLite default in
alembic.ini), uses async SQLAlchemy 2.x via ``async_engine_from_config``
so migrations work with the same asyncpg driver our runtime uses.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# --- Make the web-server source importable for Base.metadata --------------
# env.py lives at apps/web-server/server/database/alembic/env.py
# We need apps/web-server on sys.path so `from server.database.models ...`
# resolves regardless of where alembic is invoked from.
_WEB_SERVER = Path(__file__).resolve().parents[3]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.database.models import Base  # noqa: E402

# Import the RFC-0016 job-state model so its table attaches to Base.metadata
# (the migration is hand-written, but autogenerate / compare_* and any
# create_all need the model registered). Side-effect import only.
from server.jobstore import models as _jobstore_models  # noqa: E402,F401

config = context.config

# DATABASE_URL env var overrides the alembic.ini fallback. This is how
# CI and production point migrations at Postgres without editing the ini.
_env_url = os.environ.get("DATABASE_URL")
if _env_url:
    config.set_main_option("sqlalchemy.url", _env_url)

if config.config_file_name is not None and not logging.getLogger().handlers:
    # Only configure logging when NOTHING else has (i.e. `alembic upgrade` run
    # as its own process, where root has no handlers yet).
    #
    # Factory#923 / AIFactory#844: disable_existing_loggers=False is necessary
    # but NOT sufficient. It preserves existing logger OBJECTS, but fileConfig
    # still rewrites the ROOT logger from alembic.ini's [logger_root] --
    # level=WARN, handlers=console. Since server.* loggers own no handler and
    # propagate to root, MIGRATIONS_AUTO_APPLY=true meant the web-server lost
    # every INFO record the moment it booted, for the life of the process.
    # Only ERROR cleared the bar, so a component that ran perfectly logged
    # identically to one that never started.
    #
    # Guarding on root having no handlers keeps standalone `alembic upgrade`
    # logging exactly as before, and makes the in-process path inherit the
    # app's handlers instead of destroying them.
    #
    # disable_existing_loggers=False — fileConfig defaults to True, which
    # silences every logger that existed when this module imports. That's
    # fine for `alembic upgrade` invoked as its own process, but env.py is
    # also imported when migrations run inside the long-running web-server
    # (or under pytest), where killing app/caplog loggers is a real bug.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — with a live DB connection."""
    asyncio.run(run_async_migrations())


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL to stdout without a DB."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
