"""
File browser and editor routes.

Handles file operations for the Monaco editor:
- Directory listing
- File read/write
- File search
- Git diff viewing
"""

import mimetypes
import re
import subprocess
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Literal

import logging

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ..auth import _try_decode_jwt
from ..config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class FileEntry(BaseModel):
    """Single file or directory entry."""

    name: str
    path: str
    type: Literal["file", "directory"]
    size: int = 0
    modified: str
    extension: str | None = None
    language: str | None = None


class DirectoryListing(BaseModel):
    """Directory listing response."""

    path: str
    entries: list[FileEntry]
    parent: str | None = None


class FileContent(BaseModel):
    """File content response."""

    path: str
    content: str
    size: int
    modified: str
    language: str | None = None
    encoding: str = "utf-8"


class FileWrite(BaseModel):
    """File write request."""

    content: str


class SearchResult(BaseModel):
    """Search result entry."""

    path: str
    line: int
    column: int
    content: str
    match: str


class SearchResponse(BaseModel):
    """Search response."""

    query: str
    results: list[SearchResult]
    total: int
    truncated: bool = False


class GitDiff(BaseModel):
    """Git diff response."""

    path: str
    original: str
    modified: str
    status: Literal["added", "modified", "deleted", "renamed"]


# --------------------------------------------------------------------------
# Helper Functions
# --------------------------------------------------------------------------


LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".json": "json",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".xml": "xml",
    ".svg": "xml",
    ".vue": "vue",
    ".svelte": "svelte",
}


def _is_app_internal_path(resolved_path: Path) -> bool:
    """Block access to the PFactory application directory itself.

    Exception: .pfactory/ subtrees (specs, worktrees) are user data and
    must stay reachable when the target project IS PFactory (dogfooding).
    """
    settings = get_settings()
    app_root = Path(settings.BACKEND_PATH).resolve().parent.parent  # PFactory root
    try:
        rel = resolved_path.resolve().relative_to(app_root)
    except ValueError:
        return False
    if rel.parts and rel.parts[0] == ".pfactory":
        return False
    return True


def detect_language(path: str) -> str | None:
    """Detect programming language from file extension."""
    ext = Path(path).suffix.lower()
    return LANGUAGE_MAP.get(ext)


