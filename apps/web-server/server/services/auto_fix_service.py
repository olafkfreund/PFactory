"""Auto-Fix service — polls a project's issue provider, imports new
issues as specs, and starts the agent on each one.

Multi-provider: works against any of GitHub, GitLab, or Azure DevOps
via the existing ``GitProvider`` abstraction at
``apps/backend/runners/github/providers/``.  The provider is selected
by the project's ``settings.gitProvider`` field (same selection logic
the existing routes use — see ``routes/github.py::_get_project_provider``).

This service backs:
  - ``GET  /api/projects/{id}/auto-fix/config``       — load config
  - ``PUT  /api/projects/{id}/auto-fix/config``       — save config
  - ``GET  /api/projects/{id}/auto-fix/queue``        — list queue items
  - ``POST /api/projects/{id}/auto-fix/check-new``    — manual poll
  - ``POST /api/projects/{id}/auto-fix/{N}/start``    — single-issue start

The corresponding frontend hook ``useAutoFix.ts`` polls
``check-new`` every 5 minutes when the toggle is enabled.  Each new
issue found becomes a spec + an active agent run.

Queue state is persisted alongside the project in ``projects.json``
under ``settings.autoFix.queue`` (simple JSON list; a real db would be
overkill for the demo workflow this powers).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from factory_common.logsafe import sanitize_log

logger = logging.getLogger(__name__)


# Ensure ``apps/backend`` is on sys.path so ``from runners.github.providers ...``
# imports resolve.  This must happen at module load time — before any function
# that uses ``IssueFilters`` etc. is called.  Mirrors the same trick that
# ``routes/github.py::_get_project_provider`` uses.
_BACKEND_PATH = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PATH))


# Default AutoFixConfig — matches the AutoFixConfig type at
# apps/frontend-web/src/shared/types/github-api.ts:21
DEFAULT_AUTO_FIX_CONFIG: dict[str, Any] = {
    "enabled": False,
    "labels": [],
    "requireHumanApproval": False,
    "botToken": "",
    "model": "sonnet",
    "thinkingLevel": "none",
}


def get_config(project_id: str) -> dict[str, Any] | None:
    """Return the project's AutoFixConfig, or None if the project doesn't exist."""
    from ..routes.projects import load_projects

    projects = load_projects()
    if project_id not in projects:
        return None
    project = projects[project_id]
    settings = project.get("settings") or {}
    cfg = settings.get("autoFix") or {}
    # Layer defaults so old projects with partial state still answer the contract
    return {**DEFAULT_AUTO_FIX_CONFIG, **cfg, "queue": settings.get("autoFix", {}).get("queue", [])}


def save_config(project_id: str, config: dict[str, Any]) -> bool:
    """Persist the config into projects.json under settings.autoFix."""
    from ..routes.projects import load_projects, save_projects

    projects = load_projects()
    if project_id not in projects:
        return False

    project = projects[project_id]
    settings = project.setdefault("settings", {})
    existing_queue = (settings.get("autoFix") or {}).get("queue", [])
    settings["autoFix"] = {
        **DEFAULT_AUTO_FIX_CONFIG,
        **config,
        # Preserve queue across config updates
        "queue": existing_queue,
    }
    save_projects(projects)
    return True


def get_queue(project_id: str) -> list[dict[str, Any]]:
    """Return the auto-fix queue (list of ``AutoFixQueueItem``)."""
    from ..routes.projects import load_projects

    projects = load_projects()
    if project_id not in projects:
        return []
    settings = projects[project_id].get("settings") or {}
    return (settings.get("autoFix") or {}).get("queue", [])


def _set_queue(project_id: str, queue: list[dict[str, Any]]) -> None:
    """Replace the queue in projects.json."""
    from ..routes.projects import load_projects, save_projects

    projects = load_projects()
    if project_id not in projects:
        return
    settings = projects[project_id].setdefault("settings", {})
    auto_fix = settings.setdefault("autoFix", dict(DEFAULT_AUTO_FIX_CONFIG))
    auto_fix["queue"] = queue
    save_projects(projects)


def _upsert_queue_item(project_id: str, item: dict[str, Any]) -> None:
    """Add or update a queue item keyed by issueNumber+repo."""
    queue = get_queue(project_id)
    key = (item["issueNumber"], item.get("repo", ""))
    found = False
    for i, existing in enumerate(queue):
        if (existing["issueNumber"], existing.get("repo", "")) == key:
            queue[i] = item
            found = True
            break
    if not found:
        queue.append(item)
    _set_queue(project_id, queue)


def _existing_issue_numbers(project_path: Path) -> set[int]:
    """Scan ``.pfactory/specs/`` for already-imported issue numbers.

    Spec dirs follow the pattern ``NNN-gh{ISSUE}-{slug}`` where ISSUE
    is the integer issue/MR/work-item number from the source provider.
    """
    specs_dir = project_path / ".pfactory" / "specs"
    if not specs_dir.exists():
        return set()
    out: set[int] = set()
    for d in specs_dir.iterdir():
        if not d.is_dir():
            continue
        # Match "NNN-gh{ISSUE}-..." or "NNN-mr{N}-..." (GitLab/ADO future-proof)
        name = d.name
        for prefix in ("-gh", "-mr", "-wi"):  # gh=github, mr=gitlab MR, wi=ado work item
            if prefix not in name:
                continue
            tail = name.split(prefix, 1)[1]
            digits = ""
            for ch in tail:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            if digits:
                try:
                    out.add(int(digits))
                except ValueError:
                    pass
                break
    return out


def _next_spec_id(project_path: Path) -> int:
    """Return the next NNN to use for a new spec dir."""
    specs_dir = project_path / ".pfactory" / "specs"
    if not specs_dir.exists():
        return 1
    nxt = 1
    for d in specs_dir.iterdir():
        if d.is_dir() and d.name[:3].isdigit():
            try:
                nxt = max(nxt, int(d.name[:3]) + 1)
            except ValueError:
                pass
    return nxt


def _slug(title: str) -> str:
    """Lowercase, hyphenated, alnum-only, 40 chars max."""
    s = (title or "untitled").lower().replace(" ", "-")[:40]
    return "".join(c for c in s if c.isalnum() or c == "-") or "untitled"


def _provider_for(project_id: str):
    """Return the project's configured ``GitProvider`` instance.

    Reuses the same selection logic the existing routes use.  Raises
    ``ValueError`` if the project doesn't exist.
    """
    from ..routes.projects import load_projects

    projects = load_projects()
    if project_id not in projects:
        raise ValueError(f"Project {project_id} not found")
    project = projects[project_id]
    settings = project.get("settings") or {}
    provider_type_str = (settings.get("gitProvider") or "github").lower()
    project_path = project.get("path", "")

    from runners.github.providers.factory import get_provider
    from runners.github.providers.protocol import ProviderType

    token = settings.get("gitToken")
    base_url = settings.get("gitBaseUrl")
    org = settings.get("gitOrg")
    proj_name = settings.get("gitProject")
    repo_name = settings.get("gitRepo")

    # Best-effort repo auto-detection — match the existing routes/github.py logic
    if not repo_name:
        try:
            from ..routes.github import _get_repo_full_name

            repo_name = _get_repo_full_name(project_path) or ""
        except Exception:
            repo_name = ""

    if provider_type_str == "gitlab":
        kwargs: dict[str, Any] = {}
        if token:
            kwargs["_token"] = token
        if base_url:
            kwargs["_base_url"] = base_url
        if project_path:
            kwargs["_project_dir"] = project_path
        return get_provider(ProviderType.GITLAB, repo=repo_name, **kwargs)

    if provider_type_str == "azure_devops":
        kwargs = {}
        if token:
            kwargs["_pat"] = token
        if org:
            kwargs["_organization"] = org
        if proj_name:
            kwargs["_project"] = proj_name
        return get_provider(ProviderType.AZURE_DEVOPS, repo=repo_name, **kwargs)

    # Default: GitHub. The key is `token` (not `_token`): the factory keys its
    # REST-vs-gh-CLI choice on `"token" in kwargs`, so `_token` would fall
    # through to the gh-CLI GitHubProvider — which has no such field, so it
    # raises TypeError (Factory#365).
    kwargs = {}
    if token:
        kwargs["token"] = token
    if project_path:
        kwargs["_project_dir"] = project_path
    return get_provider(ProviderType.GITHUB, repo=repo_name, **kwargs)


def _issue_prefix_for(provider_type: str) -> str:
    """Choose the spec-name prefix that signals which provider an issue came from."""
    pt = (provider_type or "github").lower()
    if pt == "gitlab":
        return "mr"
    if pt == "azure_devops":
        return "wi"
    return "gh"


def _write_spec_dir(
    project_path: Path,
    issue: dict[str, Any],
    provider_type: str,
    *,
    delegate_by_default: bool = False,
) -> str:
    """Create ``.pfactory/specs/NNN-{prefix}{N}-{slug}/`` with
    requirements.json + spec.md.  Returns the spec id (dir name).

    Provider-agnostic — works for any ``IssueData``-shaped dict.

    Args:
        delegate_by_default: When True (project setting ``delegateByDefault``
            is on AND ``gitProvider == "github"``), the freshly-written
            ``requirements.json`` carries ``metadata.enableDelegation = true``
            so the subsequent ``start_auto_fix`` call takes the delegation
            branch. Closes gap #2 from issue #144.
    """
    specs_dir = project_path / ".pfactory" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    issue_number = int(issue["number"])
    title = issue.get("title", f"Issue #{issue_number}")
    body = issue.get("body", "") or ""
    labels = issue.get("labels", []) or []
    url = issue.get("url", "")

    prefix = _issue_prefix_for(provider_type)
    spec_name = f"{_next_spec_id(project_path):03d}-{prefix}{issue_number}-{_slug(title)}"
    spec_dir = specs_dir / spec_name
    spec_dir.mkdir(parents=True, exist_ok=True)

    requirements: dict[str, Any] = {
        "title": title,
        "description": body,
        "source": provider_type,
        "issue": {
            "number": issue_number,
            "url": url,
            "state": (issue.get("state") or "").lower(),
            "labels": labels,
        },
    }
    if delegate_by_default:
        # Note also the issue number under metadata so the wizard task-start
        # path (which doesn't have an explicit issue_number param) can find
        # it via task_metadata.githubIssueNumber.
        requirements["metadata"] = {
            "enableDelegation": True,
            "githubIssueNumber": issue_number,
        }
    (spec_dir / "requirements.json").write_text(json.dumps(requirements, indent=2))

    spec_md = f"# {title}\n\n"
    spec_md += f"**Source:** {provider_type} issue [#{issue_number}]({url})\n"
    if labels:
        spec_md += f"**Labels:** {', '.join(labels)}\n"
    spec_md += f"\n## Description\n\n{body}\n"
    (spec_dir / "spec.md").write_text(spec_md)

    return spec_name


async def check_new_issues(project_id: str) -> list[dict[str, Any]]:
    """Return issues that aren't yet imported into the project's specs.

    Returns ``IssueData``-shaped dicts.  Side-effect-free (does NOT
    create specs or start agents — the caller decides).
    """
    from ..routes.projects import load_projects

    projects = load_projects()
    if project_id not in projects:
        raise ValueError(f"Project {project_id} not found")
    project_path = Path(projects[project_id]["path"])
    settings = projects[project_id].get("settings") or {}
    provider_type = (settings.get("gitProvider") or "github").lower()

    # Existing issue numbers already on disk
    existing = _existing_issue_numbers(project_path)

    # Fetch open issues from the provider
    from runners.github.providers.protocol import IssueFilters

    provider = _provider_for(project_id)
    cfg = get_config(project_id) or {}
    label_filter = cfg.get("labels") or []
    filters = IssueFilters(state="open", labels=label_filter)
    issues = await provider.fetch_issues(filters)

    new: list[dict[str, Any]] = []
    for iss in issues:
        if iss.number in existing:
            continue
        new.append(
            {
                "number": iss.number,
                "title": iss.title,
                "body": iss.body,
                "state": iss.state,
                "labels": list(iss.labels or []),
                "url": iss.url,
                "provider": provider_type,
            }
        )

    logger.info(
        "[auto_fix] check_new_issues project=%s provider=%s existing=%d new=%d",
        sanitize_log(project_id),
        sanitize_log(provider_type),
        len(existing),
        len(new),
    )
    return new


def _read_task_metadata(spec_dir: Path) -> dict[str, Any]:
    """Best-effort read of requirements.json/task_metadata.json for delegation flags.

    The frontend writes delegation flags to ``requirements.json[metadata]``;
    the backend reads them from there or from ``task_metadata.json``. Either
    source is acceptable. Missing files return an empty dict.
    """
    out: dict[str, Any] = {}
    req_file = spec_dir / "requirements.json"
    if req_file.exists():
        try:
            req = json.loads(req_file.read_text())
            md = req.get("metadata")
            if isinstance(md, dict):
                out.update(md)
        except (json.JSONDecodeError, OSError):
            pass
    tm_file = spec_dir / "task_metadata.json"
    if tm_file.exists():
        try:
            tm = json.loads(tm_file.read_text())
            if isinstance(tm, dict):
                out.update(tm)
        except (json.JSONDecodeError, OSError):
            pass
    return out


async def start_auto_fix(project_id: str, issue_number: int) -> dict[str, Any]:
    """Import a single issue → write spec → start the agent (or delegate).

    Idempotent on the spec side: if the issue is already imported, the
    existing spec is reused and the agent is started against it.

    Delegation branch (#94): when the task's ``enableDelegation`` flag is
    set AND the project's git provider is GitHub, we run only the planner
    phase, post the enriched plan as an issue comment, assign Copilot,
    and stop. The local coder/QA pipeline does not run for delegated tasks.
    """
    from ..routes.projects import load_projects
    from ..services.agent_service import get_agent_service
    from ..websockets.events import broadcast_event

    projects = load_projects()
    if project_id not in projects:
        raise ValueError(f"Project {project_id} not found")
    project_path = Path(projects[project_id]["path"])
    settings = projects[project_id].get("settings") or {}
    provider_type = (settings.get("gitProvider") or "github").lower()

    # Find or create the spec dir
    spec_id: str | None = None
    specs_dir = project_path / ".pfactory" / "specs"
    if specs_dir.exists():
        prefix = _issue_prefix_for(provider_type)
        marker = f"-{prefix}{issue_number}-"
        for d in specs_dir.iterdir():
            if d.is_dir() and marker in d.name:
                spec_id = d.name
                break

    now_iso = datetime.now(timezone.utc).isoformat()

    if spec_id is None:
        # Fetch the issue and create the spec
        provider = _provider_for(project_id)
        iss = await provider.fetch_issue(issue_number)
        issue_dict = {
            "number": iss.number,
            "title": iss.title,
            "body": iss.body,
            "state": iss.state,
            "labels": list(iss.labels or []),
            "url": iss.url,
        }
        # Gap #2 (#144): honour the project-level delegateByDefault toggle
        # by injecting enableDelegation into the new spec's metadata.
        delegate_default = bool(settings.get("delegateByDefault")) and provider_type == "github"
        spec_id = _write_spec_dir(
            project_path,
            issue_dict,
            provider_type,
            delegate_by_default=delegate_default,
        )

    # Determine whether to delegate before recording the queue item, so
    # the queue carries the right initial status.
    spec_dir = project_path / ".pfactory" / "specs" / spec_id
    task_metadata = _read_task_metadata(spec_dir)
    # Delegation works on GitHub (Copilot, V1) and GitLab (Duo Workflow,
    # V1.5 #98). ADO has no autonomous-agent equivalent — the provider's
    # assign_to_user raises NotImplementedError which the runner handles.
    delegate = bool(task_metadata.get("enableDelegation")) and provider_type in (
        "github",
        "gitlab",
    )
    initial_status = "planning" if delegate else "building"

    queue_item = {
        "issueNumber": issue_number,
        "repo": settings.get("gitRepo", ""),
        "status": initial_status,
        "specId": spec_id,
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }
    _upsert_queue_item(project_id, queue_item)

    task_id = f"{project_id}:{spec_id}"
    agent_service = get_agent_service()

    if delegate:
        # Delegation flow (#94, #144): hand off to the shared runner so
        # the wizard's task-start path (routes/execution.py) and Auto-Fix
        # take the exact same code path. The runner awaits the planner
        # subprocess (fix for gap #3 from #144) before reading the plan
        # and posting the enrichment comment.
        from .delegation_runner import run_delegation

        provider = _provider_for(project_id)
        result = await run_delegation(
            project_id=project_id,
            project_path=project_path,
            spec_id=spec_id,
            issue_number=issue_number,
            provider=provider,
        )

        # Mirror the delegation outcome onto the queue so the tracker has
        # a delegatedAt timestamp to start its 24h decline clock.
        _upsert_queue_item(
            project_id,
            {
                **queue_item,
                "status": "delegated",
                "delegatedAt": result["delegatedAt"],
                "updatedAt": result["delegatedAt"],
            },
        )
        return {
            "specId": spec_id,
            "taskId": task_id,
            "status": "delegated",
        }

    # ----------------------------------------------------------------------
    # Default flow: PFactory runs the full pipeline.
    # ----------------------------------------------------------------------
    try:
        await agent_service.start_task_execution(
            task_id=task_id,
            project_path=project_path,
            spec_id=spec_id,
            auto_continue=True,
            force=True,  # Auto-fix is by definition unattended — bypass approval gate
        )
    except ValueError as e:
        # Already running — that's fine
        if "already running" not in str(e):
            raise

    await broadcast_event(
        "auto_fix:started",
        {"projectId": project_id, "issueNumber": issue_number, "specId": spec_id},
    )

    return {"specId": spec_id, "taskId": task_id, "status": "started"}


async def _pull_clone_if_any(project_id: str) -> None:
    """If the project was registered via gitUrl (#82 PR-A), fast-forward
    the local clone before reading the issue list (#82 PR-B's
    pull-on-poll hook).

    No-op for local-path projects (the common laptop case) and on git
    errors — a stale clone is better than a poll cycle that aborts.
    """
    from ..routes.projects import load_projects

    projects = load_projects()
    proj = projects.get(project_id) or {}
    git_url = proj.get("clonedFrom")
    if not git_url:
        return
    project_path = Path(proj.get("path", ""))
    if not project_path.is_dir():
        return
    try:
        from .project_workspace_service import (
            GitOperationError,
            clone_or_update,
        )

        await clone_or_update(
            git_url=git_url,
            branch=proj.get("clonedBranch"),
            slug=project_path.name,
            root=project_path.parent,
        )
        logger.debug("[auto_fix] pulled %s before poll", sanitize_log(project_path))
    except GitOperationError as e:
        logger.warning(
            "[auto_fix] pull-on-poll failed for project=%s: %s — continuing with stale clone",
            sanitize_log(project_id),
            sanitize_log(e),
        )


async def check_new_and_start_all(project_id: str) -> dict[str, Any]:
    """Manual-poll convenience: find new issues + start each one.

    Also advances delegated tasks (#94): for items already in
    ``status="delegated"``, scan the provider for Copilot's resulting PR
    and transition to ``in_review`` (or ``declined`` after 24h).

    For portal-managed clones (#82 PR-B): runs ``git pull`` before
    reading the issue list so the agent always sees the latest commits.

    This is what the frontend's "Poll now" button and the 5-min
    auto-poll loop ultimately invoke.
    """
    cfg = get_config(project_id)
    if not cfg:
        raise ValueError(f"Project {project_id} not found")

    # Fast-forward portal-managed clones before we look for new issues.
    await _pull_clone_if_any(project_id)

    new_issues = await check_new_issues(project_id)
    started: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for iss in new_issues:
        try:
            result = await start_auto_fix(project_id, iss["number"])
            started.append({**iss, **result})
        except Exception as e:  # pragma: no cover — bubble for visibility
            logger.warning(
                "[auto_fix] start failed project=%s issue=%s err=%s",
                sanitize_log(project_id),
                sanitize_log(iss["number"]),
                sanitize_log(e),
            )
            errors.append({"issueNumber": iss["number"], "error": str(e)})

    # Advance any delegated tasks alongside polling for new issues.
    delegation_summary: dict[str, Any] = {}
    try:
        from .delegation_tracker import scan_delegated_tasks

        delegation_summary = await scan_delegated_tasks(project_id)
    except Exception as e:  # pragma: no cover
        logger.warning(
            "[auto_fix] delegation tracker failed project=%s err=%s",
            sanitize_log(project_id),
            sanitize_log(e),
        )

    return {
        "checked": len(new_issues),
        "started": started,
        "errors": errors,
        "delegation": delegation_summary,
    }
