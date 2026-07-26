"""
Application settings routes.

Handles reading and writing application configuration.
"""

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, AliasChoices

# --------------------------------------------------------------------------
# Type Definitions for Validation
# --------------------------------------------------------------------------

# BUG-4.1-005: Theme must be one of these values
ThemeType = Literal["light", "dark", "system"]

# Color theme — Ocean only
ColorThemeType = Literal["ocean"]

# BUG-4.1-011: Memory embedding provider must be one of these values
MemoryEmbeddingProviderType = Literal[
    "openai", "voyage", "azure_openai", "ollama", "google", "openrouter"
]

from ..config import get_settings
from ..paths import atomic_write_secret_json

router = APIRouter()
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class AppSettings(BaseModel):
    """Application settings model."""

    # Allow both camelCase field names and snake_case aliases for backward compatibility
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    # General - with proper type validation
    # BUG-4.1-005: Validate theme against allowed values
    theme: ThemeType = Field("dark", description="UI theme (dark/light/system)")
    # BUG-4.1-006: Validate colorTheme against allowed values
    colorTheme: ColorThemeType | None = Field("ocean", description="Color theme (default/dusk/lime/ocean/retro/neo/forest)")
    language: str = Field("en", description="UI language code")
    # BUG-4.1-008: uiScale with min/max validation (75-200)
    uiScale: int | None = Field(125, ge=75, le=200, description="UI scale percentage (75-200)")

    @field_validator("theme", mode="before")
    @classmethod
    def validate_theme(cls, v):
        """Validate and normalize theme value for backward compatibility."""
        if v is None:
            return "dark"
        valid_themes = ["light", "dark", "system"]
        if v not in valid_themes:
            # Fall back to dark for invalid values (backward compatibility)
            return "dark"
        return v

    @field_validator("colorTheme", mode="before")
    @classmethod
    def validate_color_theme(cls, v):
        """Normalize any color theme value to 'ocean' (sole supported theme)."""
        return "ocean"

    @field_validator("uiScale", mode="before")
    @classmethod
    def validate_ui_scale(cls, v):
        """Validate and clamp uiScale to valid range for backward compatibility."""
        if v is None:
            return 125
        try:
            scale = int(v)
            # Clamp to valid range instead of rejecting (backward compatibility)
            return max(75, min(200, scale))
        except (TypeError, ValueError):
            return 125

    # Claude settings - using camelCase with snake_case aliases for backward compatibility
    defaultModel: str = Field(
        "claude-sonnet-4-5-20250929",
        alias="default_model",
        description="Default Claude model for tasks",
    )
    agentFramework: str | None = Field("claude-code", description="Agent framework to use")
    thinkingLevel: str = Field(
        "extended",
        alias="thinking_level",
        description="Thinking level (none, standard, extended)",
    )
    maxThinkingTokens: int | None = Field(
        None,
        alias="max_thinking_tokens",
        description="Max thinking tokens (None for unlimited)",
    )
    selectedAgentProfile: str | None = Field(None, description="Selected agent profile ID")

    # Task execution - using camelCase with snake_case aliases
    autoContinue: bool = Field(
        True,
        alias="auto_continue",
        description="Automatically continue to next phase after spec creation",
    )
    autoQa: bool = Field(
        True,
        alias="auto_qa",
        description="Automatically run QA after implementation",
    )
    # Terminal - using camelCase with snake_case aliases
    defaultShell: str = Field("/bin/bash", alias="default_shell", description="Default shell for terminals")
    terminalFontSize: int = Field(14, alias="terminal_font_size", description="Terminal font size")
    autoNameTerminals: bool = Field(True, description="Auto-generate terminal names")

    # Developer tools
    preferredIDE: str | None = Field(None, description="Preferred IDE")
    customIDEPath: str | None = Field(None, description="Custom IDE path")
    preferredTerminal: str | None = Field(None, description="Preferred terminal")
    customTerminalPath: str | None = Field(None, description="Custom terminal path")

    # Integrations - using camelCase with snake_case aliases
    githubEnabled: bool = Field(False, alias="github_enabled", description="Enable GitHub integration")

    # Memory - using camelCase with snake_case aliases
    graphitiEnabled: bool = Field(True, alias="graphiti_enabled", description="Enable Graphiti memory")
    memoryEnabled: bool | None = Field(None, description="Enable memory system")
    # BUG-4.1-011: Validate memoryEmbeddingProvider against allowed values
    memoryEmbeddingProvider: MemoryEmbeddingProviderType | None = Field(
        None,
        description="Memory embedding provider (openai/voyage/azure_openai/ollama/google/openrouter)",
    )

    @field_validator("memoryEmbeddingProvider", mode="before")
    @classmethod
    def validate_memory_embedding_provider(cls, v):
        """Validate memoryEmbeddingProvider for backward compatibility."""
        if v is None:
            return None
        valid_providers = ["openai", "voyage", "azure_openai", "ollama", "google", "openrouter"]
        if v not in valid_providers:
            # Return None for invalid values (backward compatibility)
            return None
        return v

    # Paths
    autoBuildPath: str | None = Field(
        None,
        description="Path to PFactory backend (apps/backend directory)",
    )
    autoUpdateAutoBuild: bool = Field(True, description="Auto-update PFactory source")

    # Global API keys
    globalClaudeOAuthToken: str | None = Field(None, description="Global Claude OAuth token", repr=False)
    globalOpenAIApiKey: str | None = Field(None, description="Global OpenAI API key", repr=False)
    globalAnthropicApiKey: str | None = Field(None, description="Global Anthropic API key", repr=False)

    # Onboarding
    onboardingCompleted: bool | None = Field(None, description="Whether onboarding is completed")

    # Updates
    betaUpdates: bool | None = Field(False, description="Opt into beta updates")

    # Email Notification OAuth Credentials (app-level, not per-user)
    emailMicrosoftClientId: str | None = Field(None, description="Microsoft OAuth Client ID for email notifications")
    emailMicrosoftClientSecret: str | None = Field(None, description="Microsoft OAuth Client Secret for email notifications", repr=False)
    emailGoogleClientId: str | None = Field(None, description="Google OAuth Client ID for email notifications")
    emailGoogleClientSecret: str | None = Field(None, description="Google OAuth Client Secret for email notifications", repr=False)

    # LLM Provider Settings (for AI features: changelog, insights)
    llmProvider: Literal["ollama", "anthropic", "openai"] | None = Field(
        default="ollama",
        alias="llmProvider",
        validation_alias=AliasChoices("llmProvider", "llm_provider"),
    )

    llmOllamaBaseUrl: str | None = Field(
        default="http://localhost:11434",
        alias="llmOllamaBaseUrl",
        validation_alias=AliasChoices("llmOllamaBaseUrl", "llm_ollama_base_url"),
    )

    llmOllamaModel: str | None = Field(
        default="qwen3-30b-local:latest",
        alias="llmOllamaModel",
        validation_alias=AliasChoices("llmOllamaModel", "llm_ollama_model"),
    )

    llmAnthropicModel: str | None = Field(
        default="claude-sonnet-4-5-20250929",
        alias="llmAnthropicModel",
        validation_alias=AliasChoices("llmAnthropicModel", "llm_anthropic_model"),
    )

    llmOpenaiModel: str | None = Field(
        default="gpt-4o",
        alias="llmOpenaiModel",
        validation_alias=AliasChoices("llmOpenaiModel", "llm_openai_model"),
    )

    llmOpenaiBaseUrl: str | None = Field(
        default=None,
        alias="llmOpenaiBaseUrl",
        validation_alias=AliasChoices("llmOpenaiBaseUrl", "llm_openai_base_url"),
    )

    @field_validator("llmProvider", mode="before")
    @classmethod
    def validate_llm_provider(cls, v: Any) -> str | None:
        """Validate LLM provider."""
        if v is None:
            return "ollama"  # Default to Ollama
        if isinstance(v, str):
            v = v.lower()
            if v in ["ollama", "anthropic", "openai"]:
                return v
        return "ollama"  # Fallback

    # Phase-specific model overrides — supports provider-prefixed IDs like
    # 'ollama:llama3', 'openai_compat:mistral-7b', or plain shorthand 'opus'
    phaseModels: dict | None = Field(
        None,
        description=(
            "Per-phase model overrides, e.g. {spec: 'opus', coding: 'ollama:llama3'}. "
            "Valid keys: spec, planning, coding, qa, qa_fixer. "
            "Values may be any non-empty string (provider-prefixed or plain shorthand)."
        ),
    )

    @field_validator("phaseModels", mode="before")
    @classmethod
    def validate_phase_models(cls, v: Any) -> dict | None:
        """Validate phaseModels keys and values.

        Accepted keys: spec, planning, coding, qa, qa_fixer.
        Values must be non-empty strings (any provider-prefixed model ID is allowed).
        Unknown keys and None/empty values are silently dropped for backward compatibility.
        """
        if v is None:
            return None
        if not isinstance(v, dict):
            return None
        valid_keys = {"spec", "planning", "coding", "qa", "qa_fixer"}
        cleaned: dict = {}
        for key, value in v.items():
            if key not in valid_keys:
                # Skip unknown keys rather than raising, for forward compatibility
                continue
            if not isinstance(value, str) or not value.strip():
                # Skip non-string or empty values
                continue
            cleaned[key] = value
        return cleaned if cleaned else None


