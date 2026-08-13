"""
Project management routes.

Handles CRUD operations for projects (git repositories that PFactory manages).
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from factory_common.logsafe import sanitize_log
from server.services.git_utils import confine_to_workspace, safe_spec_component

# --------------------------------------------------------------------------
# Type Definitions for Validation
# --------------------------------------------------------------------------

# BUG-1.2-003: Memory backend must be one of these values
MemoryBackendType = Literal["graphiti", "file"]

from ..config import get_settings
from . import changelog, context, git, github, insights

router = APIRouter()
logger = logging.getLogger(__name__)

# Include project-specific sub-routers
# These will be available under /api/projects/{projectId}/...
router.include_router(github.project_router, prefix="/{projectId}/github", tags=["GitHub"])
router.include_router(changelog.router, prefix="/{projectId}/changelog", tags=["Changelog"])
router.include_router(insights.router, prefix="/{projectId}/insights", tags=["Insights"])
router.include_router(
    insights.files_router, prefix="/{projectId}/files/insights", tags=["Files Insights"]
)
router.include_router(context.project_router, prefix="/{projectId}", tags=["Context"])
router.include_router(git.project_router, prefix="", tags=["Git"])
router.include_router(git.releases_router, prefix="/{projectId}/releases", tags=["Releases"])


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class ProjectBase(BaseModel):
    """Base project model."""

    path: str = Field(..., description="Absolute path to the project directory")
    name: str | None = Field(None, description="Display name for the project")


class ProjectCreate(BaseModel):
    """Model for creating a new project.

    Two mutually exclusive shapes (#82 PR-A):

    1. **Local path** (laptop installs) — set ``path``. Existing
       behaviour: the directory is registered as-is.
    2. **Git URL** (SaaS/K8s installs) — set ``gitUrl`` (alias of
       ``git_url``), optionally ``branch``. The portal clones the
       repository into ``PROJECT_WORKSPACE_ROOT`` (defaults to
       ``~/.pfactory/workspaces/``) and registers the clone's path.
       This is the path that unblocks shared-host deployment shapes
       where the user's repo isn't on the portal's filesystem.

    Exactly one of ``path`` or ``gitUrl`` must be provided.
    """

    model_config = ConfigDict(populate_by_name=True)

    path: str | None = Field(
        None, description="Absolute path to the project directory (local mode)"
    )
    name: str | None = Field(None, description="Display name for the project")
    gitUrl: str | None = Field(
        None,
        alias="git_url",
        description="Git URL to clone (HTTPS or SSH). Triggers the portal-managed clone path.",
    )
    branch: str | None = Field(
        None,
        description="Branch to checkout after clone. Defaults to the remote's HEAD.",
    )
    gitCredentialId: str | None = Field(
        None,
        alias="git_credential_id",
        description="Reference to a stored git credential. Reserved for PR-C; ignored in PR-A.",
    )

    @field_validator("path", mode="after")
    @classmethod
    def _normalize_path(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) and v.strip() else None

    @field_validator("gitUrl", mode="after")
    @classmethod
    def _normalize_git_url(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) and v.strip() else None

    @model_validator(mode="after")
    def _require_exactly_one_source(self):
        if not self.path and not self.gitUrl:
            raise ValueError("Either 'path' (local mode) or 'gitUrl' (clone mode) must be provided")
        if self.path and self.gitUrl:
            raise ValueError(
                "'path' and 'gitUrl' are mutually exclusive — provide one or the other"
            )
        return self


class NotificationSettings(BaseModel):
    """Notification settings model - BUG-1.2-004: Now properly typed."""

    onTaskComplete: bool = Field(default=True)
    onTaskFailed: bool = Field(default=True)
    onReviewNeeded: bool = Field(default=True)
    sound: bool = Field(default=True)
    emailEnabled: bool = Field(default=False)


class ProjectSettings(BaseModel):
    """Project settings model matching frontend expectations."""

    model_config = ConfigDict(populate_by_name=True)

    model: str = Field(default="claude-sonnet-4-5-20250929")
    # BUG-1.2-003: Validate memoryBackend against allowed values
    memoryBackend: MemoryBackendType = Field(default="file", alias="memory_backend")
    # BUG-1.2-004: notifications now properly typed
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    graphitiMcpEnabled: bool = Field(default=False, alias="graphiti_mcp_enabled")
    graphitiMcpUrl: str | None = Field(default=None, alias="graphiti_mcp_url")
    mainBranch: str | None = Field(default=None, alias="main_branch")
    useClaudeMd: bool = Field(default=True, alias="use_claude_md")
    gitProvider: str = Field(default="github", alias="git_provider")
    gitToken: str | None = Field(default=None, alias="git_token", repr=False)
    gitBaseUrl: str | None = Field(default=None, alias="git_base_url")
    gitOrg: str | None = Field(default=None, alias="git_org")
    gitProject: str | None = Field(default=None, alias="git_project")
    gitRepo: str | None = Field(default=None, alias="git_repo")

    @field_validator("memoryBackend", mode="before")
    @classmethod
    def validate_memory_backend(cls, v):
        """Validate memoryBackend for backward compatibility."""
        if v is None:
            return "file"
        valid_backends = ["graphiti", "file"]
        if v not in valid_backends:
            # Fall back to file for invalid values (backward compatibility)
            return "file"
        return v

    @field_validator("notifications", mode="before")
    @classmethod
    def validate_notifications(cls, v):
        """Convert dict to NotificationSettings for backward compatibility."""
        if v is None:
            return NotificationSettings()
        if isinstance(v, dict):
            return NotificationSettings(**v)
        return v


class Project(ProjectBase):
    """Full project model with computed fields."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., description="Unique project ID")
    name: str = Field(..., description="Display name")
    createdAt: str = Field(
        ..., alias="created_at", description="ISO timestamp when project was added"
    )
    updatedAt: str = Field(
        ..., alias="updated_at", description="ISO timestamp when project was last updated"
    )
    autoBuildPath: str | None = Field(
        None, alias="auto_build_path", description="Path to .pfactory if initialized"
    )
    settings: ProjectSettings = Field(default_factory=ProjectSettings)


# --------------------------------------------------------------------------
# Helper Functions
# --------------------------------------------------------------------------


def get_projects_file() -> Path:
    """Get path to the projects data file."""
    settings = get_settings()
    return Path(settings.PROJECTS_DATA_DIR) / "projects.json"


def load_projects() -> dict[str, dict]:
    """Load projects from disk."""
    projects_file = get_projects_file()
    if projects_file.exists():
        return json.loads(projects_file.read_text())
    return {}


def resolve_project_path(project_id: str) -> Path:
    """Resolve a project id to its filesystem path, or raise 404.

    Single source of truth for the ``load_projects() -> 404 -> Path(...)`` idiom
    that several route modules each re-implemented. The 404 detail is kept
    identical to those copies so callers behave exactly as before.
    """
    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return Path(projects[project_id]["path"])


def save_projects(projects: dict[str, dict]) -> None:
    """Save projects to disk."""
    projects_file = get_projects_file()
    projects_file.parent.mkdir(parents=True, exist_ok=True)
    projects_file.write_text(json.dumps(projects, indent=2))


def ensure_tracked_project(repo: str) -> str | None:
    """Register a lightweight, repo-only project for a plan session (W4, #218).

    A plan session targets a ``repo`` (owner/name) that often has no local clone
    on the portal, so it never appears in the project dropdown. Upsert a minimal
    tracked record (no ``path``) so the repo is visible/selectable; if a project
    is already registered for this repo (e.g. a real local clone), reuse it and
    don't clobber it. Returns the project id, or ``None`` for a falsy repo.
    Best-effort: never raises.
    """
    if not repo or not repo.strip():
        return None
    repo = repo.strip()
    # RFC-0020 3.5 (Factory#366): the reference may be provider-qualified
    # ("gitlab:group/project"). Split it so the stored record holds a real
    # project path and the HOST it lives on, instead of a path with a provider
    # glued to the front of the owner. Registering it unqualified is how a
    # tracked project came to read as gitProvider "github" whatever the tenant
    # declared, which is one of the ways a GitLab tenant's run ended up on
    # GitHub. An unqualified reference is GitHub, exactly as before.
    from plan.repo_ref import parse_repo_ref

    provider, repo = parse_repo_ref(repo) or ("github", repo)
    try:
        projects = load_projects()
        # Reuse an existing project registered for this repo (don't overwrite a
        # real clone's record).
        for pid, pdata in projects.items():
            if pdata.get("repo") == repo:
                return pid
        project_id = repo.replace("/", "-")
        if project_id not in projects:
            projects[project_id] = {
                "name": repo.split("/")[-1],
                "repo": repo,
                "path": "",  # repo-only; no local clone yet
                "source": "plan-session",
                "created_at": datetime.now().isoformat(),
                # The declared host, so the project's own provider resolution
                # agrees with the contract that created it.
                "settings": {"gitProvider": provider},
            }
            save_projects(projects)
        return project_id
    except Exception:
        logger.exception("[projects] ensure_tracked_project failed for repo=%s", sanitize_log(repo))
        return None


def analyze_project(path: str) -> dict:
    """Analyze a project directory for git and PFactory status.

    A repo-only "tracked" project (registered from a plan session, W4 / #218)
    has no local clone yet, so ``path`` may be empty or not exist -- return
    neutral defaults rather than touching the filesystem.
    """
    if not path:
        return {"is_git_repo": False, "has_magestic_ai": False, "task_count": 0}
    try:
        project_path = confine_to_workspace(path)  # #335: refuse paths outside the workspace
    except ValueError:
        # Outside the allowed workspace -- treat as unusable, never touch the filesystem.
        return {"is_git_repo": False, "has_magestic_ai": False, "task_count": 0}
    if not project_path.exists():
        return {"is_git_repo": False, "has_magestic_ai": False, "task_count": 0}

    # Check if it's a git repository
    is_git_repo = (project_path / ".git").exists()

    # Check for .pfactory directory
    magestic_ai_dir = project_path / ".pfactory"
    has_magestic_ai = magestic_ai_dir.exists()

    # Count specs/tasks
    task_count = 0
    specs_dir = magestic_ai_dir / "specs"
    if specs_dir.exists():
        task_count = len([d for d in specs_dir.iterdir() if d.is_dir()])

    return {
        "is_git_repo": is_git_repo,
        "has_magestic_ai": has_magestic_ai,
        "task_count": task_count,
    }


def project_to_response(project_id: str, project_data: dict) -> dict:
    """Convert stored project data to response dict matching frontend expectations."""
    project_path_str = project_data.get("path") or ""
    analysis = analyze_project(project_path_str)

    # Convert has_magestic_ai to autoBuildPath (string path or empty string)
    auto_build_path = ".pfactory" if analysis["has_magestic_ai"] else ""

    # Build settings: start with defaults, then overlay saved settings
    default_settings = {
        "model": "claude-sonnet-4-5-20250929",
        "memoryBackend": "file",
        "notifications": {
            "onTaskComplete": True,
            "onTaskFailed": True,
            "onReviewNeeded": True,
            "sound": True,
        },
        "graphitiMcpEnabled": False,
        "graphitiMcpUrl": None,
        "mainBranch": None,
        "useClaudeMd": True,
    }
    # Merge saved settings from projects.json (written by update_project_settings)
    saved_settings = project_data.get("settings", {})
    if saved_settings:
        # Merge notifications separately to preserve individual keys
        if "notifications" in saved_settings:
            default_settings["notifications"].update(saved_settings["notifications"])
            saved_settings = {k: v for k, v in saved_settings.items() if k != "notifications"}
        default_settings.update(saved_settings)

    return {
        "id": project_id,
        "path": project_path_str,
        "name": project_data.get("name")
        or (Path(project_path_str).name if project_path_str else project_id),
        "repo": project_data.get("repo"),
        "createdAt": project_data.get("created_at", datetime.now().isoformat()),
        "updatedAt": project_data.get("updated_at", datetime.now().isoformat()),
        "autoBuildPath": auto_build_path,
        "settings": default_settings,
    }


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


async def _resolve_git_credential(cred_id: str) -> tuple[str, str] | None:
    """Look up a stored Git credential by id and return (username, token).

    Returns ``None`` (rather than raising) if the credential doesn't
    exist or its kind isn't supported — the caller falls back to an
    unauthenticated clone, which will give a clearer error if the
    remote actually needs auth. Used by ``add_project`` (#82 PR-C).
    """
    from sqlalchemy import select

    from ..database import GitCredential
    from ..database.engine import get_db

    async for session in get_db():
        result = await session.execute(select(GitCredential).where(GitCredential.id == cred_id))
        cred = result.scalar_one_or_none()
        if cred is None:
            return None
        if cred.kind != "pat":
            # Deploy Keys + GitHub App tokens are out of scope for V1.
            return None
        return (cred.username or "oauth2", cred.token)
    return None


@router.get("")
async def list_projects():
    """List all registered projects.

    Returns projects array directly (not wrapped) because
    the frontend api-client.ts adds the {success, data} wrapper automatically.
    """
    projects = load_projects()
    project_list = [project_to_response(pid, pdata) for pid, pdata in projects.items()]
    return project_list


class DiscoveredProject(BaseModel):
    """A discovered project folder."""

    name: str
    path: str
    has_git: bool = False
    has_package_json: bool = False
    has_requirements: bool = False
    has_magestic_ai: bool = False
    has_claude_md: bool = False


class ScanProjectsRequest(BaseModel):
    """Request model for scanning filesystem for projects."""

    basePath: str = Field(..., description="Base directory to scan for projects")
    maxDepth: int = Field(default=1, ge=1, le=5, description="Maximum scan depth (1-5, default 1)")


@router.post("/scan")
async def scan_for_projects(request: ScanProjectsRequest):
    """
    Scan filesystem for PFactory projects.

    Recursively scans a directory tree to find potential project directories.
    Identifies projects by looking for indicators like:
    - .git directory (version control)
    - package.json (Node.js projects)
    - requirements.txt or pyproject.toml (Python projects)
    - .pfactory directory (PFactory initialized projects)
    - CLAUDE.md file (Claude project documentation)

    Args:
        request: ScanProjectsRequest with basePath and optional maxDepth

    Returns:
        List of DiscoveredProject objects with project metadata

    Raises:
        HTTPException: 400 if path doesn't exist or isn't a directory
    """
    try:
        # Validate and resolve base path
        try:
            base = confine_to_workspace(
                request.basePath
            )  # #335: confine scan root to the workspace
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="path outside the allowed workspace",
            )

        if not base.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Path does not exist: {request.basePath}",
            )

        if not base.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Path is not a directory: {request.basePath}",
            )

        projects = []

        def scan_directory(dir_path: Path, current_depth: int):
            """Recursively scan directory for projects."""
            if current_depth > request.maxDepth:
                return

            try:
                # Sort entries for consistent ordering
                for entry in sorted(dir_path.iterdir(), key=lambda e: e.name.lower()):
                    if not entry.is_dir():
                        continue

                    # Skip hidden directories and common non-project dirs
                    if entry.name.startswith(".") or entry.name in (
                        "node_modules",
                        "__pycache__",
                        "venv",
                        ".venv",
                        "dist",
                        "build",
                        "target",
                        ".git",
                        "eggs",
                        ".eggs",
                        ".pytest_cache",
                        ".tox",
                        "htmlcov",
                        "coverage",
                    ):
                        continue

                    # Check for project indicators
                    has_git = (entry / ".git").exists()
                    has_package = (entry / "package.json").exists()
                    has_requirements = (entry / "requirements.txt").exists() or (
                        entry / "pyproject.toml"
                    ).exists()
                    has_magestic_ai = (entry / ".pfactory").exists()
                    has_claude_md = (entry / "CLAUDE.md").exists()

                    # If it looks like a project, add it
                    if has_git or has_package or has_requirements:
                        projects.append(
                            DiscoveredProject(
                                name=entry.name,
                                path=str(entry),
                                has_git=has_git,
                                has_package_json=has_package,
                                has_requirements=has_requirements,
                                has_magestic_ai=has_magestic_ai,
                                has_claude_md=has_claude_md,
                            )
                        )
                    elif current_depth < request.maxDepth:
                        # Not a project, but scan deeper if we haven't reached max depth
                        scan_directory(entry, current_depth + 1)

            except PermissionError:
                # Skip directories we can't read
                pass
            except Exception:
                # Skip directories that cause other errors (symlinks, etc)
                pass

        # Start scanning from base path at depth 1
        scan_directory(base, 1)

        # Return list directly - frontend api-client.ts adds {success, data} wrapper
        return [p.model_dump() for p in projects]

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Handle unexpected errors
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to scan for projects: {e!s}",
        )


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_project(project: ProjectCreate):
    """Add a new project. Two paths (#82 PR-A):

    1. **Local path** (``path`` set) — register an existing directory
       as-is. The legacy behaviour, unchanged.
    2. **Git URL** (``gitUrl`` set) — clone the repository into the
       portal's workspace root and register the local clone's path.
       Used for SaaS/K8s deployments where the user's repo isn't on the
       portal's filesystem.

    Returns project dict directly (not wrapped) because
    the frontend api-client.ts adds the {success, data} wrapper automatically.
    """
    created_directory = False

    if project.gitUrl:
        # Clone mode — defer to project_workspace_service.
        from ..services.project_workspace_service import (
            GitOperationError,
            clone_or_update,
        )

        # Stored credential lookup (#82 PR-C). When the caller passes
        # gitCredentialId, fetch the (username, token) tuple from the
        # git_credentials table and pass it to the clone service.
        credential: tuple[str, str] | None = None
        if project.gitCredentialId:
            credential = await _resolve_git_credential(project.gitCredentialId)
        try:
            cloned_path = await clone_or_update(
                git_url=project.gitUrl,
                branch=project.branch,
                credential=credential,
            )
        except GitOperationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Clone failed: {e}",
            )
        except ValueError:
            # The workspace-slug barrier refuses a gitUrl whose path component
            # would escape the workspace root (e.g. one ending in "/.."). That
            # is a bad request, not a server fault -- without this it surfaced
            # as an unhandled ValueError and a 500.
            # `from None`: the barrier's message quotes the rejected slug, and
            # the client already knows what it sent -- no need to echo it back.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="gitUrl does not yield a valid workspace name",
            ) from None
        project_path = cloned_path.resolve()
        created_directory = True
    else:
        # Local mode — register the existing directory.
        assert project.path is not None  # model_validator guarantees this
        try:
            project_path = confine_to_workspace(
                project.path
            )  # #335: confine registered dir to the workspace
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="path outside the allowed workspace",
            )

        if not project_path.exists():
            try:
                project_path.mkdir(parents=True, exist_ok=True)
                created_directory = True
            except OSError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot create directory: {project.path} ({e})",
                )

        if not project_path.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Path is not a directory: {project.path}",
            )

    # Check if already registered
    projects = load_projects()
    for pid, pdata in projects.items():
        if pdata["path"] == str(project_path.resolve()):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Project already registered",
            )

    # Create project entry
    project_id = str(uuid4())
    now = datetime.now().isoformat()
    project_data = {
        "path": str(project_path.resolve()),
        "name": project.name or project_path.name,
        "created_at": now,
        "updated_at": now,
    }
    # Clone-mode (#82 PR-A) — persist the source URL + branch so the
    # Auto-Fix pull-on-poll hook (PR-B) can fast-forward the workspace
    # before each poll cycle. Local-mode projects don't carry these.
    if project.gitUrl:
        project_data["clonedFrom"] = project.gitUrl
        if project.branch:
            project_data["clonedBranch"] = project.branch

    projects[project_id] = project_data
    save_projects(projects)

    response = project_to_response(project_id, project_data)
    if created_directory:
        response["createdDirectory"] = True
    return response


