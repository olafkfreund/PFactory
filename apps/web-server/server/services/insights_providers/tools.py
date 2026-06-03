"""
Reusable tool definitions and execution for insights chat providers.

Provides read-only tools (read_file, list_directory, search_code) sandboxed
to the project directory. Tools use OpenAI-compatible function-calling schema.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_FILE_LINES = 500
MAX_FILE_BYTES = 50 * 1024  # 50KB
MAX_SEARCH_RESULTS = 50
SEARCH_TIMEOUT = 10


def _validate_path(project_path: Path, requested: str) -> Path:
    """Resolve requested path and ensure it stays within project_path."""
    resolved_project = project_path.resolve()
    resolved = (resolved_project / requested).resolve()
    if not str(resolved).startswith(str(resolved_project)):
        raise ValueError(f"Path escapes project directory: {requested}")
    return resolved


def _read_file(project_path: Path, args: dict) -> str:
    file_path = args.get("file_path", "")
    if not file_path:
        return "Error: file_path is required"

    resolved = _validate_path(project_path, file_path)
    if not resolved.is_file():
        return f"Error: Not a file or does not exist: {file_path}"

    try:
        raw = resolved.read_bytes()
    except PermissionError:
        return f"Error: Permission denied: {file_path}"

    if b"\x00" in raw[:8192]:
        return f"Binary file: {file_path} ({len(raw)} bytes)"

    if len(raw) > MAX_FILE_BYTES:
        text = raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
        lines = text.splitlines()[:MAX_FILE_LINES]
        numbered = [f"{i+1}\t{line}" for i, line in enumerate(lines)]
        return "\n".join(numbered) + f"\n\n[Truncated — showing first {len(lines)} lines / {MAX_FILE_BYTES // 1024}KB of {len(raw)} bytes]"

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) > MAX_FILE_LINES:
        lines = lines[:MAX_FILE_LINES]
        numbered = [f"{i+1}\t{line}" for i, line in enumerate(lines)]
        return "\n".join(numbered) + f"\n\n[Truncated — showing first {MAX_FILE_LINES} of {len(text.splitlines())} lines]"

    numbered = [f"{i+1}\t{line}" for i, line in enumerate(lines)]
    return "\n".join(numbered)


def _list_directory(project_path: Path, args: dict) -> str:
    dir_path = args.get("path", ".")
    resolved = _validate_path(project_path, dir_path)
    if not resolved.is_dir():
        return f"Error: Not a directory or does not exist: {dir_path}"

    try:
        entries = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return f"Error: Permission denied: {dir_path}"

    lines = []
    for entry in entries:
        prefix = "📁 " if entry.is_dir() else "📄 "
        rel = entry.relative_to(project_path.resolve())
        lines.append(f"{prefix}{rel}")

    if not lines:
        return f"Empty directory: {dir_path}"
    return "\n".join(lines)


def _search_code(project_path: Path, args: dict) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        return "Error: pattern is required"

    glob_filter = args.get("glob", None)
    max_results = min(int(args.get("max_results", MAX_SEARCH_RESULTS)), MAX_SEARCH_RESULTS)

    cmd = [
        "grep", "-rn", "--include=*",
        "-m", str(max_results),
    ]

    if glob_filter:
        cmd = ["grep", "-rn", f"--include={glob_filter}", "-m", str(max_results)]

    cmd.extend([pattern, "."])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=SEARCH_TIMEOUT,
            cwd=str(project_path.resolve()),
        )
        output = result.stdout.strip()
        if not output:
            return f"No matches found for pattern: {pattern}"

        lines = output.splitlines()[:max_results]
        return "\n".join(lines)
    except subprocess.TimeoutExpired:
        return f"Search timed out after {SEARCH_TIMEOUT}s for pattern: {pattern}"
    except Exception as e:
        return f"Search error: {e}"


_TOOL_MAP = {
    "read_file": _read_file,
    "list_directory": _list_directory,
    "search_code": _search_code,
}


def execute_tool(name: str, args: dict, project_path: Path) -> str:
    """Execute a tool by name. Never raises — errors become result strings."""
    try:
        fn = _TOOL_MAP.get(name)
        if not fn:
            return f"Unknown tool: {name}"
        return fn(project_path, args)
    except ValueError as e:
        return f"Security error: {e}"
    except Exception as e:
        logger.error(f"[tools] Error executing {name}: {e}", exc_info=True)
        return f"Tool error: {e}"


def get_tool_definitions() -> list[dict]:
    """Return OpenAI-compatible tool definitions for the API payload."""
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the contents of a file in the project. Returns numbered lines. Capped at 500 lines / 50KB.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Path relative to the project root (e.g. 'src/main.py', 'README.md')",
                        },
                    },
                    "required": ["file_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List files and subdirectories at a given path in the project (1 level deep).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory path relative to the project root. Use '.' for root.",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_code",
                "description": "Search for a regex pattern across project files. Returns matching lines with file paths and line numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Regex pattern to search for (grep -E syntax).",
                        },
                        "glob": {
                            "type": "string",
                            "description": "Optional file glob filter (e.g. '*.py', '*.ts').",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of matches to return (default 50, max 50).",
                        },
                    },
                    "required": ["pattern"],
                },
            },
        },
    ]