class SettingsUpdate(BaseModel):
    """Model for partial settings update."""

    # Allow both camelCase field names and snake_case aliases for backward compatibility
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    theme: str | None = None
    colorTheme: str | None = None
    language: str | None = None
    uiScale: int | None = None
    # Using camelCase with snake_case aliases for backward compatibility
    defaultModel: str | None = Field(None, alias="default_model")
    agentFramework: str | None = None
    thinkingLevel: str | None = Field(None, alias="thinking_level")
    maxThinkingTokens: int | None = Field(None, alias="max_thinking_tokens")
    selectedAgentProfile: str | None = None
    autoContinue: bool | None = Field(None, alias="auto_continue")
    autoQa: bool | None = Field(None, alias="auto_qa")
    defaultShell: str | None = Field(None, alias="default_shell")
    terminalFontSize: int | None = Field(None, alias="terminal_font_size")
    autoNameTerminals: bool | None = None
    preferredIDE: str | None = None
    customIDEPath: str | None = None
    preferredTerminal: str | None = None
    customTerminalPath: str | None = None
    githubEnabled: bool | None = Field(None, alias="github_enabled")
    graphitiEnabled: bool | None = Field(None, alias="graphiti_enabled")
    memoryEnabled: bool | None = None
    memoryEmbeddingProvider: str | None = None
    autoBuildPath: str | None = None
    autoUpdateAutoBuild: bool | None = None
    globalClaudeOAuthToken: str | None = Field(default=None, repr=False)
    globalOpenAIApiKey: str | None = Field(default=None, repr=False)
    globalAnthropicApiKey: str | None = Field(default=None, repr=False)
    onboardingCompleted: bool | None = None
    betaUpdates: bool | None = None
    emailMicrosoftClientId: str | None = None
    emailMicrosoftClientSecret: str | None = Field(default=None, repr=False)
    llmProvider: str | None = None
    llmOllamaBaseUrl: str | None = None
    llmOllamaModel: str | None = None
    llmAnthropicModel: str | None = None
    llmOpenaiModel: str | None = None
    llmOpenaiBaseUrl: str | None = None
    phaseModels: dict | None = None


class UpdateApiKeyRequest(BaseModel):
    """Request model for updating API keys."""

    keyType: str = Field(..., description="Type of API key (anthropic, openai, claude)")
    keyValue: str = Field(..., description="The API key value")
    saveToEnv: bool = Field(True, description="Whether to save to .env file")


# --------------------------------------------------------------------------
# Helper Functions
# --------------------------------------------------------------------------


def get_settings_file() -> Path:
    """Get path to the settings file."""
    settings = get_settings()
    return Path(settings.PROJECTS_DATA_DIR) / "settings.json"


def load_app_settings() -> AppSettings:
    """Load application settings from disk."""
    settings_file = get_settings_file()
    if settings_file.exists():
        try:
            data = json.loads(settings_file.read_text())
            return AppSettings(**data)
        except (json.JSONDecodeError, TypeError):
            pass
    return AppSettings()


def save_app_settings(settings: AppSettings) -> None:
    """Save application settings to disk."""
    settings_file = get_settings_file()
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(settings.model_dump_json(indent=2))


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("", response_model=AppSettings)
async def get_app_settings():
    """Get current application settings."""
    return load_app_settings()


@router.put("", response_model=AppSettings)
async def update_app_settings(update: SettingsUpdate):
    """Update application settings (partial update)."""
    current = load_app_settings()

    # Apply updates
    update_dict = update.model_dump(exclude_unset=True)
    current_dict = current.model_dump()
    current_dict.update(update_dict)

    updated = AppSettings(**current_dict)
    save_app_settings(updated)

    return updated


@router.post("/reset", response_model=AppSettings)
async def reset_app_settings():
    """Reset application settings to defaults."""
    default = AppSettings()
    save_app_settings(default)
    return default


@router.get("/token")
async def get_api_token():
    """Get the current API token (for display to user)."""
    settings = get_settings()
    return {
        "token": settings.API_TOKEN,
        "note": "Use this token in the Authorization header: Bearer <token>",
    }


@router.post("/token/regenerate")
async def regenerate_api_token():
    """Regenerate the API token."""
    import secrets
    from pathlib import Path

    from ..paths import get_data_file

    settings = get_settings()
    token_file = get_data_file(".token")

    # Generate new token
    new_token = secrets.token_urlsafe(32)
    token_file.write_text(new_token)
    token_file.chmod(0o600)

    # Update settings (note: requires server restart to take effect)
    return {
        "token": new_token,
        "note": "Server restart required for new token to take effect",
    }


@router.post("/api-key")
async def update_api_key(request: UpdateApiKeyRequest):
    """Update global API key and optionally save to .env file.

    This endpoint allows updating API keys for various services (Anthropic, OpenAI, Claude)
    and saving them securely to a .env file with proper validation and permissions.
    """
    from pathlib import Path

    # Validate key type
    valid_key_types = ["anthropic", "openai", "claude"]
    if request.keyType.lower() not in valid_key_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid keyType. Must be one of: {', '.join(valid_key_types)}"
        )

    # Validate key format (basic validation)
    if not request.keyValue or len(request.keyValue) < 20:
        raise HTTPException(
            status_code=400,
            detail="API key appears invalid. Must be at least 20 characters."
        )

    # Additional format validation based on key type
    key_type = request.keyType.lower()
    if key_type == "anthropic" and not request.keyValue.startswith("sk-ant-"):
        raise HTTPException(status_code=400, detail="Anthropic API keys must start with 'sk-ant-'")
    elif key_type == "openai" and not request.keyValue.startswith("sk-"):
        raise HTTPException(status_code=400, detail="OpenAI API keys must start with 'sk-'")
    elif key_type == "claude" and not (
        request.keyValue.startswith("sk-ant-") or request.keyValue.startswith("sess-")
    ):
        raise HTTPException(
            status_code=400, detail="Claude API keys must start with 'sk-ant-' or 'sess-'"
        )

    try:
        # Update in-memory settings
        current = load_app_settings()

        # Map key type to settings field
        key_field_map = {
            "anthropic": "globalAnthropicApiKey",
            "openai": "globalOpenAIApiKey",
            "claude": "globalClaudeOAuthToken",
        }

        field_name = key_field_map[key_type]
        setattr(current, field_name, request.keyValue)

        # Save to settings.json
        save_app_settings(current)

        # Optionally save to .env file with secure permissions
        if request.saveToEnv:
            settings = get_settings()
            env_path = Path(settings.PROJECTS_DATA_DIR) / ".env"

            # Read existing .env or start fresh
            existing = {}
            if env_path.exists():
                for line in env_path.read_text().split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        existing[key.strip()] = value.strip()

            # Update with new key
            env_key_map = {
                "anthropic": "ANTHROPIC_API_KEY",
                "openai": "OPENAI_API_KEY",
                "claude": "CLAUDE_API_KEY",
            }

            existing[env_key_map[key_type]] = request.keyValue

            # Write back with secure permissions
            env_path.parent.mkdir(parents=True, exist_ok=True)
            content = "\n".join(f"{k}={v}" for k, v in existing.items())
            env_path.write_text(content)

            # Set secure file permissions (owner read/write only)
            env_path.chmod(0o600)

        return {
            "success": True,
            "message": f"{key_type.title()} API key updated successfully",
            "savedToEnv": request.saveToEnv,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update API key: {str(e)}"
        )