@router.get("/{project_id}")
async def get_project(project_id: str):
    """Get a specific project by ID."""
    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    return project_to_response(project_id, projects[project_id])


@router.put("/{project_id}")
async def update_project(project_id: str, project: ProjectCreate):
    """Update a project's metadata."""
    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Update fields
    project_data = projects[project_id]
    if project.path:
        try:
            project_path = confine_to_workspace(
                project.path
            )  # #335: confine updated path to the workspace
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="path outside the allowed workspace",
            )
        if not project_path.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Path does not exist: {project.path}",
            )
        project_data["path"] = str(project_path.resolve())

    if project.name:
        project_data["name"] = project.name

    project_data["updated_at"] = datetime.now().isoformat()

    projects[project_id] = project_data
    save_projects(projects)

    return project_to_response(project_id, project_data)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_project(project_id: str):
    """Remove a project (unregister, does not delete files)."""
    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    del projects[project_id]
    save_projects(projects)


@router.post("/{project_id}/initialize")
async def initialize_project(project_id: str):
    """Initialize PFactory in a project (create .pfactory directory).

    Returns InitializationResult format expected by frontend.
    """
    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_data = projects[project_id]
    project_path = Path(project_data["path"])

    try:
        # Create .pfactory directory structure
        magestic_ai_dir = project_path / ".pfactory"
        (magestic_ai_dir / "specs").mkdir(parents=True, exist_ok=True)

        # Update timestamp and autoBuildPath
        project_data["updated_at"] = datetime.now().isoformat()
        project_data["autoBuildPath"] = ".pfactory"
        projects[project_id] = project_data
        save_projects(projects)

        # Return nested format expected by frontend
        return {"success": True, "data": {"success": True}}
    except Exception:
        logger.exception(
            "[projects] initialize_project failed for project_id=%s", sanitize_log(project_id)
        )
        return {"success": False, "error": "Failed to initialize project."}


