"""PFactory test-pipeline runners.

Distinct from ``apps.backend.tools.executor.ToolExecutor`` which executes
Claude Agent SDK tool calls (Read/Write/Bash) inside an agent session.
This subpackage is the *test pipeline* execution layer — Docker for
runtime lanes (functional/mutation/dast/fuzz) and native pass-through
for static lanes (sast/deps/secrets).

Public surface:
  - DockerRunner — sandboxed container execution
  - DockerRunResult — captured stdout / exit / coverage / junit
  - dispatch_lane — route a Subtask to the right runner per its lane
  - lang_registry — per-language tool lookup
"""

from .docker_runner import (
    DockerRunner,
    DockerRunnerError,
    DockerRunResult,
    DockerTimeoutError,
)
from .lane_dispatch import (
    LaneNotImplementedError,
    dispatch_lane,
)
from .lang_registry import (
    UnsupportedLanguageError,
    get_tool_for_lane,
    languages_supporting_lane,
)

__all__ = [
    "DockerRunResult",
    "DockerRunner",
    "DockerRunnerError",
    "DockerTimeoutError",
    "LaneNotImplementedError",
    "UnsupportedLanguageError",
    "dispatch_lane",
    "get_tool_for_lane",
    "languages_supporting_lane",
]
