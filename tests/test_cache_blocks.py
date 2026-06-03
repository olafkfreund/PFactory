"""
Unit tests for apps/backend/core/cache.py
==========================================

Covers build_cached_system_blocks and build_cached_system_str without any
network access or SDK subprocess involvement.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# SDK pre-mock — must precede any import of backend modules
# ---------------------------------------------------------------------------
if "claude_agent_sdk" not in sys.modules:
    _sdk_mock = MagicMock()
    sys.modules["claude_agent_sdk"] = _sdk_mock
    sys.modules["claude_agent_sdk.types"] = MagicMock()

# ---------------------------------------------------------------------------
# Add backend to sys.path
# ---------------------------------------------------------------------------
_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.cache import build_cached_system_blocks, build_cached_system_str  # noqa: E402

# ===========================================================================
# Fixtures
# ===========================================================================

CLAUDE_MD = "# CLAUDE.md\nThis project uses FastAPI and React.\n" * 30  # >1 k chars
PROJECT_CTX = '{"stack": "python", "framework": "fastapi"}'
BASE = "You are an expert full-stack developer."


# ===========================================================================
# build_cached_system_blocks
# ===========================================================================


class TestBuildCachedSystemBlocks:
    """Tests for the direct-API structured-blocks helper."""

    def test_returns_list(self) -> None:
        result = build_cached_system_blocks(BASE)
        assert isinstance(result, list)

    def test_no_static_content_returns_single_plain_block(self) -> None:
        """When no cacheable content is provided the output is one plain text block."""
        result = build_cached_system_blocks(BASE)
        assert len(result) == 1
        block = result[0]
        assert block["type"] == "text"
        assert block["text"] == BASE
        assert "cache_control" not in block

    def test_claude_md_present_adds_cached_block(self) -> None:
        result = build_cached_system_blocks(BASE, claude_md_content=CLAUDE_MD)
        # First block: claude_md with cache_control
        assert result[0]["text"] == CLAUDE_MD
        assert "cache_control" in result[0]
        # Last block: base_instructions without cache_control
        assert result[-1]["text"] == BASE
        assert "cache_control" not in result[-1]

    def test_project_context_present_adds_cached_block(self) -> None:
        result = build_cached_system_blocks(BASE, project_context=PROJECT_CTX)
        assert result[0]["text"] == PROJECT_CTX
        assert "cache_control" in result[0]
        assert result[-1]["text"] == BASE
        assert "cache_control" not in result[-1]

    def test_both_static_blocks_cache_control_on_last_static_only(self) -> None:
        """cache_control must be on the last cached block, not on every block."""
        result = build_cached_system_blocks(
            BASE, claude_md_content=CLAUDE_MD, project_context=PROJECT_CTX
        )
        # [claude_md_block, project_context_block, base_instructions_block]
        assert len(result) == 3

        claude_md_block = result[0]
        project_ctx_block = result[1]
        base_block = result[2]

        # claude_md block must NOT have cache_control (only the last static block does)
        assert "cache_control" not in claude_md_block, (
            "cache_control must not appear on the first static block; "
            "only the last static block carries the breakpoint marker"
        )
        # project_context block (last static) MUST have cache_control
        assert "cache_control" in project_ctx_block
        # base_instructions (dynamic, last overall) must NOT have cache_control
        assert "cache_control" not in base_block

    def test_default_ttl_is_ephemeral(self) -> None:
        result = build_cached_system_blocks(BASE, claude_md_content=CLAUDE_MD)
        cc = result[0]["cache_control"]
        assert cc == {"type": "ephemeral"}

    def test_ttl_1h_produces_correct_cache_control(self) -> None:
        """1h TTL must produce {"type": "ephemeral", "ttl": "1h"} — not "ephemeral_1h"."""
        result = build_cached_system_blocks(
            BASE, claude_md_content=CLAUDE_MD, ttl="1h"
        )
        cc = result[0]["cache_control"]
        assert cc == {"type": "ephemeral", "ttl": "1h"}, (
            f"Expected ephemeral+1h TTL shape but got: {cc!r}"
        )

    def test_block_ordering_static_before_dynamic(self) -> None:
        """Static cacheable content must always precede the dynamic base_instructions."""
        result = build_cached_system_blocks(
            BASE, claude_md_content=CLAUDE_MD, project_context=PROJECT_CTX
        )
        texts = [b["text"] for b in result]
        base_idx = texts.index(BASE)
        md_idx = texts.index(CLAUDE_MD)
        ctx_idx = texts.index(PROJECT_CTX)
        assert md_idx < base_idx, "CLAUDE.md block must come before base_instructions"
        assert ctx_idx < base_idx, "project_context block must come before base_instructions"

    def test_empty_string_claude_md_treated_as_absent(self) -> None:
        """An empty string for claude_md_content must not add a cached block."""
        result = build_cached_system_blocks(BASE, claude_md_content="")
        assert len(result) == 1
        assert "cache_control" not in result[0]

    def test_empty_string_project_context_treated_as_absent(self) -> None:
        result = build_cached_system_blocks(BASE, project_context="")
        assert len(result) == 1
        assert "cache_control" not in result[0]

    def test_all_blocks_have_type_text(self) -> None:
        result = build_cached_system_blocks(
            BASE, claude_md_content=CLAUDE_MD, project_context=PROJECT_CTX
        )
        for block in result:
            assert block["type"] == "text"

    def test_cache_control_count_exactly_one(self) -> None:
        """Exactly one block should carry a cache_control marker."""
        result = build_cached_system_blocks(
            BASE, claude_md_content=CLAUDE_MD, project_context=PROJECT_CTX
        )
        blocks_with_cc = [b for b in result if "cache_control" in b]
        assert len(blocks_with_cc) == 1, (
            f"Expected exactly 1 cache_control marker, found {len(blocks_with_cc)}"
        )


# ===========================================================================
# build_cached_system_str
# ===========================================================================


class TestBuildCachedSystemStr:
    """Tests for the SDK-compatible string-collapse helper."""

    def test_returns_str(self) -> None:
        result = build_cached_system_str(BASE)
        assert isinstance(result, str)

    def test_no_static_content_returns_base_instructions(self) -> None:
        result = build_cached_system_str(BASE)
        assert result == BASE

    def test_claude_md_included_before_base(self) -> None:
        result = build_cached_system_str(BASE, claude_md_content=CLAUDE_MD)
        md_pos = result.index(CLAUDE_MD)
        base_pos = result.index(BASE)
        assert md_pos < base_pos

    def test_project_context_included_before_base(self) -> None:
        result = build_cached_system_str(BASE, project_context=PROJECT_CTX)
        ctx_pos = result.index(PROJECT_CTX)
        base_pos = result.index(BASE)
        assert ctx_pos < base_pos

    def test_both_static_sections_ordering(self) -> None:
        result = build_cached_system_str(
            BASE, claude_md_content=CLAUDE_MD, project_context=PROJECT_CTX
        )
        md_pos = result.index(CLAUDE_MD)
        ctx_pos = result.index(PROJECT_CTX)
        base_pos = result.index(BASE)
        assert md_pos < ctx_pos < base_pos

    def test_identical_output_for_same_inputs(self) -> None:
        """Byte-identical output is mandatory for automatic server-side caching."""
        a = build_cached_system_str(BASE, claude_md_content=CLAUDE_MD)
        b = build_cached_system_str(BASE, claude_md_content=CLAUDE_MD)
        assert a == b

    def test_empty_claude_md_omitted(self) -> None:
        result_with = build_cached_system_str(BASE, claude_md_content="")
        result_without = build_cached_system_str(BASE)
        assert result_with == result_without

    def test_no_cache_control_markers_in_string(self) -> None:
        """The string form must not contain raw cache_control JSON fragments."""
        result = build_cached_system_str(
            BASE, claude_md_content=CLAUDE_MD, project_context=PROJECT_CTX
        )
        assert "cache_control" not in result
        assert "ephemeral" not in result


# ===========================================================================
# Cross-function consistency
# ===========================================================================


class TestConsistency:
    """build_cached_system_blocks and build_cached_system_str must agree on ordering."""

    def test_text_content_order_matches_between_helpers(self) -> None:
        blocks = build_cached_system_blocks(
            BASE, claude_md_content=CLAUDE_MD, project_context=PROJECT_CTX
        )
        flat = build_cached_system_str(
            BASE, claude_md_content=CLAUDE_MD, project_context=PROJECT_CTX
        )
        # All block texts must appear in flat string in the same order
        positions = [flat.index(b["text"]) for b in blocks if b["text"] in flat]
        assert positions == sorted(positions), (
            "Block ordering mismatch between build_cached_system_blocks and "
            "build_cached_system_str"
        )


class TestArchitectAmendments:
    """The three guards added per the architect-reviewer's design review."""

    def test_min_size_guard_emits_warning_below_floor(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # 10-char claude_md → ~2.5 tokens, well below any model's floor (≥1024).
        caplog.set_level("WARNING", logger="core.cache")
        build_cached_system_str(
            BASE,
            claude_md_content="too short!",
            model="claude-sonnet-4-6",
        )
        messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("below floor" in m and "claude-sonnet-4-6" in m for m in messages), (
            f"Expected a 'below floor' warning for sonnet; got: {messages}"
        )

    def test_min_size_guard_silent_when_model_not_provided(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Same tiny claude_md but no model → no warning (legacy callers preserved).
        caplog.set_level("WARNING", logger="core.cache")
        build_cached_system_str(BASE, claude_md_content="too short!")
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warnings == [], (
            f"Expected no warnings when model=None; got: {[r.message for r in warnings]}"
        )

    def test_hash_change_warning_fires_on_second_call(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Reset state — the module-level _PREFIX_HASHES is shared across tests.
        from core.cache import _PREFIX_HASHES
        _PREFIX_HASHES.pop("/tmp/project-hash-test", None)

        caplog.set_level("WARNING", logger="core.cache")
        # First call — populates hash, no warning expected.
        build_cached_system_str(
            BASE,
            claude_md_content="# version 1",
            project_dir="/tmp/project-hash-test",
        )
        first_warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        caplog.clear()

        # Second call with DIFFERENT static content — warning expected.
        build_cached_system_str(
            BASE,
            claude_md_content="# version 2 — edited",
            project_dir="/tmp/project-hash-test",
        )
        second_warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]

        assert not any("prefix changed" in m.lower() for m in first_warnings), (
            f"First call should not warn about prefix change; got: {first_warnings}"
        )
        assert any("prefix changed" in m.lower() for m in second_warnings), (
            f"Second call should warn about prefix change; got: {second_warnings}"
        )

    def test_hash_warning_silent_when_unchanged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from core.cache import _PREFIX_HASHES
        _PREFIX_HASHES.pop("/tmp/project-hash-stable", None)

        caplog.set_level("WARNING", logger="core.cache")
        build_cached_system_str(BASE, claude_md_content="stable",
                                project_dir="/tmp/project-hash-stable")
        caplog.clear()
        # Identical content — no warning.
        build_cached_system_str(BASE, claude_md_content="stable",
                                project_dir="/tmp/project-hash-stable")
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert all("prefix changed" not in r.message.lower() for r in warnings), (
            f"Identical static prefix must not trigger change warning; got: "
            f"{[r.message for r in warnings]}"
        )

    def test_ttl_invalid_value_raises_value_error(self) -> None:
        from core.cache import _make_cache_control
        with pytest.raises(ValueError, match="Invalid ttl"):
            _make_cache_control("forever")  # type: ignore[arg-type]

    def test_ttl_valid_values_accepted(self) -> None:
        from core.cache import _make_cache_control
        # Both must succeed without raising.
        assert _make_cache_control("ephemeral") == {"type": "ephemeral"}
        assert _make_cache_control("1h") == {"type": "ephemeral", "ttl": "1h"}
