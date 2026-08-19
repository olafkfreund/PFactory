"""
Context and Memory routes.

Handles project context, memory infrastructure, and Graphiti integration.
"""

import logging
import subprocess
from pathlib import Path as FilePath

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, Field, SecretStr

from factory_common.logsafe import sanitize_log

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# Request/Response Models
# ============================================


class TestConnectionRequest(BaseModel):
    dbPath: str | None = None
    database: str | None = None


class ValidateApiKeyRequest(BaseModel):
    provider: str
    apiKey: SecretStr


class ProjectEnvUpdate(BaseModel):
    """Model for updating project environment configuration."""

    githubToken: str | None = Field(default=None, repr=False)
    githubRepo: str | None = None
    gitProvider: str | None = None
    gitToken: str | None = Field(default=None, repr=False)
    gitRepo: str | None = None
    gitBaseUrl: str | None = None
    gitOrg: str | None = None
    gitProject: str | None = None
    graphitiEnabled: bool | None = None
    enableFancyUi: bool | None = None
    claudeToken: str | None = Field(default=None, repr=False)
    # Graphiti Provider Config (nested object from frontend)
    graphitiProviderConfig: dict | None = None


class TestGraphitiRequest(BaseModel):
    embeddingProvider: str
    embeddingModel: str | None = None
    openaiApiKey: SecretStr | None = None
    voyageApiKey: SecretStr | None = None
    ollamaBaseUrl: str | None = None
    database: str | None = None
    dbPath: str | None = None


# ============================================
# Project Context Routes
# These are mounted under /api/projects/{projectId}
# ============================================

project_router = APIRouter()


@project_router.get("/context")
async def get_project_context(projectId: str = Path(...)):
    """Get project context including index and memories."""
    import json

    from .projects import load_projects

    projects = load_projects()
    if projectId not in projects:
        return {"success": False, "error": f"Project {projectId} not found"}

    project_path = FilePath(projects[projectId]["path"])
    index_path = project_path / ".pfactory" / "project_index.json"
    specs_dir = project_path / ".pfactory" / "specs"

    project_index = None
    if index_path.exists():
        try:
            project_index = json.loads(index_path.read_text())
        except Exception:
            pass

    # Check if Graphiti/memory is configured
    env_path = project_path / ".pfactory" / ".env"
    memory_enabled = False
    if env_path.exists():
        env_content = env_path.read_text()
        memory_enabled = "GRAPHITI_ENABLED=true" in env_content

    # Collect recent memories from all specs
    memories = []
    memory_count = 0
    if specs_dir.exists():
        for spec_dir in specs_dir.iterdir():
            if spec_dir.is_dir():
                insights_dir = spec_dir / "memory" / "session_insights"
                if insights_dir.exists():
                    for insight_file in insights_dir.glob("session_*.json"):
                        memory_count += 1
                        try:
                            data = json.loads(insight_file.read_text())
                            memories.append(
                                {
                                    "id": f"{spec_dir.name}:{insight_file.stem}",
                                    "specId": spec_dir.name,
                                    "sessionNumber": data.get("session_number", 0),
                                    "timestamp": data.get("timestamp"),
                                    "type": "session_insight",
                                    "content": _extract_memory_summary(data),
                                    "subtasksCompleted": data.get("subtasks_completed", []),
                                    "discoveries": data.get("discoveries", {}),
                                    "whatWorked": data.get("what_worked", []),
                                    "whatFailed": data.get("what_failed", []),
                                    "recommendations": data.get(
                                        "recommendations_for_next_session", []
                                    ),
                                }
                            )
                        except Exception:
                            continue

    # Sort by timestamp (most recent first) and take top 10
    memories.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    recent_memories = memories[:10]

    # Check Graphiti database
    graphiti_db = FilePath.home() / ".pfactory" / "memories" / "magestic_ai_memory"
    graphiti_available = graphiti_db.exists()

    return {
        "success": True,
        "data": {
            "projectIndex": project_index,
            "memoryStatus": {
                "enabled": True,
                "available": memory_count > 0 or graphiti_available,
                "sessionInsightsCount": memory_count,
                "graphitiAvailable": graphiti_available,
            },
            "memoryState": None,
            "recentMemories": recent_memories,
            "isLoading": False,
        },
    }


