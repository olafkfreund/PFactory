"""
ToolExecutor — Provider-agnostic local tool execution
======================================================

Executes tool calls (Read, Write, Edit, Bash, Glob, Grep) locally with
security enforcement.  Designed to be reused by any LLM provider that
supports tool/function calling (Ollama, OpenAI-compatible, etc.).

Security layers:
    1. Path boundary — all file paths must resolve within ``working_dir``
    2. Input validation — reuses ``security.tool_input_validator``
    3. Bash allowlist — reuses ``security.hooks.bash_security_hook``

Usage::

    from pathlib import Path
    from tools.executor import ToolExecutor

    executor = ToolExecutor(working_dir=Path("/path/to/project"))
    result = await executor.execute("Read", {"file_path": "src/main.py"})
    print(result.content)   # file contents or error message
    print(result.is_error)  # True if execution failed
"""

from __future__ import annotations

import asyncio
import glob as glob_module
import logging
import shutil
from pathlib import Path
from typing import Any

from providers.types import ToolResultBlock
from security.tool_input_validator import validate_tool_input

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_READ_BYTES = 512 * 1024  # 512 KB max file read
_MAX_GLOB_RESULTS = 200
_MAX_GREP_RESULTS = 100
_DEFAULT_BASH_TIMEOUT = 120  # seconds
_MAX_BASH_TIMEOUT = 600  # seconds


async def _read_with_limit(
    path: Path, max_chars: int, encoding: str = "utf-8"
) -> tuple[str, bool]:
    """Read a text file with a character limit.

    Uses ``f.read(n)`` which reads at most *n* characters, avoiding TOCTOU
    races (no separate stat) and unbounded memory consumption.

    Returns ``(content, was_truncated)``.
    """
    def _do_read() -> tuple[str, bool]:
        with open(path, encoding=encoding, errors="replace") as f:
            data = f.read(max_chars + 1)
            truncated = len(data) > max_chars
            if truncated:
                data = data[:max_chars]
            return data, truncated

    return await asyncio.to_thread(_do_read)


