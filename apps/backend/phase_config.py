"""
Phase Configuration Module
===========================

Handles model and thinking level configuration for different execution phases.
Reads configuration from task_metadata.json and provides resolved model IDs.
"""

import json
import logging
import os
from pathlib import Path
from typing import Literal, TypedDict

logger = logging.getLogger(__name__)

# Model shorthand to full model ID mapping
MODEL_ID_MAP: dict[str, str] = {
    "opus": "claude-opus-4-7",
    "opus-1m": "claude-opus-4-6",  # legacy alias — kept for users who pinned 4.6 + 1M beta
    "opus-4.5": "claude-opus-4-5-20251101",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}

# Model shorthand to required SDK beta headers
# Maps model shorthands that need special beta flags (e.g., 1M context window)
MODEL_BETAS_MAP: dict[str, list[str]] = {
    "opus-1m": ["context-1m-2025-08-07"],
}

# Thinking level to budget tokens mapping (None = no extended thinking)
# Values must match frontend THINKING_BUDGET_MAP
THINKING_BUDGET_MAP: dict[str, int | None] = {
    "none": None,
    "low": 1024,
    "medium": 4096,  # Moderate analysis
    "high": 16384,  # Deep thinking for QA review
}

# Effort level mapping for adaptive thinking models (e.g., Opus 4.6)
# These models support CLAUDE_CODE_EFFORT_LEVEL env var for effort-based routing
EFFORT_LEVEL_MAP: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
}

# Models that support adaptive thinking via effort level (env var)
# These models get both max_thinking_tokens AND effort_level
ADAPTIVE_THINKING_MODELS: set[str] = {"claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"}

# Spec runner phase-specific thinking levels
# Heavy phases use max for deep analysis
# Light phases use medium after compaction
SPEC_PHASE_THINKING_LEVELS: dict[str, str] = {
    # Heavy phases - high (discovery, spec creation, self-critique)
    "discovery": "high",
    "spec_writing": "high",
    "self_critique": "high",
    # Light phases - medium (after first invocation with compaction)
    "requirements": "medium",
    "research": "medium",
    "context": "medium",
    "planning": "medium",
    "validation": "medium",
    "quick_spec": "medium",
    "historical_context": "medium",
    "complexity_assessment": "medium",
}

# Default phase configuration (fallback, matches 'Balanced' profile)
DEFAULT_PHASE_MODELS: dict[str, str] = {
    "spec": "sonnet",
    "planning": "sonnet",  # Changed from "opus" (fix #433)
    "coding": "sonnet",
    "qa": "sonnet",
    "qa_fixer": "sonnet",
}

DEFAULT_PHASE_THINKING: dict[str, str] = {
    "spec": "medium",
    "planning": "high",
    "coding": "medium",
    "qa": "high",
    "qa_fixer": "low",
}


class PhaseModelConfig(TypedDict, total=False):
    spec: str
    planning: str
    coding: str
    qa: str
    qa_fixer: str


class PhaseThinkingConfig(TypedDict, total=False):
    spec: str
    planning: str
    coding: str
    qa: str
    qa_fixer: str


class TaskMetadataConfig(TypedDict, total=False):
    """Structure of model-related fields in task_metadata.json"""

    isAutoProfile: bool
    phaseModels: PhaseModelConfig
    phaseThinking: PhaseThinkingConfig
    model: str
    thinkingLevel: str
    fastMode: bool


Phase = Literal["spec", "planning", "coding", "qa", "qa_fixer"]


def resolve_model_id(model: str) -> str:
    """
    Resolve a model shorthand (haiku, sonnet, opus) to a full model ID.
    If the model is already a full ID, return it unchanged.

    Priority:
    1. Environment variable override (from API Profile)
    2. Hardcoded MODEL_ID_MAP
    3. Pass through unchanged (assume full model ID)

    Args:
        model: Model shorthand or full ID

    Returns:
        Full Claude model ID
    """
    # Check for environment variable override (from API Profile custom model mappings)
    if model in MODEL_ID_MAP:
        env_var_map = {
            "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "opus-1m": "ANTHROPIC_DEFAULT_OPUS_MODEL",
            # opus-4.5 intentionally omitted — always resolves to its hardcoded
            # model ID (claude-opus-4-5-20251101) regardless of env var overrides.
        }
        env_var = env_var_map.get(model)
        if env_var:
            env_value = os.environ.get(env_var)
            if env_value:
                return env_value

        # Fall back to hardcoded mapping
        return MODEL_ID_MAP[model]

    # Already a full model ID or unknown shorthand
    return model