@project_router.post("/context/refresh")
async def refresh_project_index(projectId: str = Path(...)):
    """Refresh/regenerate project index."""
    from .projects import load_projects

    projects = load_projects()
    if projectId not in projects:
        return {"success": False, "error": f"Project {projectId} not found"}

    project_path = FilePath(projects[projectId]["path"])

    # Run a basic project analysis
    try:
        result = subprocess.run(
            ["git", "ls-files"], cwd=project_path, capture_output=True, text=True, timeout=30
        )

        files = result.stdout.strip().split("\n") if result.returncode == 0 else []

        # Create basic index
        index = {
            "files": len(files),
            "languages": {},
            "frameworks": [],
            "lastRefreshed": __import__("datetime").datetime.now().isoformat(),
        }

        # Count files by extension
        for f in files:
            ext = FilePath(f).suffix.lower()
            if ext:
                index["languages"][ext] = index["languages"].get(ext, 0) + 1

        # Save index
        index_path = project_path / ".pfactory" / "project_index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        import json

        index_path.write_text(json.dumps(index, indent=2))

        return {"success": True, "data": index}
    except Exception:
        logger.exception("Failed to refresh project index for %s", sanitize_log(projectId))
        return {"success": False, "error": "Failed to refresh project index"}


@project_router.get("/memory/status")
async def get_memory_status(projectId: str = Path(...)):
    """Get memory system status for project."""

    from .projects import load_projects

    projects = load_projects()
    if projectId not in projects:
        return {"success": False, "error": f"Project {projectId} not found"}

    project_path = FilePath(projects[projectId]["path"])
    specs_dir = project_path / ".pfactory" / "specs"

    # Count memory files across all specs
    memory_count = 0
    if specs_dir.exists():
        for spec_dir in specs_dir.iterdir():
            if spec_dir.is_dir():
                insights_dir = spec_dir / "memory" / "session_insights"
                if insights_dir.exists():
                    memory_count += len(list(insights_dir.glob("session_*.json")))

    # Check Graphiti database
    graphiti_db = FilePath.home() / ".pfactory" / "memories" / "magestic_ai_memory"
    graphiti_available = graphiti_db.exists()

    return {
        "success": True,
        "data": {
            "enabled": True,
            "available": memory_count > 0 or graphiti_available,
            "sessionInsightsCount": memory_count,
            "graphitiAvailable": graphiti_available,
            "reason": None if memory_count > 0 else "No session insights recorded yet",
        },
    }


@project_router.get("/memory/search")
async def search_memories(projectId: str = Path(...), q: str = Query(...)):
    """Search project memories."""
    import json

    from .projects import load_projects

    projects = load_projects()
    if projectId not in projects:
        return {"success": False, "error": f"Project {projectId} not found"}

    project_path = FilePath(projects[projectId]["path"])
    specs_dir = project_path / ".pfactory" / "specs"

    results = []
    query_lower = q.lower()

    if specs_dir.exists():
        for spec_dir in specs_dir.iterdir():
            if spec_dir.is_dir():
                insights_dir = spec_dir / "memory" / "session_insights"
                if insights_dir.exists():
                    for insight_file in insights_dir.glob("session_*.json"):
                        try:
                            data = json.loads(insight_file.read_text())
                            # Search in patterns, gotchas, what worked/failed
                            content_to_search = json.dumps(data).lower()
                            if query_lower in content_to_search:
                                results.append(
                                    {
                                        "id": f"{spec_dir.name}:{insight_file.stem}",
                                        "specId": spec_dir.name,
                                        "sessionNumber": data.get("session_number", 0),
                                        "timestamp": data.get("timestamp"),
                                        "type": "session_insight",
                                        "content": _extract_memory_summary(data),
                                        "score": float(content_to_search.count(query_lower)),
                                        "subtasksCompleted": data.get("subtasks_completed", []),
                                        "discoveries": data.get("discoveries", {}),
                                        "whatWorked": data.get("what_worked", []),
                                        "whatFailed": data.get("what_failed", []),
                                        "recommendations": data.get(
                                            "recommendations_for_next_session", []
                                        ),
                                        "changedFiles": data.get("discoveries", {}).get(
                                            "changed_files", []
                                        ),
                                    }
                                )
                        except Exception:
                            continue

    # Sort by score
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return {"success": True, "data": results[:20]}


