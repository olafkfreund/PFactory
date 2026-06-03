"""
Async SQLAlchemy engine and session management.

Default backend is SQLite (aiosqlite) for local dev; production uses
Postgres (asyncpg) via the ``DATABASE_URL`` env var. The engine picks
the driver from the URL scheme, and the SQLite-specific WAL pragma
listener is only registered when the SQLite dialect is in use.
"""

import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base
from ..paths import get_data_dir

logger = logging.getLogger(__name__)

# SQLite default location: ~/.pfactory/data.db. These constants are kept
# at module level for backwards compatibility with init_db() and any
# external code that imports DATABASE_PATH for diagnostics.
DATABASE_DIR = get_data_dir()
DATABASE_PATH = DATABASE_DIR / "data.db"
_DEFAULT_SQLITE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH}"


def _resolve_database_url() -> str:
    """Resolve the active DATABASE_URL.

    Priority:
      1. ``DATABASE_URL`` env var — production sets this to
         ``postgresql+asyncpg://...``.
      2. SQLite fallback at ``~/.pfactory/data.db`` for local dev.

    Empty / whitespace-only env values are treated as unset.
    """
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or _DEFAULT_SQLITE_URL


def _connect_args_for(url: str) -> dict:
    """Return driver-specific connect args.

    ``check_same_thread=False`` is a SQLite-only knob that the asyncpg
    driver rejects. Postgres needs no extra connect args here.
    """
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


DATABASE_URL = _resolve_database_url()

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    connect_args=_connect_args_for(DATABASE_URL),
)

logger.info(
    "Database engine bound: dialect=%s driver=%s",
    engine.dialect.name,
    engine.dialect.driver,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def _enable_wal_mode(dbapi_connection, connection_record):
    """Enable WAL mode on every new SQLite connection.

    WAL (Write-Ahead Logging) mode allows concurrent readers and a
    single writer, which is essential for a web server handling
    multiple simultaneous requests.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


# WAL is a SQLite-only feature. Register the listener only when the engine's
# dialect is SQLite — Postgres has its own concurrency model and doesn't
# need (or accept) the journal-mode PRAGMA.
if engine.dialect.name == "sqlite":
    event.listen(engine.sync_engine, "connect", _enable_wal_mode)


_ALEMBIC_INI_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"


def _alembic_config():
    """Build an Alembic Config pointing at our alembic.ini + current DATABASE_URL.

    Imported lazily so alembic isn't a hard dependency for any code path
    that doesn't touch init_db(). We also rewrite ``script_location`` to an
    absolute path because Alembic resolves it relative to cwd, and init_db()
    can be invoked from any cwd (FastAPI app uses different working dirs
    than CLI / tests).
    """
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI_PATH))
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    cfg.set_main_option(
        "script_location",
        str(_ALEMBIC_INI_PATH.parent / "server" / "database" / "alembic"),
    )
    return cfg


def _alembic_upgrade_head_sync() -> None:
    """Run `alembic upgrade head` programmatically. Synchronous (DDL transaction)."""
    from alembic import command

    command.upgrade(_alembic_config(), "head")


def _verify_schema_at_head_sync(sync_conn) -> None:
    """Raise RuntimeError if the DB schema isn't at the current head revision.

    Called when MIGRATIONS_AUTO_APPLY=false (out-of-band Helm Job mode).
    """
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    head_rev = script.get_current_head()

    ctx = MigrationContext.configure(sync_conn)
    current_rev = ctx.get_current_revision()

    if current_rev != head_rev:
        raise RuntimeError(
            f"Database schema is at {current_rev!r}, expected head {head_rev!r}. "
            "Run `alembic upgrade head` out-of-band (Helm Job), or set "
            "MIGRATIONS_AUTO_APPLY=true to apply on boot."
        )


async def init_db() -> None:
    """Initialize the database.

    Schema lifecycle depends on the ``MIGRATIONS_AUTO_APPLY`` setting:
      - true  → run `alembic upgrade head` (default; suits local dev)
      - false → verify schema is at head and fail fast otherwise
                (suits K8s deployments where a Helm Job runs Alembic
                before the app pods start, so the app role can lack
                DDL privileges)

    Then seeds the default user when ``DISABLE_AUTH`` is on.
    Safe to call multiple times — Alembic upgrades are idempotent.
    """
    # Lazy import to avoid a circular dependency between engine.py and config.py
    # at module load (config imports settings which imports paths which...).
    from ..config import get_settings
    settings = get_settings()

    # SQLite default needs its parent directory created; Postgres URLs don't.
    if engine.dialect.name == "sqlite":
        DATABASE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"Initializing SQLite database at {DATABASE_PATH}")
    else:
        logger.info(
            "Initializing database (dialect=%s, driver=%s)",
            engine.dialect.name, engine.dialect.driver,
        )

    if settings.MIGRATIONS_AUTO_APPLY:
        logger.info("MIGRATIONS_AUTO_APPLY=true — running `alembic upgrade head`")
        # Alembic uses a sync engine internally; run via to_thread so we
        # don't block the asyncio loop on the DDL transactions.
        import asyncio
        await asyncio.to_thread(_alembic_upgrade_head_sync)
    else:
        logger.info(
            "MIGRATIONS_AUTO_APPLY=false — verifying schema is at head "
            "(expecting an out-of-band Helm Job to have run migrations)"
        )
        async with engine.connect() as conn:
            await conn.run_sync(_verify_schema_at_head_sync)

    # SQLite-specific journal-mode log (Postgres has no PRAGMA equivalent).
    if engine.dialect.name == "sqlite":
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            mode = result.scalar()
            logger.info(f"SQLite journal mode: {mode}")

    # Ensure a default user exists when auth is disabled (settings already
    # resolved above for the autoApply gate).
    if settings.DISABLE_AUTH:
        from .models import User
        async with async_session_factory() as session:
            from sqlalchemy import select
            existing = await session.execute(
                select(User).where(User.id == "default")
            )
            if not existing.scalar_one_or_none():
                session.add(User(
                    id="default",
                    email="default@localhost",
                    name="Default User",
                    password_hash="disabled",
                    role="admin",
                    is_active=True,
                ))
                await session.commit()
                logger.info("Created default user for auth-disabled mode")

    logger.info("Database initialization complete")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session.

    Usage in route handlers::

        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Item))
            return result.scalars().all()

    The session is automatically closed when the request finishes.
    Commits must be done explicitly within the route handler.
    """
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
