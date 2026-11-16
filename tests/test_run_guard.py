"""Anti-loop guardrail and run limits (PFactory#1008, port of AIFactory#2033)."""

import asyncio

import pytest

from agents import run_guard as g

P = "PFACTORY_"


def _call(tool="Edit", args=None):
    return {"tool_name": tool, "tool_input": args or {"f": "a"}}


def _run(coro):
    return asyncio.run(coro)


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


def test_repeated_failure_blocks_then_tool_failure_halts(monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    guard = g.Guard()
    err = {**_call(), "tool_response": {"is_error": True}}
    for _ in range(5):
        assert _run(guard.pre(_call())) == {}
        _run(guard.post(err))
    assert _run(guard.pre(_call()))["decision"] == "block"
    assert g.guard_halt(_Holder(guard)) is None  # one call blocked, session not halted
    for i in range(3):  # different args: same tool keeps failing -> halt
        _run(guard.post({**_call(args={"f": str(i)}), "tool_response": {"is_error": True}}))
    assert g.guard_halt(_Holder(guard)).startswith("same_tool_failure")
    assert _run(guard.pre(_call(args={"f": "new"})))["decision"] == "block"


class _Holder:
    """Stands in for a client carrying its guard, as create_client sets it up."""

    def __init__(self, guard):
        self._run_guard = guard


def test_failure_hook_counts_failed_calls(monkeypatch):
    """The SDK sends a failed tool call to PostToolUseFailure (payload has `error`)."""
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    guard = g.Guard()
    for _ in range(5):
        _run(guard.failure({**_call(), "error": "boom"}))
    assert _run(guard.pre(_call()))["decision"] == "block"
    _run(guard.failure({**_call(), "error": "x", "is_interrupt": True}))  # not counted
    for i in range(8):
        _run(guard.failure({**_call(args={"f": str(i)}), "error": "x"}))
    assert g.guard_halt(_Holder(guard)).startswith("same_tool_failure")


def test_guards_are_per_client(monkeypatch):
    """Two clients on one project (evaluator + review lane) never share state."""
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    a, b = g.Guard(), g.Guard()
    for _ in range(5):
        _run(a.failure({**_call(), "error": "boom"}))
    assert _run(a.pre(_call()))["decision"] == "block"
    assert _run(b.pre(_call())) == {}


def test_guardrail_off_never_blocks(monkeypatch):
    monkeypatch.setenv(P + "ACT_GUARDRAIL", "off")
    guard = g.Guard()
    for _ in range(20):
        _run(guard.post({**_call(), "tool_response": {"is_error": True}}))
        _run(guard.failure({**_call(), "error": "x"}))
    assert _run(guard.pre(_call())) == {}
    assert g.guard_halt(_Holder(guard)) is None


def test_idempotent_read_no_progress_blocks(monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    guard = g.Guard()
    call = _call("Read", {"file_path": "x"})
    for _ in range(5):
        _run(guard.post({**call, "tool_response": "same"}))
    assert _run(guard.pre(call))["decision"] == "block"


def test_hook_error_never_raises(monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    guard = g.Guard()
    bad = {"tool_name": ["unhashable"], "tool_input": object()}
    assert _run(guard.pre(bad)) == {}
    assert _run(guard.post(bad)) == {}
    assert _run(guard.failure(bad)) == {}
    assert g.guard_halt(object()) is None


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
        self._run_guard = g.Guard()
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

    def halt_on_second(i: int) -> None:
        if i == 1:
            client._run_guard.ctl.halt_reason = "same_tool_failure: Edit failed 8x"

    client = _Client([_Msg(), _Msg(), _Msg()], halt_on_second)
    status, reason, info = await run_agent_session(client, "x", tmp_path, False, LogPhase.CODING)
    assert (status, info["type"]) == ("error", "guardrail_halt")
    assert reason.startswith("same_tool_failure") and client.interrupted == 1
    assert client.yielded == 3  # drained to the end, not abandoned after interrupt