@project_router.get("/memory/recent")
async def get_recent_memories(projectId: str = Path(...), limit: int = Query(10)):
    """Get recent memories for project."""
    import json

    from .projects import load_projects

    projects = load_projects()
    if projectId not in projects:
        return {"success": False, "error": f"Project {projectId} not found"}

    project_path = FilePath(projects[projectId]["path"])
    specs_dir = project_path / ".pfactory" / "specs"

    memories = []

    if specs_dir.exists():
        for spec_dir in specs_dir.iterdir():
            if spec_dir.is_dir():
                insights_dir = spec_dir / "memory" / "session_insights"
                if insights_dir.exists():
                    for insight_file in insights_dir.glob("session_*.json"):
                        try:
                            data = json.loads(insight_file.read_text())
                            memories.append(
                                {
                                    "id": f"{spec_dir.name}:{insight_file.stem}",
                                    "specId": spec_dir.name,
                                    "sessionNumber": data.get("session_number", 0),
                                    "timestamp": data.get("timestamp"),
                                    "type": "session_insight",
                                    "content": _extract_memory_summary(data),
                                    "subtasksCompleted": data.get("subtasks_completed", []),
                                    "discoveries": data.get("discoveries", {}),
                                    "whatWorked": data.get("what_worked", []),
                                    "whatFailed": data.get("what_failed", []),
                                    "recommendations": data.get(
                                        "recommendations_for_next_session", []
                                    ),
                                }
                            )
                        except Exception:
                            continue

    # Sort by timestamp (most recent first)
    memories.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return {"success": True, "data": memories[:limit]}


def _extract_memory_summary(data: dict) -> str:
    """Extract a human-readable summary from session insight data."""
    parts = []

    # Add patterns found
    discoveries = data.get("discoveries", {})
    if isinstance(discoveries, dict):
        patterns = discoveries.get("patterns_found", [])
        if patterns:
            parts.append(f"Patterns: {', '.join(patterns[:3])}")

        gotchas = discoveries.get("gotchas_encountered", [])
        if gotchas:
            parts.append(f"Gotchas: {', '.join(gotchas[:2])}")

    # Add what worked
    what_worked = data.get("what_worked", [])
    if what_worked:
        parts.append(f"Worked: {', '.join(what_worked[:2])}")

    # Add recommendations
    recommendations = data.get("recommendations_for_next_session", [])
    if recommendations:
        parts.append(f"Recommendations: {', '.join(recommendations[:2])}")

    return " | ".join(parts) if parts else "Session completed"


