"""
CLI Package
===========

Exposes two CLI surfaces:

1. **Existing PFactory build CLI** (``main``) — argument-parser-based runner
   used by ``run.py``, the web-server, and direct invocations like
   ``python cli/main.py``.

2. **pfactory CLI** (``pfactory_main``) — click-based subcommand group
   with ``init`` and ``migrate`` subcommands.  Accessible via::

       python -m cli init
       python -m cli migrate v0_1_catalog

   The ``__main__.py`` module routes ``python -m cli`` to ``pfactory_main``.

Module structure:
- main.py:               Argument parsing and command routing (legacy build CLI)
- pfactory_init.py:      `init` subcommand — .pfactory.yml scaffolder
- pfactory_migrate.py:   `migrate` subcommand — v0.1 workspace migration
- batch_commands.py:     Batch build execution
- build_commands.py:     Build execution and follow-up tasks
- workspace_commands.py: Workspace management (merge, review, discard)
- qa_commands.py:        QA validation commands
- utils.py:              Shared utilities and configuration

Task 15 / #31 commit 4.
"""

from __future__ import annotations

import click

from .pfactory_init import init_command
from .pfactory_migrate import migrate_command


def _get_legacy_main():  # type: ignore[return]
    """Lazy-import the legacy build CLI main to avoid import errors
    when qa_loop / deleted modules are not installed.
    """
    from .main import main

    return main


@click.group()
def pfactory_main() -> None:
    """PFactory CLI — scaffold and migrate PFactory configurations."""


pfactory_main.add_command(init_command, name="init")
pfactory_main.add_command(migrate_command, name="migrate")


def main():  # type: ignore[return]
    """Legacy build CLI — thin shim that defers the import."""
    return _get_legacy_main()()


__all__ = [
    "main",  # legacy build CLI (lazy-import)
    "pfactory_main",  # click group: init + migrate
]
