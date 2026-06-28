"""
Changelog and Insights routes.

Handles changelog generation and AI-powered insights chat.
"""

import asyncio
import base64
import json
import logging
import re
from pathlib import Path as FilePath

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel

from ..services.insights_service import get_insights_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# Helper Functions
# ============================================

def extract_last_version_from_changelog(content: str) -> str | None:
    """
    Extract the most recent version number from changelog content.

    Supports common changelog version header formats:
    - ## [1.2.3] - Keep a Changelog format with brackets
    - ## 1.2.3 - Simple semver header
    - ## [1.2.3] (2024-01-01) - With date in parentheses
    - ## v1.2.3 - With 'v' prefix
    - ## [v1.2.3] - Bracketed with 'v' prefix

    Returns the version number without brackets or 'v' prefix.
    Returns None if no version header is found.
    """
    if not content:
        return None

    # Pattern to match version headers like:
    # ## [1.2.3] or ## 1.2.3 or ## [v1.2.3] or ## v1.2.3
    # The version can be followed by optional date, link reference, or other text
    # Captures the semantic version number (X.Y.Z format, optionally with pre-release/build metadata)
    version_pattern = re.compile(
        r'^##\s+\[?v?(\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?)\]?',
        re.MULTILINE
    )

    match = version_pattern.search(content)
    if match:
        return match.group(1)

    return None


# ============================================
# Request/Response Models
# ============================================

class DoneTasksRequest(BaseModel):
    tasks: list[dict]


class LoadSpecsRequest(BaseModel):
    taskIds: list[str]


class GitHistoryOptions(BaseModel):
    type: str  # 'recent', 'since-date', 'tag-range', 'since-version'
    count: int | None = None
    sinceDate: str | None = None
    fromTag: str | None = None
    toTag: str | None = None
    includeMergeCommits: bool = False


class BranchDiffOptions(BaseModel):
    baseBranch: str
    compareBranch: str
    baseBranchRef: str | None = None
    compareBranchRef: str | None = None


class ChangelogGenerateRequest(BaseModel):
    sourceMode: str  # 'tasks', 'git-history', 'branch-diff'
    version: str
    date: str
    format: str  # 'keep-a-changelog', 'simple-list', 'github-release'
    audience: str  # 'technical', 'user-facing', 'marketing'
    emojiLevel: str | None = None
    customInstructions: str | None = None
    taskIds: list[str] | None = None
    gitHistory: GitHistoryOptions | None = None
    branchDiff: BranchDiffOptions | None = None


class ChangelogSaveRequest(BaseModel):
    content: str
    version: str
    format: str = "markdown"


class SuggestVersionRequest(BaseModel):
    taskIds: list[str]


class SuggestVersionCommitsRequest(BaseModel):
    commits: list[dict]


class CommitsPreviewRequest(BaseModel):
    options: dict
    mode: str  # 'history' or 'branch-diff'


class SaveImageRequest(BaseModel):
    imageData: str  # base64 encoded
    filename: str


# ============================================
# Changelog Routes
# ============================================

@router.post("/done-tasks")
async def get_changelog_done_tasks(projectId: str = Path(...), request: DoneTasksRequest = ...):
    """Get completed tasks suitable for changelog."""
    # Filter tasks that are done
    done_tasks = [t for t in request.tasks if t.get("status") == "done"]
    return {"success": True, "data": done_tasks}