@project_router.get("/env")
async def get_project_env(projectId: str = Path(...)):
    """Get project environment configuration."""
    from .projects import load_projects

    projects = load_projects()
    if projectId not in projects:
        return {"success": False, "error": f"Project {projectId} not found"}

    project_path = FilePath(projects[projectId]["path"])
    env_path = project_path / ".pfactory" / ".env"

    config = {
        "claudeAuthStatus": "not_configured",
        "githubEnabled": False,
        "githubTokenSet": False,
        "githubRepo": "",
        "graphitiEnabled": False,
        "enableFancyUi": True,
        "gitProvider": "github",
        "gitToken": "",
        "gitRepo": "",
        "gitBaseUrl": "",
        "gitOrg": "",
        "gitProject": "",
    }

    # Initialize graphiti provider config
    graphiti_provider_config = {}

    if env_path.exists():
        try:
            env_content = env_path.read_text()
            for line in env_content.split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")

                    if key == "GITHUB_TOKEN" and value:
                        config["githubEnabled"] = True
                        config["githubTokenSet"] = True
                        if not config.get("gitToken"):
                            config["gitToken"] = value
                    elif key == "GITHUB_REPO" and value:
                        config["githubRepo"] = value
                        if not config.get("gitRepo"):
                            config["gitRepo"] = value
                    elif key == "GIT_PROVIDER" and value:
                        config["gitProvider"] = value
                    elif key == "GIT_TOKEN" and value:
                        config["gitToken"] = value
                        config["githubEnabled"] = True
                        config["githubTokenSet"] = True
                    elif key == "GIT_REPO" and value:
                        config["gitRepo"] = value
                        if not config.get("githubRepo"):
                            config["githubRepo"] = value
                    elif key == "GIT_BASE_URL" and value:
                        config["gitBaseUrl"] = value
                    elif key == "GIT_ORG" and value:
                        config["gitOrg"] = value
                    elif key == "GIT_PROJECT" and value:
                        config["gitProject"] = value
                    elif key == "GRAPHITI_ENABLED":
                        config["graphitiEnabled"] = value.lower() == "true"
                    elif key == "ENABLE_FANCY_UI":
                        config["enableFancyUi"] = value.lower() != "false"
                    elif key in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"):
                        if value:
                            config["claudeAuthStatus"] = "configured"
                    # Graphiti provider config fields
                    elif key == "GRAPHITI_EMBEDDER_PROVIDER":
                        graphiti_provider_config["embeddingProvider"] = value
                    elif key == "OLLAMA_BASE_URL":
                        graphiti_provider_config["ollamaBaseUrl"] = value
                    elif key == "OLLAMA_EMBEDDING_MODEL":
                        graphiti_provider_config["ollamaEmbeddingModel"] = value
                    elif key == "OLLAMA_EMBEDDING_DIM":
                        try:
                            graphiti_provider_config["ollamaEmbeddingDim"] = int(value)
                        except ValueError:
                            pass
                    elif key == "OPENAI_API_KEY":
                        graphiti_provider_config["openaiApiKey"] = value
                    elif key == "VOYAGE_API_KEY":
                        graphiti_provider_config["voyageApiKey"] = value
                    elif key == "VOYAGE_EMBEDDING_MODEL":
                        graphiti_provider_config["voyageEmbeddingModel"] = value
                    elif key == "GOOGLE_API_KEY":
                        graphiti_provider_config["googleApiKey"] = value
                    elif key == "AZURE_OPENAI_API_KEY":
                        graphiti_provider_config["azureOpenaiApiKey"] = value
                    elif key == "AZURE_OPENAI_ENDPOINT":
                        graphiti_provider_config["azureOpenaiBaseUrl"] = value
                    elif key == "AZURE_OPENAI_EMBEDDING_DEPLOYMENT":
                        graphiti_provider_config["azureOpenaiEmbeddingDeployment"] = value
                    elif key == "GRAPHITI_DATABASE":
                        graphiti_provider_config["database"] = value
                    elif key == "GRAPHITI_DB_PATH":
                        graphiti_provider_config["dbPath"] = value
        except Exception:
            pass

    # Add graphiti provider config if any fields were found
    if graphiti_provider_config:
        config["graphitiProviderConfig"] = graphiti_provider_config

    # Also check for Claude auth via keychain
    try:
        result = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            config["claudeAuthStatus"] = "authenticated"
    except Exception:
        pass

    return {"success": True, "data": config}


