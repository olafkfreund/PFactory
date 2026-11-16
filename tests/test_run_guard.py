"""Anti-loop guardrail and run limits (PFactory#1008, port of AIFactory#2033)."""

import asyncio
from types import SimpleNamespace

import pytest

from agents import run_guard as g

P = "PFACTORY_"


def _fail(tool="Edit", args=None):
    return {"cwd": "/w", "tool_name": tool, "tool_input": args or {"f": "a"}}


def test_guardrail_on_by_default_and_off_switch(monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    assert g.guardrail_enabled()
    for off in ("off", "0", "false", "NO"):
        monkeypatch.setenv(P + "ACT_GUARDRAIL", off)
        assert not g.guardrail_enabled()


def test_max_turns_env(monkeypatch):
    monkeypatch.delenv(P + "MAX_TURNS", raising=False)
    assert g.max_turns() == 1000
    monkeypatch.setenv(P + "MAX_TURNS", "25")
    assert g.max_turns() == 25
    monkeypatch.setenv(P + "MAX_TURNS", "abc")
    assert g.max_turns() == 1000


def test_repeated_failure_blocks_then_tool_failure_halts(tmp_path, monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    g.register_session("/w", tmp_path)
    err = {**_fail(), "tool_response": {"is_error": True}}
    for _ in range(5):
        assert asyncio.run(g.guardrail_pretool_hook(_fail())) == {}
        asyncio.run(g.guardrail_posttool_hook(err))
    assert asyncio.run(g.guardrail_pretool_hook(_fail()))["decision"] == "block"
    assert g.guard_halt(tmp_path) is None  # one call blocked, session not halted
    for i in range(3):  # different args: same tool keeps failing -> halt
        asyncio.run(
            g.guardrail_posttool_hook(
                {**_fail(args={"f": str(i)}), "tool_response": {"is_error": True}}
            )
        )
    assert g.guard_halt(tmp_path).startswith("same_tool_failure")
    assert asyncio.run(g.guardrail_pretool_hook(_fail(args={"f": "new"})))["decision"] == "block"
    g.clear_halt(tmp_path)
    assert g.guard_halt(tmp_path) is None


def test_failure_hook_counts_failed_calls(tmp_path, monkeypatch):
    """The SDK sends a failed tool call to PostToolUseFailure (payload has `error`)."""
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    g.register_session("/w", tmp_path)
    fail = {**_fail(), "error": "boom"}
    for _ in range(5):
        asyncio.run(g.guardrail_failure_hook(fail))
    assert asyncio.run(g.guardrail_pretool_hook(_fail()))["decision"] == "block"
    asyncio.run(g.guardrail_failure_hook({**fail, "is_interrupt": True}))  # not counted
    for i in range(8):
        asyncio.run(g.guardrail_failure_hook({**_fail(args={"f": str(i)}), "error": "x"}))
    assert g.guard_halt(tmp_path).startswith("same_tool_failure")


def test_guardrail_off_never_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv(P + "ACT_GUARDRAIL", "off")
    g.register_session("/w", tmp_path)
    for _ in range(20):
        asyncio.run(g.guardrail_posttool_hook({**_fail(), "tool_response": {"is_error": True}}))
    assert asyncio.run(g.guardrail_pretool_hook(_fail())) == {}
    assert g.guard_halt(tmp_path) is None


def test_idempotent_read_no_progress_blocks(tmp_path, monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    g.register_session("/w", tmp_path)
    call = _fail("Read", {"file_path": "x"})
    for _ in range(5):
        asyncio.run(g.guardrail_posttool_hook({**call, "tool_response": "same"}))
    assert asyncio.run(g.guardrail_pretool_hook(call))["decision"] == "block"


def test_hook_error_never_raises(monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    assert asyncio.run(g.guardrail_pretool_hook({"cwd": object()})) == {}
    assert asyncio.run(g.guardrail_posttool_hook({"cwd": object()})) == {}


def test_ceiling_env(monkeypatch):
    monkeypatch.delenv(P + "RUN_TOKEN_CEILING", raising=False)
    assert g.token_ceiling() == 10_000_000
    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "0")
    assert g.token_ceiling() == 0
    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "x")
    assert g.token_ceiling() == 10_000_000


def test_check_ceiling(monkeypatch):
    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "100")
    g.check_ceiling(100)
    with pytest.raises(g.TokenCeilingError, match="token_ceiling"):
        g.check_ceiling(101)
    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "0")
    g.check_ceiling(10**12)


# ── run_agent_session wiring ──────────────────────────────────────────────


class _Msg:
    content: tuple = ()


class _Client:
    def __init__(self, messages, on_message=None) -> None:
        self.interrupted = 0
        self.yielded = 0
        self._messages = messages
        self._on = on_message

    async def query(self, prompt: str) -> None:  # noqa: ARG002
        return None

    async def interrupt(self) -> None:
        self.interrupted += 1

    async def receive_response(self):
        for i, m in enumerate(self._messages):
            if self._on:
                self._on(i)
            self.yielded += 1
            yield m


async def test_guardrail_halt_interrupts_and_returns_typed_error(tmp_path, monkeypatch):
    from agents.session import run_agent_session
    from task_logger import LogPhase

    g.register_session("/w", tmp_path)

    def halt_on_second(i: int) -> None:
        if i == 1:
            g._SESSIONS["/w"].ctl.halt_reason = "same_tool_failure: Edit failed 8x"

    client = _Client([_Msg(), _Msg(), _Msg()], halt_on_second)
    status, reason, info = await run_agent_session(client, "x", tmp_path, False, LogPhase.CODING)
    assert (status, info["type"]) == ("error", "guardrail_halt")
    assert reason.startswith("same_tool_failure") and client.interrupted == 1
    assert client.yielded == 3  # drained to the end, not abandoned after interrupt
