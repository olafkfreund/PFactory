#!/usr/bin/env python3
"""
Unit tests for the OpenAI-compatible providers
==============================================

Covers:
- OpenAICompatibleProvider (text-only)        — providers/openai_compatible.py
- OpenAICompatibleAgenticProvider             — providers/openai_compatible_agentic.py
- Factory routing + aliases                   — providers/factory.py

HTTP calls are mocked via ``urllib.request.urlopen`` patches so the tests
run without any network or running LLM server.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# SDK pre-mock — needed because providers/claude.py imports claude_agent_sdk
# at module import time and that module isn't available in the test env.
# ---------------------------------------------------------------------------

if "claude_agent_sdk" not in sys.modules:
    _sdk_mock = MagicMock()
    _sdk_mock.ClaudeSDKClient = MagicMock()
    _sdk_mock.ClaudeAgentOptions = MagicMock()
    _sdk_mock.HookMatcher = MagicMock()
    sys.modules["claude_agent_sdk"] = _sdk_mock
    sys.modules["claude_agent_sdk.types"] = MagicMock()

# ---------------------------------------------------------------------------
# Ensure apps/backend on sys.path
# ---------------------------------------------------------------------------

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from providers.factory import (  # noqa: E402
    get_provider,
    get_qa_llm_provider,
    list_provider_aliases,
)
from providers.openai_compatible import OpenAICompatibleProvider  # noqa: E402
from providers.openai_compatible_agentic import (  # noqa: E402
    OpenAICompatibleAgenticProvider,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_urlopen_response(payload: dict) -> MagicMock:
    """Return a MagicMock that mimics ``urllib.request.urlopen``'s context manager."""
    raw = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__ = MagicMock(
        return_value=MagicMock(
            read=MagicMock(return_value=raw),
            getcode=MagicMock(return_value=200),
        )
    )
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _http_error(code: int, reason: str = "Not Found") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://test/v1/models",
        code=code,
        msg=reason,
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b""),
    )


async def _collect(provider) -> list:
    """Run an async generator to completion and collect all yielded messages."""
    return [msg async for msg in provider.receive_response()]


# ===========================================================================
# Factory routing
# ===========================================================================


class TestFactoryRouting:
    def test_canonical_name_resolves_text(self) -> None:
        p = get_qa_llm_provider("openai-compatible", model="x")
        assert isinstance(p, OpenAICompatibleProvider)

    def test_canonical_name_resolves_agentic(self) -> None:
        p = get_provider(
            "openai-compatible",
            phase="coding",
            model="x",
            working_dir=Path("."),
        )
        assert isinstance(p, OpenAICompatibleAgenticProvider)

    @pytest.mark.parametrize(
        "alias",
        [
            "openai", "openai-api", "oai", "lm-studio", "lmstudio",
            "vllm", "openrouter", "together", "together-ai", "groq",
            "localai", "anyscale",
        ],
    )
    def test_aliases_all_resolve(self, alias: str) -> None:
        p = get_qa_llm_provider(alias, model="x")
        assert isinstance(p, OpenAICompatibleProvider)

    def test_aliases_resolve_to_canonical_name(self) -> None:
        aliases = list_provider_aliases()
        oai_aliases = {a for a, canon in aliases.items() if canon == "openai-compatible"}
        # Must include the human-friendly ones
        for required in {"openai", "lm-studio", "vllm", "openrouter"}:
            assert required in oai_aliases


# ===========================================================================
# OpenAICompatibleProvider (text-only)
# ===========================================================================