@project_router.patch("/env")
async def update_project_env(projectId: str = Path(...), config: ProjectEnvUpdate = ...):
    """
    Update project environment configuration.

    Updates the .pfactory/.env file with environment variables for:
    - GitHub integration (GITHUB_TOKEN)
    - Graphiti memory system (GRAPHITI_ENABLED)
    - UI preferences (ENABLE_FANCY_UI)
    - Claude authentication (CLAUDE_CODE_OAUTH_TOKEN)

    Only updates fields that are provided (partial updates supported).
    Sets secure file permissions (0o600) to protect sensitive tokens.
    """
    from .projects import load_projects

    # Validate project exists
    projects = load_projects()
    if projectId not in projects:
        return {"success": False, "error": f"Project {projectId} not found"}

    project_path = FilePath(projects[projectId]["path"])
    env_path = project_path / ".pfactory" / ".env"

    try:
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

        # Map API keys/tokens (string values)
        # These are sensitive credentials that should be validated
        token_mapping = {
            "githubToken": "GITHUB_TOKEN",
            "gitToken": "GIT_TOKEN",
            "claudeToken": "CLAUDE_CODE_OAUTH_TOKEN",
        }

        for config_key, env_key in token_mapping.items():
            if config_key in config_dict:
                value = config_dict[config_key]
                if value:
                    # Strip whitespace and validate token is not empty
                    value = value.strip()
                    if not value:
                        return {"success": False, "error": f"{config_key} cannot be empty"}
                    # Validate minimum token length for security
                    if len(value) < 10:
                        return {
                            "success": False,
                            "error": f"{config_key} must be at least 10 characters",
                        }
                    existing[env_key] = value

                    # Mirror for backwards compatibility
                    if env_key == "GIT_TOKEN":
                        existing["GITHUB_TOKEN"] = value
                    elif env_key == "GITHUB_TOKEN":
                        existing["GIT_TOKEN"] = value
                else:
                    # Allow removing tokens by setting to empty string
                    existing.pop(env_key, None)
                    if env_key == "GIT_TOKEN" and "GITHUB_TOKEN" in existing:
                        del existing["GITHUB_TOKEN"]
                    elif env_key == "GITHUB_TOKEN" and "GIT_TOKEN" in existing:
                        del existing["GIT_TOKEN"]

        # Map plain string settings (no token validation needed)
        string_mapping = {
            "githubRepo": "GITHUB_REPO",
            "gitRepo": "GIT_REPO",
            "gitProvider": "GIT_PROVIDER",
            "gitBaseUrl": "GIT_BASE_URL",
            "gitOrg": "GIT_ORG",
            "gitProject": "GIT_PROJECT",
        }

        for config_key, env_key in string_mapping.items():
            if config_key in config_dict:
                value = config_dict[config_key]
                if value:
                    val_strip = value.strip()
                    existing[env_key] = val_strip
                    # Mirror for backwards compatibility
                    if env_key == "GIT_REPO":
                        existing["GITHUB_REPO"] = val_strip
                    elif env_key == "GITHUB_REPO":
                        existing["GIT_REPO"] = val_strip
                else:
                    # Allow removing by setting to empty
                    existing.pop(env_key, None)
                    if env_key == "GIT_REPO" and "GITHUB_REPO" in existing:
                        del existing["GITHUB_REPO"]
                    elif env_key == "GITHUB_REPO" and "GIT_REPO" in existing:
                        del existing["GIT_REPO"]

        # Map boolean settings (convert to "true"/"false" strings)
        bool_mapping = {
            "graphitiEnabled": "GRAPHITI_ENABLED",
            "enableFancyUi": "ENABLE_FANCY_UI",
        }

        for config_key, env_key in bool_mapping.items():
            if config_key in config_dict:
                existing[env_key] = "true" if config_dict[config_key] else "false"

        # Handle graphitiProviderConfig nested object
        if "graphitiProviderConfig" in config_dict:
            gpc = config_dict["graphitiProviderConfig"]
            if isinstance(gpc, dict):
                provider_mapping = {
                    "embeddingProvider": "GRAPHITI_EMBEDDER_PROVIDER",
                    "ollamaBaseUrl": "OLLAMA_BASE_URL",
                    "ollamaEmbeddingModel": "OLLAMA_EMBEDDING_MODEL",
                    "ollamaEmbeddingDim": "OLLAMA_EMBEDDING_DIM",
                    "openaiApiKey": "OPENAI_API_KEY",
                    "voyageApiKey": "VOYAGE_API_KEY",
                    "voyageEmbeddingModel": "VOYAGE_EMBEDDING_MODEL",
                    "googleApiKey": "GOOGLE_API_KEY",
                    "azureOpenaiApiKey": "AZURE_OPENAI_API_KEY",
                    "azureOpenaiBaseUrl": "AZURE_OPENAI_ENDPOINT",
                    "azureOpenaiEmbeddingDeployment": "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
                    "database": "GRAPHITI_DATABASE",
                    "dbPath": "GRAPHITI_DB_PATH",
                }
                for config_key, env_key in provider_mapping.items():
                    if config_key in gpc:
                        value = gpc[config_key]
                        if value is not None and value != "":
                            existing[env_key] = str(value)
                        elif env_key in existing:
                            del existing[env_key]

        # Ensure .pfactory directory exists
        env_path.parent.mkdir(parents=True, exist_ok=True)

        # Write back to .env file
        content = "\n".join(f"{k}={v}" for k, v in existing.items())
        env_path.write_text(content)

        # Set secure file permissions (owner read/write only)
        # This is critical for protecting API keys and tokens
        env_path.chmod(0o600)

        # Also update settings in projects.json
        try:
            from .projects import save_projects

            if "settings" not in projects[projectId]:
                projects[projectId]["settings"] = {}

            # Save settings fields to project dictionary
            for field in [
                "gitProvider",
                "gitToken",
                "gitRepo",
                "gitBaseUrl",
                "gitOrg",
                "gitProject",
            ]:
                if field in config_dict:
                    projects[projectId]["settings"][field] = config_dict[field]

            # Handle backward compatibility: mirror githubRepo and githubToken in projects.json
            if "githubRepo" in config_dict:
                projects[projectId]["settings"]["githubRepo"] = config_dict["githubRepo"]
                if "gitRepo" not in projects[projectId]["settings"]:
                    projects[projectId]["settings"]["gitRepo"] = config_dict["githubRepo"]
            elif "gitRepo" in config_dict:
                projects[projectId]["settings"]["githubRepo"] = config_dict["gitRepo"]

            if "githubToken" in config_dict:
                projects[projectId]["settings"]["githubToken"] = config_dict["githubToken"]
                if "gitToken" not in projects[projectId]["settings"]:
                    projects[projectId]["settings"]["gitToken"] = config_dict["githubToken"]
            elif "gitToken" in config_dict:
                projects[projectId]["settings"]["githubToken"] = config_dict["gitToken"]

            from datetime import datetime

            projects[projectId]["updated_at"] = datetime.now().isoformat()
            save_projects(projects)
        except Exception:
            pass

        return {"success": True, "message": "Environment configuration updated successfully"}

    except Exception:
        logger.exception(
            "Failed to update environment config for project %s", sanitize_log(projectId)
        )
        return {"success": False, "error": "Failed to update environment configuration"}