@router.post("/specs")
async def load_task_specs(projectId: str = Path(...), request: LoadSpecsRequest = ...):
    """Load spec details for tasks."""
    from .projects import load_projects

    projects = load_projects()
    if projectId not in projects:
        return {"success": False, "error": f"Project {projectId} not found"}

    project_path = FilePath(projects[projectId]["path"])
    specs_dir = project_path / ".pfactory" / "specs"

    specs = []
    for task_id in request.taskIds:
        # Try to find spec.md for this task
        # Task IDs are like "001-feature-name"
        spec_path = specs_dir / task_id / "spec.md"
        if spec_path.exists():
            try:
                content = spec_path.read_text()
                specs.append({
                    "taskId": task_id,
                    "content": content,
                    "path": str(spec_path.relative_to(project_path))
                })
            except Exception as e:
                specs.append({
                    "taskId": task_id,
                    "content": None,
                    "error": str(e)
                })
        else:
            # Try finding by glob pattern for numeric prefix
            matching = list(specs_dir.glob(f"{task_id}*/spec.md"))
            if matching:
                try:
                    content = matching[0].read_text()
                    specs.append({
                        "taskId": task_id,
                        "content": content,
                        "path": str(matching[0].relative_to(project_path))
                    })
                except Exception as e:
                    specs.append({
                        "taskId": task_id,
                        "content": None,
                        "error": str(e)
                    })
            else:
                specs.append({
                    "taskId": task_id,
                    "content": None,
                    "error": "Spec not found"
                })

    return {"success": True, "data": specs}


@router.post("/generate")
async def generate_changelog(projectId: str = Path(...), request: ChangelogGenerateRequest = ...):
    """Generate changelog using AI."""
    from ..services.changelog_service import get_changelog_service
    from .projects import load_projects

    projects = load_projects()
    if projectId not in projects:
        raise HTTPException(status_code=404, detail="Project not found")

    project_path = FilePath(projects[projectId]["path"])
    service = get_changelog_service()

    # Check if already running
    if service.is_running(projectId):
        return {"success": False, "error": "Generation already in progress"}

    # Convert request to dict for service
    request_dict = {
        "sourceMode": request.sourceMode,
        "version": request.version,
        "date": request.date,
        "format": request.format,
        "audience": request.audience,
        "emojiLevel": request.emojiLevel,
        "customInstructions": request.customInstructions,
        "taskIds": request.taskIds,
    }

    # Add git history options if present
    if request.gitHistory:
        request_dict["gitHistory"] = {
            "type": request.gitHistory.type,
            "count": request.gitHistory.count,
            "sinceDate": request.gitHistory.sinceDate,
            "fromTag": request.gitHistory.fromTag,
            "toTag": request.gitHistory.toTag,
            "includeMergeCommits": request.gitHistory.includeMergeCommits,
        }

    # Add branch diff options if present
    if request.branchDiff:
        request_dict["branchDiff"] = {
            "baseBranch": request.branchDiff.baseBranch,
            "compareBranch": request.branchDiff.compareBranch,
            "baseBranchRef": request.branchDiff.baseBranchRef,
            "compareBranchRef": request.branchDiff.compareBranchRef,
        }

    # Start generation in background
    success = await service.start_generation(
        project_id=projectId,
        project_path=project_path,
        request=request_dict
    )

    if not success:
        return {"success": False, "error": "Failed to start generation"}

    return {"success": True, "message": "Changelog generation started"}


