# ruff: noqa: ARG002  # tool_use_id/context are the SDK hook contract
"""Anti-loop guardrail and run limits for agent sessions (PFactory#1008).

Port of AIFactory#2033. Three controls, all switchable from the environment:

  - ``PFACTORY_ACT_GUARDRAIL``     anti-loop guardrail, ON unless ``0|false|no|off``.
  - ``PFACTORY_RUN_TOKEN_CEILING`` per-run token ceiling (default 10M, ``0`` disables).
  - ``PFACTORY_MAX_TURNS``         SDK ``max_turns`` for a session (default 1000).

The guardrail runs three policies keyed on a tool-call signature (tool + args
hash): the same call failing repeatedly, any one tool failing repeatedly, and an
idempotent read returning the same result repeatedly. A halt is per session and
kept in memory on the client's :class:`Guard`: ``run_agent_session`` reads it with
:func:`guard_halt`, stops the stream and returns a typed error. Hooks never raise.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
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


def _ok(tool_response: Any) -> bool:
    if isinstance(tool_response, dict):
        return not (tool_response.get("is_error") or tool_response.get("isError"))
    if isinstance(tool_response, str):
        return not tool_response.lstrip().lower().startswith(("error", "traceback", "exception"))
    return True


class Guard:
    """One client's controller and the SDK hooks bound to it.

    Bound per client (not per working directory) so two clients on the same
    project, e.g. the evaluator and the review lane, never share failure counts
    or halts. ``create_client`` hangs it on the client; ``run_agent_session``
    reads it back with :func:`guard_halt`. The hooks never raise into the agent.
    """

    def __init__(self) -> None:
        self.ctl = Controller()

    async def pre(
        self, input_data: dict[str, Any], tool_use_id: str | None = None, context: Any = None
    ) -> dict[str, Any]:
        """PreToolUse: refuse a call the controller has ruled out."""
        if not guardrail_enabled():
            return {}
        try:
            reason = self.ctl.before_call(
                input_data.get("tool_name", ""), input_data.get("tool_input")
            )
            if reason:
                return {"decision": "block", "reason": reason}
        except Exception:  # noqa: BLE001 - never break the agent on a guard error
            logger.debug("guardrail pretool hook failed", exc_info=True)
        return {}

    async def post(
        self, input_data: dict[str, Any], tool_use_id: str | None = None, context: Any = None
    ) -> dict[str, Any]:
        """PostToolUse: a call that returned (it may still carry an error result)."""
        if not guardrail_enabled():
            return {}
        try:
            self.ctl.after_call(
                input_data.get("tool_name", ""),
                input_data.get("tool_input"),
                ok=_ok(input_data.get("tool_response")),
                result=input_data.get("tool_response"),
            )
        except Exception:  # noqa: BLE001
            logger.debug("guardrail posttool hook failed", exc_info=True)
        return {}

    async def failure(
        self, input_data: dict[str, Any], tool_use_id: str | None = None, context: Any = None
    ) -> dict[str, Any]:
        """PostToolUseFailure: the SDK sends a failed tool call here, not to PostToolUse."""
        if not guardrail_enabled() or input_data.get("is_interrupt"):
            return {}
        try:
            self.ctl.after_call(
                input_data.get("tool_name", ""), input_data.get("tool_input"), ok=False
            )
        except Exception:  # noqa: BLE001
            logger.debug("guardrail failure hook failed", exc_info=True)
        return {}


def guard_halt(client: Any) -> str | None:
    """The halt reason of the client's guard, if its session halted."""
    guard = getattr(client, "_run_guard", None)
    return guard.ctl.halt_reason if isinstance(guard, Guard) else None


class TokenCeilingError(RuntimeError):
    """A plan run used more tokens than ``PFACTORY_RUN_TOKEN_CEILING``."""


def check_ceiling(total_tokens: int) -> None:
    """Raise when a run's token total is over the ceiling (0 disables)."""
    ceiling = token_ceiling()
    if ceiling and total_tokens > ceiling:
        raise TokenCeilingError(f"token_ceiling: run used {total_tokens} tokens, ceiling {ceiling}")