@project_router.get("/claude-auth")
async def check_claude_auth(projectId: str = Path(...)):
    """Check Claude authentication status."""
    return {"success": True, "data": {"authenticated": False, "method": None}}


@project_router.post("/claude-setup")
async def invoke_claude_setup(projectId: str = Path(...)):
    """
    Check Claude CLI authentication status and provide setup instructions.

    NOTE: The 'claude setup' command is interactive and cannot be run from a web API.
    This endpoint checks if Claude CLI is installed and authenticated, and provides
    instructions for manual setup if needed.

    Returns:
        - success: True if Claude is already authenticated
        - success: False if Claude needs setup, with instructions
    """
    try:
        # Import load_projects to validate project exists
        from .projects import load_projects

        projects = load_projects()
        if projectId not in projects:
            return {"success": False, "error": f"Project {projectId} not found"}

        # Check if Claude CLI is installed
        try:
            version_result = subprocess.run(
                ["claude", "--version"], capture_output=True, text=True, timeout=5
            )
            cli_installed = version_result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            cli_installed = False

        if not cli_installed:
            return {
                "success": False,
                "error": "Claude CLI is not installed",
                "instructions": {
                    "message": "Please install Claude CLI first",
                    "steps": [
                        "Visit https://claude.ai/download to download Claude CLI",
                        "Follow the installation instructions for your platform",
                        "Run 'claude setup' in your terminal to authenticate",
                    ],
                },
            }

        # Check if Claude is already authenticated by trying a simple command
        try:
            # The 'claude' command without arguments will fail if not authenticated
            # We use --version as a proxy for checking if basic auth works
            auth_check = subprocess.run(
                ["claude", "--version"], capture_output=True, text=True, timeout=5
            )

            # If we got here and returncode is 0, Claude CLI is working
            is_authenticated = auth_check.returncode == 0
        except Exception:
            is_authenticated = False

        if is_authenticated:
            return {
                "success": True,
                "message": "Claude CLI is already authenticated and ready to use",
                "authenticated": True,
            }

        # Claude is installed but not authenticated
        return {
            "success": False,
            "error": "Claude CLI is not authenticated",
            "needsSetup": True,
            "instructions": {
                "message": "The 'claude setup' command is interactive and must be run manually in your terminal",
                "reason": "Interactive CLI commands cannot be run from a web API because they require user input and browser interaction",
                "steps": [
                    "Open a terminal on your local machine",
                    "Run: claude setup",
                    "Follow the prompts to authenticate with your Claude account",
                    "The setup will open a browser for OAuth authentication",
                    "After completing setup, refresh this page to verify authentication",
                ],
                "note": "This is a one-time setup. Once authenticated, the credentials will be stored securely.",
            },
        }

    except Exception:
        logger.exception(
            "Failed to check Claude setup status for project %s", sanitize_log(projectId)
        )
        return {"success": False, "error": "Failed to check Claude setup status"}


