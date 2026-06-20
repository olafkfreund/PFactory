"""
Anthropic Message Batches helper for parallel one-shot completions.

Issue #11 — Epic #6.

The Claude Agent SDK (`claude-agent-sdk`) is built around stateful sessions
with tool loops — it has no batch primitives (verified in PR #11
investigation). For bulk one-shot completions we drop to the raw
``anthropic`` client (``anthropic>=0.84.0``, pinned in requirements.txt).

This module is a **primitive**: it ships ready for callers but the current
production code does not yet have a callsite that produces ≥2 simultaneous
independent prompts. The first real consumer will likely be an end-of-build
insight sweep that batches deferred insight extractions
(``analysis.insight_extractor.extract_session_insights_bulk``).

Public surface:

    BatchRequest        — dataclass describing one entry in the batch
    BatchResult         — dataclass describing one result from the batch
    submit_batch()      — POST to messages.batches.create; returns batch_id
    await_batch()       — poll messages.batches.retrieve with exponential
                          backoff; stream messages.batches.results when done
    extract_savings()   — aggregate usage and report estimated discount

Cost model (verified by python-pro research):

    - Batch input/output tokens billed at 0.5× base rate ("batch" service tier).
    - Cache hit input tokens billed at 0.1× base rate.
    - Batch + cache hit on input tokens stack multiplicatively: 0.05× of base.
    - For Slice 1 we ship batch alone (50% saving) without cache stacking;
      verifying cache dedup behavior across batch entries is a follow-up.

Operational notes:

    - The batch keeps processing on Anthropic's servers even if our process
      exits — persist the batch_id to disk before considering work done.
    - ``request_counts`` is all zeros until ``processing_status == "ended"`` —
      do NOT try to render partial progress from intermediate polls.
    - Completion text is at ``result.message.content[N].text`` (iterate to
      find first ``.text``-bearing block; tool-use blocks come first when
      the model decides it wants tools — defensive extraction).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchRequest:
    """A single entry in an Anthropic message batch."""

    custom_id: str
    model: str
    max_tokens: int
    messages: list[Mapping[str, Any]]
    # Plain str OR list[TextBlockParam] dicts (the latter supports
    # cache_control markers for prompt caching; see core/cache.py).
    system: str | list[Mapping[str, Any]] = ""
    temperature: float | None = None
    stop_sequences: list[str] | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class BatchResult:
    """One result from a completed batch."""

    custom_id: str
    status: Literal["succeeded", "errored", "canceled", "expired"]
    content: str | None
    usage: Mapping[str, Any] | None
    error: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_params(req: BatchRequest) -> dict[str, Any]:
    """Translate a BatchRequest into the params dict the SDK expects."""
    params: dict[str, Any] = {
        "model": req.model,
        "max_tokens": req.max_tokens,
        "messages": list(req.messages),
    }
    if req.system:
        params["system"] = req.system
    if req.temperature is not None:
        params["temperature"] = req.temperature
    if req.stop_sequences:
        params["stop_sequences"] = list(req.stop_sequences)
    if req.metadata:
        params["metadata"] = dict(req.metadata)
    return params


def _extract_text(content_blocks: Any) -> str | None:
    """Find the first text-bearing block in a content list."""
    if not content_blocks:
        return None
    for block in content_blocks:
        # SDK content blocks expose .text on TextBlock; ToolUseBlock has .input.
        text = getattr(block, "text", None)
        if text is not None:
            return text
    return None


def _parse_individual_response(item: Any) -> BatchResult:
    """Convert one MessageBatchIndividualResponse into a BatchResult."""
    result = item.result
    rtype = result.type

    if rtype == "succeeded":
        msg = result.message
        usage_obj = msg.usage
        usage: dict[str, Any] = {
            "input_tokens": usage_obj.input_tokens,
            "output_tokens": usage_obj.output_tokens,
            "cache_read_input_tokens": getattr(usage_obj, "cache_read_input_tokens", None),
            "cache_creation_input_tokens": getattr(usage_obj, "cache_creation_input_tokens", None),
            "service_tier": getattr(usage_obj, "service_tier", None),
        }
        return BatchResult(
            custom_id=item.custom_id,
            status="succeeded",
            content=_extract_text(msg.content),
            usage=usage,
            error=None,
        )

    if rtype == "errored":
        return BatchResult(
            custom_id=item.custom_id,
            status="errored",
            content=None,
            usage=None,
            error=getattr(result.error, "message", str(result.error)),
        )

    # "canceled" or "expired"
    return BatchResult(
        custom_id=item.custom_id,
        status=rtype,
        content=None,
        usage=None,
        error=None,
    )


def _resolve_api_key(api_key: str | None) -> str:
    """Return the Anthropic API key from an explicit caller arg only.

    PFactory is OAuth-only by default (see apps/backend/core/auth.py).
    The Batch API is the one exception where an OAuth token cannot work
    (Anthropic's batch endpoint accepts only direct API keys), so this
    helper requires the caller to ``pass api_key=…`` explicitly — usually
    sourced from a per-project Settings field the user filled in
    consciously, not from a bare env var.

    Falling back to ``os.environ.get("ANTHROPIC_API_KEY")`` was removed
    because it caused silent billing surprises: if a user happened to
    have ANTHROPIC_API_KEY in their shell env for unrelated reasons,
    batch insight extraction would silently bill against that account.
    """
    if not api_key:
        raise RuntimeError(
            "core.batch requires an explicit api_key= argument. "
            "PFactory is OAuth-only by default — bare env vars are not "
            "used as a fallback to prevent silent direct-API billing. "
            "Read the user's API key from Settings → Integrations "
            "(globalAnthropicApiKey field) and pass it explicitly. See "
            "guides/BATCH_API.md for the full policy rationale."
        )
    return api_key


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def submit_batch(
    requests: list[BatchRequest],
    *,
    api_key: str | None = None,
) -> str:
    """Submit ``requests`` to the Anthropic Message Batches API.

    Returns the batch_id string. The batch begins processing immediately on
    Anthropic's servers and will continue even if this process exits.
    Batches expire after 24h if not polled to completion.

    Raises ValueError if ``requests`` is empty.
    Raises RuntimeError if no API key is available.
    """
    if not requests:
        raise ValueError("submit_batch requires at least one BatchRequest")

    # Import lazily so a bare `from core.batch import BatchRequest` doesn't
    # require anthropic to be installed.
    from anthropic import AsyncAnthropic

    sdk_requests = [{"custom_id": r.custom_id, "params": _build_params(r)} for r in requests]

    client = AsyncAnthropic(api_key=_resolve_api_key(api_key))
    async with client:
        batch = await client.messages.batches.create(requests=sdk_requests)

    logger.info("Batch submitted: id=%s requests=%d", batch.id, len(requests))
    return batch.id


async def await_batch(
    batch_id: str,
    *,
    api_key: str | None = None,
    timeout: float = 120.0,
    initial_poll_interval: float = 2.0,
    max_poll_interval: float = 30.0,
) -> list[BatchResult]:
    """Poll until the batch reaches ``processing_status == "ended"``, then
    stream and return all per-request results.

    Polling uses exponential backoff: starts at ``initial_poll_interval``,
    doubles each iteration, capped at ``max_poll_interval``.

    Raises TimeoutError if the batch has not ended within ``timeout`` seconds.
    Per-request errors do NOT raise — they appear in the result list with
    status='errored'. Only the batch itself timing out or a transport error
    raises.
    """
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=_resolve_api_key(api_key))
    deadline = time.monotonic() + timeout
    interval = initial_poll_interval

    async with client:
        # Poll until done.
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Batch {batch_id} did not complete within {timeout}s")

            batch = await client.messages.batches.retrieve(
                batch_id,
                timeout=min(30.0, remaining),
            )
            if batch.processing_status == "ended":
                counts = batch.request_counts
                logger.info(
                    "Batch ended: id=%s succeeded=%d errored=%d canceled=%d expired=%d",
                    batch_id,
                    counts.succeeded,
                    counts.errored,
                    counts.canceled,
                    counts.expired,
                )
                break

            logger.debug(
                "Batch %s still %s; sleeping %.1fs",
                batch_id,
                batch.processing_status,
                interval,
            )
            await asyncio.sleep(min(interval, remaining))
            interval = min(interval * 2, max_poll_interval)

        # Stream results.
        results: list[BatchResult] = []
        async for item in await client.messages.batches.results(batch_id):
            results.append(_parse_individual_response(item))
        return results


def extract_savings(results: list[BatchResult]) -> dict[str, Any]:
    """Aggregate per-result usage and report the estimated batch saving.

    Returns a dict with totals for input/output/cache tokens, plus an
    ``estimated_saving_pct`` value (0.5 = 50% — the batch discount applied
    to all input and output tokens on succeeded entries).
    """
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "succeeded": 0,
        "errored": 0,
        "canceled": 0,
        "expired": 0,
    }
    service_tiers: set[str] = set()

    for r in results:
        totals[r.status] = totals.get(r.status, 0) + 1
        if r.usage:
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ):
                value = r.usage.get(key) or 0
                totals[key] += value
            tier = r.usage.get("service_tier")
            if tier:
                service_tiers.add(tier)

    return {
        **totals,
        "service_tiers": sorted(service_tiers),
        # 0.5 = 50% discount on batch-tier tokens (input + output). When
        # caching is later wired in, the saving on cache-hit input tokens
        # stacks multiplicatively to 0.05× of base.
        "estimated_saving_pct": 0.5 if "batch" in service_tiers else 0.0,
    }


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "BatchRequest",
    "BatchResult",
    "await_batch",
    "extract_savings",
    "submit_batch",
]
