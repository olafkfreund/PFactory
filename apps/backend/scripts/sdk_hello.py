"""
Small helper to sanity-check Claude SDK connectivity with the active Claude profile token.

Uses the Claude Agent SDK via create_client (core.client) — never calls the raw Anthropic API.
It pulls a token from, in order:
1) CLAUDE_CODE_OAUTH_TOKEN env var
2) Active/first usable profile in ~/.pfactory/claude-profiles.json
3) ~/.claude/oauth_token

Then it sends a simple "hello" prompt and streams the response.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

# Ensure backend modules are importable when running directly
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.append(str(BACKEND_DIR))

from agents.session import LogPhase, run_agent_session  # noqa: E402
from core.client import create_client  # noqa: E402


def load_token_from_profiles() -> str | None:
    """Load OAuth token from active/usable Claude profile."""
    profiles_path = Path.home() / ".pfactory" / "claude-profiles.json"
    if not profiles_path.exists():
        return None

    try:
        data = json.loads(profiles_path.read_text())
    except json.JSONDecodeError:
        return None

    profiles = data.get("profiles") or []
    active_id = data.get("activeProfileId")

    usable = [p for p in profiles if p.get("oauthToken") or p.get("token")]
    if not usable:
        return None

    # Prefer active profile if it has a token
    for p in usable:
        if p.get("id") == active_id:
            return p.get("oauthToken") or p.get("token")

    # Fallback to first usable
    p = usable[0]
    return p.get("oauthToken") or p.get("token")


def resolve_token() -> str:
    """Resolve a token from env, profiles, or ~/.claude/oauth_token."""
    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if env_token:
        return env_token

    profile_token = load_token_from_profiles()
    if profile_token:
        return profile_token

    file_token_path = Path.home() / ".claude" / "oauth_token"
    if file_token_path.exists():
        token = file_token_path.read_text().strip()
        if token:
            return token

    raise RuntimeError("No OAuth token found (env, profiles, or ~/.claude/oauth_token).")


async def main():
    token = resolve_token()
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token  # ensure SDK sees it

    # Use a temp spec dir; this is just a connectivity check
    spec_dir = Path(tempfile.mkdtemp(prefix="sdk-hello-"))
    project_dir = Path(__file__).resolve().parents[2]  # repo root

    client = create_client(
        project_dir=project_dir,
        spec_dir=spec_dir,
        model="opus",
        agent_type="coder",
        max_thinking_tokens=1000,
    )

    status, response, error_info = await run_agent_session(
        client=client,
        message="Say a short hello and stop.",
        spec_dir=spec_dir,
        verbose=True,
        phase=LogPhase.CODING,
    )

    print("\n--- SDK Hello Result ---")
    print("Status:", status)
    print("Response:\n", response)


if __name__ == "__main__":
    asyncio.run(main())