class TestTextProvider:
    def test_default_construction(self) -> None:
        p = OpenAICompatibleProvider()
        assert p._model == "gpt-4o-mini"
        assert p._base_url == "https://api.openai.com"
        assert p._api_key is None

    def test_trailing_slash_stripped(self) -> None:
        p = OpenAICompatibleProvider(base_url="https://api.example.com/")
        assert p._base_url == "https://api.example.com"

    def test_empty_api_key_normalised_to_none(self) -> None:
        p = OpenAICompatibleProvider(api_key="")
        assert p._api_key is None

    def test_build_payload(self) -> None:
        p = OpenAICompatibleProvider(model="m1")
        body = p._build_payload("hello world")
        assert body["model"] == "m1"
        assert body["messages"] == [{"role": "user", "content": "hello world"}]
        assert body["stream"] is False
        assert body["temperature"] == 0

    def test_build_payload_extra_options_override_default(self) -> None:
        p = OpenAICompatibleProvider(
            extra_options={"temperature": 0.7, "max_tokens": 256}
        )
        body = p._build_payload("x")
        assert body["temperature"] == 0.7
        assert body["max_tokens"] == 256

    def test_build_headers_without_key(self) -> None:
        p = OpenAICompatibleProvider()
        headers = p._build_headers()
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_build_headers_with_key(self) -> None:
        p = OpenAICompatibleProvider(api_key="sk-abc123")
        headers = p._build_headers()
        assert headers["Authorization"] == "Bearer sk-abc123"

    def test_build_headers_with_extra(self) -> None:
        p = OpenAICompatibleProvider(extra_headers={"HTTP-Referer": "https://x"})
        headers = p._build_headers()
        assert headers["HTTP-Referer"] == "https://x"

    def test_extract_content_happy_path(self) -> None:
        text = OpenAICompatibleProvider._extract_content({
            "choices": [{"message": {"role": "assistant", "content": "  hi  "}}]
        })
        assert text == "hi"

    def test_extract_content_missing_choices_raises(self) -> None:
        with pytest.raises(RuntimeError, match="missing 'choices'"):
            OpenAICompatibleProvider._extract_content({"foo": "bar"})

    def test_extract_content_with_error_field_raises(self) -> None:
        with pytest.raises(RuntimeError, match="returned error"):
            OpenAICompatibleProvider._extract_content({
                "error": {"message": "invalid key", "code": 401}
            })

    def test_extract_content_empty_string_falls_back(self) -> None:
        text = OpenAICompatibleProvider._extract_content({
            "choices": [{"message": {"content": ""}}]
        })
        assert text == "(no output from server)"

    @pytest.mark.asyncio
    async def test_end_to_end_with_mocked_urlopen(self) -> None:
        provider = OpenAICompatibleProvider(model="gpt-4o-mini", api_key="sk-test")

        api_response = {
            "id": "chatcmpl-1",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hello back!"},
                    "finish_reason": "stop",
                }
            ],
        }

        with patch(
            "providers.openai_compatible.urllib.request.urlopen",
            return_value=_mock_urlopen_response(api_response),
        ):
            await provider.query("Hi")
            messages = await _collect(provider)

        assert len(messages) == 1
        assert type(messages[0]).__name__ == "AssistantMessage"
        assert messages[0].content[0].text == "Hello back!"

    @pytest.mark.asyncio
    async def test_aenter_health_check_404_is_accepted(self) -> None:
        """Servers without /v1/models (404) must still pass the health check."""
        provider = OpenAICompatibleProvider(base_url="http://localhost:1234")

        with patch(
            "providers.openai_compatible.urllib.request.urlopen",
            side_effect=_http_error(404, "Not Found"),
        ):
            async with provider:
                pass  # __aenter__ should not raise


# ===========================================================================
# OpenAICompatibleAgenticProvider
# ===========================================================================