@router.post("/save")
async def save_changelog(projectId: str = Path(...), request: ChangelogSaveRequest = ...):
    """Save generated changelog and update project version files."""
    from .projects import load_projects

    projects = load_projects()
    if projectId not in projects:
        return {"success": False, "error": f"Project {projectId} not found"}

    project_path = FilePath(projects[projectId]["path"])
    changelog_path = project_path / "CHANGELOG.md"

    try:
        # 1. Save changelog content
        existing_content = ""
        if changelog_path.exists():
            existing_content = changelog_path.read_text()

        # Prepend new content or replace based on format
        if request.format == "prepend" and existing_content:
            # Find where to insert (after any header)
            lines = existing_content.split("\n")
            insert_idx = 0
            for i, line in enumerate(lines):
                if line.startswith("# "):
                    insert_idx = i + 1
                    # Skip any blank lines after header
                    while insert_idx < len(lines) and not lines[insert_idx].strip():
                        insert_idx += 1
                    break

            new_content = "\n".join(lines[:insert_idx]) + "\n\n" + request.content + "\n\n" + "\n".join(lines[insert_idx:])
        else:
            new_content = request.content

        changelog_path.write_text(new_content)

        # 2. Update version in project files
        updated_files = []

        # Detect and update package.json (Node.js/TypeScript projects)
        package_json = project_path / "package.json"
        if package_json.exists():
            try:
                with open(package_json, 'r') as f:
                    pkg = json.load(f)
                pkg['version'] = request.version
                with open(package_json, 'w') as f:
                    json.dump(pkg, f, indent=2)
                    f.write('\n')  # Add trailing newline
                updated_files.append("package.json")
            except Exception as e:
                logger.warning(f"Failed to update package.json: {e}")

        # Detect and update pyproject.toml (Python Poetry projects)
        pyproject_toml = project_path / "pyproject.toml"
        if pyproject_toml.exists():
            try:
                content = pyproject_toml.read_text()
                # Simple regex replacement for version line
                updated = re.sub(
                    r'(version\s*=\s*)["\']([^"\']+)["\']',
                    f'\\1"{request.version}"',
                    content,
                    count=1
                )
                pyproject_toml.write_text(updated)
                updated_files.append("pyproject.toml")
            except Exception as e:
                logger.warning(f"Failed to update pyproject.toml: {e}")

        # Detect and update __init__.py __version__ (Python packages)
        init_py_paths = list(project_path.glob("*/__init__.py"))
        for init_py in init_py_paths:
            try:
                content = init_py.read_text()
                if '__version__' in content:
                    updated = re.sub(
                        r'(__version__\s*=\s*)["\']([^"\']+)["\']',
                        f'\\1"{request.version}"',
                        content
                    )
                    init_py.write_text(updated)
                    updated_files.append(str(init_py.relative_to(project_path)))
                    break  # Only update first __init__.py with __version__
            except Exception as e:
                logger.warning(f"Failed to update {init_py}: {e}")

        return {
            "success": True,
            "data": {
                "path": str(changelog_path),
                "version": request.version,
                "updatedFiles": updated_files
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("")
async def read_existing_changelog(projectId: str = Path(...)):
    """Read existing CHANGELOG.md from project.

    Returns ExistingChangelog format with:
    - exists: boolean indicating if changelog file exists
    - content: the changelog content (if exists)
    - lastVersion: the most recent version extracted from headers (e.g., '1.2.3')
    - error: error message if reading failed

    Parses version headers in common formats:
    - ## [1.2.3] - Keep a Changelog format
    - ## 1.2.3 - Simple semver
    - ## v1.2.3 or ## [v1.2.3] - With 'v' prefix
    """
    from .projects import load_projects

    projects = load_projects()
    if projectId not in projects:
        return {"success": False, "error": f"Project {projectId} not found"}

    project_path = FilePath(projects[projectId]["path"])
    changelog_path = project_path / "CHANGELOG.md"

    if not changelog_path.exists():
        return {
            "success": True,
            "data": {
                "exists": False
            }
        }

    try:
        content = changelog_path.read_text()
        last_version = extract_last_version_from_changelog(content)

        return {
            "success": True,
            "data": {
                "exists": True,
                "content": content,
                "lastVersion": last_version
            }
        }
    except Exception as e:
        return {
            "success": True,
            "data": {
                "exists": True,
                "error": str(e)
            }
        }


@router.post("/suggest-version")
async def suggest_version(projectId: str = Path(...), request: SuggestVersionRequest = ...):
    """Suggest next version based on tasks."""
    return {
        "success": True,
        "data": {
            "suggestedVersion": "1.0.0",
            "currentVersion": "0.0.0",
            "bumpType": "minor"
        }
    }


@router.post("/suggest-version-commits")
async def suggest_version_from_commits(projectId: str = Path(...), request: SuggestVersionCommitsRequest = ...):
    """Suggest version based on commits."""
    return {
        "success": True,
        "data": {
            "suggestedVersion": "1.0.0",
            "currentVersion": "0.0.0",
            "bumpType": "minor"
        }
    }


@router.get("/branches")
async def get_changelog_branches(projectId: str = Path(...)):
    """Get git branches for changelog diff.

    Returns GitBranchInfo objects with {name, isRemote, isCurrent} for frontend dropdown components.
    """
    import subprocess

    from .projects import load_projects

    projects = load_projects()
    if projectId not in projects:
        return {"success": False, "error": f"Project {projectId} not found"}

    project_path = projects[projectId]["path"]

    try:
        # Get current branch name
        current_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10
        )
        current_branch = current_result.stdout.strip() if current_result.returncode == 0 else ""

        # Get all branches with format: refname:short and whether it's remote
        # Using %(HEAD) to detect current branch as backup
        result = subprocess.run(
            ["git", "branch", "-a", "--format=%(refname:short)|%(HEAD)"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip()}

        branch_objects = []
        seen_names: dict[str, int] = {}  # display_name -> index in branch_objects

        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue

            parts = line.split("|")
            branch_name = parts[0].strip()
            is_head = len(parts) > 1 and parts[1].strip() == "*"

            # Skip HEAD reference
            if branch_name.endswith("/HEAD") or branch_name == "HEAD":
                continue

            # Determine if remote and clean up name
            is_remote = branch_name.startswith("origin/")
            display_name = branch_name.replace("origin/", "") if is_remote else branch_name
            # ref is the actual git-resolvable reference
            git_ref = branch_name  # e.g., "origin/main" for remote, "master" for local

            # Determine if current branch
            is_current = is_head or display_name == current_branch

            if display_name in seen_names:
                if not is_remote:
                    # Local branch replaces remote entry (local takes priority)
                    idx = seen_names[display_name]
                    branch_objects[idx] = {
                        "name": display_name,
                        "ref": git_ref,
                        "isRemote": False,
                        "isCurrent": is_current
                    }
                # Skip remote duplicates when local already exists
                continue

            seen_names[display_name] = len(branch_objects)
            branch_objects.append({
                "name": display_name,
                "ref": git_ref,
                "isRemote": is_remote,
                "isCurrent": is_current
            })

        return {"success": True, "data": branch_objects}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Git command timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/tags")
async def get_changelog_tags(projectId: str = Path(...)):
    """Get git tags for changelog diff.

    Returns GitTagInfo objects with {name, date?, commit?} for frontend dropdown components.
    """
    import subprocess

    from .projects import load_projects

    projects = load_projects()
    if projectId not in projects:
        return {"success": False, "error": f"Project {projectId} not found"}

    project_path = projects[projectId]["path"]

    try:
        # Get all tags with their creation date and commit hash
        # Format: tagname|date|commit
        # Uses both %(*objectname:short) for annotated tags and %(objectname:short) for lightweight tags
        result = subprocess.run(
            ["git", "tag", "--sort=-v:refname", "--format=%(refname:short)|%(creatordate:iso-strict)|%(*objectname:short)%(objectname:short)"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip()}

        tag_objects = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue

            parts = line.split("|")
            tag_name = parts[0].strip()

            if not tag_name:
                continue

            tag_info = {"name": tag_name}

            # Add date if available (index 1)
            if len(parts) > 1 and parts[1].strip():
                tag_info["date"] = parts[1].strip()

            # Add commit if available (index 2)
            if len(parts) > 2 and parts[2].strip():
                # Take first 7 chars for short hash (annotated tags may concatenate both)
                commit_hash = parts[2].strip()[:7]
                tag_info["commit"] = commit_hash

            tag_objects.append(tag_info)

        return {"success": True, "data": tag_objects}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Git command timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/commits-preview")
async def get_commits_preview(projectId: str = Path(...), request: CommitsPreviewRequest = ...):
    """Get preview of commits for changelog."""
    import subprocess

    from .projects import load_projects

    projects = load_projects()
    if projectId not in projects:
        return {"success": False, "error": f"Project {projectId} not found"}

    project_path = projects[projectId]["path"]
    options = request.options
    mode = request.mode

    try:
        # Build git log command based on mode
        # Use NUL (%x00) as record separator and unit separator (%x1f) for fields
        # to safely handle commit messages containing pipes or special characters
        cmd = ["git", "log", "--format=%x00%H%x1f%s%x1f%an%x1f%ae%x1f%aI"]

        if mode == "branch-diff":
            # Compare two branches - use ref (git-resolvable) if provided, fall back to name
            base_branch = options.get("baseBranchRef") or options.get("baseBranch", "main")
            compare_branch = options.get("compareBranchRef") or options.get("compareBranch", "HEAD")
            cmd.append(f"{base_branch}..{compare_branch}")
        else:
            # History mode with various options
            history_type = options.get("type", "last-n")

            if history_type == "last-n":
                count = options.get("count", 20)
                cmd.extend(["-n", str(count)])
            elif history_type == "since-date":
                since_date = options.get("sinceDate")
                if since_date:
                    cmd.extend(["--since", since_date])
            elif history_type == "since-version":
                from_tag = options.get("fromTag")
                if from_tag:
                    cmd.append(f"{from_tag}..HEAD")
            elif history_type == "tag-range":
                from_tag = options.get("fromTag")
                to_tag = options.get("toTag", "HEAD")
                if from_tag:
                    cmd.append(f"{from_tag}..{to_tag}")

            # Optionally exclude merge commits
            if not options.get("includeMergeCommits", True):
                cmd.append("--no-merges")

        result = subprocess.run(
            cmd,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            # Check if it's just an empty result (no commits)
            if "unknown revision" in result.stderr.lower() or "bad revision" in result.stderr.lower():
                return {"success": True, "data": []}
            return {"success": False, "error": result.stderr.strip()}

        commits = []
        for record in result.stdout.split("\x00"):
            record = record.strip()
            if not record:
                continue
            parts = record.split("\x1f")
            if len(parts) >= 5:
                commits.append({
                    "hash": parts[0],
                    "message": parts[1],
                    "author": parts[2],
                    "email": parts[3],
                    "date": parts[4].strip(),
                    "selected": True  # Default to selected
                })

        return {"success": True, "data": commits}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Git command timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/images")
async def save_changelog_image(projectId: str = Path(...), request: SaveImageRequest = ...):
    """
    Save a base64 encoded image to the project's assets directory.

    Decodes the base64 image data and saves it to .pfactory/assets/
    with secure file permissions.

    Args:
        projectId: The project ID
        request: SaveImageRequest containing imageData (base64) and filename

    Returns:
        Success response with the relative path to the saved image
    """
    from .projects import load_projects

    try:
        # Validate project exists
        projects = load_projects()
        if projectId not in projects:
            raise HTTPException(status_code=404, detail=f"Project {projectId} not found")

        # Get project path
        project_path = FilePath(projects[projectId]["path"])

        # Validate inputs
        if not request.imageData or not request.imageData.strip():
            return {"success": False, "error": "Image data is required"}

        if not request.filename or not request.filename.strip():
            return {"success": False, "error": "Filename is required"}

        # Sanitize filename - remove path separators to prevent directory traversal
        filename = request.filename.strip().replace("/", "_").replace("\\", "_")

        # Validate filename has an extension
        if "." not in filename:
            return {"success": False, "error": "Filename must include an extension (e.g., .png, .jpg)"}

        # Create assets directory if it doesn't exist
        assets_dir = project_path / ".pfactory" / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        # Decode base64 image data
        # Handle data URLs (e.g., "data:image/png;base64,iVBORw0KG...")
        image_data_str = request.imageData.strip()
        if "," in image_data_str and image_data_str.startswith("data:"):
            # Extract base64 portion after comma
            image_data_str = image_data_str.split(",", 1)[1]

        try:
            image_bytes = base64.b64decode(image_data_str)
        except Exception as decode_error:
            return {
                "success": False,
                "error": f"Failed to decode base64 image data: {str(decode_error)}"
            }

        # Validate decoded data is not empty
        if not image_bytes:
            return {"success": False, "error": "Decoded image data is empty"}

        # Save image to file
        image_path = assets_dir / filename
        image_path.write_bytes(image_bytes)

        # Set secure file permissions (owner read/write only)
        image_path.chmod(0o600)

        # Return relative path from project root
        relative_path = f".pfactory/assets/{filename}"

        return {
            "success": True,
            "data": {
                "path": relative_path,
                "filename": filename,
                "size": len(image_bytes)
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": f"Failed to save image: {str(e)}"}


# ============================================
# Insights Routes
# ============================================

insights_router = APIRouter()


def _get_project_path(project_id: str) -> FilePath:
    """Get project path from project ID (delegates to the canonical resolver)."""
    # Import here to avoid circular import.
    from .projects import resolve_project_path

    return resolve_project_path(project_id)


class InsightsMessageRequest(BaseModel):
    message: str
    modelConfig: dict | None = None


@insights_router.get("/providers")
async def detect_insights_providers(projectId: str = Path(...)):
    """Detect all available LLM providers for insights chat."""
    from ..services.insights_providers import detect_all_providers
    providers = await detect_all_providers()
    return {
        "success": True,
        "data": [p.to_dict() for p in providers],
    }


class GenerateTaskRequest(BaseModel):
    modelConfig: dict | None = None


class CreateTaskRequest(BaseModel):
    title: str
    description: str
    metadata: dict | None = None


class RenameSessionRequest(BaseModel):
    title: str


class UpdateModelConfigRequest(BaseModel):
    provider: str | None = None
    profileId: str | None = None
    model: str | None = None
    thinkingLevel: str | None = None
    temperature: float | None = None


@insights_router.get("")
async def get_insights_session(projectId: str = Path(...)):
    """Get current insights session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    session = service.get_current_session(project_path, projectId)

    return {
        "success": True,
        "data": {
            "id": session.id,
            "projectId": session.project_id,
            "title": session.title,
            "messages": [
                {
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "suggestedTask": msg.suggested_task,
                    "toolsUsed": msg.tools_used,
                }
                for msg in session.messages
            ],
            "modelConfig": session.model_config,
            "createdAt": session.created_at,
            "updatedAt": session.updated_at,
        }
    }


@insights_router.post("/message")
async def send_insights_message(projectId: str = Path(...), request: InsightsMessageRequest = ...):
    """Send a message to insights AI."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()

    # Start message processing in background (non-blocking, tracked for cancellation)
    service.start_message(
        project_path=project_path,
        project_id=projectId,
        message=request.message,
        model_config=request.modelConfig,
    )

    return {"success": True}


@insights_router.post("/stop")
async def stop_insights_message(projectId: str = Path(...)):
    """Stop the currently running insights chat for a project."""
    service = get_insights_service()
    cancelled = service.stop_message(projectId)
    return {"success": True, "cancelled": cancelled}


@insights_router.delete("")
async def clear_insights_session(projectId: str = Path(...)):
    """
    Clear current insights session and create a new one.

    This endpoint:
    - Deletes the current active session (if it exists)
    - Creates a new empty session
    - Sets the new session as active
    - Returns the newly created session data

    Args:
        projectId: The project ID

    Returns:
        Success response with the newly created session data

    Raises:
        HTTPException: 404 if project not found
        HTTPException: 500 if session clearing fails
    """
    try:
        # Get project path (raises 404 if project not found)
        project_path = _get_project_path(projectId)

        # Get insights service
        service = get_insights_service()

        # Clear current session and create new one
        # This deletes the old session and returns the newly created session
        new_session = service.clear_session(project_path, projectId)

        # Return success with new session data (similar to new_insights_session endpoint)
        return {
            "success": True,
            "message": "Session cleared successfully",
            "data": {
                "id": new_session.id,
                "projectId": new_session.project_id,
                "title": new_session.title,
                "messageCount": len(new_session.messages),
                "createdAt": new_session.created_at,
                "updatedAt": new_session.updated_at,
            }
        }
    except HTTPException:
        # Re-raise HTTP exceptions (like 404 from _get_project_path)
        raise
    except Exception as e:
        # Log error and return 500
        import logging
        logging.getLogger(__name__).error(f"Failed to clear insights session: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear insights session: {str(e)}"
        )


@insights_router.post("/create-task")
async def create_task_from_insights(projectId: str = Path(...), request: CreateTaskRequest = ...):
    """Create a task from insights conversation."""
    from .tasks import TaskCreate, create_task

    try:
        task_request = TaskCreate(
            project_id=projectId,
            title=request.title,
            description=request.description,
        )
        result = await create_task(task_request)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"create_task_from_insights failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@insights_router.post("/generate-task")
async def generate_task_from_chat(projectId: str = Path(...), request: GenerateTaskRequest = ...):
    """Generate a structured task (title + description) from the current chat session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()

    try:
        result = await service.generate_task_from_chat(
            project_path=project_path,
            project_id=projectId,
            model_config=request.modelConfig,
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"generate_task_from_chat failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@insights_router.get("/sessions")
async def list_insights_sessions(projectId: str = Path(...)):
    """List all insights sessions."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    sessions = service.list_sessions(project_path)
    return {"success": True, "data": sessions}


@insights_router.post("/sessions")
async def new_insights_session(projectId: str = Path(...)):
    """Create a new insights session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    session = service.create_session(project_path, projectId)

    return {
        "success": True,
        "data": {
            "id": session.id,
            "projectId": session.project_id,
            "title": session.title,
            "messages": [],
            "createdAt": session.created_at,
            "updatedAt": session.updated_at,
        }
    }


@insights_router.post("/sessions/{sessionId}/switch")
async def switch_insights_session(projectId: str = Path(...), sessionId: str = Path(...)):
    """Switch to a different insights session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    session = service.switch_session(project_path, sessionId)

    if not session:
        raise HTTPException(status_code=404, detail=f"Session {sessionId} not found")

    return {
        "success": True,
        "data": {
            "id": session.id,
            "projectId": session.project_id,
            "title": session.title,
            "messages": [
                {
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "suggestedTask": msg.suggested_task,
                    "toolsUsed": msg.tools_used,
                }
                for msg in session.messages
            ],
            "modelConfig": session.model_config,
            "createdAt": session.created_at,
            "updatedAt": session.updated_at,
        }
    }


@insights_router.delete("/sessions/{sessionId}")
async def delete_insights_session(projectId: str = Path(...), sessionId: str = Path(...)):
    """Delete an insights session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    result = service.delete_session(project_path, sessionId)
    return {"success": result["deleted"], "data": {"switchedTo": result.get("switchedTo")}}


@insights_router.patch("/sessions/{sessionId}")
async def rename_insights_session(projectId: str = Path(...), sessionId: str = Path(...), request: RenameSessionRequest = ...):
    """Rename an insights session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    success = service.rename_session(project_path, sessionId, request.title)
    return {"success": success}


@insights_router.patch("/sessions/{sessionId}/model")
async def update_insights_model_config(
    projectId: str = Path(...),
    sessionId: str = Path(...),
    request: UpdateModelConfigRequest = ...
):
    """Update model config for a session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    config = {k: v for k, v in request.model_dump().items() if v is not None}
    success = service.update_model_config(project_path, sessionId, config)
    return {"success": success}
