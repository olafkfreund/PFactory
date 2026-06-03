"""Tests for the shared agent-output JSON envelope (#96).

Covers the four extraction strategies plus the failure contract that call
sites such as ``analysis.insight_extractor`` rely on.
"""

from __future__ import annotations

import pytest
from agents.output_envelope import (
    OutputEnvelopeError,
    extract_json,
    strip_code_fence,
)

# ── Happy path: already-clean JSON ──────────────────────────────────────────


def test_direct_object_not_salvaged() -> None:
    doc, salvaged = extract_json('{"verdict": "accept"}')
    assert doc == {"verdict": "accept"}
    assert salvaged is False


def test_direct_array_not_salvaged() -> None:
    doc, salvaged = extract_json("[1, 2, 3]")
    assert doc == [1, 2, 3]
    assert salvaged is False


def test_surrounding_whitespace_is_not_salvage() -> None:
    # Leading/trailing whitespace is stripped before the fast path, so the
    # direct parse still succeeds and nothing is flagged as salvaged.
    doc, salvaged = extract_json('\n\n  {"a": 1}\t\n')
    assert doc == {"a": 1}
    assert salvaged is False


# ── Strategy 2: markdown code fences ────────────────────────────────────────


def test_json_fence_is_stripped() -> None:
    doc, salvaged = extract_json('```json\n{"plan": []}\n```')
    assert doc == {"plan": []}
    assert salvaged is True


def test_bare_fence_is_stripped() -> None:
    doc, salvaged = extract_json('```\n{"plan": []}\n```')
    assert doc == {"plan": []}
    assert salvaged is True


# ── Strategy 3: trailing/leading prose (raw_decode) ─────────────────────────


def test_leading_prose() -> None:
    doc, salvaged = extract_json('Here is the plan:\n{"ok": true}')
    assert doc == {"ok": True}
    assert salvaged is True


def test_trailing_prose_after_value() -> None:
    doc, salvaged = extract_json('{"ok": true}\n\nHope that helps!')
    assert doc == {"ok": True}
    assert salvaged is True


def test_fenced_with_language_and_trailing_remark() -> None:
    doc, salvaged = extract_json('```json\n{"a": {"b": 1}}\n```\nDone.')
    assert doc == {"a": {"b": 1}}
    assert salvaged is True


# ── Strategy 4: brace-match fallback ────────────────────────────────────────


def test_brace_match_when_raw_decode_cannot_start() -> None:
    # A leading "[...]" token makes the first JSON-value start a "[" that
    # raw_decode rejects; the outermost { … } brace-match still recovers
    # the object. (Stray *braces* before the object are not recoverable —
    # faithful to the original first/last brace-match.)
    text = '[note] {"x": 1}'
    doc, salvaged = extract_json(text)
    assert doc == {"x": 1}
    assert salvaged is True


# ── Failure contract ────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [None, "", "   ", "\n\t  \n"])
def test_empty_raises(bad: str | None) -> None:
    with pytest.raises(OutputEnvelopeError):
        extract_json(bad)


def test_no_json_raises() -> None:
    with pytest.raises(OutputEnvelopeError):
        extract_json("the model declined to answer")


def test_strict_rejects_anything_but_clean_json() -> None:
    with pytest.raises(OutputEnvelopeError):
        extract_json('```json\n{"a": 1}\n```', strict=True)
    # ...but accepts already-clean JSON.
    doc, salvaged = extract_json('{"a": 1}', strict=True)
    assert doc == {"a": 1}
    assert salvaged is False


# ── strip_code_fence helper ─────────────────────────────────────────────────


def test_strip_code_fence_no_fence_is_identity() -> None:
    assert strip_code_fence('  {"a": 1}  ') == '{"a": 1}'


def test_strip_code_fence_removes_fences() -> None:
    assert strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