@router.get("/local-llm/detect")
async def detect_local_llm_providers():
    """Detect locally installed/running LLM providers via CLI and process checks.

    Uses ``shutil.which`` for binary detection, ``subprocess`` for version
    and model list commands, and ``pgrep`` for running-process checks.
    No HTTP port probing — instant and avoids false positives.
    """
    import asyncio
    import shutil

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _run(cmd: list[str], timeout: float = 3.0) -> tuple[bool, str]:
        """Run *cmd* asynchronously, return (ok, stdout)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode == 0, (stdout or b"").decode().strip()
        except Exception:
            return False, ""

    async def _is_process_running(name: str) -> bool:
        ok, _ = await _run(["pgrep", "-x", name], timeout=2.0)
        return ok

    # ------------------------------------------------------------------
    # Per-provider detection
    # ------------------------------------------------------------------

    async def detect_ollama() -> dict:
        result = {
            "id": "ollama",
            "name": "Ollama",
            "url": "http://localhost:11434",
            "detected": False,
            "installed": False,
            "running": False,
            "version": "",
            "modelCount": 0,
            "models": [],
        }
        if not shutil.which("ollama"):
            return result
        result["installed"] = True

        ok, out = await _run(["ollama", "--version"])
        if ok and out:
            # "ollama version is 0.14.3" → "0.14.3"
            result["version"] = out.split()[-1] if out else ""

        result["running"] = await _is_process_running("ollama")

        # `ollama list` works when the server is running
        ok, out = await _run(["ollama", "list"], timeout=5.0)
        if ok and out:
            lines = out.strip().splitlines()
            # First line is a header row
            model_lines = [l for l in lines[1:] if l.strip()]
            all_names = [l.split()[0] for l in model_lines if l.split()]
            # Filter out embedding/reranker models — only show chat LLMs
            _embed_kw = {"embed", "minilm", "bge", "gte", "e5", "rerank"}
            model_names = [
                n for n in all_names
                if not any(kw in n.lower() for kw in _embed_kw)
            ]
            result["models"] = model_names
            result["modelCount"] = len(model_names)
            if model_names:
                result["detected"] = True
                result["running"] = True  # list worked ⇒ server is up
        elif result["running"]:
            # Server is running but no models pulled yet
            result["detected"] = True

        return result

    async def detect_lmstudio() -> dict:
        result = {
            "id": "lmstudio",
            "name": "LM Studio",
            "url": "http://localhost:1234",
            "detected": False,
            "installed": False,
            "running": False,
            "version": "",
            "modelCount": 0,
            "models": [],
        }
        # LM Studio CLI
        if shutil.which("lms"):
            result["installed"] = True
            ok, out = await _run(["lms", "version"])
            if ok and out:
                result["version"] = out.strip()
            ok, out = await _run(["lms", "status"])
            if ok and "running" in out.lower():
                result["running"] = True
                result["detected"] = True
            ok, out = await _run(["lms", "ls"])
            if ok and out:
                lines = [l.strip() for l in out.splitlines() if l.strip()]
                result["models"] = lines
                result["modelCount"] = len(lines)
        # Fallback: check for running process
        if not result["installed"]:
            if await _is_process_running("lm-studio") or await _is_process_running("lmstudio"):
                result["detected"] = True
                result["running"] = True
        return result

    async def detect_localai() -> dict:
        result = {
            "id": "localai-llamacpp",
            "name": "LocalAI / llama.cpp",
            "url": "http://localhost:8080",
            "detected": False,
            "installed": False,
            "running": False,
            "version": "",
            "modelCount": 0,
            "models": [],
        }
        # LocalAI binary
        if shutil.which("local-ai"):
            result["installed"] = True
            ok, out = await _run(["local-ai", "--version"])
            if ok and out:
                result["version"] = out.strip()
            result["detected"] = True
        # llama-server (llama.cpp)
        elif shutil.which("llama-server") or shutil.which("llama-cpp-server"):
            result["installed"] = True
            result["detected"] = True
        # Process check
        for proc_name in ("local-ai", "llama-server", "llama-cpp-server"):
            if await _is_process_running(proc_name):
                result["running"] = True
                result["detected"] = True
                break
        return result

    async def detect_vllm() -> dict:
        result = {
            "id": "vllm",
            "name": "vLLM",
            "url": "http://localhost:8000",
            "detected": False,
            "installed": False,
            "running": False,
            "version": "",
            "modelCount": 0,
            "models": [],
        }
        # vLLM is a Python package
        ok, out = await _run(["python3", "-c", "import vllm; print(vllm.__version__)"])
        if ok and out:
            result["installed"] = True
            result["version"] = out.strip()
            result["detected"] = True
        # Process check
        if await _is_process_running("vllm"):
            result["running"] = True
            result["detected"] = True
        return result

    async def detect_jan() -> dict:
        result = {
            "id": "jan",
            "name": "Jan",
            "url": "http://localhost:1337",
            "detected": False,
            "installed": False,
            "running": False,
            "version": "",
            "modelCount": 0,
            "models": [],
        }
        # Jan is an Electron app
        if shutil.which("jan"):
            result["installed"] = True
            result["detected"] = True
        # Check common install locations
        jan_paths = [
            Path("/opt/jan/jan"),
            Path.home() / ".local" / "bin" / "jan",
            Path("/usr/bin/jan"),
        ]
        for p in jan_paths:
            if p.exists():
                result["installed"] = True
                result["detected"] = True
                break
        # Process check
        if await _is_process_running("jan"):
            result["running"] = True
            result["detected"] = True
        return result

    # ------------------------------------------------------------------
    # Run all detections concurrently
    # ------------------------------------------------------------------
    results = await asyncio.gather(
        detect_ollama(),
        detect_lmstudio(),
        detect_localai(),
        detect_vllm(),
        detect_jan(),
    )

    return {"providers": list(results)}


@router.get("/ollama/models")
async def list_ollama_models(ollamaBaseUrl: str = Query(default="http://localhost:11434")):
    """List available Ollama models."""
    try:
        from ..services.ollama_utils import fetch_ollama_models, is_embedding_model

        raw_models = await fetch_ollama_models(ollamaBaseUrl)

        # Extract model list, filtering out embedding-only models
        embedding_families = {"bert", "nomic-bert"}
        models = []
        for model in raw_models:
            details = model.get("details", {})
            families = {f.lower() for f in details.get("families", [])}

            # Skip embedding models (family is bert-based or name contains embedding keywords)
            if families & embedding_families:
                continue
            if is_embedding_model(model["name"]):
                continue

            models.append({
                "name": model["name"],
                "size": model["size"],
                "modified": model["modified_at"],
                "details": details,
            })

        return {"models": models}
    except Exception:
        logger.exception("Failed to list Ollama models")
        return {"success": False, "error": "Failed to list Ollama models"}


@router.get("/openai-compat/models")
async def list_openai_compat_models(
    baseUrl: str = Query(default="http://localhost:8080"),
    apiKey: str | None = Query(default=None),
):
    """List available models from an OpenAI-compatible server.

    Calls ``GET {baseUrl}/v1/models``, filters out embedding/reranker models,
    and returns ``{models: [{name: str}]}`` — the same envelope shape used by
    the Ollama models endpoint so callers can treat both identically.
    """
    try:
        import httpx

        headers: dict[str, str] = {}
        if apiKey:
            headers["Authorization"] = f"Bearer {apiKey}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{baseUrl}/v1/models", headers=headers)
            response.raise_for_status()
            data = response.json()

        # OpenAI-compatible /v1/models returns {"data": [{"id": "...", ...}, ...]}
        raw_models = data.get("data", [])

        # Filter out embedding / reranker models by common name keywords
        _embed_kw = {"embed", "embedding", "minilm", "bge", "gte", "e5", "rerank"}
        models = []
        for model in raw_models:
            model_id: str = model.get("id", "")
            name_lower = model_id.lower()
            if any(kw in name_lower for kw in _embed_kw):
                continue
            models.append({"name": model_id})

        return {"models": models}
    except Exception:
        logger.exception("Failed to list OpenAI-compatible models from %s", baseUrl)
        return {"success": False, "error": "Failed to list models from the configured server"}


class OpenAICompatTestRequest(BaseModel):
    """Request model for testing an OpenAI-compatible server connection."""

    baseUrl: str = Field(..., description="Base URL of the OpenAI-compatible server")
    apiKey: SecretStr | None = Field(None, description="Optional API key for authentication")


@router.post("/openai-compat/test")
async def test_openai_compat_connection(request: OpenAICompatTestRequest):
    """Test connectivity to an OpenAI-compatible server.

    Sends ``GET {baseUrl}/v1/models`` with a 5-second timeout. Returns the
    number of (non-embedding) models available so the caller can confirm the
    server is reachable and serving models.
    """
    try:
        import httpx

        headers: dict[str, str] = {}
        if request.apiKey:
            headers["Authorization"] = f"Bearer {request.apiKey.get_secret_value()}"

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{request.baseUrl}/v1/models", headers=headers)
            response.raise_for_status()
            data = response.json()

        raw_models = data.get("data", [])

        # Filter out embedding / reranker models (same keywords as the list endpoint)
        _embed_kw = {"embed", "embedding", "minilm", "bge", "gte", "e5", "rerank"}
        model_count = sum(
            1 for m in raw_models
            if not any(kw in m.get("id", "").lower() for kw in _embed_kw)
        )

        return {
            "success": True,
            "modelCount": model_count,
            "message": f"Connected successfully. {model_count} model(s) available.",
        }
    except Exception:
        logger.exception("OpenAI-compatible connection test failed for %s", request.baseUrl)
        return {"success": False, "error": "Connection test failed"}


@router.post("/ollama/pull")
async def pull_ollama_model(
    modelName: str = Body(..., embed=True),
    ollamaBaseUrl: str = Body(default="http://localhost:11434", embed=True),
):
    """Pull (download) an Ollama model."""
    try:
        import httpx
        import json

        # Stream the pull progress
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST",
                f"{ollamaBaseUrl}/api/pull",
                json={"name": modelName}
            ) as response:
                response.raise_for_status()

                # Stream progress updates
                async for line in response.aiter_lines():
                    if line:
                        progress_data = json.loads(line)
                        # Could emit WebSocket progress here
                        logger.info(f"Pull progress: {progress_data}")

                return {"success": True, "message": f"Model {modelName} pulled successfully"}
    except Exception:
        logger.exception("Failed to pull Ollama model %s", modelName)
        return {"success": False, "error": "Failed to pull the requested model"}


@router.post("/ollama/test")
async def test_ollama_connection(
    ollamaBaseUrl: str = Body(..., embed=True),
    modelName: str = Body(..., embed=True)
):
    """Test Ollama connection and model availability."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Check if server is reachable
            response = await client.get(f"{ollamaBaseUrl}/api/tags")
            response.raise_for_status()

            # Check if model exists
            data = response.json()
            models = [m["name"] for m in data.get("models", [])]

            if modelName not in models:
                return {
                    "success": False,
                    "error": f"Model '{modelName}' not found. Available models: {', '.join(models)}",
                }

            # Test model with simple query
            test_response = await client.post(
                f"{ollamaBaseUrl}/v1/chat/completions",
                json={
                    "model": modelName,
                    "messages": [{"role": "user", "content": "Test"}],
                    "max_tokens": 10,
                },
                timeout=30.0,
            )
            test_response.raise_for_status()

            return {"success": True, "message": "Connection successful!"}
    except Exception:
        logger.exception("Ollama connection test failed")
        return {"success": False, "error": "Connection test failed"}