class ToolExecutor:
    """Execute tool calls locally with security enforcement.

    All file operations are restricted to ``working_dir`` (path boundary).
    Bash commands are validated through the project's security allowlist.

    Args:
        working_dir: Root directory for all file operations and bash execution.
            File paths that escape this directory are rejected.
        bash_timeout: Default timeout for bash commands in seconds.
    """

    def __init__(
        self,
        working_dir: Path,
        bash_timeout: int = _DEFAULT_BASH_TIMEOUT,
        extra_roots: list[Path] | None = None,
    ) -> None:
        self._working_dir = working_dir.resolve()
        self._bash_timeout = min(bash_timeout, _MAX_BASH_TIMEOUT)
        # Additional directories file ops may touch beyond ``working_dir``.
        # PFactory threads the per-task spec/workspace dir here so agents that
        # run their file ops through this executor (e.g. the Ollama provider)
        # can write ``test_plan.json`` / generated tests into the workspace,
        # which lives outside the SUT project dir. Each must contain the path.
        self._extra_roots: list[Path] = [r.resolve() for r in (extra_roots or [])]

        # Dispatch table
        self._handlers: dict[str, Any] = {
            "Read": self._exec_read,
            "Write": self._exec_write,
            "Edit": self._exec_edit,
            "Bash": self._exec_bash,
            "Glob": self._exec_glob,
            "Grep": self._exec_grep,
        }

    async def execute(self, tool_name: str, tool_input: dict) -> ToolResultBlock:
        """Execute a tool call and return the result.

        Args:
            tool_name: Tool identifier (Read, Write, Edit, Bash, Glob, Grep).
            tool_input: Tool parameters dict.

        Returns:
            ``ToolResultBlock`` with ``content`` (str) and ``is_error`` (bool).
        """
        # Small/local models (e.g. qwen) sometimes emit tool arguments as a
        # JSON list (or other non-object) instead of a parameters object. The
        # downstream validator/handlers assume a dict, so a list here would
        # raise *outside* the handler try-block below and abort the whole
        # agent session. Reject it cleanly as a recoverable tool error so the
        # model can correct on the next turn.
        if not isinstance(tool_input, dict):
            return ToolResultBlock(
                content=(
                    f"Error: {tool_name} arguments must be a JSON object, got "
                    f"{type(tool_input).__name__}. Re-send the call with named "
                    "parameters, e.g. {\"file_path\": ..., \"content\": ...}."
                ),
                is_error=True,
            )

        # Input validation (reuse existing validator)
        is_valid, error_msg = validate_tool_input(tool_name, tool_input)
        if not is_valid:
            logger.warning("Tool input validation failed: %s", error_msg)
            return ToolResultBlock(content=f"Error: {error_msg}", is_error=True)

        handler = self._handlers.get(tool_name)
        if handler is None:
            return ToolResultBlock(
                content=f"Error: Unknown tool '{tool_name}'. "
                f"Available: {sorted(self._handlers.keys())}",
                is_error=True,
            )

        try:
            return await handler(tool_input)
        except Exception as exc:
            logger.error("Tool %s execution failed: %s", tool_name, exc, exc_info=True)
            return ToolResultBlock(
                content=f"Error executing {tool_name}: {exc}",
                is_error=True,
            )

    # ------------------------------------------------------------------
    # Path security
    # ------------------------------------------------------------------

    def _validate_path(self, file_path: str) -> tuple[Path, str | None]:
        """Resolve and validate a file path is within working_dir.

        Returns:
            (resolved_path, error_message) — error is None if valid.
        """
        try:
            # Handle relative paths by joining with working_dir
            p = Path(file_path)
            if not p.is_absolute():
                p = self._working_dir / p
            resolved = p.resolve()
        except (ValueError, OSError) as exc:
            return Path(), f"Invalid path '{file_path}': {exc}"

        # Check path boundary: within working_dir OR any extra allowed root.
        allowed_roots = [self._working_dir, *self._extra_roots]
        for root in allowed_roots:
            try:
                resolved.relative_to(root)
                return resolved, None
            except ValueError:
                continue

        return Path(), (
            f"Path '{file_path}' resolves to '{resolved}' which is outside "
            f"the project directory '{self._working_dir}'"
            + (
                f" and the allowed workspace root(s) {[str(r) for r in self._extra_roots]}"
                if self._extra_roots
                else ""
            )
            + ". Access denied."
        )

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def _exec_read(self, tool_input: dict) -> ToolResultBlock:
        """Read file with line numbers (cat -n style)."""
        file_path = tool_input["file_path"]
        offset = tool_input.get("offset", 1)  # 1-based
        limit = tool_input.get("limit")

        resolved, error = self._validate_path(file_path)
        if error:
            return ToolResultBlock(content=error, is_error=True)

        if not resolved.is_file():
            return ToolResultBlock(
                content=f"File not found: {file_path}", is_error=True
            )

        try:
            content, truncated = await _read_with_limit(resolved, _MAX_READ_BYTES)
        except PermissionError:
            return ToolResultBlock(
                content=f"Permission denied: {file_path}", is_error=True
            )

        lines = content.splitlines()

        # Apply offset/limit (offset is 1-based)
        start = max(0, offset - 1)
        if limit:
            end = start + limit
        else:
            end = len(lines)

        numbered = []
        for i, line in enumerate(lines[start:end], start=start + 1):
            numbered.append(f"{i:>6}\t{line}")

        result_text = "\n".join(numbered)
        if truncated:
            result_text += (
                f"\n\n[Truncated: file exceeded {_MAX_READ_BYTES:,} character limit. "
                "Use offset and limit to read specific portions.]"
            )
        return ToolResultBlock(content=result_text)

    async def _exec_write(self, tool_input: dict) -> ToolResultBlock:
        """Write content to a file, creating parent dirs if needed."""
        file_path = tool_input["file_path"]
        content = tool_input["content"]

        # Local models sometimes pass ``content`` as a list (e.g. lines, or a
        # JSON structure) rather than a string. write_text() requires str —
        # coerce: a list of strings joins to text; anything else serialises to
        # JSON (keeps a list/dict file like test_plan.json valid).
        if not isinstance(content, str):
            if isinstance(content, list) and all(isinstance(x, str) for x in content):
                content = "\n".join(content)
            else:
                import json as _json
                content = _json.dumps(content, indent=2, default=str)

        resolved, error = self._validate_path(file_path)
        if error:
            return ToolResultBlock(content=error, is_error=True)

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(resolved.write_text, content, "utf-8")
        except PermissionError:
            return ToolResultBlock(
                content=f"Permission denied: {file_path}", is_error=True
            )

        return ToolResultBlock(
            content=f"Successfully wrote {len(content)} bytes to {file_path}"
        )

    async def _exec_edit(self, tool_input: dict) -> ToolResultBlock:
        """Find-and-replace in a file (first occurrence)."""
        file_path = tool_input["file_path"]
        old_string = tool_input["old_string"]
        new_string = tool_input["new_string"]

        resolved, error = self._validate_path(file_path)
        if error:
            return ToolResultBlock(content=error, is_error=True)

        if not resolved.is_file():
            return ToolResultBlock(
                content=f"File not found: {file_path}", is_error=True
            )

        try:
            content = await asyncio.to_thread(resolved.read_text, "utf-8", "replace")
        except PermissionError:
            return ToolResultBlock(
                content=f"Permission denied reading: {file_path}", is_error=True
            )

        count = content.count(old_string)
        if count == 0:
            return ToolResultBlock(
                content=f"old_string not found in {file_path}. "
                "Ensure the string matches exactly (including whitespace).",
                is_error=True,
            )

        # Replace first occurrence
        new_content = content.replace(old_string, new_string, 1)

        try:
            await asyncio.to_thread(resolved.write_text, new_content, "utf-8")
        except PermissionError:
            return ToolResultBlock(
                content=f"Permission denied writing: {file_path}", is_error=True
            )

        return ToolResultBlock(
            content=f"Successfully edited {file_path} "
            f"(replaced 1 of {count} occurrence{'s' if count > 1 else ''})"
        )

    async def _exec_bash(self, tool_input: dict) -> ToolResultBlock:
        """Run a bash command with security validation."""
        command = tool_input["command"]
        timeout = min(
            tool_input.get("timeout", self._bash_timeout),
            _MAX_BASH_TIMEOUT,
        )

        # Security: validate via bash_security_hook
        try:
            from security.hooks import bash_security_hook

            hook_result = await bash_security_hook(
                input_data={
                    "tool_name": "Bash",
                    "tool_input": {"command": command},
                    "cwd": str(self._working_dir),
                },
            )
            if hook_result.get("decision") == "block":
                reason = hook_result.get("reason", "Command blocked by security policy")
                return ToolResultBlock(
                    content=f"Security: {reason}", is_error=True
                )
        except ImportError:
            logger.error("security.hooks not available — blocking all bash commands")
            return ToolResultBlock(
                content="Security: bash commands disabled (security module unavailable)",
                is_error=True,
            )
        except Exception as exc:
            logger.warning("Bash security check failed: %s", exc)
            return ToolResultBlock(
                content=f"Security validation error: {exc}", is_error=True
            )

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._working_dir),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout)
            )
        except asyncio.TimeoutError:
            return ToolResultBlock(
                content=f"Command timed out after {timeout}s: {command}",
                is_error=True,
            )

        output_parts = []
        if stdout:
            output_parts.append(stdout.decode("utf-8", errors="replace"))
        if stderr:
            output_parts.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace')}")

        output = "\n".join(output_parts) or "(no output)"

        if proc.returncode != 0:
            return ToolResultBlock(
                content=f"Exit code {proc.returncode}\n{output}",
                is_error=True,
            )

        return ToolResultBlock(content=output)

    async def _exec_glob(self, tool_input: dict) -> ToolResultBlock:
        """Find files matching a glob pattern."""
        pattern = tool_input["pattern"]
        search_dir = tool_input.get("path")

        if search_dir:
            resolved, error = self._validate_path(search_dir)
            if error:
                return ToolResultBlock(content=error, is_error=True)
            base = resolved
        else:
            base = self._working_dir

        if not base.is_dir():
            return ToolResultBlock(
                content=f"Directory not found: {base}", is_error=True
            )

        full_pattern = str(base / pattern)
        matches = await asyncio.to_thread(
            glob_module.glob, full_pattern, recursive=True
        )

        # Sort and cap results
        matches.sort()
        total = len(matches)
        matches = matches[:_MAX_GLOB_RESULTS]

        if not matches:
            return ToolResultBlock(content=f"No files match pattern: {pattern}")

        # Show paths relative to working dir for readability
        relative = []
        for m in matches:
            try:
                relative.append(str(Path(m).relative_to(self._working_dir)))
            except ValueError:
                relative.append(m)

        result = "\n".join(relative)
        if total > _MAX_GLOB_RESULTS:
            result += f"\n\n... and {total - _MAX_GLOB_RESULTS} more (capped at {_MAX_GLOB_RESULTS})"

        return ToolResultBlock(content=result)

    async def _exec_grep(self, tool_input: dict) -> ToolResultBlock:
        """Search file contents using regex."""
        pattern = tool_input["pattern"]
        search_path = tool_input.get("path")
        glob_filter = tool_input.get("glob")

        if search_path:
            resolved, error = self._validate_path(search_path)
            if error:
                return ToolResultBlock(content=error, is_error=True)
            target = str(resolved)
        else:
            target = str(self._working_dir)

        # Prefer ripgrep if available, else grep
        rg_path = shutil.which("rg")

        if rg_path:
            cmd = [rg_path, "-n", "--max-count", "100", "--no-heading"]
            if glob_filter:
                cmd.extend(["--glob", glob_filter])
            cmd.extend([pattern, target])
        else:
            cmd = ["grep", "-rn", "--max-count=100"]
            if glob_filter:
                cmd.extend(["--include", glob_filter])
            cmd.extend([pattern, target])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._working_dir),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30.0
            )
        except asyncio.TimeoutError:
            return ToolResultBlock(
                content=f"Grep timed out searching for: {pattern}",
                is_error=True,
            )
        except FileNotFoundError:
            return ToolResultBlock(
                content="Neither 'rg' nor 'grep' found on system.",
                is_error=True,
            )

        output = stdout.decode("utf-8", errors="replace").strip()

        if not output:
            return ToolResultBlock(content=f"No matches found for: {pattern}")

        # Cap output lines
        lines = output.splitlines()
        total = len(lines)
        if total > _MAX_GREP_RESULTS:
            lines = lines[:_MAX_GREP_RESULTS]
            lines.append(
                f"\n... {total - _MAX_GREP_RESULTS} more matches (capped at {_MAX_GREP_RESULTS})"
            )

        return ToolResultBlock(content="\n".join(lines))


__all__ = ["ToolExecutor"]
