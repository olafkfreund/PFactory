"""
Tool Definitions — JSON schemas for LLM tool calling
=====================================================

Defines tool schemas in the format expected by Ollama's ``/api/chat`` endpoint
(which follows the OpenAI function-calling convention).

Each tool has a ``type: "function"`` wrapper with ``function.name``,
``function.description``, and ``function.parameters`` (JSON Schema).

Usage::

    from tools.definitions import get_tool_definitions

    # Get all tool schemas
    all_tools = get_tool_definitions()

    # Get specific tools only
    read_write = get_tool_definitions(["Read", "Write"])
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Individual tool schemas
# ---------------------------------------------------------------------------

_TOOL_READ = {
    "type": "function",
    "function": {
        "name": "Read",
        "description": (
            "Read a file from the filesystem. Returns the file content with "
            "line numbers. Use offset and limit for large files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to read.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-based). Optional.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read. Optional.",
                },
            },
            "required": ["file_path"],
        },
    },
}

_TOOL_WRITE = {
    "type": "function",
    "function": {
        "name": "Write",
        "description": (
            "Write content to a file. Creates the file and parent directories "
            "if they don't exist. Overwrites existing content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to write.",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file.",
                },
            },
            "required": ["file_path", "content"],
        },
    },
}

_TOOL_EDIT = {
    "type": "function",
    "function": {
        "name": "Edit",
        "description": (
            "Edit a file by replacing an exact string match. The old_string "
            "must appear exactly once in the file for the replacement to succeed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to edit.",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to find and replace.",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text.",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
}

_TOOL_BASH = {
    "type": "function",
    "function": {
        "name": "Bash",
        "description": (
            "Execute a bash command and return its stdout and stderr. "
            "Commands run in the project working directory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds. Default 120, max 600.",
                },
            },
            "required": ["command"],
        },
    },
}

_TOOL_GLOB = {
    "type": "function",
    "function": {
        "name": "Glob",
        "description": (
            "Find files matching a glob pattern. Returns matching file paths "
            "relative to the search directory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.ts').",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in. Defaults to working directory.",
                },
            },
            "required": ["pattern"],
        },
    },
}

_TOOL_GREP = {
    "type": "function",
    "function": {
        "name": "Grep",
        "description": (
            "Search file contents using a regular expression pattern. "
            "Returns matching lines with file paths and line numbers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in. Defaults to working directory.",
                },
                "glob": {
                    "type": "string",
                    "description": "Glob filter for files (e.g. '*.py', '*.ts').",
                },
            },
            "required": ["pattern"],
        },
    },
}

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ALL_TOOLS: dict[str, dict] = {
    "Read": _TOOL_READ,
    "Write": _TOOL_WRITE,
    "Edit": _TOOL_EDIT,
    "Bash": _TOOL_BASH,
    "Glob": _TOOL_GLOB,
    "Grep": _TOOL_GREP,
}


def get_tool_definitions(tool_names: list[str] | None = None) -> list[dict]:
    """Return tool definitions filtered by name.

    Args:
        tool_names: List of tool names to include. If ``None``, returns all
            available tool definitions.

    Returns:
        List of tool definition dicts in Ollama/OpenAI function-calling format.

    Raises:
        ValueError: If a requested tool name is not recognised.
    """
    if tool_names is None:
        return list(_ALL_TOOLS.values())

    result = []
    for name in tool_names:
        if name not in _ALL_TOOLS:
            raise ValueError(
                f"Unknown tool: {name!r}. Available: {sorted(_ALL_TOOLS.keys())}"
            )
        result.append(_ALL_TOOLS[name])
    return result


__all__ = ["get_tool_definitions"]
