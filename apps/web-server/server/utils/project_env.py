"""Allowlist for the repo-controlled project ``.env`` (#3710).

``<project>/.<x>factory/.env`` lives in the project checkout, so the repository
controls it. Only the named settings below may reach a runner's environment.
Mirrors AIFactory ``server/services/project_env.py`` (#1745, #1795).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from factory_common.logsafe import sanitize_log

logger = logging.getLogger(__name__)

_AGENT_MCP_KEY = re.compile(r"AGENT_MCP_[A-Za-z0-9_]+_(?:ADD|REMOVE)")
_REFUSED_KEY = re.compile(
    r"LD_.*|DYLD_.*|NODE_OPTIONS|BASH_ENV|ENV|PYTHON.*|GIT_.*|PATH|HOME|SHELL|.*_PROXY",
    re.IGNORECASE,
)
_FACTORY_GIT_KEYS = frozenset(
    {"GIT_PROVIDER", "GIT_TOKEN", "GIT_BASE_URL", "GIT_ORG", "GIT_PROJECT", "GIT_REPO"}
)

# Endpoint settings and the host credentials they would redirect. The repo may set an
# endpoint only when the host holds none of its credentials (#1004).
_ENDPOINT_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "GIT_BASE_URL": ("GIT_TOKEN", "GITHUB_TOKEN"),
    "OPENAI_COMPATIBLE_BASE_URL": ("OPENAI_COMPATIBLE_API_KEY", "OPENAI_API_KEY"),
    "AZURE_OPENAI_ENDPOINT": ("AZURE_OPENAI_API_KEY",),
    "AZURE_OPENAI_BASE_URL": ("AZURE_OPENAI_API_KEY",),
    "OLLAMA_BASE_URL": ("OLLAMA_API_KEY",),
    "OLLAMA_CLOUD_BASE_URL": ("OLLAMA_API_KEY",),
}

PROJECT_ENV_KEYS = frozenset(
    {
        # routes/context.py settings
        "GIT_PROVIDER",
        "GIT_TOKEN",
        "GIT_BASE_URL",
        "GIT_ORG",
        "GIT_PROJECT",
        "GIT_REPO",
        "GITHUB_TOKEN",
        "GITHUB_REPO",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_AUTH_TOKEN",
        "ENABLE_FANCY_UI",
        "GRAPHITI_ENABLED",
        "GRAPHITI_EMBEDDER_PROVIDER",
        "OLLAMA_BASE_URL",
        "OLLAMA_EMBEDDING_MODEL",
        "OLLAMA_EMBEDDING_DIM",
        "OPENAI_API_KEY",
        "VOYAGE_API_KEY",
        "VOYAGE_EMBEDDING_MODEL",
        "GOOGLE_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
        "GRAPHITI_DATABASE",
        "GRAPHITI_DB_PATH",
        # Graphiti provider settings read by apps/backend (GraphitiConfig.from_env)
        "AZURE_OPENAI_BASE_URL",
        "AZURE_OPENAI_LLM_DEPLOYMENT",
        "GOOGLE_LLM_MODEL",
        "GOOGLE_EMBEDDING_MODEL",
        "GRAPHITI_LLM_PROVIDER",
        "GRAPHITI_ANTHROPIC_MODEL",
        "OLLAMA_API_KEY",
        "OLLAMA_CLOUD_BASE_URL",
        "OLLAMA_LLM_MODEL",
        "OPENAI_COMPATIBLE_API_KEY",
        "OPENAI_COMPATIBLE_BASE_URL",
        "OPENAI_COMPATIBLE_MAX_TOKENS",
        "OPENAI_COMPATIBLE_REASONING_EFFORT",
        "OPENAI_EMBEDDING_MODEL",
        "OPENAI_MODEL",
        # core/client.py
        "USE_CLAUDE_MD",
        "GRAPHITI_MCP_URL",
        "CONTEXT7_ENABLED",
        "PLAYWRIGHT_MCP_ENABLED",
        "PUPPETEER_MCP_ENABLED",
    }
)


def is_project_env_key(key: str) -> bool:
    """Return True iff ``key`` may be loaded from a project ``.env``."""
    return bool(
        (key in PROJECT_ENV_KEYS or _AGENT_MCP_KEY.fullmatch(key))
        and (key in _FACTORY_GIT_KEYS or not _REFUSED_KEY.fullmatch(key))
    )


def load_project_env_file(env_file: Path, env: dict[str, str]) -> None:
    """Load allowlisted ``KEY=VALUE`` pairs from a project .env without overwriting.

    Other keys are ignored and logged by name, never by value.
    """
    if not env_file.exists():
        return
    ignored: list[str] = []
    host_keys = frozenset(env)  # before the file adds anything
    try:
        with env_file.open() as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if not is_project_env_key(k) or any(
                    c in host_keys for c in _ENDPOINT_CREDENTIALS.get(k, ())
                ):
                    ignored.append(k)
                elif k not in env:
                    env[k] = v.strip().strip('"').strip("'")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(
            "Failed to load project env file %s: %s", sanitize_log(env_file), sanitize_log(e)
        )
    if ignored:
        logger.warning(
            "Ignored non-allowlisted keys in %s: %s",
            sanitize_log(env_file),
            sanitize_log(", ".join(ignored)),
        )
