"""
MCP CLI commands
================

Operator-facing diagnostics for the MCP catalog and credentials chain.

``pfactory mcp-doctor`` prints the catalog × credentials matrix so operators
can verify their setup before launching agents — saves the round-trip of
"run a task, wait for the MCP subprocess to fail, dig through logs".

When invoked with ``--project PATH``, also resolves the per-project infra
markers so the operator sees exactly which servers WOULD auto-enable for
that project.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final

# Color codes — keep simple, fall back to plain text on non-TTY.
_USE_COLOR: Final = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_GREEN = "\033[32m" if _USE_COLOR else ""
_RED = "\033[31m" if _USE_COLOR else ""
_YELLOW = "\033[33m" if _USE_COLOR else ""
_DIM = "\033[2m" if _USE_COLOR else ""
_BOLD = "\033[1m" if _USE_COLOR else ""
_RESET = "\033[0m" if _USE_COLOR else ""

# Hints printed when a provider has no detectable credentials. Kept short —
# the operator docs have the full setup story; this is just the nudge to
# get them to the right CLI command.
_HINT_FOR_PROVIDER: Final[dict[str, str]] = {
    "github": "run 'gh auth login' OR export GITHUB_TOKEN=<your-pat>",
    "kubernetes": "configure ~/.kube/config OR export KUBECONFIG=<path>",
    "aws": "run 'aws configure' OR export AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY",
    "azure": "run 'az login' OR set AZURE_TENANT_ID + AZURE_CLIENT_ID + AZURE_CLIENT_SECRET",
    "gcp": "run 'gcloud auth application-default login' OR set GOOGLE_APPLICATION_CREDENTIALS",
    "gitlab": "export GITLAB_TOKEN=<your-pat> (and GITLAB_INSTANCE_URL if self-managed)",
    "azure_devops": "export AZURE_DEVOPS_PERSONAL_ACCESS_TOKEN + AZURE_DEVOPS_ORG_SERVICE_URL",
}


def _check_marker_match(entry, infra_markers: dict | None) -> tuple[bool, str]:
    """Return (matches, description) for the entry's marker requirement."""
    if not entry.marker_capability_keys:
        return True, "always-on"
    if infra_markers is None:
        return True, "(markers not checked — pass --project to verify)"
    matched_keys = [k for k in entry.marker_capability_keys if infra_markers.get(k)]
    if matched_keys:
        return True, f"matched: {', '.join(matched_keys)}"
    return False, f"none of: {', '.join(entry.marker_capability_keys)}"


def _check_operator_config_perms() -> str | None:
    """Return a warning string if the operator config has loose perms, else None."""
    config_path = Path.home() / ".pfactory" / "mcp-credentials.json"
    if not config_path.exists():
        return None
    mode = config_path.stat().st_mode & 0o777
    if mode & 0o077:
        return (
            f"{_RED}WARNING{_RESET}: {config_path} has mode {mode:o} — "
            "PFactory refuses to read it. Fix with: chmod 0600 ~/.pfactory/mcp-credentials.json"
        )
    return None


def handle_mcp_doctor_command(project_dir: Path | None = None) -> int:
    """Print the MCP catalog × credentials matrix.

    Returns 0 on clean run, 1 only on import / config errors. Missing creds
    are informational — operators expect that on a fresh laptop.
    """
    try:
        from agents.tools_pkg.mcp_catalog import CATALOG
        from core.mcp_credentials import get_credential_status
    except ImportError as exc:
        print(f"{_RED}ERROR{_RESET}: cannot import MCP framework: {exc}", file=sys.stderr)
        return 1

    # Optional: resolve infra markers for a specific project
    infra_markers: dict | None = None
    project_path: Path | None = None
    if project_dir is not None:
        try:
            from prompts_pkg.project_context import detect_infra_markers

            project_path = project_dir.resolve()
            infra_markers = detect_infra_markers(project_path)
        except Exception as exc:  # broad: any project-scan failure is informational
            print(
                f"{_YELLOW}note{_RESET}: marker detection failed for {project_dir}: {exc}",
                file=sys.stderr,
            )
            infra_markers = None

    perm_warning = _check_operator_config_perms()
    if perm_warning:
        print(perm_warning)
        print()

    # Header
    header = f"{_BOLD}MCP catalog status{_RESET} ({len(CATALOG)} entries)"
    if project_path is not None:
        header += f"  {_DIM}— project: {project_path}{_RESET}"
    print(header)
    print()

    any_unavailable = False

    for entry in CATALOG:
        creds = (
            get_credential_status(entry.credential_provider)
            if entry.credential_provider
            else None
        )
        markers_ok, marker_desc = _check_marker_match(entry, infra_markers)

        # Status icon — would this server actually auto-enable right now?
        would_enable = (
            markers_ok
            and (creds is None or creds.available)
        )
        icon = f"{_GREEN}✓{_RESET}" if would_enable else f"{_RED}✗{_RESET}"

        cred_text = (
            f"{_GREEN}{creds.source}{_RESET}"
            if (creds and creds.available)
            else f"{_RED}none{_RESET}"
            if creds
            else f"{_DIM}n/a{_RESET}"
        )
        marker_color = _GREEN if markers_ok else _RED
        marker_text = f"{marker_color}{marker_desc}{_RESET}"

        # Two-line per entry: status + indent for hint
        print(f"  {icon} {_BOLD}{entry.id:<14}{_RESET} creds: {cred_text}")
        print(f"     {_DIM}markers:{_RESET} {marker_text}")
        print(
            f"     {_DIM}agents:{_RESET}  "
            f"{_DIM}{', '.join(entry.default_for_agents)}{_RESET}"
        )

        # Hint when creds are missing (and project markers would actually want this server)
        if creds and not creds.available and markers_ok:
            hint = _HINT_FOR_PROVIDER.get(entry.credential_provider)
            if hint:
                print(f"     {_YELLOW}→{_RESET} {hint}")
            any_unavailable = True

        print()

    # Operator config presence note
    config_path = Path.home() / ".pfactory" / "mcp-credentials.json"
    print(
        f"{_DIM}Operator config:{_RESET} {config_path} "
        f"({'present' if config_path.exists() else 'not present'})"
    )

    if infra_markers is None and project_dir is None:
        print(
            f"\n{_DIM}Tip: pass --project PATH to also resolve per-project infra markers.{_RESET}"
        )

    return 0