# --------------------------------------------------------------------------
# Tab State
# --------------------------------------------------------------------------


def get_tab_state_file() -> Path:
    """Get path to the tab state file."""
    settings = get_settings()
    return Path(settings.PROJECTS_DATA_DIR) / "tab-state.json"


@router.get("/tab-state")
async def get_tab_state():
    """Get saved tab state."""
    tab_file = get_tab_state_file()
    if tab_file.exists():
        try:
            data = json.loads(tab_file.read_text())
            return {"success": True, "data": data}
        except json.JSONDecodeError:
            pass
    return {"success": True, "data": {"tabs": [], "activeTabId": None}}


@router.put("/tab-state")
async def save_tab_state(state: dict):
    """Save tab state."""
    try:
        tab_file = get_tab_state_file()
        tab_file.parent.mkdir(parents=True, exist_ok=True)
        tab_file.write_text(json.dumps(state, indent=2))
        return {"success": True}
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save tab state: {str(e)}"
        )
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tab state data: {str(e)}"
        )


# --------------------------------------------------------------------------
# Claude Profiles
# --------------------------------------------------------------------------


def get_profiles_file() -> Path:
    """Get path to the Claude profiles file."""
    settings = get_settings()
    return Path(settings.PROJECTS_DATA_DIR) / "claude-profiles.json"


def normalize_profile_fields(profile: dict) -> dict:
    """Normalize profile field names to frontend-compatible camelCase.

    Converts old field names to new ones for backward compatibility:
    - token -> oauthToken
    - isActive -> isDefault
    """
    result = profile.copy()

    # Migrate token -> oauthToken
    if "token" in result and "oauthToken" not in result:
        result["oauthToken"] = result.pop("token")
    elif "token" in result:
        result.pop("token")  # Remove old field if new one exists

    # Migrate isActive -> isDefault
    if "isActive" in result and "isDefault" not in result:
        result["isDefault"] = result.pop("isActive")
    elif "isActive" in result:
        result.pop("isActive")  # Remove old field if new one exists

    return result


def load_profiles() -> dict:
    """Load Claude profiles with field name normalization."""
    profiles_file = get_profiles_file()
    if profiles_file.exists():
        try:
            data = json.loads(profiles_file.read_text())
            # Normalize field names for backward compatibility
            if "profiles" in data:
                data["profiles"] = [
                    normalize_profile_fields(p) for p in data["profiles"]
                ]
            return data
        except json.JSONDecodeError:
            pass
    return {"profiles": [], "activeProfileId": None}


def save_profiles(data: dict) -> None:
    """Save Claude profiles atomically, mode 0600 (profiles hold OAuth tokens).

    Atomic because load_profiles() silently returns {"profiles": []} on a
    JSONDecodeError: a reader that catches a torn write concludes there are no
    profiles, and the next save writes that back — destroying every token. See
    paths.atomic_write_secret_json (#298).
    """
    atomic_write_secret_json(get_profiles_file(), data)


def _upsert_by_id(items: list[dict], obj: dict, key: str = "id") -> None:
    """Replace the item whose ``key`` matches ``obj[key]``; append if none match."""
    for i, existing in enumerate(items):
        if existing.get(key) == obj[key]:
            items[i] = obj
            return
    items.append(obj)


def _sync_env_token_for_active_profile(
    data: dict,
    profile_id: str | None,
    logger: logging.Logger,
) -> None:
    """Update CLAUDE_CODE_OAUTH_TOKEN to match the active profile token."""
    if not profile_id:
        return

    token = None
    for profile in data.get("profiles", []):
        if profile.get("id") == profile_id:
            token = profile.get("oauthToken") or profile.get("token")
            break

    if token:
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
        logger.info("[Claude Profiles] Updated CLAUDE_CODE_OAUTH_TOKEN for active profile")
    else:
        logger.warning(
            "[Claude Profiles] Active profile has no token; CLAUDE_CODE_OAUTH_TOKEN not updated"
        )


@router.get("/claude-profiles")
async def get_claude_profiles():
    """Get all Claude profiles."""
    return {"success": True, "data": load_profiles()}


