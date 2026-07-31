#!/usr/bin/env python3
"""
Tests for OllamaAgenticProvider and tools/ package
====================================================

Section 1 — Unit tests (mocked, always run):
    - TestToolDefinitions: schema format, filtering, unknown tool
    - TestToolExecutorSecurity: path traversal, absolute path escape, valid paths
    - TestToolExecutorReadWrite: read with line numbers, write+read roundtrip, edit
    - TestAgenticProviderInit: defaults, custom values, BaseLLMProvider subclass, payload
    - TestAgenticProviderLoop: mocked HTTP — text-only, tool-call cycle, max_turns

Section 2 — Live integration tests (@pytest.mark.slow, requires Ollama):
    - TestOllamaAgenticLive: real tool calls against a local Ollama server
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# SDK pre-mock — must happen before any provider import
# ---------------------------------------------------------------------------

if "claude_agent_sdk" not in sys.modules:
    _sdk_mock = MagicMock()
    _sdk_mock.ClaudeSDKClient = MagicMock()
    _sdk_mock.ClaudeAgentOptions = MagicMock()
    _sdk_mock.HookMatcher = MagicMock()
    sys.modules["claude_agent_sdk"] = _sdk_mock
    sys.modules["claude_agent_sdk.types"] = MagicMock()

# ---------------------------------------------------------------------------
# sys.path — ensure apps/backend is importable
# ---------------------------------------------------------------------------

_BACKEND_DIR = str(Path(__file__).resolve().parent.parent / "apps" / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from providers import BaseLLMProvider  # noqa: E402
from providers.ollama_agentic import OllamaAgenticProvider  # noqa: E402
from providers.types import (  # noqa: E402
    AssistantMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from tools.definitions import get_tool_definitions  # noqa: E402
from tools.executor import ToolExecutor  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers (same pattern as test_qa_providers.py)
# ---------------------------------------------------------------------------


def _run(coro):
    """Run a coroutine in a new event loop (test helper)."""
    return asyncio.run(coro)


async def _collect(async_gen) -> list:
    """Collect all items yielded by an async generator."""
    items = []
    async for item in async_gen:
        items.append(item)
    return items


def _ollama_models(base_url: str = "http://localhost:11434") -> set[str] | None:
    """Model names this Ollama has PULLED, or None when the server is unreachable.

    `/api/tags` is the same single call the old reachability probe made -- it
    just threw the body away, which is why a server that was up but lacked the
    model produced a hard test FAILURE rather than a skip (#418):

        RuntimeError: Ollama API HTTP error 404: model 'qwen3.5:27b' not found

    Distinguishing "unreachable" (None) from "reachable, model absent" (a set
    without it) is the whole point: they need different messages, and neither is
    a failing test.
    """
    try:
        req = urllib.request.Request(f"{base_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - fixed localhost URL
            data = json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
    return {m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)}


# ===================================================================
# Section 1: Unit tests (mocked, always run)
# ===================================================================


class TestToolDefinitions:
    """Tests for tools.definitions.get_tool_definitions()."""

    def test_all_six_tools_returned(self):
        defs = get_tool_definitions()
        assert len(defs) == 6
        names = {d["function"]["name"] for d in defs}
        assert names == {"Read", "Write", "Edit", "Bash", "Glob", "Grep"}

    def test_schema_format_correct(self):
        defs = get_tool_definitions()
        for d in defs:
            assert d["type"] == "function"
            fn = d["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            params = fn["parameters"]
            assert params["type"] == "object"
            assert "properties" in params
            assert "required" in params

    def test_filtering_works(self):
        subset = get_tool_definitions(["Read", "Write"])
        assert len(subset) == 2
        names = {d["function"]["name"] for d in subset}
        assert names == {"Read", "Write"}

    def test_unknown_tool_raises(self):
        with pytest.raises(ValueError, match="Unknown tool.*'FakeTool'"):
            get_tool_definitions(["FakeTool"])


class TestToolExecutorSecurity:
    """Path boundary enforcement in ToolExecutor."""

    def test_path_traversal_blocked(self, tmp_path):
        executor = ToolExecutor(working_dir=tmp_path)
        # ../../../etc/passwd  should resolve outside working_dir
        result = _run(
            executor.execute("Read", {"file_path": str(tmp_path / ".." / ".." / "etc" / "passwd")})
        )
        assert result.is_error
        assert "outside" in result.content.lower() or "denied" in result.content.lower()

    def test_absolute_path_outside_blocked(self, tmp_path):
        executor = ToolExecutor(working_dir=tmp_path)
        result = _run(executor.execute("Read", {"file_path": "/etc/hostname"}))
        assert result.is_error
        assert "outside" in result.content.lower() or "denied" in result.content.lower()

    def test_valid_path_within_working_dir(self, tmp_path):
        # Create a file inside working dir
        test_file = tmp_path / "valid.txt"
        test_file.write_text("hello world")

        executor = ToolExecutor(working_dir=tmp_path)
        result = _run(executor.execute("Read", {"file_path": str(test_file)}))
        assert not result.is_error
        assert "hello world" in result.content


class TestToolExecutorReadWrite:
    """Read, Write, Edit operations using real temp files."""

    def test_read_file_with_line_numbers(self, tmp_path):
        test_file = tmp_path / "lines.txt"
        test_file.write_text("line one\nline two\nline three\n")

        executor = ToolExecutor(working_dir=tmp_path)
        result = _run(executor.execute("Read", {"file_path": str(test_file)}))
        assert not result.is_error
        # cat -n style output: line numbers followed by content
        assert "1\tline one" in result.content
        assert "2\tline two" in result.content
        assert "3\tline three" in result.content

    def test_write_and_read_roundtrip(self, tmp_path):
        executor = ToolExecutor(working_dir=tmp_path)

        target = str(tmp_path / "output.txt")
        content = "Hello from test!"

        # Write
        w_result = _run(executor.execute("Write", {"file_path": target, "content": content}))
        assert not w_result.is_error
        assert "Successfully wrote" in w_result.content

        # Read back
        r_result = _run(executor.execute("Read", {"file_path": target}))
        assert not r_result.is_error
        assert "Hello from test!" in r_result.content

    def test_edit_string_replacement(self, tmp_path):
        test_file = tmp_path / "editable.txt"
        test_file.write_text("def foo():\n    return 42\n")

        executor = ToolExecutor(working_dir=tmp_path)
        result = _run(
            executor.execute(
                "Edit",
                {
                    "file_path": str(test_file),
                    "old_string": "return 42",
                    "new_string": "return 99",
                },
            )
        )
        assert not result.is_error
        assert "Successfully edited" in result.content

        # Verify on disk
        assert "return 99" in test_file.read_text()
        assert "return 42" not in test_file.read_text()


class TestAgenticProviderInit:
    """OllamaAgenticProvider initialization and payload building."""

    def test_default_values(self):
        p = OllamaAgenticProvider()
        assert p._model == "llama3.2"
        assert p._base_url == "http://localhost:11434"
        assert p._max_turns == 25
        assert p._timeout == 600

    def test_custom_values(self, tmp_path):
        p = OllamaAgenticProvider(
            model="qwen3:30b",
            base_url="http://ollama.example.com:11434",
            timeout=300,
            working_dir=tmp_path,
            max_turns=10,
            extra_options={"temperature": 0.7},
        )
        assert p._model == "qwen3:30b"
        assert p._base_url == "http://ollama.example.com:11434"  # matches custom base_url
        assert p._max_turns == 10
        assert p._timeout == 300

    def test_is_base_llm_provider_subclass(self):
        assert issubclass(OllamaAgenticProvider, BaseLLMProvider)

    def test_payload_includes_tools_array(self, tmp_path):
        p = OllamaAgenticProvider(model="test-model", working_dir=tmp_path)
        messages = [{"role": "user", "content": "hello"}]
        payload = p._build_payload(messages)

        assert "tools" in payload
        assert isinstance(payload["tools"], list)
        assert len(payload["tools"]) == 6
        assert payload["model"] == "test-model"
        assert payload["stream"] is False


class TestAgenticProviderLoop:
    """Mocked _http_post to test the agentic loop without network."""

    def test_text_only_response_yields_one_assistant_message(self, tmp_path):
        """When the model returns text with no tool_calls, we get one AssistantMessage."""
        p = OllamaAgenticProvider(model="mock", working_dir=tmp_path)

        # Mock HTTP to return a text-only response
        mock_response = {
            "message": {
                "role": "assistant",
                "content": "The answer is 42.",
                "tool_calls": None,
            }
        }

        with patch.object(p, "_http_post", return_value=mock_response):
            _run(p.query("What is the answer?"))
            messages = _run(_collect(p.receive_response()))

        assert len(messages) == 1
        msg = messages[0]
        assert type(msg).__name__ == "AssistantMessage"
        assert any(type(b).__name__ == "TextBlock" and "42" in b.text for b in msg.content)

    def test_tool_call_response_yields_assistant_and_user(self, tmp_path):
        """When the model calls a tool, we get AssistantMessage + UserMessage pair."""
        # Create a file for the Read tool to find
        test_file = tmp_path / "data.txt"
        test_file.write_text("secret content")

        p = OllamaAgenticProvider(model="mock", working_dir=tmp_path, max_turns=5)

        # Turn 1: model calls Read tool
        tool_call_response = {
            "message": {
                "role": "assistant",
                "content": "Let me read that file.",
                "tool_calls": [
                    {
                        "function": {
                            "name": "Read",
                            "arguments": {"file_path": str(test_file)},
                        }
                    }
                ],
            }
        }
        # Turn 2: model provides final text
        final_response = {
            "message": {
                "role": "assistant",
                "content": "The file contains: secret content.",
                "tool_calls": None,
            }
        }

        call_count = 0

        def mock_post(url, payload):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tool_call_response
            return final_response

        with patch.object(p, "_http_post", side_effect=mock_post):
            _run(p.query("Read the file"))
            messages = _run(_collect(p.receive_response()))

        # Should be: AssistantMessage (with ToolUseBlock) → UserMessage (with ToolResultBlock) → AssistantMessage (final text)
        assert len(messages) == 3
        assert type(messages[0]).__name__ == "AssistantMessage"
        assert type(messages[1]).__name__ == "UserMessage"
        assert type(messages[2]).__name__ == "AssistantMessage"

        # Verify the tool use block
        tool_blocks = [b for b in messages[0].content if type(b).__name__ == "ToolUseBlock"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].name == "Read"

        # Verify the tool result
        result_blocks = [b for b in messages[1].content if type(b).__name__ == "ToolResultBlock"]
        assert len(result_blocks) == 1
        assert "secret content" in str(result_blocks[0].content)

    def test_max_turns_sentinel(self, tmp_path):
        """When max_turns is reached, the loop stops with a sentinel message."""
        p = OllamaAgenticProvider(model="mock", working_dir=tmp_path, max_turns=2)

        # Every turn returns a tool call, forcing the loop to hit max_turns
        infinite_tool_response = {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "Glob",
                            "arguments": {"pattern": "*.py"},
                        }
                    }
                ],
            }
        }

        with patch.object(p, "_http_post", return_value=infinite_tool_response):
            _run(p.query("find files"))
            messages = _run(_collect(p.receive_response()))

        # Each turn produces AssistantMessage + UserMessage, then sentinel
        # 2 turns × 2 messages + 1 sentinel = 5
        assert len(messages) == 5
        last = messages[-1]
        assert type(last).__name__ == "AssistantMessage"
        assert any(
            "maximum" in b.text.lower() for b in last.content if type(b).__name__ == "TextBlock"
        )


# ===================================================================
# Section 2: Live integration tests (requires Ollama)
# ===================================================================


_LIVE_MODEL = "qwen3.5:27b"
_OLLAMA_URL = "http://localhost:11434"

# Which model to drive. Pinned for determinism, overridable so a box serving a
# different model can actually run these instead of always skipping (#418).
_LIVE_MODEL = os.environ.get("PFACTORY_OLLAMA_TEST_MODEL", _LIVE_MODEL)

_PULLED = _ollama_models(_OLLAMA_URL)
_skip_no_ollama = pytest.mark.skipif(
    _PULLED is None or _LIVE_MODEL not in _PULLED,
    reason=(
        f"Ollama not reachable at {_OLLAMA_URL}"
        if _PULLED is None
        else f"Ollama at {_OLLAMA_URL} has no {_LIVE_MODEL!r} pulled "
        f"(has: {', '.join(sorted(_PULLED)) or 'nothing'}). "
        f"Override with PFACTORY_OLLAMA_TEST_MODEL."
    ),
)


@pytest.mark.slow
@_skip_no_ollama
class TestOllamaAgenticLive:
    """Live integration tests against a local Ollama server.

    Requires:
      - Ollama running at localhost:11434
      - qwen3.5:27b model pulled

    Run with: pytest tests/test_ollama_agentic.py -v -m slow
    Skip with: pytest tests/test_ollama_agentic.py -v -m "not slow"
    """

    @pytest.fixture
    def provider(self, tmp_path):
        """Create an OllamaAgenticProvider pointed at tmp_path."""
        return OllamaAgenticProvider(
            model=_LIVE_MODEL,
            base_url=_OLLAMA_URL,
            working_dir=tmp_path,
            max_turns=10,
            timeout=120,
            extra_options={"temperature": 0},
        )

    def test_live_read_file(self, provider, tmp_path):
        """Model should call Read tool to read a file and describe its contents."""
        # Create a temp file with known content
        test_file = tmp_path / "greeting.txt"
        test_file.write_text("Hello from PFactory integration test!\nLine two here.\n")

        prompt = (
            f"Read the file at {test_file} and tell me what's in it. "
            "Use the Read tool to read it. /no_think"
        )

        messages = _run(self._query_and_collect(provider, prompt))

        # At least one AssistantMessage should have a ToolUseBlock with name="Read"
        tool_use_found = False
        tool_result_found = False
        final_text = ""

        for i, msg in enumerate(messages):
            if type(msg).__name__ == "AssistantMessage":
                for block in msg.content:
                    if type(block).__name__ == "ToolUseBlock" and block.name == "Read":
                        tool_use_found = True
                    if type(block).__name__ == "TextBlock":
                        final_text += block.text

            if type(msg).__name__ == "UserMessage":
                for block in msg.content:
                    if type(block).__name__ == "ToolResultBlock":
                        if "Hello from PFactory" in str(block.content):
                            tool_result_found = True

        assert tool_use_found, f"Expected Read tool call in messages: {self._summarize(messages)}"
        assert tool_result_found, (
            f"Expected tool result with file content: {self._summarize(messages)}"
        )
        # The final text should reference the file content in some way
        assert (
            "hello" in final_text.lower()
            or "pfactory" in final_text.lower()
            or "greeting" in final_text.lower()
            or "integration" in final_text.lower()
        ), f"Expected final text to reference file content. Got: {final_text[:500]}"

    def test_live_write_and_verify(self, provider, tmp_path):
        """Model should write a Python file and then read it back."""
        target = tmp_path / "adder.py"

        prompt = (
            f"Write a Python file at {target} with a function called 'add' "
            "that takes two numbers and returns their sum. Then read the file "
            "back to verify it was written correctly. Use the Write tool to "
            "write it and the Read tool to read it back. /no_think"
        )

        messages = _run(self._query_and_collect(provider, prompt))

        # Check tool calls include Write and Read
        tool_names_used = set()
        for msg in messages:
            if type(msg).__name__ == "AssistantMessage":
                for block in msg.content:
                    if type(block).__name__ == "ToolUseBlock":
                        tool_names_used.add(block.name)

        assert "Write" in tool_names_used, (
            f"Expected Write tool call. Tools used: {tool_names_used}. Messages: {self._summarize(messages)}"
        )
        assert "Read" in tool_names_used, (
            f"Expected Read tool call. Tools used: {tool_names_used}. Messages: {self._summarize(messages)}"
        )

        # The file should actually exist on disk
        assert target.exists(), f"Expected {target} to exist on disk after Write"

        # File content should contain a function definition
        content = target.read_text()
        assert "def" in content, f"Expected 'def' in file content. Got: {content[:300]}"

    def test_live_glob_search(self, provider, tmp_path):
        """Model should use Glob to find Python files."""
        # Create some .py files in the working dir
        (tmp_path / "main.py").write_text("print('main')\n")
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        (tmp_path / "readme.txt").write_text("not python\n")
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.py").write_text("# nested\n")

        prompt = (
            f"Find all Python files in {tmp_path}. "
            "Use the Glob tool with pattern '**/*.py'. /no_think"
        )

        messages = _run(self._query_and_collect(provider, prompt))

        # Model should call Glob
        glob_called = False
        glob_result_content = ""
        for msg in messages:
            if type(msg).__name__ == "AssistantMessage":
                for block in msg.content:
                    if type(block).__name__ == "ToolUseBlock" and block.name == "Glob":
                        glob_called = True
            if type(msg).__name__ == "UserMessage":
                for block in msg.content:
                    if type(block).__name__ == "ToolResultBlock" and not block.is_error:
                        glob_result_content += str(block.content)

        assert glob_called, f"Expected Glob tool call. Messages: {self._summarize(messages)}"
        # Results should contain .py files
        assert ".py" in glob_result_content, (
            f"Expected .py files in Glob results. Got: {glob_result_content[:500]}"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _query_and_collect(provider: OllamaAgenticProvider, prompt: str) -> list[Any]:
        """Send a query and collect all response messages."""
        async with provider:
            await provider.query(prompt)
            return await _collect(provider.receive_response())

    @staticmethod
    def _summarize(messages: list[Any]) -> str:
        """Create a human-readable summary of messages for assertion errors."""
        parts = []
        for i, msg in enumerate(messages):
            cls = type(msg).__name__
            blocks = []
            for b in getattr(msg, "content", []):
                btype = type(b).__name__
                if btype == "TextBlock":
                    blocks.append(f"TextBlock({b.text[:80]}...)")
                elif btype == "ToolUseBlock":
                    blocks.append(f"ToolUseBlock({b.name})")
                elif btype == "ToolResultBlock":
                    err = " ERROR" if b.is_error else ""
                    blocks.append(f"ToolResultBlock{err}({str(b.content)[:80]}...)")
                else:
                    blocks.append(btype)
            parts.append(f"  [{i}] {cls}: {', '.join(blocks)}")
        return "\n" + "\n".join(parts)
