"""pfactory migrate — v0.1 workspace migration CLI.

Usage::

    # Migrate all v0.1 workspaces under the default workspace root
    python -m cli migrate v0_1_catalog

    # Specify workspace root explicitly
    python -m cli migrate v0_1_catalog --workspace ~/.pfactory/workspaces

    # Dry-run: print the migration plan without writing
    python -m cli migrate v0_1_catalog --dry-run

This command:

1. Walks ``<workspace>/<project_id>/specs/<spec_id>/`` for each spec.
2. For each spec, calls ``tests_catalog.migration.migrate_v0_1_workspace``
   to synthesise ``CatalogEntry`` objects from the ``tests/test_*.py`` files.
3. Groups entries by project repo root (resolved from ``context/source.json``
   if present; otherwise uses the project_id directory).
4. Writes one ``.pfactory/tests-catalog.json`` per repo root, consolidating
   all per-spec entries.
5. In ``--dry-run`` mode, prints the plan without writing.

Task 15 / #31 commit 3.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_z() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json_safe(path: Path) -> Any:
    """Return parsed JSON or None."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_repo_root_for_project(project_dir: Path) -> Path:
    """Attempt to resolve the project's repo root.

    PFactory stores ``context/source.json`` under each spec directory with
    a ``repo_path`` or ``repo_root`` key.  If found, use that.  Otherwise
    fall back to ``project_dir``.

    We look at the first spec we can find for the project rather than
    every spec (any spec in the same project shares the same repo root).
    """
    specs_dir = project_dir / "specs"
    if specs_dir.is_dir():
        for spec_dir in sorted(specs_dir.iterdir()):
            source = _load_json_safe(spec_dir / "context" / "source.json")
            if isinstance(source, dict):
                repo_path = source.get("repo_path") or source.get("repo_root")
                if repo_path:
                    p = Path(repo_path)
                    if p.is_dir():
                        return p
    return project_dir


def _migrate_spec(
    spec_dir: Path,
    catalog: Any,
) -> Any:
    """Thin wrapper around migration.migrate_v0_1_workspace.

    Imports lazily so the module is usable in test environments that install
    only a subset of the backend packages.
    """
    from tests_catalog.migration import migrate_v0_1_workspace  # type: ignore[import]

    return migrate_v0_1_workspace(spec_dir, catalog)


def _empty_catalog() -> Any:
    """Return an empty TestsCatalog."""
    from tests_catalog.schema import TestsCatalog  # type: ignore[import]

    return TestsCatalog(version=1, updated_at=_now_z(), tests=())


def _write_catalog(catalog: Any, catalog_path: Path) -> None:
    """Serialise and write a TestsCatalog to disk."""
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    d = catalog.to_dict()
    # Stamp updated_at before writing
    d["updated_at"] = _now_z()
    catalog_path.write_text(
        json.dumps(d, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main migrate subcommand
# ---------------------------------------------------------------------------


@click.command(name="migrate")
@click.argument(
    "kind",
    metavar="KIND",
)
@click.option(
    "--workspace",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Workspace root directory.  Defaults to ~/.pfactory/workspaces.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the migration plan without writing any files.",
)
def migrate_command(
    kind: str,
    workspace: Path | None,
    dry_run: bool,
) -> None:
    """Migrate old PFactory data.

    Supported migration kinds:

    \b
      v0_1_catalog  — Walk v0.1 workspaces and consolidate test entries
                      into per-repo .pfactory/tests-catalog.json files.
    """
    if kind != "v0_1_catalog":
        click.echo(
            f"error: unknown migration kind {kind!r}. Supported: v0_1_catalog",
            err=True,
        )
        sys.exit(1)

    _run_v0_1_catalog_migration(workspace=workspace, dry_run=dry_run)


def _run_v0_1_catalog_migration(
    workspace: Path | None,
    dry_run: bool,
) -> None:
    """Core logic for the v0_1_catalog migration."""
    ws_root = (workspace or Path.home() / ".pfactory" / "workspaces").resolve()

    if not ws_root.is_dir():
        click.echo(f"Workspace root does not exist (nothing to migrate): {ws_root}")
        return

    click.echo(f"Workspace root: {ws_root}")
    if dry_run:
        click.echo("Dry-run mode — no files will be written.\n")

    # Try to import migration primitives early so we fail fast if backend
    # packages are not installed.
    try:
        from tests_catalog.migration import migrate_v0_1_workspace  # noqa: F401
        from tests_catalog.schema import TestsCatalog  # noqa: F401
    except ImportError as exc:
        click.echo(
            f"error: tests_catalog package not found on PYTHONPATH. "
            f"Run from apps/backend/ or set PYTHONPATH accordingly.\n{exc}",
            err=True,
        )
        sys.exit(1)

    # ── Walk workspace/<project_id>/specs/<spec_id>/ ─────────────────
    # Group results by the project's resolved repo root.
    # repo_root → TestsCatalog (accumulated across all specs for that project)
    per_repo: dict[Path, Any] = {}
    spec_count = 0
    entry_count = 0

    project_dirs = sorted(p for p in ws_root.iterdir() if p.is_dir())
    if not project_dirs:
        click.echo("No project directories found in workspace — nothing to migrate.")
        return

    for project_dir in project_dirs:
        specs_dir = project_dir / "specs"
        if not specs_dir.is_dir():
            continue

        repo_root = _resolve_repo_root_for_project(project_dir)

        if repo_root not in per_repo:
            per_repo[repo_root] = _empty_catalog()

        spec_dirs = sorted(s for s in specs_dir.iterdir() if s.is_dir())
        for spec_dir in spec_dirs:
            tests_dir = spec_dir / "tests"
            if not tests_dir.is_dir() or not any(tests_dir.glob("test_*.py")):
                continue

            before_count = len(per_repo[repo_root].tests)
            per_repo[repo_root] = _migrate_spec(spec_dir, per_repo[repo_root])
            added = len(per_repo[repo_root].tests) - before_count
            spec_count += 1
            entry_count += added

            click.echo(
                f"  spec {spec_dir.name!s:40s} → +{added} entries  (repo: {repo_root})"
            )

    if spec_count == 0:
        click.echo("No v0.1 test directories found — nothing to migrate.")
        return

    click.echo(
        f"\nMigration plan: {spec_count} spec(s) → {entry_count} new catalog entries "
        f"across {len(per_repo)} repo(s)."
    )

    if dry_run:
        click.echo("\nDry-run complete.  Re-run without --dry-run to write.")
        return

    # ── Write per-repo catalogs ───────────────────────────────────────
    for repo_root, catalog in sorted(per_repo.items()):
        if not catalog.tests:
            continue
        catalog_path = repo_root / ".pfactory" / "tests-catalog.json"
        _write_catalog(catalog, catalog_path)
        click.echo(f"Wrote {len(catalog.tests)} entries → {catalog_path}")

    click.echo("\nMigration complete.")
