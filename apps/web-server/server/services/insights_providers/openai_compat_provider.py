"""
Generic OpenAI-compatible provider for insights chat.

Supports LM Studio, vLLM, LocalAI, Jan — any server exposing
POST /v1/chat/completions with SSE streaming.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from ...websockets.events import broadcast_event
from .base import ProviderInfo, ProviderModel, ProviderStrategy

logger = logging.getLogger(__name__)

# Known OpenAI-compat providers with their default URLs
OPENAI_COMPAT_PROVIDERS = {
    "lmstudio": {
        "display_name": "LM Studio",
        "icon": "lmstudio",
        "base_url": "http://localhost:1234",
    },
    "localai": {
        "display_name": "LocalAI",
        "icon": "localai",
        "base_url": "http://localhost:8080",
    },
    "vllm": {
        "display_name": "vLLM",
        "icon": "vllm",
        "base_url": "http://localhost:8000",
    },
    "jan": {
        "display_name": "Jan",
        "icon": "jan",
        "base_url": "http://localhost:1337",
    },
}


class OpenAICompatProvider(ProviderStrategy):
    """Provider for OpenAI-compatible HTTP servers."""

    def __init__(self, provider_id: str, base_url: str | None = None) -> None:
        config = OPENAI_COMPAT_PROVIDERS.get(provider_id, {})
        self.provider_id = provider_id
        self.base_url = base_url or config.get("base_url", "http://localhost:8080")
        self.display_name = config.get("display_name", provider_id.title())
        self.icon = config.get("icon", provider_id)

    async def detect(self) -> ProviderInfo:
        info = ProviderInfo(
            provider=self.provider_id,
            available=False,
            display_name=self.display_name,
            icon=self.icon,
            auth_method=None,
            models=[],
        )

        try:
            import httpx
            async with httpx.AsyncClient(timeout=1.5) as client:
                resp = await client.get(f"{self.base_url}/v1/models")
                resp.raise_for_status()
                data = resp.json()

                for m in data.get("data", []):
                    model_id = m.get("id", "")
                    if model_id:
                        info.models.append(ProviderModel(
                            id=model_id,
                            label=model_id,
                        ))

                if info.models:
                    info.available = True
        except Exception as e:
            logger.debug(f"[OpenAICompat:{self.provider_id}] Detection failed: {e}")

        return info

    async def send_message(
        self,
        project_path: Path,
        project_id: str,
        message: str,
        model: str | None,
        model_config: dict | None,
        conversation_history: list[dict] | None,
    ) -> str:
        effective_model = model or (model_config or {}).get("model", "")

        messages = []
        if conversation_history:
            for msg in conversation_history[-10:]:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                })
        messages.append({"role": "user", "content": message})

        payload = {
            "model": effective_model,
            "messages": messages,
            "stream": True,
        }

        logger.info(f"[OpenAICompat:{self.provider_id}] Streaming: {effective_model}")

        try:
            import httpx

            await broadcast_event("insights:chunk", {
                "projectId": project_id,
                "type": "text",
                "content": "",
            })

            accumulated = ""
            stream_start = time.monotonic()

            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers={"Accept": "text/event-stream"},
                ) as resp:
                    resp.raise_for_status()

                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue

                        # SSE format: "data: {...}"
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    accumulated += content
                                    await broadcast_event("insights:chunk", {
                                        "projectId": project_id,
                                        "type": "text",
                                        "content": content,
                                    })
                            except (json.JSONDecodeError, IndexError):
                                continue

            elapsed = time.monotonic() - stream_start
            estimated_tokens = max(1, len(accumulated) // 4)
            tokens_per_sec = round(estimated_tokens / elapsed, 1) if elapsed > 0 else 0

            await broadcast_event("insights:chunk", {
                "projectId": project_id,
                "type": "done",
                "metrics": {
                    "outputTokens": estimated_tokens,
                    "tokensPerSecond": tokens_per_sec,
                    "elapsedSeconds": round(elapsed, 1),
                    "estimated": True,
                },
            })

            return accumulated

        except Exception as e:
            logger.error(f"[OpenAICompat:{self.provider_id}] Error: {e}", exc_info=True)
            await broadcast_event("insights:chunk", {
                "projectId": project_id,
                "type": "error",
                "error": str(e),
            })
            return ""
