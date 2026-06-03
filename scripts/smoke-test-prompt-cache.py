#!/usr/bin/env python3
"""
Smoke-test prompt caching end-to-end (operator runbook).

Makes two sequential SDK calls with the same project_dir / agent_type
and verifies that the second call's response shows
``cache_read_input_tokens > 0`` — proof that the static prefix was
served from the Anthropic API's automatic prompt cache.

Usage:

    # Required: an API key (works with either of these env vars)
    export ANTHROPIC_API_KEY=sk-...
    # …or:
    export CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat...

    cd apps/backend
    .venv/bin/python ../../scripts/smoke-test-prompt-cache.py

Expected output (PASS):

    Call 1: write=N read=0
    Call 2: write=0 read=N    (or read >> write)
    PASS — prompt cache active (read=N tokens on call 2)

If the second call shows ``read=0`` we either failed to keep the prefix
byte-identical (a regression) or the prefix is below the per-model cache
floor (see core/cache.py:_MIN_CACHE_TOKENS).  Either way, FAIL exits 1.

This script is intentionally NOT a pytest because it makes real network
calls; gating it behind an explicit operator action keeps CI fast and
deterministic.  An equivalent pytest is in tests/test_cache_blocks.py
under the ``requires_api_key`` marker for CI users who want it.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Allow running from repo root or apps/backend
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "backend"))


def _check_auth() -> str | None:
    """Return the env var name that's set, or None."""
    for var in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"):
        if os.environ.get(var):
            return var
    return None


async def _single_call(project_dir: Path, attempt: int) -> tuple[int, int]:
    """One agent session → return (cache_write_tokens, cache_read_tokens)."""
    from core.client import create_client

    client = create_client(
        project_dir=project_dir,
        spec_dir=project_dir,
        model="claude-sonnet-4-6",
        agent_type="coder",
    )

    cache_write = 0
    cache_read = 0
    try:
        async with client:
            # A trivial prompt — we only care about the cache fields in the
            # response, not the model's answer.
            await client.query(f"Respond with the single word 'pong' (attempt {attempt}).")
            async for msg in client.receive_response():
                msg_type = type(msg).__name__
                if msg_type == "ResultMessage":
                    usage = getattr(msg, "usage", None) or {}
                    cache_write += usage.get("cache_creation_input_tokens", 0) or 0
                    cache_read += usage.get("cache_read_input_tokens", 0) or 0
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR on call {attempt}: {exc}")
        raise

    return cache_write, cache_read


async def _main() -> int:
    auth = _check_auth()
    if not auth:
        print(
            "FAIL — no auth credential.  Set ANTHROPIC_API_KEY or "
            "CLAUDE_CODE_OAUTH_TOKEN in the environment."
        )
        return 2

    print(f"Using auth credential from {auth}.")
    project_dir = _REPO_ROOT  # PFactory's own CLAUDE.md is a fine static prefix

    print(f"Project dir: {project_dir}")
    print(f"Static prefix source: {project_dir / 'CLAUDE.md'}")
    print()

    write_1, read_1 = await _single_call(project_dir, attempt=1)
    print(f"Call 1: write={write_1} read={read_1}")

    write_2, read_2 = await _single_call(project_dir, attempt=2)
    print(f"Call 2: write={write_2} read={read_2}")
    print()

    if read_2 > 0:
        print(f"PASS — prompt cache active (read={read_2} tokens on call 2)")
        return 0
    print(
        "FAIL — second call shows read=0.  Either the static prefix is below "
        "the per-model cache floor (see core/cache.py:_MIN_CACHE_TOKENS) or "
        "something is rewriting the prefix between calls."
    )
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