class TestAgenticProvider:
    def test_construction_includes_tools(self) -> None:
        p = OpenAICompatibleAgenticProvider(
            working_dir=Path("/tmp"),
            tool_names=["Read", "Write"],
        )
        names = [t["function"]["name"] for t in p._tool_defs]
        assert names == ["Read", "Write"]

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, {}),
            ({"a": 1}, {"a": 1}),
            ('{"a": 1}', {"a": 1}),
            ("not json", {}),
            ('{"a":', {}),  # truncated JSON
            (123, {}),  # wrong type
            ('"just a string"', {}),  # JSON but not an object
        ],
    )
    def test_parse_tool_args(self, raw, expected) -> None:
        assert OpenAICompatibleAgenticProvider._parse_tool_args(raw) == expected

    def test_parse_tool_args_size_limit(self) -> None:
        oversized = '{"x":"' + "a" * 60_000 + '"}'
        assert OpenAICompatibleAgenticProvider._parse_tool_args(oversized) == {}

    def test_build_payload_includes_tools(self) -> None:
        p = OpenAICompatibleAgenticProvider(
            working_dir=Path("/tmp"), tool_names=["Read"]
        )
        body = p._build_payload([{"role": "user", "content": "hi"}])
        assert body["model"] == "gpt-4o-mini"
        assert body["stream"] is False
        assert body["tools"] == p._tool_defs
        assert body["temperature"] == 0

    @pytest.mark.asyncio
    async def test_no_tool_calls_yields_final_message(self, tmp_path: Path) -> None:
        """When the model returns no tool_calls, we should get one assistant message and stop."""
        provider = OpenAICompatibleAgenticProvider(
            working_dir=tmp_path,
            tool_names=["Read"],
        )

        api_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "All done.",
                    },
                    "finish_reason": "stop",
                }
            ],
        }

        with patch(
            "providers.openai_compatible_agentic.urllib.request.urlopen",
            return_value=_mock_urlopen_response(api_response),
        ):
            await provider.query("Just answer plainly")
            messages = await _collect(provider)

        assert len(messages) == 1
        assert type(messages[0]).__name__ == "AssistantMessage"
        assert messages[0].content[0].text == "All done."

    @pytest.mark.asyncio
    async def test_tool_call_loop_executes_and_terminates(
        self, tmp_path: Path
    ) -> None:
        """Two-turn flow: model calls Read, then returns final text."""
        # Set up a file the agent will 'read'
        target = tmp_path / "hello.txt"
        target.write_text("Hello from file!")

        provider = OpenAICompatibleAgenticProvider(
            working_dir=tmp_path,
            tool_names=["Read"],
        )

        turn1 = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Let me read it.",
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "type": "function",
                                "function": {
                                    "name": "Read",
                                    "arguments": json.dumps(
                                        {"file_path": str(target)}
                                    ),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        turn2 = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "The file says: Hello from file!",
                    },
                    "finish_reason": "stop",
                }
            ]
        }

        urlopen_returns = [
            _mock_urlopen_response(turn1),
            _mock_urlopen_response(turn2),
        ]

        with patch(
            "providers.openai_compatible_agentic.urllib.request.urlopen",
            side_effect=urlopen_returns,
        ):
            await provider.query("Read the file")
            messages = await _collect(provider)

        # Expected: AssistantMessage (turn 1 with text+tool use)
        #           UserMessage     (tool result)
        #           AssistantMessage (turn 2 final text)
        assert [type(m).__name__ for m in messages] == [
            "AssistantMessage",
            "UserMessage",
            "AssistantMessage",
        ]
        # The final message contains the model's wrap-up text
        final_text = messages[-1].content[0].text
        assert "Hello from file!" in final_text

    @pytest.mark.asyncio
    async def test_max_turns_safety_net(self, tmp_path: Path) -> None:
        """If the model keeps requesting tools forever, we must halt at max_turns."""
        provider = OpenAICompatibleAgenticProvider(
            working_dir=tmp_path,
            tool_names=["Read"],
            max_turns=2,
        )

        # Force an existing file so the tool call doesn't surface FS errors
        target = tmp_path / "loop.txt"
        target.write_text("x")

        infinite_tool_call = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_loop",
                                "type": "function",
                                "function": {
                                    "name": "Read",
                                    "arguments": json.dumps(
                                        {"file_path": str(target)}
                                    ),
                                },
                            }
                        ],
                    }
                }
            ]
        }

        with patch(
            "providers.openai_compatible_agentic.urllib.request.urlopen",
            side_effect=[
                _mock_urlopen_response(infinite_tool_call),
                _mock_urlopen_response(infinite_tool_call),
            ],
        ):
            await provider.query("loop forever please")
            messages = await _collect(provider)

        # Final message should be the "max turns reached" notice
        text = messages[-1].content[0].text
        assert "maximum" in text.lower() and "turn" in text.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
