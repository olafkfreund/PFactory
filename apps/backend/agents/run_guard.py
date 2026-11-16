"""Anti-loop guardrail and run limits for agent sessions (PFactory#1008).

Port of AIFactory#2033. Three controls, all switchable from the environment:

  - ``PFACTORY_ACT_GUARDRAIL``     anti-loop guardrail, ON unless ``0|false|no|off``.
  - ``PFACTORY_RUN_TOKEN_CEILING`` per-run token ceiling (default 10M, ``0`` disables).
  - ``PFACTORY_MAX_TURNS``         SDK ``max_turns`` for a session (default 1000).

The guardrail runs three policies keyed on a tool-call signature (tool + args
hash): the same call failing repeatedly, any one tool failing repeatedly, and an
idempotent read returning the same result repeatedly. A halt is per session and
kept in memory: ``run_agent_session`` reads it with :func:`guard_halt`, stops the
stream and returns a typed error. Hooks never raise into the agent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = "PFACTORY_"
DEFAULT_TOKEN_CEILING = 10_000_000
DEFAULT_MAX_TURNS = 1000
# Read-only tools: the same result again means no progress. Bash is not here (a
# build or test command legitimately repeats and its output legitimately changes).
_IDEMPOTENT_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "NotebookRead"})


def _env(name: str) -> str:
    return (os.environ.get(_PREFIX + name) or "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        v = int(_env(name) or default)
        return v if v > 0 else default
    except ValueError:
        return default


def guardrail_enabled() -> bool:
    return _env("ACT_GUARDRAIL").lower() not in {"0", "false", "no", "off"}


def token_ceiling() -> int:
    """Per-run token ceiling (input + output); 0 disables."""
    raw = _env("RUN_TOKEN_CEILING")
    try:
        return max(0, int(raw)) if raw else DEFAULT_TOKEN_CEILING
    except ValueError:
        return DEFAULT_TOKEN_CEILING


def max_turns() -> int:
    return int(_env("MAX_TURNS")) if _env("MAX_TURNS").isdigit() else DEFAULT_MAX_TURNS


def _hash(obj: Any) -> str:
    try:
        blob = json.dumps(obj, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = repr(obj)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class _Sig:
    fail_streak: int = 0
    last_result: str | None = None
    same_result: int = 0


@dataclass
class Controller:
    """Rules on each tool call of one session; ``halt_reason`` is set on a halt."""

    repeat_fail: int = field(default_factory=lambda: _env_int("GUARDRAIL_REPEAT_FAIL", 5))
    tool_fail_halt: int = field(default_factory=lambda: _env_int("GUARDRAIL_TOOL_FAIL_HALT", 8))
    no_progress: int = field(default_factory=lambda: _env_int("GUARDRAIL_NOPROGRESS", 5))
    halt_reason: str | None = None
    _sigs: dict[str, _Sig] = field(default_factory=dict)
    _tool_fail: dict[str, int] = field(default_factory=dict)

    def before_call(self, tool: str, tool_input: Any) -> str | None:
        """A reason to refuse this call, else None."""
        if self.halt_reason:
            return self.halt_reason
        if self._tool_fail.get(tool, 0) >= self.tool_fail_halt:
            return self._halt(f"same_tool_failure: {tool} failed {self._tool_fail[tool]}x")
        st = self._sigs.get(f"{tool}:{_hash(tool_input)}")
        if st and st.fail_streak >= self.repeat_fail:
            return f"This identical {tool} call failed {st.fail_streak}x; try a different approach."
        if tool in _IDEMPOTENT_TOOLS and st and st.same_result >= self.no_progress:
            return f"{tool} returned the same result {st.same_result}x; re-reading is not progress."
        return None

    def after_call(self, tool: str, tool_input: Any, *, ok: bool, result: Any = None) -> None:
        st = self._sigs.setdefault(f"{tool}:{_hash(tool_input)}", _Sig())
        if ok:
            st.fail_streak = 0
            self._tool_fail[tool] = 0
            if tool in _IDEMPOTENT_TOOLS:
                rh = _hash(result)
                st.same_result = st.same_result + 1 if rh == st.last_result else 1
                st.last_result = rh
            return
        st.fail_streak += 1
        self._tool_fail[tool] = self._tool_fail.get(tool, 0) + 1
        if self._tool_fail[tool] >= self.tool_fail_halt:
            self._halt(f"same_tool_failure: {tool} failed {self._tool_fail[tool]}x")

    def _halt(self, reason: str) -> str:
        self.halt_reason = reason
        return reason


@dataclass
class _Session:
    spec_dir: Path
    ctl: Controller


_SESSIONS: dict[str, _Session] = {}


def register_session(cwd: Path | str, spec_dir: Path | str) -> None:
    """Bind a working directory to a fresh controller (one per client)."""
    _SESSIONS[str(Path(cwd).resolve())] = _Session(Path(spec_dir), Controller())


def guard_halt(spec_dir: Path | str) -> str | None:
    """The halt reason of any live session on this spec dir, if one halted."""
    for s in list(_SESSIONS.values()):
        if s.spec_dir == Path(spec_dir) and s.ctl.halt_reason:
            return s.ctl.halt_reason
    return None


def clear_halt(spec_dir: Path | str) -> None:
    for s in list(_SESSIONS.values()):
        if s.spec_dir == Path(spec_dir):
            s.ctl.halt_reason = None


def _ok(tool_response: Any) -> bool:
    if isinstance(tool_response, dict):
        return not (tool_response.get("is_error") or tool_response.get("isError"))
    if isinstance(tool_response, str):
        return not tool_response.lstrip().lower().startswith(("error", "traceback", "exception"))
    return True


def _sess(input_data: dict[str, Any]) -> _Session | None:
    cwd = input_data.get("cwd")
    return _SESSIONS.get(str(Path(cwd).resolve())) if cwd else None


async def guardrail_pretool_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None = None,  # noqa: ARG001 - SDK hook contract
    context: Any = None,  # noqa: ARG001 - SDK hook contract
) -> dict[str, Any]:
    if not guardrail_enabled():
        return {}
    try:
        s = _sess(input_data)
        reason = None
        if s:
            reason = s.ctl.before_call(
                input_data.get("tool_name", ""), input_data.get("tool_input")
            )
        if reason:
            return {"decision": "block", "reason": reason}
    except Exception:  # noqa: BLE001 - never break the agent on a guard error
        logger.debug("guardrail pretool hook failed", exc_info=True)
    return {}


async def guardrail_posttool_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None = None,  # noqa: ARG001 - SDK hook contract
    context: Any = None,  # noqa: ARG001 - SDK hook contract
) -> dict[str, Any]:
    if not guardrail_enabled():
        return {}
    try:
        s = _sess(input_data)
        if s:
            s.ctl.after_call(
                input_data.get("tool_name", ""),
                input_data.get("tool_input"),
                ok=_ok(input_data.get("tool_response")),
                result=input_data.get("tool_response"),
            )
    except Exception:  # noqa: BLE001
        logger.debug("guardrail posttool hook failed", exc_info=True)
    return {}


class TokenCeilingError(RuntimeError):
    """A plan run used more tokens than ``PFACTORY_RUN_TOKEN_CEILING``."""


def check_ceiling(total_tokens: int) -> None:
    """Raise when a run's token total is over the ceiling (0 disables)."""
    ceiling = token_ceiling()
    if ceiling and total_tokens > ceiling:
        raise TokenCeilingError(f"token_ceiling: run used {total_tokens} tokens, ceiling {ceiling}")