@router.get("/{project_id}/version")
async def check_project_version(project_id: str):
    """Check PFactory version info for a project."""
    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_data = projects[project_id]
    project_path = Path(project_data["path"])
    magestic_ai_dir = project_path / ".pfactory"

    return {
        "success": True,
        "data": {"isInitialized": magestic_ai_dir.exists(), "updateAvailable": False},
    }


class NotificationSettingsUpdate(BaseModel):
    """Model for updating notification settings."""

    onTaskComplete: bool | None = None
    onTaskFailed: bool | None = None
    onReviewNeeded: bool | None = None
    sound: bool | None = None
    emailEnabled: bool | None = None


class ProjectSettingsUpdate(BaseModel):
    """Model for updating project settings.

    BUG-1.2-005: Added notifications field to allow updating notification preferences.
    BUG-1.2-003: Added memoryBackend validation.
    """

    model_config = ConfigDict(populate_by_name=True)

    model: str | None = None
    # BUG-1.2-003: Validate memoryBackend against allowed values
    memoryBackend: MemoryBackendType | None = None
    # BUG-1.2-005: Added notifications field so preferences can be updated via API
    notifications: NotificationSettingsUpdate | None = None
    graphitiMcpEnabled: bool | None = None
    graphitiMcpUrl: str | None = None
    mainBranch: str | None = None
    useClaudeMd: bool | None = None
    gitProvider: str | None = Field(default=None, alias="git_provider")
    gitToken: str | None = Field(default=None, alias="git_token", repr=False)
    gitBaseUrl: str | None = Field(default=None, alias="git_base_url")
    gitOrg: str | None = Field(default=None, alias="git_org")
    gitProject: str | None = Field(default=None, alias="git_project")
    gitRepo: str | None = Field(default=None, alias="git_repo")
    # When true, every NEW task in this project gets ``enableRemoteControl: true``
    # in its task_metadata.  Per-task overrides win — this is just the default
    # when the user creates a task without flipping the wizard toggle.
    remoteControlByDefault: bool | None = Field(default=None, alias="remote_control_by_default")
    # When true, every NEW task in this project gets ``enableDelegation: true``
    # in its task_metadata. Only effective on GitHub projects — V1.5 (#98)
    # extends to GitLab Duo Workflow. Per-task overrides win.
    delegateByDefault: bool | None = Field(default=None, alias="delegate_by_default")

    @field_validator("memoryBackend", mode="before")
    @classmethod
    def validate_memory_backend(cls, v):
        """Validate memoryBackend for backward compatibility."""
        if v is None:
            return None
        valid_backends = ["graphiti", "file"]
        if v not in valid_backends:
            # Return None for invalid values (won't update)
            return None
        return v


