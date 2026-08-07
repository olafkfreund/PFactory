"""pfactory init — interactive scaffolder for .pfactory.yml + empty catalog.

Usage (interactive)::

    python -m cli init

Usage (non-interactive, testable)::

    python -m cli init \\
        --non-interactive \\
        --target-name api \\
        --target-type http \\
        --base-url https://api.staging.example.com \\
        --auth-type bearer \\
        --auth-token-env STAGING_API_TOKEN

This command:

1. Confirms repo root (cwd by default; override with ``--repo-root``).
2. Checks for an existing ``.pfactory.yml``; aborts unless ``--force``.
3. Collects target configuration (interactive prompts or CLI flags).
4. Renders ``.pfactory.yml`` matching the ``PFactoryConfig`` schema.
5. Validates the written file via ``pfactory_yml.load_pfactory_yml()``.
6. Creates ``.pfactory/tests-catalog.json`` (empty) if absent.
7. Prints a summary.

Task 15 / #31 commit 2.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

# ---------------------------------------------------------------------------
# Helpers — YAML rendering without a hard dependency on PyYAML in CI
# (PyYAML is in requirements.txt but we fall back to ruamel if someone
# has that instead, and finally to a simple hand-rolled renderer for
# the narrow subset of YAML we need).
# ---------------------------------------------------------------------------


def _to_yaml(data: dict[str, Any], indent: int = 0) -> str:
    """Minimalist YAML renderer sufficient for .pfactory.yml scaffolding.

    Handles str, int, bool, None, list-of-dict, and nested dict values.
    Does NOT handle multiline strings or complex types — those aren't needed
    for the scaffolded output.
    """
    try:
        import yaml

        return yaml.dump(data, default_flow_style=False, sort_keys=False)
    except ImportError:
        pass
    lines: list[str] = []
    _render_dict(data, lines, indent=0)
    return "\n".join(lines) + "\n"


def _render_value(value: Any, lines: list[str], indent: int) -> None:
    pad = "  " * indent
    if isinstance(value, dict):
        _render_dict(value, lines, indent)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                first = True
                for k, v in item.items():
                    if first:
                        lines.append(f"{pad}- {k}: {_scalar(v)}")
                        first = False
                    else:
                        lines.append(f"{pad}  {k}: {_scalar(v)}")
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    else:
        lines.append(_scalar(value))


def _render_dict(d: dict[str, Any], lines: list[str], indent: int) -> None:
    pad = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            lines.append(f"{pad}{k}:")
            _render_dict(v, lines, indent + 1)
        elif isinstance(v, list):
            lines.append(f"{pad}{k}:")
            for item in v:
                if isinstance(item, dict):
                    first_key = True
                    for ik, iv in item.items():
                        if first_key:
                            lines.append(f"{pad}  - {ik}: {_scalar(iv)}")
                            first_key = False
                        else:
                            lines.append(f"{pad}    {ik}: {_scalar(iv)}")
                else:
                    lines.append(f"{pad}  - {_scalar(item)}")
        else:
            lines.append(f"{pad}{k}: {_scalar(v)}")


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    # Quote strings that could be ambiguous
    s = str(v)
    if any(
        c in s
        for c in (
            ":",
            "#",
            "{",
            "}",
            "[",
            "]",
            ",",
            "&",
            "*",
            "?",
            "|",
            "-",
            "<",
            ">",
            "=",
            "!",
            "%",
            "@",
            "`",
        )
    ) or s in ("true", "false", "null", "yes", "no"):
        return f'"{s}"'
    return s


# ---------------------------------------------------------------------------
# Auth prompt helpers
# ---------------------------------------------------------------------------


def _prompt_auth_interactive() -> dict[str, Any] | None:
    """Interactively collect auth configuration.

    Returns a dict ready for embedding in the YAML target, or None for no auth.
    """
    auth_type = click.prompt(
        "Auth type",
        type=click.Choice(["bearer", "basic", "none"]),
        default="none",
    )
    if auth_type == "none":
        return None
    if auth_type == "bearer":
        token_env = (
            click.prompt(
                "Env-var name for bearer token (store the NAME, not the value)",
                default="API_TOKEN",
            )
            .strip()
            .upper()
        )
        return {"type": "bearer", "token_env": token_env}
    # basic
    username_env = (
        click.prompt(
            "Env-var name for basic-auth username",
            default="API_USERNAME",
        )
        .strip()
        .upper()
    )
    password_env = (
        click.prompt(
            "Env-var name for basic-auth password",
            default="API_PASSWORD",
        )
        .strip()
        .upper()
    )
    return {"type": "basic", "username_env": username_env, "password_env": password_env}


# ---------------------------------------------------------------------------
# Target builder helpers
# ---------------------------------------------------------------------------


def _build_http_target(
    name: str,
    base_url: str,
    auth: dict[str, Any] | None,
) -> dict[str, Any]:
    target: dict[str, Any] = {"name": name, "type": "http", "base_url": base_url}
    if auth:
        target["auth"] = auth
    return target


def _build_docker_compose_target(
    name: str,
    compose_file: str,
    services: list[str],
    wait_url: str | None,
) -> dict[str, Any]:
    target: dict[str, Any] = {
        "name": name,
        "type": "docker_compose",
        "compose_file": compose_file,
        "services": services,
    }
    if wait_url:
        target["wait_for"] = [{"url": wait_url, "timeout_seconds": 60}]
    return target


# ---------------------------------------------------------------------------
# Empty catalog helpers
# ---------------------------------------------------------------------------


def _now_z() -> str:
    """Return the current time as an ISO-8601 UTC string with Z suffix."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _create_empty_catalog(catalog_path: Path) -> None:
    """Write an empty tests-catalog.json if it does not already exist."""
    if catalog_path.exists():
        return
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog = {"version": 1, "updated_at": _now_z(), "tests": []}
    catalog_path.write_text(
        json.dumps(catalog, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main init command
# ---------------------------------------------------------------------------


@click.command(name="init")
@click.option(
    "--repo-root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Repository root directory.  Defaults to cwd.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite an existing .pfactory.yml without prompting.",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help="Read all configuration from flags; do not prompt.",
)
@click.option("--target-name", default=None, help="Target name (non-interactive).")
@click.option(
    "--target-type",
    type=click.Choice(["http", "kubernetes", "docker_compose", "feature_flag"]),
    default=None,
    help="Target type (non-interactive).",
)
@click.option("--base-url", default=None, help="Base URL for http target (non-interactive).")
@click.option(
    "--compose-file",
    default="docker-compose.yml",
    help="Compose file for docker_compose target (non-interactive).",
)
@click.option(
    "--compose-services",
    default=None,
    help="Comma-separated service names for docker_compose target (non-interactive).",
)
@click.option(
    "--auth-type",
    type=click.Choice(["bearer", "basic", "none"]),
    default=None,
    help="Auth type (non-interactive).",
)
@click.option(
    "--auth-token-env",
    default=None,
    help="Bearer token env-var name (non-interactive).",
)
@click.option(
    "--auth-username-env",
    default=None,
    help="Basic auth username env-var name (non-interactive).",
)
@click.option(
    "--auth-password-env",
    default=None,
    help="Basic auth password env-var name (non-interactive).",
)
def init_command(
    repo_root: Path | None,
    force: bool,
    non_interactive: bool,
    target_name: str | None,
    target_type: str | None,
    base_url: str | None,
    compose_file: str,
    compose_services: str | None,
    auth_type: str | None,
    auth_token_env: str | None,
    auth_username_env: str | None,
    auth_password_env: str | None,
) -> None:
    """Scaffold .pfactory.yml and an empty tests-catalog.json in the repo root."""
    # ── Step 1: resolve repo root ─────────────────────────────────────
    root = (repo_root or Path.cwd()).resolve()
    if not root.is_dir():
        click.echo(f"error: repo root does not exist: {root}", err=True)
        sys.exit(1)

    click.echo(f"Initializing PFactory in: {root}")

    # ── Step 2: existing file guard ───────────────────────────────────
    yml_path = root / ".pfactory.yml"
    if yml_path.exists() and not force:
        click.echo(
            "error: .pfactory.yml already exists.  Use --force to overwrite.",
            err=True,
        )
        sys.exit(1)

    # ── Step 3: collect target config ────────────────────────────────
    if non_interactive:
        # Validate required flags
        if not target_name:
            click.echo("error: --target-name is required in non-interactive mode.", err=True)
            sys.exit(1)
        if not target_type:
            click.echo("error: --target-type is required in non-interactive mode.", err=True)
            sys.exit(1)

        # Build auth
        resolved_auth: dict[str, Any] | None = None
        if auth_type and auth_type != "none":
            if auth_type == "bearer":
                if not auth_token_env:
                    click.echo("error: --auth-token-env is required for bearer auth.", err=True)
                    sys.exit(1)
                resolved_auth = {"type": "bearer", "token_env": auth_token_env}
            elif auth_type == "basic":
                if not auth_username_env or not auth_password_env:
                    click.echo(
                        "error: --auth-username-env and --auth-password-env are required for basic auth.",
                        err=True,
                    )
                    sys.exit(1)
                resolved_auth = {
                    "type": "basic",
                    "username_env": auth_username_env,
                    "password_env": auth_password_env,
                }

        # Build target
        if target_type == "http":
            if not base_url:
                click.echo("error: --base-url is required for http targets.", err=True)
                sys.exit(1)
            target_dict = _build_http_target(target_name, base_url, resolved_auth)
        elif target_type == "docker_compose":
            services_list = [s.strip() for s in (compose_services or "app").split(",") if s.strip()]
            target_dict = _build_docker_compose_target(
                target_name, compose_file, services_list, None
            )
        else:
            # kubernetes / feature_flag — minimal scaffold
            target_dict = {"name": target_name, "type": target_type}

    else:
        # Interactive mode
        click.echo("\nLet's configure your first target.")
        t_name = click.prompt("Target name", default="web")
        t_type = click.prompt(
            "Target type",
            type=click.Choice(["http", "kubernetes", "docker_compose", "feature_flag"]),
            default="http",
        )

        if t_type == "http":
            t_base_url = click.prompt(
                "Base URL",
                default="https://staging.example.com",
            )
            resolved_auth = _prompt_auth_interactive()
            target_dict = _build_http_target(t_name, t_base_url, resolved_auth)
        elif t_type == "docker_compose":
            t_compose_file = click.prompt("Compose file", default="docker-compose.yml")
            t_services_raw = click.prompt("Services (comma-separated)", default="app")
            t_services = [s.strip() for s in t_services_raw.split(",") if s.strip()]
            t_wait = click.prompt("Wait-for URL (leave empty to skip)", default="")
            target_dict = _build_docker_compose_target(
                t_name,
                t_compose_file,
                t_services,
                t_wait or None,
            )
        else:
            click.echo(f"Minimal scaffold for {t_type} target — edit .pfactory.yml to complete.")
            target_dict = {"name": t_name, "type": t_type}

    # ── Step 4: render .pfactory.yml ─────────────────────────────────
    config_data: dict[str, Any] = {
        "version": 1,
        "targets": [target_dict],
    }

    yml_text = f"# .pfactory.yml — generated by `pfactory init`\n# Edit this file to add more targets and configure test policies.\n# See: https://github.com/olafkfreund/PFactory/blob/main/docs/pfactory-yml.md\n\n{_to_yaml(config_data)}"
    yml_path.write_text(yml_text, encoding="utf-8")
    click.echo(f"\nWrote: {yml_path}")

    # ── Step 5: validate via pfactory_yml ────────────────────────────
    try:
        # Import lazily so the CLI can be imported without the full backend
        # dependency chain being installed (useful in test environments).
        from pfactory_yml import load_pfactory_yml

        config = load_pfactory_yml(root)
        if config is None:
            click.echo(
                "warning: .pfactory.yml was written but failed to parse — check the file.",
                err=True,
            )
        else:
            click.echo(f"Validated: {len(config.targets)} target(s) registered.")
    except ImportError:
        # Validation skipped — pfactory_yml not on PYTHONPATH in this invocation
        click.echo("note: pfactory_yml package not found; skipping parse validation.")
    except Exception as exc:  # noqa: BLE001
        click.echo(f"warning: validation error: {exc}", err=True)

    # ── Step 6: create empty tests-catalog.json ───────────────────────
    catalog_path = root / ".pfactory" / "tests-catalog.json"
    _create_empty_catalog(catalog_path)
    click.echo(f"Catalog:  {catalog_path}")

    # ── Step 7: summary ───────────────────────────────────────────────
    click.echo("\nDone. Next steps:")
    click.echo("  1. Review .pfactory.yml and add any remaining targets / auth.")
    click.echo("  2. Commit .pfactory.yml (do NOT commit .pfactory/tests-catalog.json")
    click.echo("     until it contains real test entries).")
    click.echo("  3. Run `pfactory run` to generate and execute the first test suite.")
