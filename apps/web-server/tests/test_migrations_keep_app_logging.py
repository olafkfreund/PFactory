"""An in-process migration must not take over the app's logging (Factory#923).

`alembic.ini` carries `[logger_root] level = WARN, handlers = console`, and
`env.py` called `fileConfig` unconditionally. `disable_existing_loggers=False`
stops existing loggers being *disabled*; it does not stop `fileConfig`
reconfiguring the ROOT logger. App loggers own no handler of their own
(`setup_logging` configures the root), so from the first in-process
`alembic upgrade` every app INFO record propagated to a WARN root and was
dropped -- for the life of the process, not just during the migration.

Only ERROR cleared the bar, which is what made this worth a test rather than a
one-line fix: a pod whose startup hooks ran perfectly logged *identically* to
one whose hooks never executed. That ambiguity is exactly what AIFactory#844 was about,
and nothing asserted on log output after startup, so it went unnoticed for as
long as MIGRATIONS_AUTO_APPLY has defaulted true.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest


@pytest.fixture
def app_owned_root_logger() -> logging.Logger:
    """Stand in for setup_logging(): the app clears and owns the root logger."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    root.handlers.clear()
    root.setLevel(logging.INFO)
    root.addHandler(logging.StreamHandler(sys.stdout))
    try:
        yield root
    finally:
        root.handlers.clear()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)


def test_in_process_migration_leaves_app_logging_intact(
    app_owned_root_logger: logging.Logger, tmp_path: Path, monkeypatch
) -> None:
    """Run a real `alembic upgrade head` the way init_db() does, on sqlite."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")

    # would snapshot the wrong URL into the module-level settings.
    from server.database.engine import _alembic_upgrade_head_sync  # noqa: PLC0415

    _alembic_upgrade_head_sync()

    assert app_owned_root_logger.level == logging.INFO, (
        "alembic reset the root logger's level; every app INFO record is now "
        "dropped for the life of this process"
    )
    assert app_owned_root_logger.handlers, "alembic replaced the app's handlers"


def test_alembic_ini_still_configures_a_standalone_run() -> None:
    """The CLI path must keep working -- the guard is 'already configured', not 'never'.

    `alembic upgrade` run as its own process has no handlers on the root, so
    alembic.ini still applies there. This asserts the ini keeps the config that
    path depends on, so a future 'just delete [logger_root]' fix does not
    silently make standalone runs mute.
    """
    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    assert "[logger_root]" in ini.read_text(encoding="utf-8")


def test_env_py_guards_the_filecondig_call() -> None:
    """The guard itself, so removing it fails here rather than in production."""
    env_py = Path(__file__).resolve().parents[1] / "server" / "database" / "alembic" / "env.py"
    source = env_py.read_text(encoding="utf-8")
    assert "not logging.getLogger().handlers" in source, (
        "env.py must only call fileConfig when nothing else has configured logging; see Factory#923"
    )
    assert "disable_existing_loggers=False" in source