@router.patch("/{project_id}/settings")
async def update_project_settings(project_id: str, settings: ProjectSettingsUpdate):
    """Update project settings."""
    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    try:
        project_data = projects[project_id]
        project_path = Path(project_data["path"])
        env_path = project_path / ".pfactory" / ".env"

        # Read existing .env or start fresh
        existing = {}
        if env_path.exists():
            for line in env_path.read_text().split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    existing[key.strip()] = value.strip()

        # Map ProjectSettingsUpdate fields to environment variables
        # Only update non-None values
        settings_dict = settings.model_dump(exclude_none=True)

        env_mapping = {
            "model": "AI_FACTORY_MODEL",
            "memoryBackend": "MEMORY_BACKEND",
            "graphitiMcpUrl": "GRAPHITI_MCP_URL",
            "mainBranch": "MAIN_BRANCH",
            "gitProvider": "GIT_PROVIDER",
            "gitToken": "GIT_TOKEN",
            "gitBaseUrl": "GIT_BASE_URL",
            "gitOrg": "GIT_ORG",
            "gitProject": "GIT_PROJECT",
            "gitRepo": "GIT_REPO",
        }

        # Handle boolean settings with "true"/"false" string values
        bool_mapping = {
            "graphitiMcpEnabled": "GRAPHITI_ENABLED",
            "useClaudeMd": "USE_CLAUDE_MD",
            "remoteControlByDefault": "REMOTE_CONTROL_BY_DEFAULT",
            "delegateByDefault": "DELEGATE_BY_DEFAULT",
        }

        # Update string/value settings
        for settings_key, env_key in env_mapping.items():
            if settings_key in settings_dict:
                existing[env_key] = str(settings_dict[settings_key])

        # Mirror for backwards compatibility
        if settings_dict.get("gitToken"):
            existing["GITHUB_TOKEN"] = str(settings_dict["gitToken"])
        if settings_dict.get("gitRepo"):
            existing["GITHUB_REPO"] = str(settings_dict["gitRepo"])

        # Update boolean settings
        for settings_key, env_key in bool_mapping.items():
            if settings_key in settings_dict:
                existing[env_key] = "true" if settings_dict[settings_key] else "false"

        # Ensure .pfactory directory exists
        env_path.parent.mkdir(parents=True, exist_ok=True)

        # Write back to .env file
        content = "\n".join(f"{k}={v}" for k, v in existing.items())
        env_path.write_text(content)

        # Set secure file permissions (owner read/write only)
        env_path.chmod(0o600)

        # Also update settings in projects.json
        if "settings" not in project_data:
            project_data["settings"] = {}

        # BUG-1.2-005: Handle notifications field specially to merge with existing values
        if "notifications" in settings_dict:
            notifications_update = settings_dict.pop("notifications")
            if notifications_update:
                # Ensure notifications dict exists
                if "notifications" not in project_data["settings"]:
                    project_data["settings"]["notifications"] = {
                        "onTaskComplete": True,
                        "onTaskFailed": True,
                        "onReviewNeeded": True,
                        "sound": True,
                    }
                # Merge the update into existing notifications
                project_data["settings"]["notifications"].update(notifications_update)

        project_data["settings"].update(settings_dict)
        project_data["updated_at"] = datetime.now().isoformat()

        save_projects(projects)

        return {"success": True, "message": "Project settings updated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update project settings: {e!s}")


