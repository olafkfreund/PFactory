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
import urllib.parse
from datetime import datetime
from pathlib import Path

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ..auth import _try_decode_jwt
from ..config import get_settings
from ..services.git_utils import confine_to_workspace  # #335

logger = logging.getLogger(__name__)

router = APIRouter()


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
    try:
        base = confine_to_workspace(base_path)  # #335
    except ValueError:
        return {"success": False, "error": "path outside the allowed workspace", "data": []}

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
                if entry.name.startswith(".") or entry.name in (
                    "node_modules",
                    "__pycache__",
                    "venv",
                    ".venv",
                    "dist",
                    "build",
                    "target",
                    ".git",
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
                    # Skip the PFactory app itself
                    if _is_app_internal_path(entry):
                        continue
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
    try:
        full_path = confine_to_workspace(path)  # #335
    except ValueError:
        return {"success": False, "error": "path outside the allowed workspace", "data": None}

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
    try:
        full_path = confine_to_workspace(path)  # #335
    except ValueError:
        return {"success": False, "error": "path outside the allowed workspace", "data": None}

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
    try:
        file_path = confine_to_workspace(path)  # #335
        root_path = confine_to_workspace(root)  # #335
    except ValueError:
        raise HTTPException(status_code=400, detail="path outside the allowed workspace")

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
        attr = match.group(1)  # e.g. src= or href=
        quote = match.group(2)  # quote character (" or ')
        url = match.group(3)  # the URL value

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

        params = urllib.parse.urlencode(
            {
                "path": str(resolved),
                "root": str(root_path),
                "token": token,
            }
        )
        return f"{attr}={quote}/api/files/serve?{params}{quote}"

    # Rewrite src="..." and href="..." (both quote styles)
    rewritten = re.sub(
        r"""(src|href)\s*=\s*(["'])(.*?)\2""",
        _rewrite_url,
        html_content,
    )

    return HTMLResponse(content=rewritten, media_type="text/html")
