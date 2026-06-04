"""Task-control MCP tools — PFactory MVP, Task 2 (#3).

Seven tools that let a Claude Code session in an AIFactory project
hand a finished spec off to PFactory and observe progress:

  Write tools
  - task_create_and_run  — create a PFactory workspace for an AIFactory
                           spec + (eventually) kick off the pipeline
  - project_create       — register an AIFactory project for handover
  - task_rerun           — re-execute one lane against an existing task

  Read tools
  - task_status   — execution state for a task (phase + lane progress)
  - task_list     — list PFactory tasks, filterable by project / status
  - project_list  — list registered projects
  - report_get    — fetch a task's report (markdown or JSON)

Storage at MVP: filesystem-only, under ``$PFACTORY_WORKSPACE_ROOT``
(default ``~/.pfactory``). Layout:

    ~/.pfactory/
      projects.json
      workspaces/{project_id}/specs/{spec_id}/
        task.md                  # handover payload, agent-readable
        status.json              # task lifecycle state
        findings/triage_report.{md,json}  # populated by the Triager (Task 8)
        context/, tests/, findings/, logs/, memory/  # Task 3+

The REST-backed inherited tool surface (task_start / task_stop / etc.)
has been removed — those were for AIFactory's coder pipeline. The
PFactory FastAPI portal (Task 9) will add HTTP endpoints that mirror
these MCP tools so the React frontend can read the same state.

Registered ONLY from the standalone MCP server
(``apps/backend/mcp_server/pfactory_server.py``), NOT from
``registry.create_all_tools`` — the in-process Claude Agent SDK
shouldn't be able to drive itself recursively.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from claude_agent_sdk import tool

    SDK_TOOLS_AVAILABLE = True
except ImportError:
    SDK_TOOLS_AVAILABLE = False
    tool = None  # type: ignore[assignment]

# Snapshotter is independent of the SDK — import unconditionally so tests
# (which skip when the SDK isn't installed) can still verify wiring.
try:
    from workspaces import SnapshotError, snapshot_aifactory_spec
except ImportError:  # apps/backend not on sys.path (e.g. running from repo root)
    try:
        from apps.backend.workspaces import SnapshotError, snapshot_aifactory_spec
    except ImportError:
        SnapshotError = Exception  # type: ignore[assignment,misc]
        snapshot_aifactory_spec = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Storage layout helpers
# ---------------------------------------------------------------------------

_DEFAULT_ROOT = Path.home() / ".pfactory"
# v0.2 modality spine — Decision 2 in the v0.2 design spec.
# 'functional' is accepted as a deprecated v0.1 alias for 'unit'.
_MVP_LANES = ("unit", "browser", "api", "integration", "mutation")
_V01_LANE_ALIASES = {"functional": "unit"}  # for plan-load compatibility


def _workspace_root() -> Path:
    """Resolve the PFactory workspace root. Env override > default."""
    root = os.environ.get("PFACTORY_WORKSPACE_ROOT")
    return Path(root).expanduser() if root else _DEFAULT_ROOT


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_visual_inspection_meta(spec_dir: Path, visual_inspection: dict | None) -> bool:
    """Write ``context/visual_inspection.json`` when a handover opts in (#170 / P5).

    ``visual_inspection`` is the optional ``task_create_and_run`` arg
    ``{enabled, target, flow}``. Returns True (and writes the file) only when
    ``enabled`` is truthy — so a normal task leaves no metadata + the default
    path is untouched. The run path (P4) reads this to drive the browser lane.
    """
    vi = visual_inspection or {}
    if not vi.get("enabled"):
        return False
    ctx = Path(spec_dir) / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "visual_inspection.json").write_text(
        json.dumps(
            {"enabled": True, "target": vi.get("target"), "flow": vi.get("flow")},
            indent=2,
        )
    )
    return True


def _projects_file(root: Path | None = None) -> Path:
    return (root or _workspace_root()) / "projects.json"


def _load_projects(root: Path | None = None) -> dict[str, Any]:
    """Return ``{"projects": [...]}``; empty if the file doesn't exist."""
    pf = _projects_file(root)
    if not pf.exists():
        return {"projects": []}
    try:
        return json.loads(pf.read_text())
    except (json.JSONDecodeError, OSError):
        return {"projects": []}


def _save_projects(data: dict[str, Any], root: Path | None = None) -> None:
    pf = _projects_file(root)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps(data, indent=2))


