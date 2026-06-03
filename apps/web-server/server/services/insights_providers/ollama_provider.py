"""
Ollama provider for insights chat.

Uses HTTP streaming to localhost:11434/api/chat (NDJSON format).
Supports tool calling for models that implement OpenAI-compatible function calling.
"""

import asyncio
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

from ...websockets.events import broadcast_event
from .base import ProviderInfo, ProviderModel, ProviderStrategy
from .tools import execute_tool, get_tool_definitions

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
MAX_TOOL_ITERATIONS = 10

# Keywords that indicate an embedding or non-chat model
EMBEDDING_NAME_KEYWORDS = {"embed", "minilm", "bge", "gte", "e5", "rerank"}
EMBEDDING_FAMILIES = {"bert", "nomic-bert"}


def _is_embedding_model(name: str, details: dict | None = None) -> bool:
    """Check if an Ollama model is an embedding/reranker model (not a chat LLM)."""
    name_lower = name.lower()
    if any(kw in name_lower for kw in EMBEDDING_NAME_KEYWORDS):
        return True
    if details:
        family = details.get("family", "").lower()
        families = {f.lower() for f in details.get("families", [])}
        if family in EMBEDDING_FAMILIES or families & EMBEDDING_FAMILIES:
            return True
    return False


class OllamaProvider(ProviderStrategy):
    """Provider that streams via Ollama HTTP API."""

    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL) -> None:
        self.base_url = base_url

    async def detect(self) -> ProviderInfo:
        info = ProviderInfo(
            provider="ollama",
            available=False,
            display_name="Ollama",
            icon="ollama",
            auth_method=None,
            models=[],
        )

        if not shutil.which("ollama"):
            return info

        # Check if server is running by fetching model list
        try:
            import httpx
            async with httpx.AsyncClient(timeout=1.5) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()

                for m in data.get("models", []):
                    name = m["name"]
                    details = m.get("details", {})

                    if _is_embedding_model(name, details):
                        continue

                    info.models.append(ProviderModel(id=name, label=name))

                if info.models:
                    info.available = True
        except Exception as e:
            logger.debug(f"[OllamaProvider] Detection failed: {e}")
            # Fallback: check if ollama is installed but server may be down
            try:
                result = subprocess.run(
                    ["ollama", "list"], capture_output=True, text=True, timeout=2,
                )
                if result.returncode == 0 and result.stdout.strip():
                    lines = result.stdout.strip().splitlines()
                    for line in lines[1:]:
                        parts = line.split()
                        if parts:
                            name = parts[0]
                            if _is_embedding_model(name):
                                continue
                            info.models.append(ProviderModel(id=name, label=name))
                    if info.models:
                        info.available = True
            except Exception:
                pass

        return info

    async def _stream_response(
        self,
        client,
        payload: dict,
        project_id: str,
    ) -> tuple[str, list[dict], dict]:
        """Stream a single Ollama chat request, returning (text, tool_calls, metrics).

        Accumulates text content (broadcasting chunks) and collects any tool_calls
        from the response. Returns metrics from the final 'done' message.
        """
        accumulated = ""
        tool_calls = []
        ollama_metrics: dict = {}

        async with client.stream(
            "POST",
            f"{self.base_url}/api/chat",
            json=payload,
        ) as resp:
            resp.raise_for_status()

            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    msg = data.get("message", {})

                    content = msg.get("content", "")
                    if content:
                        accumulated += content
                        await broadcast_event("insights:chunk", {
                            "projectId": project_id,
                            "type": "text",
                            "content": content,
                        })

                    # Collect tool calls from the message
                    msg_tool_calls = msg.get("tool_calls", [])
                    if msg_tool_calls:
                        tool_calls.extend(msg_tool_calls)

                    if data.get("done"):
                        ollama_metrics = {
                            "eval_count": data.get("eval_count", 0),
                            "prompt_eval_count": data.get("prompt_eval_count", 0),
                            "eval_duration": data.get("eval_duration", 0),
                        }
                        break
                except json.JSONDecodeError:
                    continue

        return accumulated, tool_calls, ollama_metrics

    async def send_message(
        self,
        project_path: Path,
        project_id: str,
        message: str,
        model: str | None,
        model_config: dict | None,
        conversation_history: list[dict] | None,
    ) -> str:
        effective_model = model or (model_config or {}).get("model", "llama3.2:latest")

        # Build messages array with system context and conversation history
        resolved_path = project_path.resolve()
        system_prompt = (
            f"You are a helpful coding assistant analyzing the project at: {resolved_path}\n"
            f"You have tools to read files, list directories, and search code in this project.\n"
            f"Use your tools to explore the codebase when the user asks about files, code, "
            f"structure, or anything that requires looking at the actual project contents.\n"
            f"Always use tools before answering questions about the code — do not guess."
        )

        messages = [{"role": "system", "content": system_prompt}]
        if conversation_history:
            for msg in conversation_history[-10:]:  # Last 10 messages
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                })
        messages.append({"role": "user", "content": message})

        tools = get_tool_definitions()

        logger.info(f"[OllamaProvider] Streaming with tools: {effective_model}")

        try:
            import httpx

            await broadcast_event("insights:chunk", {
                "projectId": project_id,
                "type": "text",
                "content": "",
            })

            final_text = ""
            stream_start = time.monotonic()
            total_input_tokens = 0
            total_output_tokens = 0
            last_metrics: dict = {}
            use_tools = True  # Will be set to False if model doesn't support tools

            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
                for iteration in range(MAX_TOOL_ITERATIONS):
                    payload = {
                        "model": effective_model,
                        "messages": messages,
                        "stream": True,
                    }
                    if use_tools:
                        payload["tools"] = tools

                    try:
                        text, tool_calls, ollama_metrics = await self._stream_response(
                            client, payload, project_id,
                        )
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 400 and use_tools:
                            # Model doesn't support tool calling — retry without tools
                            logger.warning(
                                f"[OllamaProvider] {effective_model} returned 400 with tools, "
                                f"retrying without tools (model may not support function calling)"
                            )
                            use_tools = False
                            payload.pop("tools", None)
                            text, tool_calls, ollama_metrics = await self._stream_response(
                                client, payload, project_id,
                            )
                        else:
                            raise
                    last_metrics = ollama_metrics
                    total_input_tokens += ollama_metrics.get("prompt_eval_count", 0)
                    total_output_tokens += ollama_metrics.get("eval_count", 0)

                    if text:
                        final_text += text

                    # No tool calls or tools disabled — model is done
                    if not tool_calls or not use_tools:
                        break

                    # Append the assistant message with tool calls
                    messages.append({
                        "role": "assistant",
                        "content": text,
                        "tool_calls": tool_calls,
                    })

                    # Execute each tool call and append results
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        tool_name = func.get("name", "unknown")
                        raw_args = func.get("arguments", {})

                        # Handle arguments as both dict and string
                        if isinstance(raw_args, str):
                            try:
                                tool_args = json.loads(raw_args)
                            except json.JSONDecodeError:
                                tool_args = {}
                        else:
                            tool_args = raw_args

                        # Format input for display
                        display_input = (
                            tool_args.get("file_path")
                            or tool_args.get("path")
                            or tool_args.get("pattern")
                            or str(tool_args)[:200]
                        )

                        await broadcast_event("insights:chunk", {
                            "projectId": project_id,
                            "type": "tool_start",
                            "tool": {"name": tool_name, "input": str(display_input)[:200]},
                        })

                        result = execute_tool(tool_name, tool_args, project_path)

                        await broadcast_event("insights:chunk", {
                            "projectId": project_id,
                            "type": "tool_end",
                        })

                        messages.append({
                            "role": "tool",
                            "content": result,
                        })

                    logger.debug(f"[OllamaProvider] Tool iteration {iteration + 1}: {len(tool_calls)} tool(s) called")

            elapsed = time.monotonic() - stream_start
            eval_ns = last_metrics.get("eval_duration", 0)
            if eval_ns > 0 and total_output_tokens > 0:
                tokens_per_sec = round(total_output_tokens / (eval_ns / 1e9), 1)
            elif elapsed > 0 and total_output_tokens > 0:
                tokens_per_sec = round(total_output_tokens / elapsed, 1)
            else:
                tokens_per_sec = 0

            await broadcast_event("insights:chunk", {
                "projectId": project_id,
                "type": "done",
                "metrics": {
                    "inputTokens": total_input_tokens,
                    "outputTokens": total_output_tokens,
                    "tokensPerSecond": tokens_per_sec,
                    "elapsedSeconds": round(elapsed, 1),
                    "estimated": False,
                },
            })

            return final_text

        except Exception as e:
            logger.error(f"[OllamaProvider] Error: {e}", exc_info=True)
            await broadcast_event("insights:chunk", {
                "projectId": project_id,
                "type": "error",
                "error": str(e),
            })
            return ""
