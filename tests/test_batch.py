"""
Unit tests for apps/backend/core/batch.py (Issue #11).

The Anthropic AsyncAnthropic client is mocked throughout — these tests
verify our wrapper layer (request shape, polling, partial-failure
extraction, savings aggregation) without making any network calls.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_BACKEND = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.batch import (  # noqa: E402
    BatchRequest,
    BatchResult,
    await_batch,
    extract_savings,
    submit_batch,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_individual(custom_id: str, *, text: str = "ok", service_tier: str = "batch"):
    """Build a fake MessageBatchIndividualResponse with a 'succeeded' result."""
    text_block = SimpleNamespace(text=text)
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=None,
        cache_creation_input_tokens=None,
        service_tier=service_tier,
    )
    message = SimpleNamespace(content=[text_block], usage=usage)
    result = SimpleNamespace(type="succeeded", message=message)
    return SimpleNamespace(custom_id=custom_id, result=result)


def _make_errored(custom_id: str, message: str = "rate limited"):
    """Build a fake errored MessageBatchIndividualResponse."""
    error = SimpleNamespace(message=message)
    result = SimpleNamespace(type="errored", error=error)
    return SimpleNamespace(custom_id=custom_id, result=result)


def _async_iter(items):
    """Convert a list into an async iterator (mimics AsyncJSONLDecoder)."""

    async def gen():
        for item in items:
            yield item

    return gen()


class _FakeAsyncAnthropic:
    """Minimal mock of anthropic.AsyncAnthropic with batches surface."""

    def __init__(self, *, retrieve_sequence, results_items):
        self._retrieve_sequence = list(retrieve_sequence)
        self._results_items = list(results_items)
        self.create_calls: list = []
        self.retrieve_calls: list = []
        self.messages = SimpleNamespace(
            batches=SimpleNamespace(
                create=self._create,
                retrieve=self._retrieve,
                results=self._results,
            )
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def _create(self, *, requests):
        self.create_calls.append(requests)
        return SimpleNamespace(id="msgbatch_test123")

    async def _retrieve(self, batch_id, *, timeout=None):
        self.retrieve_calls.append((batch_id, timeout))
        # Pop next from the queued sequence.
        next_status = (
            self._retrieve_sequence.pop(0)
            if self._retrieve_sequence
            else "ended"
        )
        counts = SimpleNamespace(
            succeeded=len([i for i in self._results_items if i.result.type == "succeeded"]),
            errored=len([i for i in self._results_items if i.result.type == "errored"]),
            canceled=0,
            expired=0,
            processing=0,
        )
        return SimpleNamespace(
            id=batch_id,
            processing_status=next_status,
            request_counts=counts,
        )

    async def _results(self, batch_id):
        # The real SDK returns an awaitable that resolves to an async iterator.
        # Our caller does: `async for item in await client...results(...)`.
        return _async_iter(self._results_items)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSubmitBatch:
    async def test_calls_anthropic_with_correct_request_shape(self):
        fake = _FakeAsyncAnthropic(retrieve_sequence=["ended"], results_items=[])
        reqs = [
            BatchRequest(
                custom_id="r1",
                model="claude-sonnet-4-5-20250929",
                max_tokens=128,
                messages=[{"role": "user", "content": "hi"}],
                system="be brief",
                temperature=0.2,
            ),
            BatchRequest(
                custom_id="r2",
                model="claude-sonnet-4-5-20250929",
                max_tokens=64,
                messages=[{"role": "user", "content": "hello"}],
            ),
        ]
        # submit_batch does `from anthropic import AsyncAnthropic` inside the
        # function — patch the source so the lazy import picks up our fake.
        with patch("anthropic.AsyncAnthropic", return_value=fake):
            batch_id = await submit_batch(reqs, api_key="test")

        assert batch_id == "msgbatch_test123"
        assert len(fake.create_calls) == 1
        body = fake.create_calls[0]
        assert len(body) == 2
        assert body[0]["custom_id"] == "r1"
        params = body[0]["params"]
        assert params["model"] == "claude-sonnet-4-5-20250929"
        assert params["max_tokens"] == 128
        assert params["messages"] == [{"role": "user", "content": "hi"}]
        assert params["system"] == "be brief"
        assert params["temperature"] == 0.2
        # Second request omits optional fields.
        params2 = body[1]["params"]
        assert "system" not in params2  # empty string → omitted
        assert "temperature" not in params2

    async def test_empty_requests_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            await submit_batch([], api_key="test")

    async def test_missing_api_key_raises(self, monkeypatch):
        # Even when ANTHROPIC_API_KEY IS set in env, core/batch refuses to
        # fall back to it — OAuth-only policy. The caller must pass api_key=
        # explicitly from a Settings-derived source.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-leaked-from-env")
        req = BatchRequest(
            custom_id="r1",
            model="claude-sonnet-4-5-20250929",
            max_tokens=128,
            messages=[{"role": "user", "content": "hi"}],
        )
        with pytest.raises(RuntimeError, match="explicit api_key"):
            await submit_batch([req], api_key=None)


class TestAwaitBatch:
    async def test_polls_until_ended_then_returns_results(self):
        # 2 in_progress polls, then ended.
        items = [_make_individual("r1", text="hello"), _make_individual("r2", text="world")]
        fake = _FakeAsyncAnthropic(
            retrieve_sequence=["in_progress", "in_progress", "ended"],
            results_items=items,
        )
        with patch("anthropic.AsyncAnthropic", return_value=fake), patch(
            "core.batch.asyncio.sleep", new=AsyncMock()
        ):
            results = await await_batch(
                    "msgbatch_test",
                    api_key="test",
                    timeout=60,
                    initial_poll_interval=0.01,
                    max_poll_interval=0.05,
                )

        assert len(results) == 2
        assert {r.custom_id for r in results} == {"r1", "r2"}
        assert all(r.status == "succeeded" for r in results)
        assert results[0].content == "hello"

    async def test_text_extraction_skips_non_text_blocks(self):
        # First block has no .text (simulates ToolUseBlock); second has .text.
        tool_block = SimpleNamespace(input={"tool": "Read"})  # no .text
        text_block = SimpleNamespace(text="the real answer")
        usage = SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=None,
            service_tier="batch",
        )
        message = SimpleNamespace(content=[tool_block, text_block], usage=usage)
        result = SimpleNamespace(type="succeeded", message=message)
        item = SimpleNamespace(custom_id="r1", result=result)

        fake = _FakeAsyncAnthropic(retrieve_sequence=["ended"], results_items=[item])
        with patch("anthropic.AsyncAnthropic", return_value=fake):
            results = await await_batch("msgbatch_test", api_key="test", timeout=10)
        assert results[0].content == "the real answer"

    async def test_partial_failure_surfaces_per_entry(self):
        items = [
            _make_individual("r1"),
            _make_errored("r2", "context too large"),
            _make_individual("r3"),
        ]
        fake = _FakeAsyncAnthropic(retrieve_sequence=["ended"], results_items=items)
        with patch("anthropic.AsyncAnthropic", return_value=fake):
            results = await await_batch("msgbatch_test", api_key="test", timeout=10)

        by_id = {r.custom_id: r for r in results}
        assert by_id["r1"].status == "succeeded"
        assert by_id["r2"].status == "errored"
        assert by_id["r2"].error == "context too large"
        assert by_id["r3"].status == "succeeded"

    async def test_timeout_raises_when_batch_never_ends(self):
        # Long retrieve_sequence of in_progress + short real timeout.
        # We don't patch asyncio.sleep here because the deadline check needs
        # real time progression — using real sleeps keeps the test reliable
        # at the cost of ~50ms wall-clock.
        fake = _FakeAsyncAnthropic(
            retrieve_sequence=["in_progress"] * 100,
            results_items=[],
        )
        with patch("anthropic.AsyncAnthropic", return_value=fake):
            with pytest.raises(TimeoutError, match="did not complete within"):
                await await_batch(
                        "msgbatch_stuck",
                        api_key="test",
                        timeout=0.05,
                        initial_poll_interval=0.005,
                        max_poll_interval=0.02,
                    )


class TestExtractSavings:
    async def test_aggregates_usage_and_reports_batch_tier(self):
        results = [
            BatchResult(
                custom_id="r1",
                status="succeeded",
                content="hi",
                usage={
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": None,
                    "cache_creation_input_tokens": None,
                    "service_tier": "batch",
                },
                error=None,
            ),
            BatchResult(
                custom_id="r2",
                status="errored",
                content=None,
                usage=None,
                error="boom",
            ),
            BatchResult(
                custom_id="r3",
                status="succeeded",
                content="ok",
                usage={
                    "input_tokens": 200,
                    "output_tokens": 80,
                    "cache_read_input_tokens": 150,
                    "cache_creation_input_tokens": None,
                    "service_tier": "batch",
                },
                error=None,
            ),
        ]
        s = extract_savings(results)
        assert s["input_tokens"] == 300
        assert s["output_tokens"] == 130
        assert s["cache_read_input_tokens"] == 150
        assert s["succeeded"] == 2
        assert s["errored"] == 1
        assert s["service_tiers"] == ["batch"]
        # Discount applied when at least one entry was batch-tier.
        assert s["estimated_saving_pct"] == 0.5

    async def test_empty_results_yields_zero_saving(self):
        s = extract_savings([])
        assert s["input_tokens"] == 0
        assert s["estimated_saving_pct"] == 0.0
