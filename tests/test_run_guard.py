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
    for _ in range(1):  # blocked repeats count: the 8th attempt halts the session
        assert _run(guard.pre(_call()))["decision"] == "block"
        assert g.guard_halt(_Holder(guard)) is None
    assert _run(guard.pre(_call()))["decision"] == "block"
    assert g.guard_halt(_Holder(guard)).startswith("same_tool_failure")


def test_distinct_failing_calls_never_halt(monkeypatch):
    """Failing test runs with different commands are normal iteration (#1525)."""
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    guard = g.Guard()
    for i in range(20):
        c = _call("Bash", {"command": f"pytest t{i}.py"})
        assert _run(guard.pre(c)) == {}
        _run(guard.post({**c, "tool_response": {"is_error": True}}))
    assert g.guard_halt(_Holder(guard)) is None


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
    for i in range(8):  # different args: no halt
        _run(guard.failure({**_call(args={"f": str(i)}), "error": "x"}))
    assert g.guard_halt(_Holder(guard)) is None
    for _ in range(3):  # the blocked repeats of the identical call halt it
        _run(guard.pre(_call()))
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


def test_ceiling_is_per_process_run_not_lifetime(monkeypatch):
    """A plan over the ceiling in an earlier run can be re-processed (#1525)."""
    from plan import service as svc_mod
    from plan.usage import PlanUsage

    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "100")
    svc = svc_mod.PlanService()
    s = svc.ingest_text(
        "# Plan\n\n## Acceptance Criteria\n- It works\n", title="t", category="software"
    )
    s = svc.get(s.session_id)
    s.usage.input_tokens = 500  # spent by an earlier run
    svc._save(s)
    svc.process(s.session_id)  # prior usage no longer counts

    real = svc_mod.decompose

    def spend(*a, usage_sink=None, **kw):
        usage_sink.append(PlanUsage(input_tokens=150))
        return real(*a, usage_sink=usage_sink, **kw)

    monkeypatch.setattr(svc_mod, "decompose", spend)
    with pytest.raises(g.TokenCeilingError):  # this run alone crosses it
        svc.process(s.session_id)


def test_ceiling_halt_writes_the_terminal_completion_event(monkeypatch, tmp_path):
    """A ceiling halt must reach the sentinel (not only a usage snapshot) with halt_reason."""
    import json  # noqa: PLC0415

    from plan import service as svc_mod  # noqa: PLC0415
    from plan.usage import PlanUsage  # noqa: PLC0415

    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "100")
    monkeypatch.setenv("PFACTORY_COMPLETION_SENTINEL", "1")
    monkeypatch.setenv("PFACTORY_COMPLETION_SENTINEL_DIR", str(tmp_path))
    svc = svc_mod.PlanService()
    s = svc.ingest_text(
        "# Plan\n\n## Acceptance Criteria\n- It works\n", title="t", category="software"
    )
    real = svc_mod.decompose

    def spend(*a, usage_sink=None, **kw):
        usage_sink.append(PlanUsage(input_tokens=150))
        return real(*a, usage_sink=usage_sink, **kw)

    monkeypatch.setattr(svc_mod, "decompose", spend)
    with pytest.raises(g.TokenCeilingError):
        svc.process(s.session_id)
    event = json.loads((tmp_path / s.session_id / "COMPLETED.json").read_text())
    assert event["status"] == "failed"
    assert event["halt_reason"].startswith("token_ceiling")