@router.get("/{project_id}/worktrees")
async def list_project_worktrees(project_id: str):
    """List worktrees for a project with detailed stats."""
    import re
    import subprocess

    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_data = projects[project_id]
    project_path = Path(project_data["path"])

    # Get the base branch (current branch of main repo)
    try:
        base_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=5,
        )
        base_branch = base_result.stdout.strip() if base_result.returncode == 0 else "main"
    except Exception:
        base_branch = "main"

    # List worktrees using git
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"worktrees": []}

        # Parse git worktree list output
        raw_worktrees = []
        current = {}
        for line in result.stdout.split("\n"):
            if line.startswith("worktree "):
                if current:
                    raw_worktrees.append(current)
                current = {"path": line[9:]}
            elif line.startswith("branch "):
                current["branch"] = line[7:]
            elif line == "bare":
                current["bare"] = True
        if current:
            raw_worktrees.append(current)

        # Filter to only pfactory spec worktrees and enrich with stats
        enriched_worktrees = []
        for wt in raw_worktrees:
            wt_path = wt.get("path", "")
            branch = wt.get("branch", "")

            # Skip main worktree and bare repos
            if wt.get("bare") or wt_path == str(project_path):
                continue

            # Extract spec name from path (e.g., .pfactory/worktrees/tasks/001-feature)
            # Pattern: pfactory worktrees are in .pfactory/worktrees/tasks/{spec-name}
            spec_match = re.search(r"/\.pfactory/worktrees/tasks/([^/]+)$", wt_path)
            if not spec_match:
                continue

            spec_name = spec_match.group(1)

            # Get diff stats between base branch and worktree branch
            try:
                # Get commit count
                commit_result = subprocess.run(
                    ["git", "rev-list", "--count", f"{base_branch}..{branch}"],
                    cwd=wt_path,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                commit_count = (
                    int(commit_result.stdout.strip()) if commit_result.returncode == 0 else 0
                )

                # Get diff stats
                diff_result = subprocess.run(
                    ["git", "diff", "--shortstat", f"{base_branch}...{branch}"],
                    cwd=wt_path,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                files_changed = 0
                additions = 0
                deletions = 0

                if diff_result.returncode == 0 and diff_result.stdout.strip():
                    stat_line = diff_result.stdout.strip()
                    # Parse "X files changed, Y insertions(+), Z deletions(-)"
                    files_match = re.search(r"(\d+) files? changed", stat_line)
                    add_match = re.search(r"(\d+) insertions?\(\+\)", stat_line)
                    del_match = re.search(r"(\d+) deletions?\(-\)", stat_line)

                    files_changed = int(files_match.group(1)) if files_match else 0
                    additions = int(add_match.group(1)) if add_match else 0
                    deletions = int(del_match.group(1)) if del_match else 0

                enriched_worktrees.append(
                    {
                        "specName": spec_name,
                        "path": wt_path,
                        "branch": branch.replace("refs/heads/", ""),
                        "baseBranch": base_branch,
                        "commitCount": commit_count,
                        "filesChanged": files_changed,
                        "additions": additions,
                        "deletions": deletions,
                    }
                )
            except Exception:
                # Still include the worktree with default stats
                enriched_worktrees.append(
                    {
                        "specName": spec_name,
                        "path": wt_path,
                        "branch": branch.replace("refs/heads/", ""),
                        "baseBranch": base_branch,
                        "commitCount": 0,
                        "filesChanged": 0,
                        "additions": 0,
                        "deletions": 0,
                    }
                )

        return {"worktrees": enriched_worktrees}
    except Exception:
        logger.exception(
            "[projects] list_project_worktrees failed for project_id=%s", sanitize_log(project_id)
        )
        return {"worktrees": [], "error": "Failed to list worktrees."}


@router.get("/{project_id}/tasks")
async def list_project_tasks(project_id: str):
    """List all tasks for a specific project.

    Returns tasks array directly (not wrapped) because
    the frontend api-client.ts adds the {success, data} wrapper automatically.
    """
    # Import here to avoid circular import
    from . import tasks as tasks_module

    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[project_id]["path"])
    spec_dirs = tasks_module.get_spec_dirs(project_path)

    repo = tasks_module.project_repo(projects[project_id])  # W5 (#218): target repo
    all_tasks = []
    for spec_dir in spec_dirs:
        task = tasks_module.spec_to_task(project_id, spec_dir)
        task.repo = repo
        all_tasks.append(tasks_module.task_to_dict(task))

    # Sort by created_at descending
    all_tasks.sort(key=lambda t: t.get("createdAt", ""), reverse=True)

    return all_tasks


class TaskCreateRequest(BaseModel):
    """Request model for creating a task via project endpoint."""

    title: str = Field(default="", description="Task title (optional, auto-generated if empty)")
    description: str = Field(..., min_length=1, description="Task description (required)")
    metadata: dict | None = Field(default=None, description="Optional task metadata")


@router.post("/{project_id}/tasks")
async def create_project_task(project_id: str, task_data: TaskCreateRequest):
    """Create a new task in a project.

    This endpoint delegates to the tasks module for actual creation.
    """
    import json
    from datetime import datetime

    from . import tasks as tasks_module

    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Auto-generate title from description if empty
    title = task_data.title.strip()
    if not title:
        # Generate title from first line/sentence of description
        desc_lines = task_data.description.strip().split("\n")
        first_line = desc_lines[0].strip()
        # Truncate to reasonable length
        title = first_line[:80] + ("..." if len(first_line) > 80 else "")
        if not title:
            title = "New Task"

    # Use the create_task logic
    project_path = Path(projects[project_id]["path"])

    # Ensure .pfactory/specs exists
    specs_dir = project_path / ".pfactory" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    # Generate spec ID and create directory
    spec_id = tasks_module.get_next_spec_id(project_path, title)
    spec_dir = specs_dir / spec_id
    spec_dir.mkdir()

    # Create initial spec.md
    spec_content = f"""# {title}

{task_data.description}

## Acceptance Criteria

- [ ] Feature works as described
- [ ] Tests pass
- [ ] Code review approved

## Notes

Created via PFactory Web UI
"""
    (spec_dir / "spec.md").write_text(spec_content)

    # Create requirements.json
    requirements = {
        "title": title,
        "description": task_data.description,
        "created_at": datetime.now().isoformat(),
    }
    if task_data.metadata:
        requirements["metadata"] = task_data.metadata
    (spec_dir / "requirements.json").write_text(json.dumps(requirements, indent=2))

    # Create task_metadata.json for phase_config.py to read model/thinking settings
    # This file is read by the backend to determine per-phase model and thinking levels
    if task_data.metadata:
        task_metadata = {}
        # Copy model-related fields that phase_config.py expects
        # Also include 'mode' for Quick Mode prompt selection and 'requireReviewBeforeCoding' for approval gate
        # Also include selectedSkills so agent_service.py can inject skill context
        model_fields = [
            "model",
            "thinkingLevel",
            "isAutoProfile",
            "phaseModels",
            "phaseThinking",
            "mode",
            "requireReviewBeforeCoding",
            "selectedSkills",
            "enableRemoteControl",
            "enableDelegation",
        ]
        for field in model_fields:
            if field in task_data.metadata:
                task_metadata[field] = task_data.metadata[field]

        if task_metadata:
            (spec_dir / "task_metadata.json").write_text(json.dumps(task_metadata, indent=2))

    task = tasks_module.spec_to_task(project_id, spec_dir)
    return tasks_module.task_to_dict(task)


@router.post("/{project_id}/tasks/{spec_id}/logs/watch")
async def watch_project_task_logs(project_id: str, spec_id: str):
    """
    Start watching task logs (stub endpoint for frontend compatibility).

    Note: Log streaming is handled via WebSocket, this endpoint is a no-op
    that prevents 404 errors in the frontend.
    """
    return {"success": True, "message": "Log watching handled via WebSocket"}


@router.post("/{project_id}/tasks/{spec_id}/logs/unwatch")
async def unwatch_project_task_logs(project_id: str, spec_id: str):
    """
    Stop watching task logs (stub endpoint for frontend compatibility).

    Note: Log streaming is handled via WebSocket, this endpoint is a no-op
    that prevents 404 errors in the frontend.
    """
    return {"success": True, "message": "Log unwatching handled via WebSocket"}


@router.get("/{project_id}/tasks/{spec_id}/logs")
async def get_project_task_logs(project_id: str, spec_id: str):
    """Get logs for a task (delegates to tasks router)."""
    from . import tasks as tasks_module

    task_id = f"{project_id}:{spec_id}"
    return await tasks_module.get_task_logs(task_id)


# --------------------------------------------------------------------------
# Task Archive Routes
# --------------------------------------------------------------------------


class ArchiveTasksRequest(BaseModel):
    """Request to archive tasks."""

    taskIds: list[str] = Field(..., description="List of task IDs to archive")
    version: str | None = Field(None, description="Version tag for the archive (e.g., 'v1.2.0')")


class UnarchiveTasksRequest(BaseModel):
    """Request to unarchive tasks."""

    taskIds: list[str] = Field(..., description="List of task IDs to unarchive")


@router.post("/{project_id}/tasks/archive")
async def archive_tasks(project_id: str, request: ArchiveTasksRequest):
    """Archive completed tasks.

    Adds archivedAt timestamp and optional version to task metadata.
    Archived tasks remain in their spec directories but are hidden from
    the default Kanban view.
    """
    projects = load_projects()

    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[project_id]["path"])
    specs_dir = project_path / ".pfactory" / "specs"

    archived_count = 0
    errors = []

    for task_id in request.taskIds:
        # Task ID format is "project_id:spec_id"
        if ":" in task_id:
            _, spec_id = task_id.split(":", 1)
        else:
            spec_id = task_id

        # The component is caller-supplied and is about to be joined onto the
        # specs root and then read from / written to. Path joins collapse
        # traversal silently, so validate before joining, never after.
        try:
            spec_id = safe_spec_component(spec_id)
        except ValueError:
            errors.append(f"Task {task_id} is not a valid task id")
            continue

        spec_dir = specs_dir / spec_id
        if not spec_dir.exists():
            errors.append(f"Task {spec_id} not found")
            continue

        # Update test_plan.json with archive metadata
        plan_file = spec_dir / "test_plan.json"
        plan = {}
        if plan_file.exists():
            try:
                plan = json.loads(plan_file.read_text())
            except json.JSONDecodeError:
                plan = {}

        # Add archive metadata
        plan["archivedAt"] = datetime.now().isoformat()
        if request.version:
            plan["archivedInVersion"] = request.version

        plan_file.write_text(json.dumps(plan, indent=2))
        archived_count += 1

    if errors and archived_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="; ".join(errors),
        )

    return {
        "success": True,
        "archivedCount": archived_count,
        "errors": errors if errors else None,
    }