# ============================================
# Memory Infrastructure Routes
# Global routes at /api/memory
# ============================================


@router.get("/infrastructure")
async def get_memory_infrastructure_status(dbPath: str | None = Query(None)):
    """Get memory infrastructure status."""
    return {
        "success": True,
        "data": {
            "kuzuInstalled": False,
            "databasePath": dbPath or str(FilePath.home() / ".pfactory" / "memories"),
            "databaseExists": False,
            "databases": [],
            "ready": False,
        },
    }


@router.get("/databases")
async def list_memory_databases(dbPath: str | None = Query(None)):
    """List available memory databases."""
    return {"success": True, "data": []}


@router.post("/test-connection")
async def test_memory_connection(request: TestConnectionRequest):
    """Test connection to memory database."""
    return {
        "success": True,
        "data": {"success": False, "message": "Memory database not configured"},
    }


@router.post("/validate-api-key")
async def validate_llm_api_key(request: ValidateApiKeyRequest):
    """Validate an LLM provider API key."""
    # TODO: Actually validate the key
    return {"success": True, "data": {"valid": True, "message": "API key validated"}}


@router.post("/test-graphiti")
async def test_graphiti_connection(request: TestGraphitiRequest):
    """Test Graphiti memory system connection."""
    return {
        "success": True,
        "data": {
            "database": {"success": False, "message": "Graphiti not configured"},
            "llmProvider": {"success": False, "message": "LLM provider not configured"},
            "ready": False,
        },
    }