def _spec_dir(project_id: str, spec_id: str, root: Path | None = None) -> Path:
    return (root or _workspace_root()) / "workspaces" / project_id / "specs" / spec_id


def _status_file(project_id: str, spec_id: str, root: Path | None = None) -> Path:
    return _spec_dir(project_id, spec_id, root) / "status.json"


def _find_task(task_id: str, root: Path | None = None) -> tuple[str, str] | None:
    """Locate a task by ID. Returns (project_id, spec_id) or None.

    The spec_id IS the task_id in MVP — they're allocated 1:1 when
    task_create_and_run runs. A separate id field exists in status.json
    so a future store could decouple them without breaking the API.
    """
    workspaces_root = (root or _workspace_root()) / "workspaces"
    if not workspaces_root.exists():
        return None
    for project_dir in workspaces_root.iterdir():
        if not project_dir.is_dir():
            continue
        specs_dir = project_dir / "specs"
        if not specs_dir.exists():
            continue
        candidate = specs_dir / task_id
        if candidate.is_dir():
            return (project_dir.name, task_id)
    return None


def _load_status(
    project_id: str, spec_id: str, root: Path | None = None
) -> dict[str, Any] | None:
    sf = _status_file(project_id, spec_id, root)
    if not sf.exists():
        return None
    try:
        return json.loads(sf.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Response envelope helpers
# ---------------------------------------------------------------------------


def _format_error(exc: Exception | str) -> dict[str, Any]:
    """Return the MCP content-block error shape (``isError=True``)."""
    text = str(exc) if isinstance(exc, Exception) else exc
    return {
        "content": [{"type": "text", "text": f"Error: {text}"}],
        "isError": True,
    }


def _format_json(data: Any) -> dict[str, Any]:
    """Return the MCP content-block success shape with JSON payload."""
    return {
        "content": [{"type": "text", "text": json.dumps(data, indent=2, default=str)}]
    }


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def create_task_control_tools() -> list:
    """Create the seven PFactory MVP task-control tools.

    Returns a list of tool functions decorated with ``@tool`` from
    ``claude_agent_sdk``. The standalone MCP server passes this list
    to ``create_sdk_mcp_server`` to publish them over stdio.
    """
    if not SDK_TOOLS_AVAILABLE:
        return []

    tools: list = []

    # ── task_create_and_run ──────────────────────────────────────────────

    @tool(
        "task_create_and_run",
        "Create a PFactory task for an AIFactory spec and (eventually) "
        "kick off the autonomous test-generation pipeline. At MVP the task "
        "is recorded with status=pending; the pipeline runs once the "
        "Planner/Generator/Executor/Evaluator/Triager agents land "
        "(Tasks 5-8). Returns the new task_id, portal_url, and "
        "workspace spec_dir path.",
        {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (from project_list / project_create)",
                },
                "spec_id": {
                    "type": "string",
                    "description": "AIFactory spec ID — the spec_dir under ~/.aifactory/workspaces/{project_id}/specs/{spec_id}/ that the Planner will read read-only",
                },
                "branch": {
                    "type": "string",
                    "description": "Git branch containing the completed feature code",
                },
                "base_ref": {
                    "type": "string",
                    "description": "Base ref to diff against (typically the PR base, e.g. main)",
                },
                "confirm": {
                    "type": "boolean",
                    "default": False,
                    "description": "Pass true to actually create the workspace. If false, returns a preview without side effects.",
                },
                "visual_inspection": {
                    "type": "object",
                    "description": "Optional (#170). When the handover enables a visual inspection, pass {enabled: true, target: <visual target name>, flow: <what to inspect>}. The browser lane then records + packages an automated-test/ run with screenshots + a human report. Omit (or enabled:false) for a normal task.",
                    "properties": {
                        "enabled": {"type": "boolean", "default": False},
                        "target": {"type": "string", "description": "Name of a visual target in .pfactory.yml"},
                        "flow": {"type": "string", "description": "What to inspect (the user flow / acceptance focus)"},
                    },
                },
            },
            "required": ["project_id", "spec_id", "branch", "base_ref"],
        },
    )
    async def task_create_and_run(args: dict[str, Any]) -> dict[str, Any]:
        project_id = args["project_id"]
        spec_id = args["spec_id"]
        branch = args["branch"]
        base_ref = args["base_ref"]
        confirm = bool(args.get("confirm", False))
        # Visual Inspection opt-in (#170 / P5): the handover asks "enable visual
        # inspection?"; when enabled, {enabled, target, flow} threads to the
        # workspace so the browser lane records + packages an automated-test/ run.
        visual_inspection = args.get("visual_inspection") or {}

        projects = _load_projects()
        project_entry = next(
            (p for p in projects["projects"] if p.get("id") == project_id),
            None,
        )
        if project_entry is None:
            return _format_error(
                f"unknown project_id: {project_id!r}. Run project_list to see registered projects "
                f"or project_create to register one."
            )

        task_id = spec_id  # MVP: 1:1 mapping
        spec_dir = _spec_dir(project_id, task_id)

        if not confirm:
            return _format_json(
                {
                    "preview": True,
                    "would_create": str(spec_dir),
                    "project_id": project_id,
                    "spec_id": spec_id,
                    "branch": branch,
                    "base_ref": base_ref,
                    "hint": "Re-run with confirm=true to create the workspace.",
                }
            )

        if spec_dir.exists():
            return _format_error(
                f"spec_dir already exists: {spec_dir}. Use task_rerun to re-execute a lane "
                f"against an existing task."
            )

        spec_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("context", "tests", "findings", "logs", "memory"):
            (spec_dir / sub).mkdir(exist_ok=True)

        # Snapshot the AIFactory spec into context/ (Task 3, #4). If the
        # snapshot itself fails (missing source dir), unwind the workspace
        # we just created so a retry isn't blocked by the "already exists"
        # guard above.
        snapshot_warnings: list[str] = []
        if snapshot_aifactory_spec is not None:
            try:
                snap = snapshot_aifactory_spec(
                    project_id=project_id,
                    spec_id=spec_id,
                    branch=branch,
                    base_ref=base_ref,
                    project_root_path=project_entry.get("root_path"),
                    dest_spec_dir=spec_dir,
                )
                snapshot_warnings = list(snap.warnings)
            except SnapshotError as exc:
                # Roll back the partial workspace so the user can fix and retry.
                import shutil as _shutil

                _shutil.rmtree(spec_dir, ignore_errors=True)
                return _format_error(str(exc))
        else:
            snapshot_warnings.append(
                "snapshotter not importable in this environment — context/ left empty"
            )

        # task.md — agent-readable handover payload
        (spec_dir / "task.md").write_text(
            f"# PFactory task\n\n"
            f"- project_id: {project_id}\n"
            f"- spec_id: {spec_id}\n"
            f"- branch: {branch}\n"
            f"- base_ref: {base_ref}\n"
            f"- created_at: {_now_iso()}\n\n"
            f"## Source\n\n"
            f"This task tests the AIFactory spec at "
            f"`~/.aifactory/workspaces/{project_id}/specs/{spec_id}/`.\n"
            f"The Planner agent (Task 5) reads that snapshot and emits a "
            f"lane-tagged `test_plan.json` under this workspace.\n"
        )

        # Visual Inspection metadata (#170 / P5) — written only when opted in,
        # so the default path is untouched. The run path (P4) reads this to
        # drive the browser lane against the named target + package the result.
        vi_enabled = write_visual_inspection_meta(spec_dir, visual_inspection)

        # status.json — lifecycle state
        status = {
            "task_id": task_id,
            "project_id": project_id,
            "spec_id": spec_id,
            "branch": branch,
            "base_ref": base_ref,
            "status": "pending",
            "phase": "created",
            "lane_progress": dict.fromkeys(_MVP_LANES, "pending"),
            "visual_inspection": vi_enabled,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        _status_file(project_id, task_id).write_text(json.dumps(status, indent=2))

        # Auto-fire the Planner (Task 5, #6). Skipped when
        # PFACTORY_AUTO_PLAN=0 (used by tests + the manual CLI path).
        # Errors inside the planner do NOT raise here — they show up in
        # status.json as status=planner_failed. See planner.run_planner.
        planner_scheduled = False
        try:
            from agents.planner import schedule_planner

            project_root = project_entry.get("root_path", ".")
            task = schedule_planner(
                spec_dir=spec_dir,
                project_dir=Path(project_root).expanduser(),
                mode="initial",
            )
            planner_scheduled = task is not None
        except ImportError:
            # planner module not importable (e.g. minimal venv without SDK
            # transitive deps); leave status=pending and surface a warning.
            snapshot_warnings.append(
                "planner module not importable — task stays at status=pending"
            )

        portal_port = os.environ.get("PFACTORY_PORTAL_PORT", "3114")
        return _format_json(
            {
                "task_id": task_id,
                "project_id": project_id,
                "spec_dir": str(spec_dir),
                "portal_url": f"http://localhost:{portal_port}/tasks/{task_id}",
                "status": "pending",
                "snapshot_warnings": snapshot_warnings,
                "planner_scheduled": planner_scheduled,
                "note": (
                    "Workspace created + AIFactory spec snapshotted into context/. "
                    "Pipeline execution (planner + generators + executor + evaluator + triager) "
                    "wires up in Tasks 5-8."
                ),
            }
        )

    tools.append(task_create_and_run)

    # ── task_status ──────────────────────────────────────────────────────

    @tool(
        "task_status",
        "Get the lifecycle state of a PFactory task: status, current phase, "
        "per-lane progress, branch, base_ref, timestamps. Cheap; safe to poll.",
        {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "PFactory task ID"},
            },
            "required": ["task_id"],
        },
    )
    async def task_status(args: dict[str, Any]) -> dict[str, Any]:
        task_id = args["task_id"]
        located = _find_task(task_id)
        if not located:
            return _format_error(f"unknown task_id: {task_id!r}")
        project_id, spec_id = located
        status = _load_status(project_id, spec_id)
        if status is None:
            return _format_error(
                f"task {task_id!r} has no status.json — workspace likely corrupted"
            )
        return _format_json(status)

    tools.append(task_status)

    # ── task_list ────────────────────────────────────────────────────────

    @tool(
        "task_list",
        "List PFactory tasks. Optionally filter by project_id or status. "
        "Returns lean entries (task_id, project_id, status, phase, created_at, updated_at).",
        {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Optional project filter",
                },
                "status": {
                    "type": "string",
                    "description": "Optional status filter (e.g. pending, running, done, failed)",
                },
                "limit": {
                    "type": "integer",
                    "default": 50,
                    "description": "Max results",
                },
            },
        },
    )
    async def task_list(args: dict[str, Any]) -> dict[str, Any]:
        project_filter = args.get("project_id")
        status_filter = args.get("status")
        limit = int(args.get("limit", 50))

        results: list[dict[str, Any]] = []
        workspaces_root = _workspace_root() / "workspaces"
        if workspaces_root.exists():
            for project_dir in sorted(workspaces_root.iterdir()):
                if not project_dir.is_dir():
                    continue
                if project_filter and project_dir.name != project_filter:
                    continue
                specs_dir = project_dir / "specs"
                if not specs_dir.exists():
                    continue
                for spec_dir in sorted(specs_dir.iterdir()):
                    if not spec_dir.is_dir():
                        continue
                    status = _load_status(project_dir.name, spec_dir.name)
                    if not status:
                        continue
                    if status_filter and status.get("status") != status_filter:
                        continue
                    results.append(
                        {
                            "task_id": status.get("task_id"),
                            "project_id": status.get("project_id"),
                            "status": status.get("status"),
                            "phase": status.get("phase"),
                            "created_at": status.get("created_at"),
                            "updated_at": status.get("updated_at"),
                        }
                    )
                    if len(results) >= limit:
                        break
                if len(results) >= limit:
                    break

        return _format_json({"count": len(results), "tasks": results})

    tools.append(task_list)

    # ── project_list ─────────────────────────────────────────────────────

    @tool(
        "project_list",
        "List AIFactory projects registered with PFactory. Each project "
        "maps to a local AIFactory checkout the user wants to hand specs over from.",
        {"type": "object", "properties": {}},
    )
    async def project_list(args: dict[str, Any]) -> dict[str, Any]:
        data = _load_projects()
        return _format_json(
            {"count": len(data["projects"]), "projects": data["projects"]}
        )

    tools.append(project_list)

    # ── project_create ───────────────────────────────────────────────────

    @tool(
        "project_create",
        "Register an AIFactory project with PFactory. The project_id and "
        "name should match the AIFactory project being handed over from. "
        "root_path points at the local checkout where the feature branch lives.",
        {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Project ID (typically matches the AIFactory project_id)",
                },
                "name": {
                    "type": "string",
                    "description": "Human-readable project name",
                },
                "root_path": {
                    "type": "string",
                    "description": "Absolute path to the local checkout",
                },
            },
            "required": ["id", "name", "root_path"],
        },
    )
    async def project_create(args: dict[str, Any]) -> dict[str, Any]:
        missing = [k for k in ("id", "name", "root_path") if not args.get(k)]
        if missing:
            return _format_error(
                f"project_create requires {', '.join(missing)}. Provide id "
                "(matches the AIFactory project_id), name, and root_path "
                "(absolute path to the local checkout where the feature branch lives)."
            )
        pid = args["id"]
        name = args["name"]
        root_path = args["root_path"]

        data = _load_projects()
        if any(p.get("id") == pid for p in data["projects"]):
            return _format_error(f"project_id already registered: {pid!r}")

        entry = {
            "id": pid,
            "name": name,
            "root_path": str(Path(root_path).expanduser()),
            "created_at": _now_iso(),
        }
        data["projects"].append(entry)
        _save_projects(data)
        return _format_json(entry)

    tools.append(project_create)

    # ── report_get ───────────────────────────────────────────────────────

    @tool(
        "report_get",
        "Fetch a task's final report. Format is 'md' (default, human-readable) "
        "or 'json' (machine-readable). Reports are populated by the Triager "
        "(Task 8) at the end of the pipeline.",
        {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "PFactory task ID"},
                "format": {
                    "type": "string",
                    "enum": ["md", "json"],
                    "default": "md",
                    "description": "Report format",
                },
            },
            "required": ["task_id"],
        },
    )
    async def report_get(args: dict[str, Any]) -> dict[str, Any]:
        task_id = args["task_id"]
        fmt = args.get("format", "md")
        if fmt not in ("md", "json"):
            return _format_error(f"format must be 'md' or 'json'; got {fmt!r}")
        located = _find_task(task_id)
        if not located:
            return _format_error(f"unknown task_id: {task_id!r}")
        project_id, spec_id = located
        spec_dir = _spec_dir(project_id, spec_id)
        filename = "triage_report.md" if fmt == "md" else "triage_report.json"
        # The Triager writes findings/triage_report.{md,json}. Prefer that;
        # fall back to the legacy report.{md,json} at the spec-dir root for
        # any pre-Triager-wiring workspaces.
        report_path = spec_dir / "findings" / filename
        if not report_path.exists():
            legacy = spec_dir / ("report.md" if fmt == "md" else "report.json")
            if legacy.exists():
                report_path = legacy
            else:
                return _format_error(
                    f"no {fmt} report for task {task_id!r} yet — the Triager (Task 8) hasn't run"
                )
        return _format_json(
            {
                "task_id": task_id,
                "format": fmt,
                "path": str(report_path),
                "body": report_path.read_text(),
            }
        )

    tools.append(report_get)

    # ── task_rerun ───────────────────────────────────────────────────────

    @tool(
        "task_rerun",
        "Re-execute a previously-run task against its existing context "
        "snapshot. Resets the named lane to pending and re-fires the Planner, "
        "which auto-chains the rest of the pipeline (gen → executor → "
        "evaluator → triager). Lane must be one of the v0.2 lit lanes.",
        {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "PFactory task ID"},
                "lane": {
                    "type": "string",
                    "default": "unit",
                    "description": "Lane to reset before the rerun (v0.2 lit lanes)",
                },
            },
            "required": ["task_id"],
        },
    )
    async def task_rerun(args: dict[str, Any]) -> dict[str, Any]:
        task_id = args["task_id"]
        lane = args.get("lane", "unit")
        # v0.1 alias compatibility — accept old names with a silent remap
        lane = _V01_LANE_ALIASES.get(lane, lane)
        if lane not in _MVP_LANES:
            return _format_error(
                f"lane {lane!r} not supported in v0.2 — lit lanes are {list(_MVP_LANES)}. "
                f"SAST/DAST/Fuzz are out of scope (see Decision 2)."
            )
        located = _find_task(task_id)
        if not located:
            return _format_error(f"unknown task_id: {task_id!r}")
        project_id, spec_id = located
        status = _load_status(project_id, spec_id)
        if status is None:
            return _format_error(f"task {task_id!r} has no status.json")
        # Bump rerun marker + reset the lane, then re-fire the pipeline.
        rerun_count = int(status.get("rerun_count", 0)) + 1
        status["rerun_count"] = rerun_count
        status["lane_progress"][lane] = "pending"
        status["status"] = "pending"
        status["phase"] = "created"
        status["updated_at"] = _now_iso()
        _status_file(project_id, spec_id).write_text(json.dumps(status, indent=2))

        # Re-fire the Planner against the existing snapshot. schedule_planner
        # is gated by PFACTORY_AUTO_PLAN (off in tests + the manual CLI path)
        # and the success path of each stage auto-chains the next agent.
        planner_scheduled = False
        try:
            from agents.planner import schedule_planner

            project_entry = next(
                (
                    p
                    for p in _load_projects().get("projects", [])
                    if p.get("id") == project_id
                ),
                {},
            )
            project_root = project_entry.get("root_path", ".")
            task = schedule_planner(
                spec_dir=_spec_dir(project_id, spec_id),
                project_dir=Path(project_root).expanduser(),
                mode="initial",
            )
            planner_scheduled = task is not None
        except ImportError:
            pass  # planner not importable (minimal venv) — status stays pending

        return _format_json(
            {
                "task_id": task_id,
                "lane": lane,
                "rerun_count": rerun_count,
                "status": "pending",
                "planner_scheduled": planner_scheduled,
                "note": (
                    "Rerun recorded; Planner re-fired (auto-chains the pipeline)."
                    if planner_scheduled
                    else "Rerun recorded. Planner not auto-fired "
                    "(PFACTORY_AUTO_PLAN=0 or planner unavailable)."
                ),
            }
        )

    tools.append(task_rerun)

    return tools