@router.post("/{project_id}/tasks/unarchive")
async def unarchive_tasks(project_id: str, request: UnarchiveTasksRequest):
    """Unarchive tasks.

    Removes archivedAt and archivedInVersion from task metadata,
    making them visible in the Kanban board again.
    """
    projects = load_projects()

    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[project_id]["path"])
    specs_dir = project_path / ".pfactory" / "specs"

    unarchived_count = 0
    errors = []

    for task_id in request.taskIds:
        # Task ID format is "project_id:spec_id"
        if ":" in task_id:
            _, spec_id = task_id.split(":", 1)
        else:
            spec_id = task_id

        # The component is caller-supplied and is about to be joined onto the
        # specs root and then read from / written to. Path joins collapse
        # traversal silently, so validate before joining, never after.
        try:
            spec_id = safe_spec_component(spec_id)
        except ValueError:
            errors.append(f"Task {task_id} is not a valid task id")
            continue

        spec_dir = specs_dir / spec_id
        if not spec_dir.exists():
            errors.append(f"Task {spec_id} not found")
            continue

        # Update test_plan.json to remove archive metadata
        plan_file = spec_dir / "test_plan.json"
        if not plan_file.exists():
            errors.append(f"Task {spec_id} has no plan file")
            continue

        try:
            plan = json.loads(plan_file.read_text())
        except json.JSONDecodeError:
            errors.append(f"Task {spec_id} has invalid plan file")
            continue

        # Remove archive metadata
        plan.pop("archivedAt", None)
        plan.pop("archivedInVersion", None)

        plan_file.write_text(json.dumps(plan, indent=2))
        unarchived_count += 1

    if errors and unarchived_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="; ".join(errors),
        )

    return {
        "success": True,
        "unarchivedCount": unarchived_count,
        "errors": errors if errors else None,
    }