def is_binary_file(path: Path) -> bool:
    """Check if a file is binary (not text)."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
            if b"\x00" in chunk:
                return True
            # Check for high ratio of non-text characters
            text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)))
            non_text = sum(1 for b in chunk if b not in text_chars)
            return non_text > len(chunk) * 0.3
    except Exception:
        return True


def resolve_path(project_id: str, relative_path: str) -> Path:
    """Resolve a relative path within a project, with security checks."""
    from .projects import load_projects  # Local import to avoid circular dependency
    projects = load_projects()

    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[project_id]["path"]).resolve()

    # Handle empty path as project root
    if not relative_path or relative_path == ".":
        return project_path

    # Resolve the full path
    full_path = (project_path / relative_path).resolve()

    # Security check: ensure path is within project
    try:
        full_path.relative_to(project_path)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path outside project directory",
        )

    return full_path


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Project Discovery Routes (for adding new projects)
# --------------------------------------------------------------------------


class DiscoveredProject(BaseModel):
    """A discovered project folder."""
    name: str
    path: str
    has_git: bool = False
    has_package_json: bool = False
    has_requirements: bool = False
    has_magestic_ai: bool = False
    has_claude_md: bool = False


@router.get("/discover")
async def discover_projects(
    base_path: str = Query(..., description="Base directory to scan for projects"),
    max_depth: int = Query(1, description="How deep to scan (1 = direct children only)"),
):
    """
    Discover potential project folders in a directory.
    Returns folders that look like projects (have .git, package.json, etc).
    """
    base = Path(base_path).expanduser().resolve()

    if not base.exists():
        return {"success": False, "error": f"Path does not exist: {base_path}", "data": []}

    if not base.is_dir():
        return {"success": False, "error": f"Path is not a directory: {base_path}", "data": []}

    projects = []

    def scan_directory(dir_path: Path, current_depth: int):
        if current_depth > max_depth:
            return

        try:
            for entry in sorted(dir_path.iterdir(), key=lambda e: e.name.lower()):
                if not entry.is_dir():
                    continue

                # Skip hidden directories and common non-project dirs
                if entry.name.startswith('.') or entry.name in (
                    'node_modules', '__pycache__', 'venv', '.venv',
                    'dist', 'build', 'target', '.git'
                ):
                    continue

                # Check for project indicators
                has_git = (entry / '.git').exists()
                has_package = (entry / 'package.json').exists()
                has_requirements = (entry / 'requirements.txt').exists() or (entry / 'pyproject.toml').exists()
                has_magestic_ai = (entry / '.pfactory').exists()
                has_claude_md = (entry / 'CLAUDE.md').exists()

                # If it looks like a project, add it
                if has_git or has_package or has_requirements:
                    # Skip the PFactory app itself
                    if _is_app_internal_path(entry):
                        continue
                    projects.append(DiscoveredProject(
                        name=entry.name,
                        path=str(entry),
                        has_git=has_git,
                        has_package_json=has_package,
                        has_requirements=has_requirements,
                        has_magestic_ai=has_magestic_ai,
                        has_claude_md=has_claude_md,
                    ))
                elif current_depth < max_depth:
                    # Not a project, but scan deeper
                    scan_directory(entry, current_depth + 1)
        except PermissionError:
            pass  # Skip directories we can't read

    scan_directory(base, 1)

    # Return array directly - api-client.ts adds the {success, data} wrapper
    return [p.model_dump() for p in projects]


@router.get("/list")
async def list_directory_direct(
    path: str = Query(..., description="Absolute path to directory"),
    show_hidden: bool = Query(False, description="Show hidden files"),
):
    """List contents of a directory by absolute path."""
    full_path = Path(path).expanduser().resolve()

    if _is_app_internal_path(full_path):
        return {"success": False, "error": "Access denied", "data": None}

    if not full_path.exists():
        return {"success": False, "error": "Directory not found", "data": None}

    if not full_path.is_dir():
        return {"success": False, "error": "Path is not a directory", "data": None}

    entries = []
    try:
        for entry in sorted(full_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            # Skip hidden files unless requested
            if entry.name.startswith(".") and not show_hidden:
                continue

            try:
                stat = entry.stat()
                file_entry = {
                    "name": entry.name,
                    "path": str(entry),
                    "type": "directory" if entry.is_dir() else "file",
                    "size": stat.st_size if entry.is_file() else 0,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "extension": entry.suffix.lower() if entry.is_file() else None,
                    "language": detect_language(entry.name) if entry.is_file() else None,
                }
                entries.append(file_entry)
            except (PermissionError, OSError):
                continue  # Skip files we can't access
    except PermissionError:
        return {"success": False, "error": "Permission denied", "data": None}

    parent = str(full_path.parent) if full_path.parent != full_path else None

    # Return raw data - api-client.ts adds {success, data} wrapper
    return {
        "path": str(full_path),
        "entries": entries,
        "parent": parent,
    }


@router.get("/read")
async def read_file_direct(
    path: str = Query(..., description="Absolute path to file"),
):
    """Read file contents by absolute path."""
    full_path = Path(path).expanduser().resolve()

    if _is_app_internal_path(full_path):
        return {"success": False, "error": "Access denied", "data": None}

    if not full_path.exists():
        return {"success": False, "error": "File not found", "data": None}

    if not full_path.is_file():
        return {"success": False, "error": "Path is not a file", "data": None}

    if is_binary_file(full_path):
        return {"success": False, "error": "Cannot read binary file", "data": None}

    try:
        content = full_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = full_path.read_text(encoding="latin-1")
        except Exception:
            return {"success": False, "error": "Unable to decode file", "data": None}
    except PermissionError:
        return {"success": False, "error": "Permission denied", "data": None}

    stat = full_path.stat()

    # Return raw data - api-client.ts adds {success, data} wrapper
    return {
        "path": str(full_path),
        "content": content,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "language": detect_language(str(full_path)),
    }


def _validate_serve_token(request: Request, token: str) -> bool:
    """Validate authentication for the /serve endpoint.

    Checks (in order):
    1. Authorization header (standard Bearer token flow)
    2. ``token`` query param (for rewritten HTML asset URLs)

    Returns True if the request is authenticated.
    """
    settings = get_settings()

    if settings.DISABLE_AUTH:
        return True

    # 1. Try Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        header_token = auth_header[7:]
        if _try_decode_jwt(header_token) is not None:
            return True
        if header_token == settings.API_TOKEN:
            return True

    # 2. Try query-param token (used by rewritten HTML asset URLs)
    if token:
        if _try_decode_jwt(token) is not None:
            return True
        if token == settings.API_TOKEN:
            return True

    return False


@router.get("/serve")
async def serve_project_file(
    request: Request,
    path: str = Query(..., description="Absolute path to the file to serve"),
    root: str = Query(..., description="Project root directory (for resolving relative URLs)"),
    token: str = Query(default="", description="Bearer token for authentication"),
):
    """Serve a project file with its correct MIME type.

    For HTML files, rewrites src= and href= attributes so that linked
    CSS/JS/images load through this same endpoint.  External URLs
    (http://, https://, //, data:, #, mailto:) are left untouched.
    """
    # Authenticate: check token from query param or Authorization header
    if not _validate_serve_token(request, token):
        raise HTTPException(status_code=401, detail="Authentication required")
    file_path = Path(path).expanduser().resolve()
    root_path = Path(root).expanduser().resolve()

    # Security: file must exist inside the declared project root
    if not root_path.is_dir():
        raise HTTPException(status_code=400, detail="Root is not a directory")
    try:
        file_path.relative_to(root_path)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied: path outside project root")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Determine MIME type
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    # Non-HTML files: serve directly with FileResponse
    if not mime_type.startswith("text/html"):
        return FileResponse(str(file_path), media_type=mime_type)

    # HTML files: rewrite asset URLs so linked resources load correctly
    try:
        html_content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        html_content = file_path.read_text(encoding="latin-1")

    # Carry the token through to rewritten URLs
    html_dir = file_path.parent

    def _rewrite_url(match: re.Match) -> str:
        attr = match.group(1)   # e.g. src= or href=
        quote = match.group(2)  # quote character (" or ')
        url = match.group(3)    # the URL value

        # Skip external / special URLs
        if url.startswith(("http://", "https://", "//", "data:", "#", "mailto:", "javascript:")):
            return match.group(0)

        # Resolve the URL to an absolute filesystem path
        if url.startswith("/"):
            # Absolute path from project root (e.g., /static/calculator.css)
            resolved = (root_path / url.lstrip("/")).resolve()
        else:
            # Relative path from HTML file's directory
            resolved = (html_dir / url).resolve()

        # Security: must stay within project root
        try:
            resolved.relative_to(root_path)
        except ValueError:
            return match.group(0)  # leave unchanged

        params = urllib.parse.urlencode({
            "path": str(resolved),
            "root": str(root_path),
            "token": token,
        })
        return f'{attr}={quote}/api/files/serve?{params}{quote}'

    # Rewrite src="..." and href="..." (both quote styles)
    rewritten = re.sub(
        r'''(src|href)\s*=\s*(["'])(.*?)\2''',
        _rewrite_url,
        html_content,
    )

    return HTMLResponse(content=rewritten, media_type="text/html")


@router.get("/{project_id}/list", response_model=DirectoryListing)
async def list_directory(
    project_id: str,
    path: str = Query("", description="Relative path within project"),
    show_hidden: bool = Query(False, description="Show hidden files"),
):
    """List contents of a directory."""
    full_path = resolve_path(project_id, path)

    if not full_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Directory not found",
        )

    if not full_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is not a directory",
        )

    entries = []
    for entry in sorted(full_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        # Skip hidden files unless requested
        if entry.name.startswith(".") and not show_hidden:
            continue

        stat = entry.stat()
        file_entry = FileEntry(
            name=entry.name,
            path=str(entry.relative_to(resolve_path(project_id, ""))),
            type="directory" if entry.is_dir() else "file",
            size=stat.st_size if entry.is_file() else 0,
            modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
            extension=entry.suffix.lower() if entry.is_file() else None,
            language=detect_language(entry.name) if entry.is_file() else None,
        )
        entries.append(file_entry)

    # Calculate parent path
    parent = None
    if path:
        parent_path = Path(path).parent
        parent = str(parent_path) if str(parent_path) != "." else ""

    return DirectoryListing(
        path=path or ".",
        entries=entries,
        parent=parent,
    )


@router.get("/{project_id}/read", response_model=FileContent)
async def read_file(
    project_id: str,
    path: str = Query(..., description="Relative path to file"),
):
    """Read file contents."""
    full_path = resolve_path(project_id, path)

    if not full_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )

    if not full_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is not a file",
        )

    if is_binary_file(full_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot read binary file",
        )

    try:
        content = full_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Try with latin-1 as fallback
        try:
            content = full_path.read_text(encoding="latin-1")
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unable to decode file",
            )

    stat = full_path.stat()

    return FileContent(
        path=path,
        content=content,
        size=stat.st_size,
        modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        language=detect_language(path),
    )


@router.put("/{project_id}/write")
async def write_file(
    project_id: str,
    path: str = Query(..., description="Relative path to file"),
    file_data: FileWrite = ...,
):
    """Write content to a file."""
    full_path = resolve_path(project_id, path)

    # Ensure parent directory exists
    full_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        full_path.write_text(file_data.content, encoding="utf-8")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to write file: {str(e)}",
        )

    return {"success": True, "path": path}


@router.delete("/{project_id}/delete")
async def delete_file(
    project_id: str,
    path: str = Query(..., description="Relative path to file or directory"),
):
    """Delete a file or directory."""
    full_path = resolve_path(project_id, path)

    if not full_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Path not found",
        )

    try:
        if full_path.is_dir():
            import shutil
            shutil.rmtree(full_path)
        else:
            full_path.unlink()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete: {str(e)}",
        )

    return {"success": True}


@router.get("/{project_id}/search", response_model=SearchResponse)
async def search_files(
    project_id: str,
    query: str = Query(..., description="Search query (regex supported)"),
    path: str = Query("", description="Directory to search in"),
    file_pattern: str = Query("*", description="File glob pattern"),
    max_results: int = Query(100, description="Maximum results to return"),
):
    """Search for text in files using ripgrep or fallback."""
    full_path = resolve_path(project_id, path)

    if not full_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Directory not found",
        )

    results = []
    truncated = False

    # Try ripgrep first
    try:
        cmd = [
            "rg",
            "--json",
            "--max-count", str(max_results),
            "--glob", file_pattern,
            query,
            str(full_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        for line in proc.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = __import__("json").loads(line)
                if data.get("type") == "match":
                    match_data = data["data"]
                    results.append(SearchResult(
                        path=str(Path(match_data["path"]["text"]).relative_to(full_path)),
                        line=match_data["line_number"],
                        column=match_data["submatches"][0]["start"] if match_data.get("submatches") else 0,
                        content=match_data["lines"]["text"].rstrip(),
                        match=match_data["submatches"][0]["match"]["text"] if match_data.get("submatches") else query,
                    ))
            except Exception:
                continue

    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Fallback to Python search
        pattern = re.compile(query, re.IGNORECASE)
        for file_path in full_path.rglob(file_pattern):
            if len(results) >= max_results:
                truncated = True
                break

            if not file_path.is_file() or is_binary_file(file_path):
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                for i, line in enumerate(content.split("\n"), 1):
                    match = pattern.search(line)
                    if match:
                        results.append(SearchResult(
                            path=str(file_path.relative_to(full_path)),
                            line=i,
                            column=match.start(),
                            content=line.rstrip(),
                            match=match.group(),
                        ))
                        if len(results) >= max_results:
                            truncated = True
                            break
            except Exception:
                continue

    return SearchResponse(
        query=query,
        results=results,
        total=len(results),
        truncated=truncated,
    )


@router.get("/{project_id}/diff")
async def get_git_diff(
    project_id: str,
    path: str = Query("", description="Path to get diff for (empty for all)"),
    base: str = Query("HEAD", description="Base commit/branch"),
):
    """Get git diff for files."""
    project_path = resolve_path(project_id, "")

    if not (project_path / ".git").exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Not a git repository",
        )

    try:
        cmd = ["git", "diff", "--name-status", base]
        if path:
            cmd.append(path)

        proc = subprocess.run(
            cmd,
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=30,
        )

        if proc.returncode != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Git error: {proc.stderr}",
            )

        diffs = []
        for line in proc.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            status_code = parts[0]
            file_path = parts[1] if len(parts) > 1 else ""

            status_map = {
                "A": "added",
                "M": "modified",
                "D": "deleted",
                "R": "renamed",
            }

            diffs.append({
                "path": file_path,
                "status": status_map.get(status_code[0], "modified"),
            })

        return {"base": base, "diffs": diffs}

    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Git operation timed out",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Git error: {str(e)}",
        )


# --------------------------------------------------------------------------
# Files Insights Router
# --------------------------------------------------------------------------

insights_router = APIRouter()


def _get_project_path(project_id: str) -> Path:
    """Get project path from project ID (delegates to the canonical resolver)."""
    # Import here to avoid circular import.
    from .projects import resolve_project_path

    return resolve_project_path(project_id)


@insights_router.delete("")
async def clear_insights_session(projectId: str):
    """
    Clear current files insights session and create a new one.

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
        from ..services.insights_service import get_insights_service
        service = get_insights_service()

        # Clear current session and create new one
        # This deletes the old session and returns the newly created session
        new_session = service.clear_session(project_path, projectId)

        # Return success with new session data (similar to new_insights_session endpoint)
        return {
            "success": True,
            "message": "Files insights session cleared successfully",
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
        logging.getLogger(__name__).error(f"Failed to clear files insights session: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear files insights session: {str(e)}"
        )
