"""
Minimal smoke test for run_agent_session using a dummy client.

This does NOT call the real Claude SDK; it just exercises the streaming loop
with a synthetic AssistantMessage/TextBlock so you can see the flow without
needing a token or spec data.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

# Ensure backend modules are importable when running this file directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from agents.session import LogPhase, run_agent_session  # noqa: E402


class DummyTextBlock:
    def __init__(self, text: str):
        self.text = text


class DummyAssistantMessage:
    def __init__(self, content):
        self.content = content


class DummyClient:
    async def query(self, message: str):
        print(f"[DummyClient] query called with: {message}")

    async def receive_response(self):
        # Yield a single assistant message with one text block
        yield DummyAssistantMessage([DummyTextBlock("Hello from dummy client!")])


async def main():
    spec_dir = Path(tempfile.mkdtemp(prefix="dummy-spec-"))
    status, response, error_info = await run_agent_session(
        client=DummyClient(),
        message="Hello, world!",
        spec_dir=spec_dir,
        verbose=True,
        phase=LogPhase.CODING,
    )
    print(f"\nStatus: {status}")
    print(f"Response: {response}")
    print(f"Error info: {error_info}")


if __name__ == "__main__":
    asyncio.run(main())