class ClaudeProfile(BaseModel):
    """Claude profile model with frontend-compatible field names.

    Uses camelCase field names with snake_case aliases for backward compatibility
    with existing stored data.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    name: str
    email: str | None = None
    # Frontend expects oauthToken, backend stored token - alias for backward compat
    oauthToken: str | None = Field(None, alias="token", repr=False)
    # Frontend expects isDefault, backend stored isActive - alias for backward compat
    isDefault: bool = Field(False, alias="isActive")


@router.post("/claude-profiles")
async def save_claude_profile(profile: ClaudeProfile):
    """Save a Claude profile.

    Creates a new profile or updates an existing one. Validates name, email, and token
    to ensure data integrity and prevent duplicates.
    """
    import uuid

    # Validate and sanitize profile name
    if not profile.name or not profile.name.strip():
        return {"success": False, "error": "Profile name cannot be empty"}

    name = profile.name.strip()
    if len(name) < 1 or len(name) > 100:
        return {"success": False, "error": "Profile name must be between 1 and 100 characters"}

    # Sanitize email if provided
    email = profile.email.strip() if profile.email else None
    if email and len(email) > 255:
        return {"success": False, "error": "Email cannot exceed 255 characters"}

    # Validate token if provided (following pattern from set_claude_profile_token)
    token = profile.oauthToken.strip() if profile.oauthToken else None
    if token:
        if len(token) < 20:
            return {"success": False, "error": "Token must be at least 20 characters"}
        if not (token.startswith("sess-") or token.startswith("sk-ant-")):
            return {"success": False, "error": "Invalid token format. Must start with 'sess-' or 'sk-ant-'"}

    data = load_profiles()
    profiles = data.get("profiles", [])

    # Generate ID for new profiles
    is_new = not profile.id
    if is_new:
        profile.id = str(uuid.uuid4())

    # Check for duplicate names (excluding current profile if updating)
    for p in profiles:
        if p.get("name") == name and p.get("id") != profile.id:
            return {"success": False, "error": f"Profile name '{name}' is already in use"}

    # Create profile dict with sanitized values
    # Use frontend-compatible field names (oauthToken, isDefault)
    profile_data = {
        "id": profile.id,
        "name": name,
        "email": email,
        "oauthToken": token,
        "isDefault": profile.isDefault,
    }

    # Update or add profile
    _upsert_by_id(profiles, profile_data)

    data["profiles"] = profiles
    save_profiles(data)  # This function sets secure file permissions (0o600)
    return {"success": True, "data": profile_data}


@router.delete("/claude-profiles/{profile_id}")
async def delete_claude_profile(profile_id: str):
    """Delete a Claude profile.

    BUG-4.3-001: Now validates that the profile exists before deletion.
    Returns error if profile is not found.
    """
    # Validate profile_id is not empty/whitespace
    if not profile_id or not profile_id.strip():
        return {"success": False, "error": "Profile ID cannot be empty"}

    profile_id = profile_id.strip()
    data = load_profiles()
    profiles = data.get("profiles", [])

    # Check if profile exists before deletion
    original_count = len(profiles)
    data["profiles"] = [p for p in profiles if p.get("id") != profile_id]

    # BUG-4.3-001: Return error if profile was not found
    if len(data["profiles"]) == original_count:
        return {"success": False, "error": f"Profile {profile_id} not found"}

    # Clear active profile if it was the deleted one
    if data.get("activeProfileId") == profile_id:
        data["activeProfileId"] = None

    save_profiles(data)

    # If no profiles remain, clean up the static OAuth fallback file
    # so ClaudeProvider.detect() doesn't report Claude as still active
    remaining = data.get("profiles", [])
    if not remaining:
        oauth_file = Path.home() / ".claude" / "oauth_token"
        if oauth_file.exists():
            try:
                oauth_file.unlink()
                logging.getLogger(__name__).info(
                    "Removed OAuth fallback file after last Claude profile deleted"
                )
            except OSError as e:
                logging.getLogger(__name__).warning(
                    f"Failed to remove OAuth fallback file: {e}"
                )

    return {"success": True}


class ProfileRename(BaseModel):
    name: str


@router.patch("/claude-profiles/{profile_id}")
async def rename_claude_profile(profile_id: str, update: ProfileRename):
    """Rename a Claude profile with validation."""
    try:
        # Validate name
        if not update.name or not update.name.strip():
            return {"success": False, "error": "Profile name cannot be empty"}

        # Strip whitespace and validate length
        name = update.name.strip()
        if len(name) < 1:
            return {"success": False, "error": "Profile name cannot be empty"}
        if len(name) > 100:
            return {"success": False, "error": "Profile name cannot exceed 100 characters"}

        # Load profiles and update
        data = load_profiles()
        profile_found = False

        # Check for duplicate names (excluding the current profile)
        for p in data.get("profiles", []):
            if p.get("id") != profile_id and p.get("name") == name:
                return {"success": False, "error": f"Profile name '{name}' is already in use"}

        # Find and update the profile
        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                p["name"] = name
                profile_found = True
                break

        if not profile_found:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        # Save with secure permissions (0o600 set in save_profiles)
        save_profiles(data)
        return {"success": True}
    except Exception:
        logger.exception("Failed to rename Claude profile %s", profile_id)
        return {"success": False, "error": "Failed to rename profile"}


class ActiveProfileRequest(BaseModel):
    profileId: str


@router.post("/claude-profiles/active")
async def set_active_claude_profile(request: ActiveProfileRequest):
    """Set the active Claude profile."""
    try:
        logger = logging.getLogger(__name__)
        data = load_profiles()

        # Verify the profile exists
        profile_found = False
        for p in data.get("profiles", []):
            if p.get("id") == request.profileId:
                profile_found = True
                break

        if not profile_found:
            return {"success": False, "error": f"Profile {request.profileId} not found"}

        data["activeProfileId"] = request.profileId
        save_profiles(data)
        _sync_env_token_for_active_profile(data, request.profileId, logger)
        return {"success": True}
    except Exception:
        logger.exception("Failed to set active Claude profile %s", request.profileId)
        return {"success": False, "error": "Failed to set active profile"}


@router.post("/claude-profiles/{profile_id}/initialize")
async def initialize_claude_profile(profile_id: str):
    """Initialize a Claude profile.

    Marks a profile as initialized and sets initialization timestamp.
    This can be used after a profile is created to mark it as ready for use.
    """
    try:
        from datetime import datetime

        data = load_profiles()
        profile_found = False

        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                # Mark profile as initialized with timestamp
                p["initialized"] = True
                p["initializedAt"] = datetime.now().isoformat()
                profile_found = True
                break

        if not profile_found:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        save_profiles(data)
        return {"success": True}
    except Exception:
        logger.exception("Failed to initialize Claude profile %s", profile_id)
        return {"success": False, "error": "Failed to initialize profile"}


def _poll_token_and_save(profile_id: str, logger: logging.Logger, mtime_before: float = 0):
    """
    Poll for Claude OAuth token and save it to the specified profile.

    Checks ~/.claude/.credentials.json for a token written AFTER the OAuth
    flow was initiated (using mtime_before as the baseline).

    This runs in a background thread so the HTTP request can return immediately.

    Args:
        profile_id: The profile to save the token to.
        logger: Logger instance.
        mtime_before: The mtime of credentials.json BEFORE launching the CLI.
            Only tokens from files modified after this time will be accepted.
    """
    credentials_path = Path.home() / ".claude" / ".credentials.json"

    for _ in range(90):  # Poll for up to ~3 minutes
        token = None
        try:
            # Only accept tokens from credentials.json that were written
            # AFTER the OAuth flow started (mtime > mtime_before)
            if credentials_path.exists():
                cred_mtime_now = credentials_path.stat().st_mtime
                if cred_mtime_now > mtime_before:
                    cred_data = json.loads(credentials_path.read_text())
                    t = cred_data.get("claudeAiOauth", {}).get("accessToken")
                    if t and t.startswith("sk-ant-oat01-"):
                        token = t

            if token:
                data = load_profiles()
                updated = False
                for p in data.get("profiles", []):
                    if p.get("id") == profile_id:
                        # Only save if the profile doesn't already have this token
                        if p.get("oauthToken") == token:
                            logger.info(f"[Claude OAuth] Profile {profile_id} already has this token, skipping")
                            return
                        p["oauthToken"] = token
                        p.pop("token", None)  # remove legacy field if present
                        updated = True
                        break
                if updated:
                    save_profiles(data)
                    if data.get("activeProfileId") == profile_id:
                        _sync_env_token_for_active_profile(data, profile_id, logger)
                    logger.info(f"[Claude OAuth] Token saved to profile {profile_id}")
                return
        except Exception as e:  # pragma: no cover - best-effort background
            logger.warning(f"[Claude OAuth] Polling error: {e}")
        time.sleep(2)
    logger.warning(f"[Claude OAuth] Token not detected for profile {profile_id} within timeout")


@router.post("/claude-profiles/{profile_id}/start-oauth")
async def start_claude_profile_oauth(profile_id: str):
    """
    Start OAuth token polling for a profile.

    The actual `claude auth login` command runs in the app's interactive
    terminal (frontend creates a PTY terminal and sends the command).
    This endpoint just starts a background poller that watches
    ~/.claude/.credentials.json for a new token and saves it to the profile.
    """
    logger = logging.getLogger(__name__)

    # Validate profile exists
    data = load_profiles()
    profile_exists = any(p.get("id") == profile_id for p in data.get("profiles", []))
    if not profile_exists:
        return {"success": False, "error": f"Profile {profile_id} not found"}

    # Snapshot the credentials file mtime BEFORE the user runs `claude auth login`
    # so the poller only accepts tokens written after this point.
    credentials_path = Path.home() / ".claude" / ".credentials.json"
    mtime_before = credentials_path.stat().st_mtime if credentials_path.exists() else 0

    # Start background poller to save the token once the CLI writes it.
    threading.Thread(
        target=_poll_token_and_save, args=(profile_id, logger, mtime_before), daemon=True
    ).start()

    return {
        "success": True,
        "data": {
            "message": "Token polling started. Run 'claude auth login' in the terminal to authenticate.",
        },
    }


@router.post("/claude-profiles/{profile_id}/complete-oauth")
async def complete_claude_profile_oauth(profile_id: str, body: dict):
    """
    Legacy endpoint — no longer needed since auth happens in the terminal.
    Kept for backward compatibility; returns success immediately.
    """
    return {
        "success": True,
        "data": {"message": "Auth is handled via the terminal. Token will be saved automatically."},
    }


class SetTokenRequest(BaseModel):
    token: SecretStr
    email: str | None = None


@router.post("/claude-profiles/{profile_id}/token")
async def set_claude_profile_token(profile_id: str, request: SetTokenRequest):
    """Set token for a Claude profile with validation and secure storage."""
    try:
        logger = logging.getLogger(__name__)
        # Validate token
        secret = request.token.get_secret_value() if request.token else ""
        if not secret.strip():
            return {"success": False, "error": "Token cannot be empty"}

        # Validate token length (Claude tokens are typically > 20 characters)
        if len(secret) < 20:
            return {"success": False, "error": "Token appears invalid. Must be at least 20 characters."}

        # Validate token format (Claude session tokens start with 'sess-' or API keys with 'sk-ant-')
        token = secret.strip()
        if not (token.startswith("sess-") or token.startswith("sk-ant-")):
            return {
                "success": False,
                "error": "Invalid Claude token format. Must start with 'sess-' or 'sk-ant-'",
            }

        # Load profiles and update
        data = load_profiles()
        profile_found = False

        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                # Use frontend-compatible field name
                p["oauthToken"] = token
                # Remove old field name if present (migration)
                p.pop("token", None)
                if request.email:
                    p["email"] = request.email.strip()
                profile_found = True
                break

        if not profile_found:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        # Save with secure permissions (0o600 set in save_profiles)
        save_profiles(data)
        _sync_env_token_for_active_profile(data, data.get("activeProfileId"), logger)
        return {"success": True}
    except Exception:
        logger.exception("Failed to set token for Claude profile %s", profile_id)
        return {"success": False, "error": "Failed to set profile token"}


@router.get("/claude-profiles/best")
async def get_best_available_profile(exclude: str | None = None):
    """Get the best available Claude profile with a valid token.

    BUG-4.3-003: Now validates that the profile has a token before returning.
    Prioritizes the active profile if it has a valid token and is not excluded.
    """
    data = load_profiles()
    active_id = data.get("activeProfileId")

    # Filter to usable profiles (has token, not excluded)
    # Check both oauthToken (new field name) and token (old field name)
    usable = [
        p for p in data.get("profiles", [])
        if p.get("id") != exclude and (p.get("oauthToken") or p.get("token"))
    ]

    if not usable:
        return {"success": True, "data": None}

    # Prefer active profile if it's in the usable list
    for p in usable:
        if p.get("id") == active_id:
            return {"success": True, "data": p}

    # Return first usable profile
    return {"success": True, "data": usable[0]}


# --------------------------------------------------------------------------
# Auto-Switch Settings
# --------------------------------------------------------------------------


class AutoSwitchSettingsUpdate(BaseModel):
    """Model for updating auto-switch settings."""

    enabled: bool | None = None
    threshold: int | None = Field(None, ge=0, le=100, description="Usage threshold percentage (0-100)")
    proactiveSwapEnabled: bool | None = None  # Proactive monitoring toggle
    autoSwitchOnRateLimit: bool | None = None  # Reactive recovery on rate limit/errors
    usageCheckInterval: int | None = Field(None, ge=0, description="Usage polling interval in ms (0 disables)")
    sessionThreshold: int | None = Field(None, ge=0, le=100, description="Percent threshold for session usage")
    weeklyThreshold: int | None = Field(None, ge=0, le=100, description="Percent threshold for weekly usage")


def get_auto_switch_file() -> Path:
    """Get path to the auto-switch settings file."""
    settings = get_settings()
    return Path(settings.PROJECTS_DATA_DIR) / "auto-switch.json"


@router.get("/auto-switch")
async def get_auto_switch_settings():
    """Get auto-switch settings."""
    auto_switch_file = get_auto_switch_file()
    if auto_switch_file.exists():
        try:
            data = json.loads(auto_switch_file.read_text())
            return {"success": True, "data": data}
        except json.JSONDecodeError:
            pass
    return {
        "success": True,
        "data": {
            "enabled": False,
            "threshold": 80,
            "proactiveSwapEnabled": True,
            "autoSwitchOnRateLimit": False,
            "usageCheckInterval": 30000,
            "sessionThreshold": 95,
            "weeklyThreshold": 99,
        },
    }


@router.patch("/auto-switch")
async def update_auto_switch_settings(settings_update: AutoSwitchSettingsUpdate):
    """Update auto-switch settings with validation and secure storage.
    
    Updates the auto-switch configuration which controls automatic profile switching
    based on usage thresholds. Settings are stored in auto-switch.json with secure
    file permissions.
    
    Args:
        settings_update: Auto-switch settings update with optional enabled and threshold fields
        
    Returns:
        Success response with updated settings or error details
    """
    try:
        auto_switch_file = get_auto_switch_file()
        # Load current settings (with defaults)
        current = {
            "enabled": False,
            "threshold": 80,
            "proactiveSwapEnabled": True,
            "autoSwitchOnRateLimit": False,
            "usageCheckInterval": 30000,
            "sessionThreshold": 95,
            "weeklyThreshold": 99,
        }
        if auto_switch_file.exists():
            try:
                current = json.loads(auto_switch_file.read_text())
            except json.JSONDecodeError:
                logger.exception("Failed to parse existing auto-switch.json")
                return {
                    "success": False,
                    "error": "Failed to parse existing auto-switch settings file",
                }
        
        # Update with new values (only non-None values from Pydantic model)
        update_dict = settings_update.model_dump(exclude_none=True)
        current.update(update_dict)
        
        # Ensure parent directory exists
        auto_switch_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Write updated settings with pretty formatting
        auto_switch_file.write_text(json.dumps(current, indent=2))
        
        # Set secure file permissions (owner read/write only)
        # Following security pattern from save_profiles() and other Phase 2/3 endpoints
        auto_switch_file.chmod(0o600)
        
        return {"success": True, "data": current}

    except Exception:
        logger.exception("Failed to update auto-switch settings")
        return {"success": False, "error": "Failed to update auto-switch settings"}


class RetryWithProfileRequest(BaseModel):
    """Request model for retrying with a different profile."""

    profileId: str = Field(..., min_length=1, description="ID of the profile to switch to")
    reason: str | None = Field(None, description="Reason for the switch (e.g., 'rate_limit', 'error')")
    operationContext: dict | None = Field(None, description="Optional context about the failed operation")


@router.post("/retry-with-profile")
async def retry_with_profile(request: RetryWithProfileRequest):
    """Switch to a different Claude profile and prepare for operation retry.

    This endpoint facilitates profile switching when an operation fails due to
    rate limits or other profile-specific issues. It switches the active profile
    and returns information needed for the user/frontend to retry the operation.

    Common use case:
    1. User hits rate limit with current profile
    2. Frontend calls this endpoint with a different profileId
    3. Profile is switched
    4. User manually retries the operation (or frontend auto-retries)

    Args:
        request: Profile switch request with profileId, optional reason, and operation context

    Returns:
        Success response with profile switch details including:
        - previousProfileId: The profile that was active before the switch
        - newProfileId: The newly activated profile
        - profileName: Name of the new profile for display
        - reason: Switch reason if provided
    """
    try:
        logger = logging.getLogger(__name__)
        # Validate profileId is not empty/whitespace
        profile_id = request.profileId.strip() if request.profileId else ""
        if not profile_id:
            return {"success": False, "error": "profileId cannot be empty"}

        # Load profiles
        data = load_profiles()

        # Store previous active profile for return data
        previous_profile_id = data.get("activeProfileId")

        # Check if trying to switch to the same profile
        if previous_profile_id == profile_id:
            # Find profile name for better error message
            profile_name = None
            for p in data.get("profiles", []):
                if p.get("id") == profile_id:
                    profile_name = p.get("name", "Unknown")
                    break
            return {
                "success": False,
                "error": f"Profile '{profile_name or profile_id}' is already active",
            }

        # Verify the target profile exists and get profile details
        target_profile = None
        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                target_profile = p
                break

        if not target_profile:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        # Get previous profile name for logging/response
        previous_profile_name = None
        if previous_profile_id:
            for p in data.get("profiles", []):
                if p.get("id") == previous_profile_id:
                    previous_profile_name = p.get("name")
                    break

        # Set the new profile as active
        data["activeProfileId"] = profile_id

        # Save profiles with secure permissions (via save_profiles function)
        save_profiles(data)
        _sync_env_token_for_active_profile(data, profile_id, logger)

        # Build response with comprehensive information
        response = {
            "success": True,
            "previousProfileId": previous_profile_id,
            "previousProfileName": previous_profile_name,
            "newProfileId": profile_id,
            "profileName": target_profile.get("name"),
            "profileEmail": target_profile.get("email"),
        }

        # Include reason if provided
        if request.reason:
            response["reason"] = request.reason

        # Include operation context if provided (for frontend to use in retry)
        if request.operationContext:
            response["operationContext"] = request.operationContext

        return response

    except Exception:
        logger.exception("Failed to switch profile to %s", request.profileId)
        return {"success": False, "error": "Failed to switch profile"}


@router.post("/usage-update")
async def request_usage_update():
    """Request a usage update.

    Reads local Claude stats from ~/.claude/stats-cache.json and calculates
    approximate usage percentages based on typical daily limits.

    Note: Returns raw data object (not wrapped in {success, data}) because
    the frontend api-client.ts automatically adds that wrapper.
    """
    from datetime import datetime, timedelta

    stats_file = Path.home() / ".claude" / "stats-cache.json"

    # Default response
    default_response = {
        "sessionPercent": 0,
        "weeklyPercent": 0,
        "sessionResetTime": None,
        "weeklyResetTime": None,
        "profileId": "local",
        "profileName": "Local Stats",
        "fetchedAt": datetime.now().isoformat(),
        "limitType": None,
    }

    if not stats_file.exists():
        return default_response

    try:
        stats = json.loads(stats_file.read_text())

        # Get today's date
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        # Calculate today's usage
        daily_activity = stats.get("dailyActivity", [])
        today_messages = 0
        weekly_messages = 0

        for day in daily_activity:
            if day.get("date") == today:
                today_messages = day.get("messageCount", 0)
            if day.get("date") >= week_ago:
                weekly_messages += day.get("messageCount", 0)

        # Usage limits (actual confirmed values)
        # Daily limit: 10,000 messages
        # Weekly limit: 10,000 × 7 = 70,000 messages
        # Resets on Thursday weekly
        DAILY_LIMIT_ESTIMATE = 10000
        WEEKLY_LIMIT_ESTIMATE = 70000

        session_percent = min(100, int((today_messages / DAILY_LIMIT_ESTIMATE) * 100))
        weekly_percent = min(100, int((weekly_messages / WEEKLY_LIMIT_ESTIMATE) * 100))

        # Get model usage info
        model_usage = stats.get("modelUsage", {})
        total_output_tokens = sum(m.get("outputTokens", 0) for m in model_usage.values())

        return {
            "sessionPercent": session_percent,
            "weeklyPercent": weekly_percent,
            "sessionResetTime": "Midnight",
            "weeklyResetTime": "Weekly",
            "profileId": "local",
            "profileName": f"Local ({today_messages} msgs today)",
            "fetchedAt": datetime.now().isoformat(),
            "limitType": "session" if session_percent > weekly_percent else "weekly",
            # Extra stats for tooltip
            "todayMessages": today_messages,
            "weeklyMessages": weekly_messages,
            "totalOutputTokens": total_output_tokens,
        }

    except (json.JSONDecodeError, KeyError, TypeError):
        return default_response


# --------------------------------------------------------------------------
# API Profiles (OpenAI-compatible endpoints)
# --------------------------------------------------------------------------


def get_api_profiles_file() -> Path:
    """Get path to the API profiles file."""
    settings = get_settings()
    return Path(settings.PROJECTS_DATA_DIR) / "api-profiles.json"


def load_api_profiles() -> dict:
    """Load API profiles."""
    profiles_file = get_api_profiles_file()
    if profiles_file.exists():
        try:
            return json.loads(profiles_file.read_text())
        except json.JSONDecodeError:
            pass
    return {"profiles": [], "activeProfileId": None}


def save_api_profiles(data: dict) -> None:
    """Save API profiles atomically, mode 0600 (they hold API keys and tokens).

    See paths.atomic_write_secret_json (#298): the previous write_text + chmod
    left both a torn-read window (a reader gets invalid JSON) and a
    world-readable window (created at umask, chmodded only after the keys landed).
    """
    atomic_write_secret_json(get_api_profiles_file(), data)


@router.get("/api-profiles")
async def get_api_profiles():
    """Get all API profiles."""
    return {"success": True, "data": load_api_profiles()}


@router.post("/api-profiles")
async def save_api_profile(profile: dict):
    """Save an API profile."""
    import uuid

    data = load_api_profiles()
    if not profile.get("id"):
        profile["id"] = str(uuid.uuid4())

    profiles = data.get("profiles", [])
    _upsert_by_id(profiles, profile)

    data["profiles"] = profiles
    save_api_profiles(data)
    return {"success": True, "data": profile}


class ApiProfileModels(BaseModel):
    """Optional model mappings for API profile."""

    default: str | None = Field(None, description="Default model (maps to ANTHROPIC_MODEL)")
    haiku: str | None = Field(None, description="Haiku model (maps to ANTHROPIC_DEFAULT_HAIKU_MODEL)")
    sonnet: str | None = Field(None, description="Sonnet model (maps to ANTHROPIC_DEFAULT_SONNET_MODEL)")
    opus: str | None = Field(None, description="Opus model (maps to ANTHROPIC_DEFAULT_OPUS_MODEL)")


class ApiProfileUpdate(BaseModel):
    """Model for updating API profile configuration.

    All fields are optional to support partial updates. Only provided fields
    will be updated in the profile.
    """
    name: str | None = Field(None, min_length=1, max_length=100, description="Profile name (1-100 characters)")
    baseUrl: str | None = Field(None, min_length=1, description="API endpoint URL")
    apiKey: str | None = Field(None, min_length=20, description="API key (minimum 20 characters)", repr=False)
    models: ApiProfileModels | None = Field(None, description="Optional model mappings")


@router.put("/api-profiles/{profile_id}")
async def update_api_profile(profile_id: str, profile_update: ApiProfileUpdate):
    """Update an API profile.

    Supports partial updates - only provided fields will be updated.
    Validates all inputs and maintains secure file storage.

    Validation:
    - name: 1-100 characters, no duplicates
    - baseUrl: Non-empty, valid URL format (http:// or https://)
    - apiKey: Minimum 20 characters
    - Automatically updates updatedAt timestamp
    - Maintains secure file permissions (0o600)
    """
    try:
        # Validate profile_id
        if not profile_id or not profile_id.strip():
            return {"success": False, "error": "Profile ID cannot be empty"}

        profile_id = profile_id.strip()

        # Load existing profiles
        data = load_api_profiles()
        profiles = data.get("profiles", [])

        # Find the profile to update
        profile_index = None
        current_profile = None
        for i, p in enumerate(profiles):
            if p.get("id") == profile_id:
                profile_index = i
                current_profile = p
                break

        if profile_index is None:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        # Get update data (exclude None values for partial update)
        update_data = profile_update.model_dump(exclude_none=True)

        # Validate and sanitize name if provided
        if "name" in update_data:
            name = update_data["name"].strip()
            if not name:
                return {"success": False, "error": "Profile name cannot be empty"}
            if len(name) < 1 or len(name) > 100:
                return {"success": False, "error": "Profile name must be between 1 and 100 characters"}

            # Check for duplicate names (excluding current profile)
            for p in profiles:
                if p.get("id") != profile_id and p.get("name", "").strip().lower() == name.lower():
                    return {"success": False, "error": f"Profile name '{name}' is already in use"}

            update_data["name"] = name

        # Validate and sanitize baseUrl if provided
        if "baseUrl" in update_data:
            base_url = update_data["baseUrl"].strip()
            if not base_url:
                return {"success": False, "error": "Base URL cannot be empty"}
            if not (base_url.startswith("http://") or base_url.startswith("https://")):
                return {"success": False, "error": "Base URL must start with http:// or https://"}

            update_data["baseUrl"] = base_url

        # Validate and sanitize apiKey if provided
        if "apiKey" in update_data:
            api_key = update_data["apiKey"].strip()
            if not api_key:
                return {"success": False, "error": "API key cannot be empty"}
            if len(api_key) < 20:
                return {"success": False, "error": "API key must be at least 20 characters"}

            update_data["apiKey"] = api_key

        # Update timestamp
        import time

        update_data["updatedAt"] = int(time.time() * 1000)  # Unix timestamp in milliseconds

        # Merge updates into current profile (preserving id and createdAt)
        updated_profile = {**current_profile, **update_data, "id": profile_id}

        # Preserve createdAt if it exists
        if "createdAt" in current_profile:
            updated_profile["createdAt"] = current_profile["createdAt"]

        # Update profile in list
        profiles[profile_index] = updated_profile
        data["profiles"] = profiles

        # Save with secure permissions (0o600 - owner read/write only)
        save_api_profiles(data)

        return {
            "success": True,
            "data": updated_profile
        }

    except Exception:
        logger.exception("Failed to update API profile %s", profile_id)
        return {"success": False, "error": "Failed to update API profile"}


@router.delete("/api-profiles/{profile_id}")
async def delete_api_profile(profile_id: str):
    """
    Delete an API profile.

    Validates:
    - Profile ID is not empty
    - Profile exists in api-profiles.json
    - Profile is NOT the currently active profile (deletion prevented)

    Args:
        profile_id: The ID of the profile to delete

    Returns:
        Success response on deletion, or error response with details

    Security:
        Uses save_api_profiles() which sets secure file permissions (0o600)
    """
    try:
        # Validate profile_id is not empty
        if not profile_id or not profile_id.strip():
            return {"success": False, "error": "Profile ID cannot be empty"}

        # Strip whitespace from profile_id
        profile_id = profile_id.strip()

        # Load current API profiles
        data = load_api_profiles()

        # Find the profile to delete and get its name for error messages
        profile_to_delete = None
        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                profile_to_delete = p
                break

        # Check if profile exists
        if not profile_to_delete:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        # CRITICAL: Prevent deletion of the active profile
        # This ensures users don't accidentally delete the profile they're currently using
        active_profile_id = data.get("activeProfileId")
        if active_profile_id and active_profile_id == profile_id:
            profile_name = profile_to_delete.get("name", profile_id)
            return {
                "success": False,
                "error": f"Cannot delete active profile '{profile_name}'. Please switch to a different profile first.",
            }

        # Remove the profile from the profiles array
        data["profiles"] = [p for p in data.get("profiles", []) if p.get("id") != profile_id]

        # Save updated profiles (with secure 0o600 permissions)
        save_api_profiles(data)

        return {
            "success": True,
            "message": "Profile deleted successfully",
            "deletedProfileId": profile_id,
        }

    except Exception:
        logger.exception("Failed to delete API profile %s", profile_id)
        return {"success": False, "error": "Failed to delete API profile"}


@router.post("/api-profiles/active")
async def set_active_api_profile(request: dict):
    """Set the active API profile."""
    try:
        profile_id = request.get("profileId")

        if not profile_id:
            return {"success": False, "error": "profileId is required"}

        data = load_api_profiles()

        # Verify the profile exists
        profile_found = False
        for p in data.get("profiles", []):
            if p.get("id") == profile_id:
                profile_found = True
                break

        if not profile_found:
            return {"success": False, "error": f"Profile {profile_id} not found"}

        data["activeProfileId"] = profile_id
        save_api_profiles(data)
        return {"success": True}
    except Exception:
        logger.exception("Failed to set active API profile %s", profile_id)
        return {"success": False, "error": "Failed to set active profile"}


class TestConnectionRequest(BaseModel):
    baseUrl: str
    apiKey: SecretStr


@router.post("/api-profiles/test")
async def test_api_connection(request: TestConnectionRequest):
    """Test connection to an API endpoint."""
    import urllib.request

    try:
        req = urllib.request.Request(
            f"{request.baseUrl}/models",
            headers={"Authorization": f"Bearer {request.apiKey.get_secret_value()}"}
        )
        urllib.request.urlopen(req, timeout=10)
        return {"success": True, "data": {"connected": True}}
    except Exception:
        logger.exception("API connection test failed for %s", request.baseUrl)
        return {"success": False, "error": "Connection test failed"}


@router.post("/api-profiles/discover-models")
async def discover_api_models(request: TestConnectionRequest):
    """Discover available models from an API endpoint."""
    import json as json_module
    import urllib.request

    try:
        req = urllib.request.Request(
            f"{request.baseUrl}/models",
            headers={"Authorization": f"Bearer {request.apiKey.get_secret_value()}"}
        )
        response = urllib.request.urlopen(req, timeout=10)
        data = json_module.loads(response.read().decode())
        models = [m.get("id") for m in data.get("data", [])]
        return {"success": True, "data": models}
    except Exception:
        logger.exception("Failed to discover models from %s", request.baseUrl)
        return {"success": False, "error": "Failed to discover models"}


# --------------------------------------------------------------------------
# Source Environment
# --------------------------------------------------------------------------


class SourceEnvUpdate(BaseModel):
    """Model for updating PFactory source environment configuration."""
    claudeToken: str | None = Field(None, description="Claude Code OAuth token (CLAUDE_CODE_OAUTH_TOKEN)", repr=False)
    anthropicBaseUrl: str | None = Field(None, description="Custom Anthropic API endpoint (ANTHROPIC_BASE_URL)")
    graphitiEnabled: bool | None = Field(None, description="Enable Graphiti memory system (GRAPHITI_ENABLED)")
    githubToken: str | None = Field(None, description="GitHub personal access token (GITHUB_TOKEN)", repr=False)
    openaiApiKey: str | None = Field(None, description="OpenAI API key for Graphiti (OPENAI_API_KEY)", repr=False)
    debug: bool | None = Field(None, description="Enable debug mode (DEBUG)")


@router.get("/source-env")
async def get_source_env():
    """Get source environment configuration."""
    return {"success": True, "data": {}}


@router.patch("/source-env")
async def update_source_env(config: SourceEnvUpdate):
    """
    Update PFactory source environment configuration.

    Updates the apps/backend/.env file with environment variables for:
    - Claude authentication (CLAUDE_CODE_OAUTH_TOKEN)
    - Custom API endpoints (ANTHROPIC_BASE_URL)
    - Graphiti memory system (GRAPHITI_ENABLED)
    - GitHub integration (GITHUB_TOKEN)
    - OpenAI integration for Graphiti (OPENAI_API_KEY)
    - Debug mode (DEBUG)

    Only updates fields that are provided (partial updates supported).
    Sets secure file permissions (0o600) to protect sensitive tokens.

    Args:
        config: Environment update configuration with optional fields

    Returns:
        Success response with confirmation message

    Raises:
        HTTPException: For validation errors or file system errors
    """
    try:
        settings = get_settings()
        backend_path = Path(settings.BACKEND_PATH)
        env_path = backend_path / ".env"

        # Read existing .env or start fresh
        existing = {}
        if env_path.exists():
            for line in env_path.read_text().split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    existing[key.strip()] = value.strip()

        # Get only provided fields (exclude None values)
        config_dict = config.model_dump(exclude_none=True)

        # Map configuration fields to environment variables
        # Token fields (string values that need validation)
        token_mapping = {
            "claudeToken": "CLAUDE_CODE_OAUTH_TOKEN",
            "githubToken": "GITHUB_TOKEN",
            "openaiApiKey": "OPENAI_API_KEY",
        }

        for config_key, env_key in token_mapping.items():
            if config_key in config_dict:
                value = config_dict[config_key]
                if value:
                    # Strip whitespace and validate token is not empty
                    value = value.strip()
                    if not value:
                        raise HTTPException(
                            status_code=400,
                            detail=f"{config_key} cannot be empty"
                        )
                    # Validate minimum token length for security
                    if len(value) < 10:
                        raise HTTPException(
                            status_code=400,
                            detail=f"{config_key} must be at least 10 characters"
                        )
                    existing[env_key] = value
                else:
                    # Remove token if explicitly set to empty string
                    existing.pop(env_key, None)

        # Map URL fields (string values)
        if "anthropicBaseUrl" in config_dict:
            value = config_dict["anthropicBaseUrl"]
            if value:
                value = value.strip()
                # Validate URL format
                if not value.startswith(("http://", "https://")):
                    raise HTTPException(
                        status_code=400,
                        detail="anthropicBaseUrl must start with http:// or https://",
                    )
                existing["ANTHROPIC_BASE_URL"] = value
            else:
                # Remove if explicitly set to empty string
                existing.pop("ANTHROPIC_BASE_URL", None)

        # Map boolean fields (convert to "true"/"false" strings)
        bool_mapping = {
            "graphitiEnabled": "GRAPHITI_ENABLED",
            "debug": "DEBUG",
        }

        for config_key, env_key in bool_mapping.items():
            if config_key in config_dict:
                value = config_dict[config_key]
                existing[env_key] = "true" if value else "false"

        # Write updated .env file
        env_lines = []

        # Add header comment
        env_lines.append("# PFactory Environment Configuration")
        env_lines.append("# Updated via PFactory web interface")
        env_lines.append("")

        # Write all environment variables
        for key, value in sorted(existing.items()):
            env_lines.append(f"{key}={value}")

        env_content = "\n".join(env_lines)
        env_path.write_text(env_content)

        # Set secure file permissions (owner read/write only)
        # CRITICAL: .env files often contain sensitive API keys and tokens
        env_path.chmod(0o600)

        return {
            "success": True,
            "message": "Source environment configuration updated successfully",
            "updated_fields": list(config_dict.keys()),
        }

    except HTTPException:
        raise
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse existing .env file: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update source environment: {str(e)}"
        )


@router.get("/source-token-check")
async def check_source_token():
    """Check source token status."""
    return {"success": True, "data": {"valid": False}}


# --------------------------------------------------------------------------
# CLI Tools Info
# --------------------------------------------------------------------------


@router.get("/auth-status")
async def get_auth_status():
    """Check if any OAuth token is configured."""
    from ..paths import get_data_file

    profiles_file = get_data_file("claude-profiles.json")
    has_token = False
    profile_count = 0
    email = None

    if profiles_file.exists():
        try:
            data = json.loads(profiles_file.read_text())
            profiles = data.get("profiles", [])
            profile_count = len(profiles)
            active_id = data.get("activeProfileId")
            for p in profiles:
                if p.get("oauthToken"):
                    has_token = True
                    # Get email from active profile, or first profile with a token
                    if email is None or p.get("id") == active_id:
                        email = p.get("email")
        except (json.JSONDecodeError, KeyError):
            pass

    # Also check env var fallback
    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")

    # Verify Claude CLI is actually installed before reporting hasToken
    claude_installed = shutil.which("claude") is not None
    if not claude_installed:
        try:
            result = subprocess.run(
                ["bash", "-l", "-c", "which claude"],
                capture_output=True, text=True, timeout=5,
            )
            claude_installed = result.returncode == 0
        except Exception:
            pass

    return {
        "hasToken": (has_token or bool(env_token)) and claude_installed,
        "profileCount": profile_count,
        "source": "profile" if has_token else ("env" if env_token else None),
        "email": email,
    }


@router.get("/claude-credentials-exist")
async def check_claude_credentials_exist():
    """Check if ~/.claude/.credentials.json exists with a valid token."""
    cred_path = Path.home() / ".claude" / ".credentials.json"
    exists = False

    if cred_path.exists():
        try:
            data = json.loads(cred_path.read_text())
            token = data.get("claudeAiOauth", {}).get("accessToken")
            exists = bool(token and token.startswith("sk-ant-oat01-"))
        except (json.JSONDecodeError, KeyError):
            pass

    return {"exists": exists}


@router.post("/import-claude-credentials")
async def import_claude_credentials():
    """Import token from ~/.claude/.credentials.json into PFactory profiles."""
    from ..paths import get_data_file

    cred_path = Path.home() / ".claude" / ".credentials.json"

    if not cred_path.exists():
        raise HTTPException(status_code=404, detail="Claude credentials file not found")

    try:
        data = json.loads(cred_path.read_text())
        token = data.get("claudeAiOauth", {}).get("accessToken")
        if not token or not token.startswith("sk-ant-oat01-"):
            raise HTTPException(status_code=400, detail="No valid OAuth token found in credentials file")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid credentials file format")

    # Create or update profiles file
    profiles_file = get_data_file("claude-profiles.json")
    profiles_file.parent.mkdir(parents=True, exist_ok=True)

    if profiles_file.exists():
        try:
            profiles_data = json.loads(profiles_file.read_text())
        except json.JSONDecodeError:
            profiles_data = {"profiles": [], "activeProfileId": None}
    else:
        profiles_data = {"profiles": [], "activeProfileId": None}

    # Create an imported profile
    import time
    from datetime import datetime

    profile_id = f"imported-{int(time.time())}"
    new_profile = {
        "id": profile_id,
        "name": "Claude Code (Imported)",
        "oauthToken": token,
        "isDefault": len(profiles_data["profiles"]) == 0,
        "createdAt": datetime.now().isoformat(),
    }

    profiles_data["profiles"].append(new_profile)
    profiles_data["activeProfileId"] = profile_id

    # Atomic + 0600 from birth: this file holds oauthToken (#298).
    atomic_write_secret_json(profiles_file, profiles_data)

    # Also set env var for immediate use
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token

    return {
        "success": True,
        "profileId": profile_id,
        "profileName": new_profile["name"],
    }