def get_model_betas(model_short: str) -> list[str]:
    """
    Get required SDK beta headers for a model shorthand.

    Some model configurations (e.g., opus-1m for 1M context window) require
    passing beta headers to the Claude Agent SDK.

    Args:
        model_short: Model shorthand (e.g., 'opus', 'opus-1m', 'sonnet')

    Returns:
        List of beta header strings, or empty list if none required
    """
    return MODEL_BETAS_MAP.get(model_short, [])


def get_thinking_budget(thinking_level: str) -> int | None:
    """
    Get the thinking budget for a thinking level.

    Args:
        thinking_level: Thinking level (none, low, medium, high, max)

    Returns:
        Token budget or None for no extended thinking
    """
    if thinking_level not in THINKING_BUDGET_MAP:
        valid_levels = ", ".join(THINKING_BUDGET_MAP.keys())
        logger.warning(
            "Invalid thinking_level '%s'. Valid values: %s. Defaulting to 'medium'.",
            thinking_level,
            valid_levels,
        )
        return THINKING_BUDGET_MAP["medium"]

    return THINKING_BUDGET_MAP[thinking_level]


def is_adaptive_model(model_id: str) -> bool:
    """
    Check if a model supports adaptive thinking via effort level.

    Adaptive models support the CLAUDE_CODE_EFFORT_LEVEL environment variable
    for effort-based routing in addition to max_thinking_tokens.

    Args:
        model_id: Full model ID (e.g., 'claude-opus-4-6')

    Returns:
        True if the model supports adaptive thinking
    """
    return model_id in ADAPTIVE_THINKING_MODELS


# Issue #7 — SDK-native adaptive + interleaved thinking
# The constants and helpers below are the entry point for callers that want
# to use the Claude Agent SDK's `thinking` parameter (post-Jan-2026 SDK).
# is_adaptive_model() above stays in use for the legacy CLAUDE_CODE_EFFORT_LEVEL
# path; the gate here is narrower: only Opus 4.7 routes to the SDK-native
# {"type": "adaptive"} shape — Opus 4.6 stays on the effort-level path.
_OPUS_47_ID: str = "claude-opus-4-7"

INTERLEAVED_THINKING_AGENT_TYPES: frozenset[str] = frozenset({"planner", "coder"})
INTERLEAVED_THINKING_BETA: str = "interleaved-thinking-2025-05-14"


def thinking_config_for(
    model_id: str,
    thinking_level: str,
    explicit_budget: int | None = None,
) -> dict | None:
    """
    Build the SDK `thinking` config dict for ClaudeAgentOptions.thinking,
    or None when the caller should fall back to the legacy
    `max_thinking_tokens` path.

    Precedence:
      1. explicit_budget > 0  → {"type": "enabled", "budget_tokens": N}
         honoured even on Opus 4.7 so operator-tuned budgets are preserved.
      2. Opus 4.7 with thinking_level != "none" → {"type": "adaptive"}
         (Opus 4.7 deprecated manual budget in favour of model-controlled.)
      3. anything else → None (caller uses the legacy max_thinking_tokens).

    Args:
        model_id: Full model ID (e.g. 'claude-opus-4-7').
        thinking_level: Level string ("none", "low", "medium", "high").
        explicit_budget: Optional caller-specified token budget. When > 0,
            overrides adaptive even on Opus 4.7.

    Returns:
        Thinking config dict or None.
    """
    if explicit_budget is not None and explicit_budget > 0:
        return {"type": "enabled", "budget_tokens": explicit_budget}
    if model_id == _OPUS_47_ID and thinking_level != "none":
        return {"type": "adaptive"}
    return None


def interleaved_thinking_betas_for(
    model_id: str,
    agent_type: str,
) -> list[str]:
    """
    Return [INTERLEAVED_THINKING_BETA] when the (model, agent_type) pair
    benefits from interleaved-thinking-2025-05-14, else an empty list.

    Today only Opus 4.7 supports this beta, and only planner + coder agents
    actually use the mid-tool-call reasoning. QA, fixer, and spec agents
    receive an empty list.

    Note: returned list is always a fresh allocation — safe for the caller
    to mutate (e.g. extend with context-window betas).

    Args:
        model_id: Full model ID.
        agent_type: Agent type ("planner", "coder", "qa_reviewer", …).

    Returns:
        List of beta header strings — either [INTERLEAVED_THINKING_BETA] or [].
    """
    if model_id == _OPUS_47_ID and agent_type in INTERLEAVED_THINKING_AGENT_TYPES:
        return [INTERLEAVED_THINKING_BETA]
    return []


