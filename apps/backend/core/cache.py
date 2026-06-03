"""
Prompt-caching helpers for system-prompt construction.
=======================================================

The Anthropic API supports two mechanisms for caching system content:

1. **Automatic caching** — when the *same byte-identical* prefix is submitted
   on repeated calls the server reuses the cached KV state without any explicit
   marker.  This is how the SDK path (ClaudeAgentOptions.system_prompt: str)
   benefits: keep the string identical across sessions and the API caches it
   automatically.  Minimum prefix length: 1 024 tokens for Sonnet models,
   4 096 for Opus/Haiku.

2. **Explicit breakpoints** (cache_control markers) — for the direct Anthropic
   API (anthropic.Anthropic().messages.create) callers only.  A
   ``cache_control`` dict is attached to the *last* block in the static prefix;
   everything from the start of the system array up to and including that block
   is hashed and cached.  TTL is 5 min (default) or 1 h.

The claude-agent-sdk v0.2.82 exposes ``ClaudeAgentOptions.system_prompt`` as
``str | SystemPromptPreset | SystemPromptFile | None`` (types.py line 1605).
There is no structured-block variant, so explicit cache_control markers cannot
be passed through the SDK.  Use ``build_cached_system_str`` for SDK sessions
and ``build_cached_system_blocks`` for direct-API callers.

Cache pricing summary (Anthropic docs, 2025):
  Write (5 min):  1.25× base input token price
  Write (1 h):    2.00× base input token price
  Read:           0.10× base input token price

References:
  https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level state — for hash-change detection
# ---------------------------------------------------------------------------

# Hash of the static prefix that was last assembled per project_dir.  When
# build_cached_system_str is called again for the same project and the hash
# differs, we emit a one-time warning so operators know the cache will be
# cold on the next API call.  Keyed by str(project_dir.resolve()).
_PREFIX_HASHES: dict[str, str] = {}


# Minimum cacheable prefix length per model family (Anthropic post-Jan-2026
# docs).  When the static prefix is below this floor, prompt caching is a
# strict net cost (1.25× write, no reads); we log a warning but still emit
# the string unchanged so behaviour is preserved.
#
# Prefix matching: we use str.startswith so future *-1m / dated variants
# (e.g. "claude-sonnet-4-6-20260605") resolve correctly.
#
# Reference: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
_MIN_CACHE_TOKENS: dict[str, int] = {
    "claude-haiku-4-5": 4096,
    "claude-sonnet-4-6": 1024,
    "claude-opus-4-6": 4096,
    "claude-opus-4-7": 4096,
}

# Conservative fallback for unknown models — chosen low so the warning is
# noisy rather than silent when a new model isn't in the table yet.
_DEFAULT_MIN_TOKENS = 1024


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_VALID_TTLS: frozenset[str] = frozenset({"ephemeral", "1h"})


def _make_cache_control(ttl: Literal["ephemeral", "1h"]) -> dict[str, str]:
    """Return the cache_control dict for the given TTL.

    Syntax confirmed from Anthropic docs:
      {"type": "ephemeral"}            — 5-minute default
      {"type": "ephemeral", "ttl": "1h"} — 1-hour lifetime (2× write cost)

    Raises:
        ValueError: if ttl is anything other than "ephemeral" or "1h".
            The Literal type already catches this at mypy time; the runtime
            guard exists for callers that bypass type checking.
    """
    if ttl not in _VALID_TTLS:
        raise ValueError(
            f"Invalid ttl {ttl!r}; must be one of {sorted(_VALID_TTLS)}"
        )
    if ttl == "1h":
        return {"type": "ephemeral", "ttl": "1h"}
    return {"type": "ephemeral"}


def _estimate_tokens(text: str) -> int:
    """Cheap char-count token estimate.

    Anthropic guidance: ~4 chars per token for English-prevalent prompts.
    Adequate for the cache-floor heuristic; we don't need exact counts here.
    """
    return len(text) // 4


def _min_cacheable_tokens(model: str) -> int:
    """Return the minimum cacheable prefix size for ``model``.

    Matches the model id by prefix so dated variants resolve to the same
    floor as the base id.  Unknown models fall back to ``_DEFAULT_MIN_TOKENS``.
    """
    for key, floor in _MIN_CACHE_TOKENS.items():
        if model.startswith(key):
            return floor
    return _DEFAULT_MIN_TOKENS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_cached_system_blocks(
    base_instructions: str,
    claude_md_content: str | None = None,
    project_context: str | None = None,
    ttl: Literal["ephemeral", "1h"] = "ephemeral",
) -> list[dict[str, Any]]:
    """Build system-prompt content blocks with a single cache breakpoint.

    Intended for **direct Anthropic API callers** —
    ``anthropic.Anthropic().messages.create(system=build_cached_system_blocks(...))``
    — NOT for ``ClaudeAgentOptions.system_prompt``, which only accepts ``str``.

    Block ordering (static content first, dynamic last):

        [claude_md_block]        ← present when claude_md_content is non-empty
        [project_context_block]  ← present when project_context is non-empty
        [base_instructions]      ← always present, never cached

    The ``cache_control`` marker is placed on the **last** static block so that
    the Anthropic API caches everything from the start of the array up to that
    breakpoint.  Placing the marker on every block would be incorrect — the API
    requires the marker only on the final block of the desired cached prefix.

    Args:
        base_instructions:  Dynamic, agent-type-specific instructions.  This
                            varies per agent and must never be cached.
        claude_md_content:  Full text of CLAUDE.md.  Treated as absent when
                            the value is falsy (None or empty string).
        project_context:    Serialised project context (e.g. context.json).
                            Treated as absent when falsy.
        ttl:                Cache lifetime.  ``"ephemeral"`` (default) = 5 min
                            at 1.25× write cost.  ``"1h"`` = 60 min at 2×
                            write cost.  Reads are 0.10× in either case.

    Returns:
        A list of text-block dicts.  When no cacheable content is supplied the
        list contains exactly one plain block (no ``cache_control`` key).

    Example (direct API)::

        import anthropic
        from core.cache import build_cached_system_blocks

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            system=build_cached_system_blocks(
                base_instructions=agent_intro,
                claude_md_content=load_claude_md(project_dir),
                project_context=json.dumps(ctx),
            ),
            messages=[{"role": "user", "content": task}],
        )
        usage = response.usage
        print(usage.cache_read_input_tokens, usage.cache_creation_input_tokens)
    """
    cache_ctrl = _make_cache_control(ttl)
    static_blocks: list[dict[str, Any]] = []

    if claude_md_content:
        static_blocks.append({"type": "text", "text": claude_md_content})

    if project_context:
        static_blocks.append({"type": "text", "text": project_context})

    # Place cache_control on the last static block only.
    # The Anthropic API computes one hash for the full prefix up to and
    # including the marked block; the marker must therefore sit on the *last*
    # block whose content is identical across requests.
    if static_blocks:
        static_blocks[-1]["cache_control"] = cache_ctrl

    # base_instructions is appended last and never receives cache_control
    # because it changes per agent type (coder, planner, qa_reviewer, …).
    return static_blocks + [{"type": "text", "text": base_instructions}]


def build_cached_system_str(
    base_instructions: str,
    claude_md_content: str | None = None,
    project_context: str | None = None,
    *,
    model: str | None = None,
    project_dir: str | None = None,
) -> str:
    """Collapse all sections into a single string for ``ClaudeAgentOptions.system_prompt``.

    The SDK's ``ClaudeAgentOptions.system_prompt`` only accepts ``str``
    (``claude_agent_sdk.types``, line 1605), so explicit ``cache_control``
    markers cannot be passed through the SDK path.  Instead this helper keeps
    the static prefix (CLAUDE.md + project context) byte-identical across
    sessions, enabling the Anthropic API's **automatic prompt caching** on the
    server side.

    Ordering mirrors ``build_cached_system_blocks`` — static content first,
    dynamic instructions last — so both paths produce the same logical
    structure.

    Args:
        base_instructions:  Dynamic agent-type-specific intro.  Varies across
                            agent types; placed last so it does not interfere
                            with the stable static prefix.
        claude_md_content:  CLAUDE.md text.  Omitted when falsy.
        project_context:    Serialised context dict.  Omitted when falsy.
        model:              Optional model id (e.g. ``claude-sonnet-4-6``).
                            When provided, the helper emits a warning if the
                            assembled static prefix is below the per-model
                            cache floor (caching would be a strict net cost).
                            The returned string is unchanged; this is a
                            visibility signal only.
        project_dir:        Optional project identifier (typically the resolved
                            project root).  When provided, the helper hashes
                            the static prefix and warns when the hash differs
                            from the previous value for the same project — a
                            signal that the cache will be cold on the next
                            API call (CLAUDE.md was edited, etc.).

    Returns:
        A plain string suitable for ``ClaudeAgentOptions(system_prompt=...)``.
        CRITICAL: do not mutate the returned string before passing it to the
        SDK — any change invalidates the server-side cache prefix hash.

    Example (SDK path)::

        from core.cache import build_cached_system_str
        from core.client import create_client  # existing factory

        system = build_cached_system_str(
            base_instructions=agent_intro,
            claude_md_content=load_claude_md(project_dir),
            model="claude-sonnet-4-6",
            project_dir=str(project_dir.resolve()),
        )
        client = create_client(..., base_prompt_override=system)
    """
    parts: list[str] = []
    static_parts: list[str] = []  # only the cacheable bits; excludes base_instructions

    if claude_md_content:
        rendered = "# Project Instructions (from CLAUDE.md)\n\n" + claude_md_content
        parts.append(rendered)
        static_parts.append(rendered)

    if project_context:
        rendered = "# Project Context\n\n" + project_context
        parts.append(rendered)
        static_parts.append(rendered)

    parts.append(base_instructions)

    # ---------- Cache-floor guard ----------
    # Warn when the static prefix is below the per-model minimum; below the
    # floor the server-side cache won't activate and writes (1.25×) buy nothing.
    if model is not None and static_parts:
        static_text = "\n\n".join(static_parts)
        prefix_tokens = _estimate_tokens(static_text)
        floor = _min_cacheable_tokens(model)
        if prefix_tokens < floor:
            logger.warning(
                "Prompt-cache prefix below floor for model %r — "
                "got ~%d tokens, need ≥%d. Caching will not engage.",
                model, prefix_tokens, floor,
            )

    # ---------- Mid-process hash-change warning ----------
    # Hash the static prefix and compare to the last value seen for this
    # project. A change implies the next API call will be a cache miss
    # (CLAUDE.md edited, project context regenerated, etc.).
    if project_dir is not None and static_parts:
        prefix_hash = hashlib.sha256(
            "\n\n".join(static_parts).encode("utf-8")
        ).hexdigest()
        prev = _PREFIX_HASHES.get(project_dir)
        if prev is not None and prev != prefix_hash:
            logger.warning(
                "Prompt-cache prefix changed for %s — cache will be cold "
                "on the next API call.",
                project_dir,
            )
        _PREFIX_HASHES[project_dir] = prefix_hash

    return "\n\n".join(parts)