def get_thinking_kwargs_for_model(model_id: str, thinking_level: str) -> dict:
    """
    Get thinking-related kwargs for create_client() based on model type.

    For adaptive models (Opus 4.6): returns both max_thinking_tokens and effort_level.
    For other models (Sonnet, Haiku): returns only max_thinking_tokens.

    Args:
        model_id: Full model ID (e.g., 'claude-opus-4-6')
        thinking_level: Thinking level string (none, low, medium, high, max)

    Returns:
        Dict with 'max_thinking_tokens' and optionally 'effort_level'
    """
    kwargs: dict = {"max_thinking_tokens": get_thinking_budget(thinking_level)}
    if is_adaptive_model(model_id):
        kwargs["effort_level"] = EFFORT_LEVEL_MAP.get(thinking_level, "medium")
    return kwargs


def load_task_metadata(spec_dir: Path) -> TaskMetadataConfig | None:
    """
    Load task metadata from the spec directory.

    Checks two locations in order:
    1. task_metadata.json (preferred, written by web UI)
    2. requirements.json["metadata"] (fallback for backward compatibility)

    Args:
        spec_dir: Path to the spec directory

    Returns:
        Parsed task metadata or None if not found
    """
    # First, try task_metadata.json (preferred)
    metadata_path = spec_dir / "task_metadata.json"
    if metadata_path.exists():
        try:
            with open(metadata_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: check requirements.json["metadata"]
    requirements_path = spec_dir / "requirements.json"
    if requirements_path.exists():
        try:
            with open(requirements_path) as f:
                requirements = json.load(f)
                if "metadata" in requirements and isinstance(requirements["metadata"], dict):
                    return requirements["metadata"]
        except (json.JSONDecodeError, OSError):
            pass

    return None


def get_phase_model(
    spec_dir: Path,
    phase: Phase,
    cli_model: str | None = None,
) -> str:
    """
    Get the resolved model ID for a specific execution phase.

    Priority:
    1. Phase-specific config from task_metadata.json (if auto profile) — wins
       over CLI default because the auto profile is the user's explicit
       per-phase choice (e.g. Claude plans, Ollama codes).
    2. CLI argument (if provided)
    3. Single model from task_metadata.json (if not auto profile)
    4. Default phase configuration

    Args:
        spec_dir: Path to the spec directory
        phase: Execution phase (spec, planning, coding, qa)
        cli_model: Model from CLI argument (optional)

    Returns:
        Resolved full model ID
    """
    # Load task metadata first — the auto profile's per-phase choice is the
    # user's most specific intent and must win over the run.py CLI default
    # model. (Without this, e.g. coding always falls back to the CLI's
    # 'sonnet' default and never routes to Ollama/qwen3 even when the user
    # selected ollama:qwen3:14b for the coding phase.)
    metadata = load_task_metadata(spec_dir)

    if metadata and metadata.get("isAutoProfile") and metadata.get("phaseModels"):
        phase_models = metadata["phaseModels"]
        model = phase_models.get(phase, DEFAULT_PHASE_MODELS[phase])
        return resolve_model_id(model)

    # CLI argument takes precedence over non-auto metadata
    if cli_model:
        return resolve_model_id(cli_model)

    # Non-auto profile: use single model from metadata
    if metadata and metadata.get("model"):
        return resolve_model_id(metadata["model"])

    # Fall back to default phase configuration
    return resolve_model_id(DEFAULT_PHASE_MODELS[phase])


def get_phase_model_betas(
    spec_dir: Path,
    phase: Phase,
    cli_model: str | None = None,
) -> list[str]:
    """
    Get required SDK beta headers for the model selected for a specific phase.

    Uses the same priority logic as get_phase_model() to determine which model
    shorthand is selected, then looks up any required beta headers.

    Args:
        spec_dir: Path to the spec directory
        phase: Execution phase (spec, planning, coding, qa)
        cli_model: Model from CLI argument (optional)

    Returns:
        List of beta header strings, or empty list if none required
    """
    # Same precedence as get_phase_model: auto profile metadata wins over CLI.
    metadata = load_task_metadata(spec_dir)

    if metadata and metadata.get("isAutoProfile") and metadata.get("phaseModels"):
        phase_models = metadata["phaseModels"]
        model_short = phase_models.get(phase, DEFAULT_PHASE_MODELS[phase])
        return get_model_betas(model_short)

    if cli_model:
        return get_model_betas(cli_model)

    if metadata and metadata.get("model"):
        return get_model_betas(metadata["model"])

    return get_model_betas(DEFAULT_PHASE_MODELS[phase])


def get_phase_thinking(
    spec_dir: Path,
    phase: Phase,
    cli_thinking: str | None = None,
) -> str:
    """
    Get the thinking level for a specific execution phase.

    Priority:
    1. CLI argument (if provided)
    2. Phase-specific config from task_metadata.json (if auto profile)
    3. Single thinking level from task_metadata.json (if not auto profile)
    4. Default phase configuration

    Args:
        spec_dir: Path to the spec directory
        phase: Execution phase (spec, planning, coding, qa)
        cli_thinking: Thinking level from CLI argument (optional)

    Returns:
        Thinking level string
    """
    # Same precedence as get_phase_model: auto profile metadata wins over CLI.
    metadata = load_task_metadata(spec_dir)

    if metadata and metadata.get("isAutoProfile") and metadata.get("phaseThinking"):
        phase_thinking = metadata["phaseThinking"]
        return phase_thinking.get(phase, DEFAULT_PHASE_THINKING[phase])

    if cli_thinking:
        return cli_thinking

    if metadata and metadata.get("thinkingLevel"):
        return metadata["thinkingLevel"]

    return DEFAULT_PHASE_THINKING[phase]


def get_phase_thinking_budget(
    spec_dir: Path,
    phase: Phase,
    cli_thinking: str | None = None,
) -> int | None:
    """
    Get the thinking budget tokens for a specific execution phase.

    Args:
        spec_dir: Path to the spec directory
        phase: Execution phase (spec, planning, coding, qa)
        cli_thinking: Thinking level from CLI argument (optional)

    Returns:
        Token budget or None for no extended thinking
    """
    thinking_level = get_phase_thinking(spec_dir, phase, cli_thinking)
    return get_thinking_budget(thinking_level)


def get_phase_config(
    spec_dir: Path,
    phase: Phase,
    cli_model: str | None = None,
    cli_thinking: str | None = None,
) -> tuple[str, str, int | None]:
    """
    Get the full configuration for a specific execution phase.

    Args:
        spec_dir: Path to the spec directory
        phase: Execution phase (spec, planning, coding, qa)
        cli_model: Model from CLI argument (optional)
        cli_thinking: Thinking level from CLI argument (optional)

    Returns:
        Tuple of (model_id, thinking_level, thinking_budget)
    """
    model_id = get_phase_model(spec_dir, phase, cli_model)
    thinking_level = get_phase_thinking(spec_dir, phase, cli_thinking)
    thinking_budget = get_thinking_budget(thinking_level)

    return model_id, thinking_level, thinking_budget


def get_phase_client_thinking_kwargs(
    spec_dir: Path,
    phase: Phase,
    phase_model: str,
    cli_thinking: str | None = None,
) -> dict:
    """
    Get thinking kwargs for create_client() for a specific execution phase.

    Combines get_phase_thinking() and get_thinking_kwargs_for_model() to produce
    the correct kwargs dict based on phase config and model capabilities.

    Args:
        spec_dir: Path to the spec directory
        phase: Execution phase (spec, planning, coding, qa)
        phase_model: Resolved full model ID for this phase
        cli_thinking: Thinking level from CLI argument (optional)

    Returns:
        Dict with 'max_thinking_tokens' and optionally 'effort_level'
    """
    thinking_level = get_phase_thinking(spec_dir, phase, cli_thinking)
    return get_thinking_kwargs_for_model(phase_model, thinking_level)


def get_fast_mode(spec_dir: Path) -> bool:
    """
    Check if Fast Mode is enabled for this task.

    Fast Mode provides faster Opus 4.6 output at higher cost.
    Reads the fastMode flag from task_metadata.json.

    Args:
        spec_dir: Path to the spec directory

    Returns:
        True if Fast Mode is enabled, False otherwise
    """
    metadata = load_task_metadata(spec_dir)
    if metadata:
        enabled = bool(metadata.get("fastMode", False))
        if enabled:
            logger.info(
                "[Fast Mode] ENABLED — read fastMode=true from task_metadata.json"
            )
        else:
            logger.info("[Fast Mode] disabled — fastMode not set in task_metadata.json")
        return enabled
    logger.info("[Fast Mode] disabled — no task_metadata.json found")
    return False


def infer_provider_from_model(model: str) -> str:
    """
    Infer the LLM provider from the model ID.  Works for any phase.

    The provider is determined by the model string itself, so a separate
    provider setting is no longer needed.

    studio:* prefix -> 'openai-compatible' (Google AI Studio OpenAI-compatible endpoint)
    ollama:* prefix -> 'ollama'
    openai:* or openai-compatible:* prefix -> 'openai-compatible'
    Claude shorthands (opus, sonnet, haiku) or claude-* IDs -> 'claude'
    gpt-* or *codex* IDs -> 'codex'
    gemini-* IDs -> 'gemini'
    Otherwise -> check QA_LLM_PROVIDER env var, then default 'claude'

    Args:
        model: Model shorthand or full model ID

    Returns:
        Provider name string (e.g., "claude", "codex", "gemini", "ollama",
        "openai-compatible")
    """
    m = model.strip().lower()

    # Explicit prefix: "studio:model-name"
    if m.startswith("studio:"):
        return "openai-compatible"

    # Explicit prefix: "ollama:model-name"
    if m.startswith("ollama:"):
        return "ollama"

    # Explicit prefix: "copilot:model-name" (GitHub Copilot CLI). Must precede
    # the gpt-/codex check below — "copilot:gpt-5" is Copilot, not Codex.
    if m.startswith("copilot:"):
        return "copilot"

    # Explicit prefix for OpenAI-compatible endpoints (LM Studio, vLLM,
    # OpenRouter, Together, Groq, LocalAI, ...).  Connection details come
    # from env vars OPENAI_COMPATIBLE_BASE_URL / OPENAI_COMPATIBLE_API_KEY
    # or, in a later integration, from the user's saved llm_endpoints config.
    if m.startswith("openai:") or m.startswith("openai-compatible:"):
        return "openai-compatible"

    # Claude models: known shorthands or full claude-* IDs
    if m in MODEL_ID_MAP or m.startswith("claude-"):
        return "claude"

    # OpenAI Codex models
    if m.startswith("gpt-") or "codex" in m:
        return "codex"

    # Google Gemini models
    if m.startswith("gemini"):
        return "gemini"

    # Env fallback for unknown models (e.g., ollama custom models)
    env_provider = os.environ.get("QA_LLM_PROVIDER", "").strip()
    return env_provider or "claude"


# Backward compatibility alias
infer_qa_provider_from_model = infer_provider_from_model


def strip_provider_prefix(model: str) -> str:
    """Strip a leading ``ollama:``, ``openai:``, ``openai-compatible:``, or ``studio:`` prefix.

    The factory and providers expect a bare model name.  When a user picks
    ``openai-compatible:gpt-4o-mini``, the provider only needs ``gpt-4o-mini``.
    """
    for prefix in ("openai-compatible:", "openai:", "ollama:", "studio:", "copilot:"):
        if model.lower().startswith(prefix):
            return model[len(prefix):]
    return model


_LLM_ENDPOINTS_DB_PATH = Path.home() / ".pfactory" / "data.db"


def _load_openai_endpoint_by_label(label: str) -> dict | None:
    """Look up an llm_endpoint row by label.  Returns None if not found."""
    import sqlite3
    if not _LLM_ENDPOINTS_DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(_LLM_ENDPOINTS_DB_PATH))
        row = conn.execute(
            "SELECT base_url, api_key, default_model FROM llm_endpoints "
            "WHERE label = ? LIMIT 1",
            (label,),
        ).fetchone()
        conn.close()
        if row:
            return {"base_url": row[0], "api_key": row[1], "default_model": row[2]}
    except sqlite3.Error:
        pass
    return None


def _load_first_openai_endpoint() -> dict | None:
    """Return the oldest configured llm_endpoint — for single-endpoint users."""
    import sqlite3
    if not _LLM_ENDPOINTS_DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(_LLM_ENDPOINTS_DB_PATH))
        row = conn.execute(
            "SELECT base_url, api_key, default_model FROM llm_endpoints "
            "ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return {"base_url": row[0], "api_key": row[1], "default_model": row[2]}
    except sqlite3.Error:
        pass
    return None


def get_provider_extra_kwargs(provider_name: str, model: str) -> dict:
    """Return additional kwargs to pass to ``get_provider`` for non-trivial providers.

    For ``openai-compatible`` the provider needs ``base_url`` and ``api_key``
    on top of the model name.  Resolution order:

    1. ``studio:<model>`` — Google AI Studio native OpenAI-compatible endpoint.
    2. ``openai:<label>:<model>`` — look up endpoint by label, use that model
       (or the endpoint's default_model if no model specified).
    3. ``openai:<model>`` (single colon) — use the first/only configured
       endpoint with the given model name.
    4. No DB row at all — fall back to env vars
       (``OPENAI_COMPATIBLE_BASE_URL`` / ``OPENAI_COMPATIBLE_API_KEY`` /
       ``OPENAI_API_KEY``) for power users without the UI.

    Args:
        provider_name: Canonical provider name from ``infer_provider_from_model``.
        model: The original (possibly prefixed) model string.

    Returns:
        Dict of extra kwargs to spread into the ``get_provider`` call.
    """
    if provider_name != "openai-compatible":
        return {}

    stripped = strip_provider_prefix(model).strip()

    # Special handling for Google AI Studio
    if model.lower().startswith("studio:"):
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        api_key = (
            os.environ.get("GOOGLE_API_KEY", "").strip()
            or os.environ.get("GEMINI_API_KEY", "").strip()
            or None
        )
        return {
            "model": stripped or "gemini-2.5-flash",
            "base_url": base_url,
            "api_key": api_key,
        }

    # 1) "<label>:<model>" — disambiguate among multiple endpoints
    if ":" in stripped:
        label_part, model_part = stripped.split(":", 1)
        endpoint = _load_openai_endpoint_by_label(label_part.strip())
        if endpoint:
            return {
                "model": model_part.strip() or endpoint["default_model"],
                "base_url": endpoint["base_url"],
                "api_key": endpoint["api_key"],
            }

    # 2) Just a model name — use the first/only saved endpoint
    endpoint = _load_first_openai_endpoint()
    if endpoint:
        return {
            "model": stripped if stripped and stripped != "default"
                                else endpoint["default_model"],
            "base_url": endpoint["base_url"],
            "api_key": endpoint["api_key"],
        }

    # 3) No DB row at all — env-var fallback for power users / CLI usage
    base_url = (
        os.environ.get("OPENAI_COMPATIBLE_BASE_URL", "").strip()
        or "https://api.openai.com"
    )
    api_key = (
        os.environ.get("OPENAI_COMPATIBLE_API_KEY", "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
        or None
    )
    return {
        "model": stripped or "gpt-4o-mini",
        "base_url": base_url,
        "api_key": api_key,
    }


# Provider capabilities: which providers support agentic phases (file ops, code execution)
PROVIDER_AGENTIC_SUPPORT = {
    "claude", "codex", "gemini", "ollama", "openai-compatible",
}


def validate_phase_provider(phase: Phase, model: str) -> tuple[bool, str]:
    """
    Validate that the model/provider is compatible with the phase.

    Agentic phases (spec, planning, coding, qa_fixer) require providers that
    support file operations and code execution.  Providers in
    PROVIDER_AGENTIC_SUPPORT can handle these phases.

    Args:
        phase: Execution phase (spec, planning, coding, qa, qa_fixer)
        model: Model shorthand or full model ID

    Returns:
        Tuple of (is_valid, error_message).  error_message is empty when valid.
    """
    provider = infer_provider_from_model(model)
    agentic_phases: set[str] = {"spec", "planning", "coding", "qa_fixer"}
    if phase in agentic_phases and provider not in PROVIDER_AGENTIC_SUPPORT:
        return False, (
            f"Provider '{provider}' doesn't support agentic mode needed for "
            f"{phase} phase. Supported: {sorted(PROVIDER_AGENTIC_SUPPORT)}"
        )
    return True, ""


def get_spec_phase_thinking_budget(phase_name: str) -> int | None:
    """
    Get the thinking budget for a specific spec runner phase.

    This maps granular spec phases (discovery, spec_writing, etc.) to their
    appropriate thinking budgets based on SPEC_PHASE_THINKING_LEVELS.

    Args:
        phase_name: Name of the spec phase (e.g., 'discovery', 'spec_writing')

    Returns:
        Token budget for extended thinking, or None for no extended thinking
    """
    thinking_level = SPEC_PHASE_THINKING_LEVELS.get(phase_name, "medium")
    return get_thinking_budget(thinking_level)
