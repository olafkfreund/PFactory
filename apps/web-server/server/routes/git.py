"""
Git, Ollama, MCP, and utility routes.
"""

import json
import shlex
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

router = APIRouter()


# ============================================
# Git Routes
# ============================================

def run_git_command(args: list[str], cwd: str) -> dict:
    """Run a git command and return result."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip()}
        return {"success": True, "output": result.stdout.strip()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/branches")
async def get_git_branches(path: str = Query(...)):
    """Get all branches for a repository."""
    result = run_git_command(["branch", "--format=%(refname:short)"], path)
    if result["success"]:
        branches = [b.strip() for b in result["output"].split("\n") if b.strip()]
        return {"success": True, "data": branches}
    return {"success": True, "data": []}


@router.get("/current-branch")
async def get_current_git_branch(path: str = Query(...)):
    """Get current branch name."""
    result = run_git_command(["branch", "--show-current"], path)
    if result["success"]:
        return {"success": True, "data": result["output"]}
    return {"success": True, "data": None}


@router.get("/main-branch")
async def detect_main_branch(path: str = Query(...)):
    """Detect the main branch (main or master)."""
    # Check for main first
    result = run_git_command(["rev-parse", "--verify", "main"], path)
    if result["success"]:
        return {"success": True, "data": "main"}

    # Check for master
    result = run_git_command(["rev-parse", "--verify", "master"], path)
    if result["success"]:
        return {"success": True, "data": "master"}

    return {"success": True, "data": None}


@router.get("/status")
async def check_git_status(path: str = Query(...)):
    """Check git status for a repository."""
    # Check if it's a git repo
    git_dir = Path(path) / ".git"
    if not git_dir.exists():
        return {
            "success": True,
            "data": {
                "isGitRepo": False,
                "hasCommits": False,
                "currentBranch": None
            }
        }

    # Get current branch
    branch_result = run_git_command(["branch", "--show-current"], path)
    current_branch = branch_result.get("output") if branch_result["success"] else None

    # Check for commits
    commit_result = run_git_command(["rev-parse", "HEAD"], path)
    has_commits = commit_result["success"]

    return {
        "success": True,
        "data": {
            "isGitRepo": True,
            "hasCommits": has_commits,
            "currentBranch": current_branch
        }
    }


class InitGitRequest(BaseModel):
    path: str


@router.post("/init")
async def initialize_git(request: InitGitRequest):
    """Initialize a new git repository with an initial commit (if needed)."""
    path = request.path
    git_dir = Path(path) / ".git"

    # Check if already a git repo
    is_git_repo = git_dir.exists()

    # Check if repo already has commits
    has_commits = False
    if is_git_repo:
        commit_check = run_git_command(["rev-parse", "HEAD"], path)
        has_commits = commit_check["success"]

    # If already has commits, nothing to do
    if has_commits:
        return {"success": True}

    # Initialize git repo if not already
    if not is_git_repo:
        result = run_git_command(["init"], path)
        if not result["success"]:
            return {"success": False, "error": result.get("error")}

    # Create .gitignore if it doesn't exist
    gitignore_path = Path(path) / ".gitignore"
    if not gitignore_path.exists():
        try:
            gitignore_path.write_text(
                "# Auto-generated gitignore\n"
                "node_modules/\n"
                ".env\n"
                ".env.local\n"
                "__pycache__/\n"
                "*.pyc\n"
                ".venv/\n"
                "venv/\n"
                ".pfactory/\n"
                "dist/\n"
                "build/\n"
            )
        except Exception:
            pass  # Not critical if this fails

    # Stage all files and create initial commit (only if no commits yet)
    run_git_command(["add", "-A"], path)
    run_git_command(
        ["commit", "-m", "Initial commit", "--allow-empty"],
        path
    )

    return {"success": True}


# ============================================
# Ollama Routes
# ============================================

ollama_router = APIRouter()


def check_ollama_running(base_url: str | None = None) -> bool:
    """Check if Ollama server is running."""
    import urllib.request
    url = base_url or "http://localhost:11434"
    try:
        urllib.request.urlopen(f"{url}/api/tags", timeout=5)
        return True
    except Exception:
        return False


@ollama_router.get("/status")
async def check_ollama_status(baseUrl: str | None = Query(None)):
    """Check Ollama server status."""
    running = check_ollama_running(baseUrl)
    return {
        "success": True,
        "data": {
            "running": running,
            "baseUrl": baseUrl or "http://localhost:11434"
        }
    }


@ollama_router.get("/installed")
async def check_ollama_installed():
    """Check if Ollama is installed."""
    ollama_path = shutil.which("ollama")
    return {"success": True, "data": {"installed": ollama_path is not None}}


@ollama_router.post("/install")
async def install_ollama():
    """Provide instructions to install Ollama."""
    return {
        "success": True,
        "data": {
            "message": "Install Ollama from https://ollama.ai"
        }
    }


@ollama_router.get("/models")
async def list_ollama_models(baseUrl: str | None = Query(None)):
    """List available Ollama models."""
    import json
    import urllib.request

    url = baseUrl or "http://localhost:11434"
    try:
        response = urllib.request.urlopen(f"{url}/api/tags", timeout=10)
        data = json.loads(response.read().decode())
        models = [m["name"] for m in data.get("models", [])]
        return {"success": True, "data": models}
    except Exception:
        return {"success": True, "data": []}


@ollama_router.get("/embedding-models")
async def list_ollama_embedding_models(baseUrl: str | None = Query(None)):
    """List Ollama embedding models with installation status."""
    import json
    import urllib.request

    url = baseUrl or "http://localhost:11434"

    # Get installed models from Ollama
    installed_models = set()
    try:
        response = urllib.request.urlopen(f"{url}/api/tags", timeout=10)
        data = json.loads(response.read().decode())
        for m in data.get("models", []):
            name = m.get("name", "")
            installed_models.add(name)
            # Also add without :latest suffix
            if name.endswith(":latest"):
                installed_models.add(name.replace(":latest", ""))
    except Exception:
        pass

    # Filter to embedding-capable models
    embedding_keywords = ["embed", "nomic", "minilm", "bge", "gte", "e5"]
    embedding_models = []

    for name in installed_models:
        name_lower = name.lower()
        if any(kw in name_lower for kw in embedding_keywords):
            embedding_models.append({"name": name, "installed": True})

    return {"success": True, "data": {"embedding_models": embedding_models}}


class PullModelRequest(BaseModel):
    modelName: str
    baseUrl: str | None = None


@ollama_router.post("/pull")
async def pull_ollama_model(request: PullModelRequest):
    """Pull an Ollama model."""
    import json
    import urllib.request

    url = request.baseUrl or "http://localhost:11434"
    model_name = request.modelName

    try:
        # Use Ollama's pull API
        req_data = json.dumps({"name": model_name, "stream": False}).encode()
        req = urllib.request.Request(
            f"{url}/api/pull",
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        # This is a blocking call - for large models consider background task
        response = urllib.request.urlopen(req, timeout=600)  # 10 min timeout
        result = json.loads(response.read().decode())

        # Check if pull was successful
        status = result.get("status", "")
        if "success" in status.lower() or status == "":
            return {"success": True, "data": {"status": "completed", "model": model_name}}
        else:
            return {"success": False, "error": f"Pull failed: {status}"}

    except urllib.error.URLError as e:
        return {"success": False, "error": f"Failed to connect to Ollama: {e}"}
    except Exception as e:
        return {"success": False, "error": f"Failed to pull model: {e}"}


# ============================================
# Claude Code CLI Routes
# ============================================

claude_code_router = APIRouter()


@claude_code_router.get("/version")
async def check_claude_code_version():
    """Check Claude Code CLI version.

    Returns data directly (not wrapped in {success, data}) because
    the frontend api-client.ts adds that wrapper automatically.

    Uses shutil.which for fast PATH lookup, falling back to login shell
    only when the binary isn't on the non-login PATH.
    """
    claude_path = shutil.which("claude")

    # Fallback: try login shell in case PATH is set in .bashrc/.profile
    if not claude_path:
        try:
            result = subprocess.run(
                ["bash", "-l", "-c", "which claude"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                claude_path = result.stdout.strip()
        except Exception:
            pass

    if claude_path:
        try:
            result = subprocess.run(
                [claude_path, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return {
                    "installed": result.stdout.strip(),
                    "latest": "unknown",
                    "isOutdated": False,
                    "path": claude_path,
                }
        except Exception:
            pass

    # Claude not found — check if Node.js is available (needed for install)
    node_available = shutil.which("node") is not None

    return {
        "installed": None,
        "latest": "unknown",
        "isOutdated": False,
        "path": None,
        "nodeAvailable": node_available,
    }


@claude_code_router.post("/install")
async def install_claude_code():
    """Install Claude Code CLI, including Node.js via fnm if needed.

    Installation steps:
    1. Check if claude is already installed (via login shell)
    2. Check if node/npm is available (via login shell)
    3. If no Node.js → install fnm (Fast Node Manager) + Node.js LTS
       - fnm installs to ~/.local/share/fnm/ and adds PATH to ~/.bashrc
       - No sudo needed — fully userspace
    4. Install Claude Code CLI via npm
    5. Verify installation

    All commands use `bash -l -c` so login profile PATH changes are visible.
    """
    import logging
    log = logging.getLogger(__name__)

    steps_completed: list[str] = []

    def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
        """Run a command inside a login shell.

        Takes an argument list (not a raw string) to prevent shell injection.
        Arguments are joined with shlex.quote() for safe shell execution.
        """
        safe_cmd = " ".join(shlex.quote(a) for a in args)
        return subprocess.run(
            ["bash", "-l", "-c", safe_cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    # Step 1: Check if claude is already installed
    try:
        result = _run(["claude", "--version"], timeout=10)
        if result.returncode == 0:
            return {
                "success": True,
                "data": {
                    "message": "Claude Code CLI is already installed",
                    "version": result.stdout.strip(),
                    "steps_completed": ["already-installed"],
                },
            }
    except Exception:
        pass

    # Step 2: Check if Node.js is available
    node_available = False
    try:
        result = _run(["node", "--version"], timeout=10)
        node_available = result.returncode == 0
        if node_available:
            steps_completed.append("node-present")
            log.info(f"Node.js already available: {result.stdout.strip()}")
    except Exception:
        pass

    # Step 3: Install fnm + Node.js LTS if not available
    if not node_available:
        log.info("Node.js not found — installing fnm + Node.js LTS")

        # 3a: Install fnm
        try:
            # Shell pipeline — cannot be split into an arg list.
            # Hardcoded URL, no user input, safe to pass as raw shell command.
            result = subprocess.run(
                ["bash", "-l", "-c", "curl -fsSL https://fnm.vercel.app/install | bash"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed at step 'Install fnm': {result.stderr.strip()}",
                }
            steps_completed.append("fnm")
            log.info("fnm installed successfully")
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "fnm installation timed out (60s)"}
        except Exception as e:
            return {"success": False, "error": f"Failed at step 'Install fnm': {e}"}

        # 3b: Install Node.js LTS via fnm
        try:
            result = _run(["fnm", "install", "--lts"], timeout=120)
            if result.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed at step 'Install Node.js': {result.stderr.strip()}",
                }
            steps_completed.append("node")
            log.info("Node.js LTS installed via fnm")
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Node.js installation timed out (120s)"}
        except Exception as e:
            return {"success": False, "error": f"Failed at step 'Install Node.js': {e}"}

        # 3c: Set fnm default so login shells pick it up
        try:
            _run(["fnm", "default", "lts-latest"], timeout=10)
        except Exception:
            pass  # Non-critical

        # Verify node is now available
        try:
            result = _run(["node", "--version"], timeout=10)
            if result.returncode != 0:
                return {
                    "success": False,
                    "error": "Node.js installed but not found in PATH after fnm setup",
                }
            log.info(f"Node.js verified: {result.stdout.strip()}")
        except Exception as e:
            return {
                "success": False,
                "error": f"Node.js installed but verification failed: {e}",
            }

    # Step 4: Install Claude Code CLI
    try:
        log.info("Installing Claude Code CLI via npm...")
        result = _run(
            ["npm", "install", "-g", "@anthropic-ai/claude-code"],
            timeout=180,
        )
        if result.returncode != 0:
            return {
                "success": False,
                "error": f"Failed at step 'Install Claude Code': {result.stderr.strip()}",
            }
        steps_completed.append("claude-code")
        log.info("Claude Code CLI installed")
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "npm install timed out (180s)"}
    except Exception as e:
        return {"success": False, "error": f"Failed at step 'Install Claude Code': {e}"}

    # Step 5: Verify installation
    version_str = "unknown"
    try:
        result = _run(["claude", "--version"], timeout=10)
        if result.returncode == 0:
            version_str = result.stdout.strip()
        else:
            return {
                "success": False,
                "error": f"Installation completed but verification failed: {result.stderr.strip()}",
            }
    except Exception as e:
        return {
            "success": False,
            "error": f"Installation completed but verification failed: {e}",
        }

    return {
        "success": True,
        "data": {
            "message": "Claude Code CLI installed successfully",
            "version": version_str,
            "steps_completed": steps_completed,
        },
    }


# ============================================
# MCP Routes
# ============================================

mcp_router = APIRouter()

# Catalog of well-known MCP servers and the system binary they require.
# "requires_binary": None means only npx is needed.
_MCP_CATALOG = [
    {
        "id": "mcp-postgres",
        "name": "PostgreSQL",
        "description": "Query and explore PostgreSQL databases",
        "category": "database",
        "type": "command",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres"],
        "requires_binary": "psql",
        "package": "@modelcontextprotocol/server-postgres",
    },
    {
        "id": "mcp-sqlite",
        "name": "SQLite",
        "description": "Query and explore SQLite databases",
        "category": "database",
        "type": "command",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sqlite"],
        "requires_binary": "sqlite3",
        "package": "@modelcontextprotocol/server-sqlite",
    },
    {
        "id": "mcp-mysql",
        "name": "MySQL",
        "description": "Query and explore MySQL/MariaDB databases",
        "category": "database",
        "type": "command",
        "command": "npx",
        "args": ["-y", "mcp-mysql-server"],
        "requires_binary": "mysql",
        "package": "mcp-mysql-server",
    },
    {
        "id": "mcp-puppeteer",
        "name": "Puppeteer (legacy)",
        "description": "Browser automation via Puppeteer (use Playwright instead)",
        "category": "browser",
        "type": "command",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        "requires_binary": None,
        "package": "@modelcontextprotocol/server-puppeteer",
    },
    {
        "id": "mcp-playwright",
        "name": "Playwright",
        "description": "Cross-browser automation via Playwright",
        "category": "browser",
        "type": "command",
        "command": "npx",
        "args": ["-y", "@playwright/mcp"],
        "requires_binary": None,
        "detect_binary": "playwright",
        "package": "@playwright/mcp",
    },
    {
        "id": "mcp-brave-search",
        "name": "Brave Search",
        "description": "Web and local search via Brave Search API",
        "category": "search",
        "type": "command",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "requires_binary": None,
        "package": "@modelcontextprotocol/server-brave-search",
    },
    {
        "id": "mcp-docker",
        "name": "Docker",
        "description": "Manage Docker containers, images, and volumes",
        "category": "devops",
        "type": "command",
        "command": "docker",
        "args": ["mcp"],
        "requires_binary": "docker",
        "package": None,
    },
    {
        "id": "mcp-kubernetes",
        "name": "Kubernetes",
        "description": "Manage Kubernetes clusters and workloads",
        "category": "devops",
        "type": "command",
        "command": "npx",
        "args": ["-y", "mcp-server-kubernetes"],
        "requires_binary": "kubectl",
        "package": "mcp-server-kubernetes",
    },
    {
        "id": "mcp-aws",
        "name": "AWS",
        "description": "Interact with AWS services via CLI",
        "category": "devops",
        "type": "command",
        "command": "npx",
        "args": ["-y", "mcp-server-aws"],
        "requires_binary": "aws",
        "package": "mcp-server-aws",
    },
    {
        "id": "mcp-slack",
        "name": "Slack",
        "description": "Read and post Slack messages",
        "category": "communication",
        "type": "command",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "requires_binary": None,
        "package": "@modelcontextprotocol/server-slack",
    },
    {
        "id": "mcp-redis",
        "name": "Redis",
        "description": "Interact with Redis key-value store",
        "category": "database",
        "type": "command",
        "command": "npx",
        "args": ["-y", "mcp-server-redis"],
        "requires_binary": "redis-cli",
        "package": "mcp-server-redis",
    },
    {
        "id": "mcp-google-maps",
        "name": "Google Maps",
        "description": "Geocoding, directions, and place search",
        "category": "search",
        "type": "command",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-google-maps"],
        "requires_binary": None,
        "package": "@modelcontextprotocol/server-google-maps",
    },
]


# Templates that overlap with built-in app features (skip in detect results)
_HIDDEN_TEMPLATE_IDS = {"mcp-puppeteer", "mcp-playwright"}


def _check_binary(binary: str) -> bool:
    """Check if a binary is available on PATH."""
    import shutil
    return shutil.which(binary) is not None


def _check_npm_package_installed(package: str) -> bool:
    """Check if an npm package is installed globally."""
    import shutil
    import subprocess
    if not shutil.which("npm"):
        return False
    try:
        result = subprocess.run(
            ["npm", "list", "-g", "--depth=0", package],
            capture_output=True, text=True, timeout=8,
        )
        return result.returncode == 0 and package in result.stdout
    except Exception:
        return False



class McpServerConfig(BaseModel):
    id: str
    name: str
    type: str  # 'command' or 'http'
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    headers: dict | None = None


@mcp_router.post("/health")
async def check_mcp_health(server: McpServerConfig):
    """Check health of an MCP server."""
    if server.type == "http" and server.url:
        import urllib.request
        try:
            req = urllib.request.Request(server.url, method="HEAD")
            if server.headers:
                for key, value in server.headers.items():
                    req.add_header(key, value)
            urllib.request.urlopen(req, timeout=5)
            return {
                "success": True,
                "data": {
                    "serverId": server.id,
                    "status": "healthy",
                    "message": "Server responded"
                }
            }
        except Exception as e:
            return {
                "success": True,
                "data": {
                    "serverId": server.id,
                    "status": "unhealthy",
                    "message": str(e)
                }
            }

    return {
        "success": True,
        "data": {
            "serverId": server.id,
            "status": "unknown",
            "message": "Cannot check command-based servers"
        }
    }


@mcp_router.post("/test-connection")
async def test_mcp_connection(server: McpServerConfig):
    """Test full MCP connection."""
    # TODO: Actually test MCP protocol
    return {
        "success": True,
        "data": {
            "serverId": server.id,
            "success": False,
            "message": "MCP testing not implemented",
            "tools": []
        }
    }


@mcp_router.get("/detect")
async def detect_mcp_services():
    """Detect pre-installed services and CLIs that can be used as MCP servers."""
    import shutil

    has_npx = shutil.which("npx") is not None
    results = []

    for entry in _MCP_CATALOG:
        if entry["id"] in _HIDDEN_TEMPLATE_IDS:
            continue

        req = entry.get("requires_binary")
        detect_bin = entry.get("detect_binary")

        # Determine availability
        if req is not None:
            available = _check_binary(req)
            reason = f"{req} detected" if available else f"{req} not found"
        else:
            available = has_npx
            reason = "npx available" if has_npx else "npx not found"

        # Optionally check if there's a hint binary (not required, just nice to have)
        hint_installed = False
        if detect_bin:
            hint_installed = _check_binary(detect_bin)

        # Check if the npm package is already installed globally (only if npx-based)
        pkg = entry.get("package")
        npm_installed = False
        if pkg and available:
            npm_installed = _check_npm_package_installed(pkg)

        results.append({
            "id": entry["id"],
            "name": entry["name"],
            "description": entry["description"],
            "category": entry["category"],
            "type": entry["type"],
            "command": entry["command"],
            "args": entry["args"],
            "available": available,
            "installed": npm_installed or hint_installed,
            "reason": reason,
        })

    return {"success": True, "data": results}



# ============================================
# Updates Routes
# ============================================

updates_router = APIRouter()


@updates_router.get("/source/check")
async def check_source_update():
    """Check for Magestic AI source updates."""
    return {
        "success": True,
        "data": {
            "updateAvailable": False,
            "currentVersion": "1.0.0",
            "latestVersion": "1.0.0"
        }
    }


@updates_router.post("/source/download")
async def download_source_update():
    """
    Download Magestic AI source update via git pull.

    This endpoint updates the Magestic AI application source code by performing
    a git pull from the configured remote repository.

    Returns:
        dict: Response with success status and update details

    Raises:
        Returns error response if:
        - Not a git repository
        - Has uncommitted changes (warns user)
        - Git operations fail
        - No remote configured
    """
    try:
        # Get Magestic AI source directory (project root)
        # From git.py: __file__.parent = routes/, .parent.parent = server/,
        # .parent.parent.parent = web-server/, .parent.parent.parent.parent = apps/,
        # .parent.parent.parent.parent.parent = PFactory/ (project root)
        source_dir = Path(__file__).parent.parent.parent.parent.parent
        source_path = str(source_dir.resolve())

        # Check if it's a git repository
        git_dir = source_dir / ".git"
        if not git_dir.exists():
            return {
                "success": False,
                "error": "Magestic AI source directory is not a git repository"
            }

        # Check for uncommitted changes
        status_result = run_git_command(["status", "--porcelain"], source_path)
        if not status_result["success"]:
            return {
                "success": False,
                "error": f"Failed to check git status: {status_result.get('error', 'Unknown error')}"
            }

        has_changes = bool(status_result.get("output", "").strip())

        # Check current branch
        branch_result = run_git_command(["branch", "--show-current"], source_path)
        if not branch_result["success"]:
            return {
                "success": False,
                "error": f"Failed to get current branch: {branch_result.get('error', 'Unknown error')}"
            }

        current_branch = branch_result.get("output", "unknown").strip()

        # Check if remote exists
        remote_result = run_git_command(["remote", "-v"], source_path)
        if not remote_result["success"] or not remote_result.get("output", "").strip():
            return {
                "success": False,
                "error": "No git remote configured. Cannot update source."
            }

        # Fetch updates from remote
        fetch_result = run_git_command(["fetch", "origin"], source_path)
        if not fetch_result["success"]:
            return {
                "success": False,
                "error": f"Failed to fetch updates: {fetch_result.get('error', 'Unknown error')}"
            }

        # Check if updates are available
        # Compare local HEAD with remote branch
        ahead_behind_result = run_git_command(
            ["rev-list", "--left-right", "--count", f"HEAD...origin/{current_branch}"],
            source_path
        )

        updates_available = False
        commits_behind = 0

        if ahead_behind_result["success"]:
            output = ahead_behind_result.get("output", "").strip()
            if output:
                parts = output.split()
                if len(parts) >= 2:
                    commits_behind = int(parts[1])
                    updates_available = commits_behind > 0

        # If no updates available, return early
        if not updates_available:
            return {
                "success": True,
                "message": "Magestic AI is already up to date",
                "data": {
                    "updated": False,
                    "currentBranch": current_branch,
                    "hasUncommittedChanges": has_changes,
                    "commitsBehind": 0
                }
            }

        # Warn if there are uncommitted changes
        if has_changes:
            return {
                "success": False,
                "error": "Cannot update: You have uncommitted changes. Please commit or stash them first.",
                "data": {
                    "hasUncommittedChanges": True,
                    "currentBranch": current_branch,
                    "commitsBehind": commits_behind
                }
            }

        # Perform git pull
        pull_result = run_git_command(["pull", "origin", current_branch], source_path)
        if not pull_result["success"]:
            return {
                "success": False,
                "error": f"Failed to pull updates: {pull_result.get('error', 'Unknown error')}"
            }

        # Get updated commit info
        commit_result = run_git_command(["rev-parse", "--short", "HEAD"], source_path)
        new_commit = commit_result.get("output", "unknown").strip() if commit_result["success"] else "unknown"

        return {
            "success": True,
            "message": f"Magestic AI updated successfully to commit {new_commit}",
            "data": {
                "updated": True,
                "currentBranch": current_branch,
                "newCommit": new_commit,
                "commitsPulled": commits_behind,
                "pullOutput": pull_result.get("output", "")
            }
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to update Magestic AI source: {str(e)}"
        }


@updates_router.get("/source/version")
async def get_source_version():
    """Get current Magestic AI source version."""
    return {"success": True, "data": {"version": "1.0.0"}}


# ============================================
# Release Routes
# Project-specific at /api/projects/{projectId}/releases
# ============================================

releases_router = APIRouter()


@releases_router.get("/versions")
async def get_releaseable_versions(projectId: str):
    """Get versions that can be released."""
    return {"success": True, "data": []}


# ============================================
# Project-specific Git Operations
# ============================================

project_router = APIRouter()


class SquashCommitsRequest(BaseModel):
    """Request model for squashing commits."""
    count: int = Field(..., ge=2, description="Number of commits to squash (minimum 2)")
    message: str | None = Field(None, description="Custom commit message for the squashed commit")


@project_router.post("/{projectId}/git/squash")
async def squash_commits(projectId: str, request: SquashCommitsRequest):
    """Squash multiple commits into a single commit.

    This endpoint uses git reset --soft to safely squash commits without
    requiring interactive rebase. This approach:
    - Resets the branch pointer back N commits
    - Keeps all changes staged
    - Creates a new commit with all the squashed changes

    Args:
        projectId: Project ID
        request: Squash request with commit count and optional message

    Returns:
        Success response with confirmation message

    Raises:
        HTTPException: If project not found or git operations fail
    """
    from fastapi import HTTPException

    # Load projects to get project path
    try:
        from ..config import get_settings
        settings = get_settings()
        projects_file = settings.projects_file

        if not projects_file.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Project {projectId} not found"
            )

        with open(projects_file) as f:
            projects_data = json.loads(f.read())

        # Find project by ID
        project = None
        for proj in projects_data.get("projects", []):
            if proj.get("id") == projectId:
                project = proj
                break

        if not project:
            raise HTTPException(
                status_code=404,
                detail=f"Project {projectId} not found"
            )

        project_path = project.get("path")
        if not project_path:
            raise HTTPException(
                status_code=404,
                detail=f"Project path not found for {projectId}"
            )

    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": f"Failed to load project: {str(e)}"}

    # Validate commit count
    count = request.count
    if count < 2:
        return {"success": False, "error": "Must squash at least 2 commits"}

    # Check if repository has enough commits
    commit_count_result = run_git_command(
        ["rev-list", "--count", "HEAD"],
        project_path
    )

    if not commit_count_result["success"]:
        return {"success": False, "error": "Failed to count commits"}

    try:
        total_commits = int(commit_count_result["output"])
        if total_commits < count:
            return {
                "success": False,
                "error": f"Repository only has {total_commits} commit(s), cannot squash {count}"
            }
    except ValueError:
        return {"success": False, "error": "Invalid commit count"}

    # Get the current branch name
    branch_result = run_git_command(["branch", "--show-current"], project_path)
    if not branch_result["success"]:
        return {"success": False, "error": "Failed to get current branch"}

    current_branch = branch_result["output"]

    # Check for uncommitted changes
    status_result = run_git_command(["status", "--porcelain"], project_path)
    if status_result["success"] and status_result["output"].strip():
        return {
            "success": False,
            "error": "Cannot squash with uncommitted changes. Please commit or stash your changes first."
        }

    # Get the commit message of the oldest commit to be squashed (for default message)
    oldest_commit_msg_result = run_git_command(
        ["log", f"HEAD~{count-1}", "-1", "--format=%s"],
        project_path
    )

    # Get the commit message of the newest commit
    newest_commit_msg_result = run_git_command(
        ["log", "HEAD", "-1", "--format=%s"],
        project_path
    )

    # Determine the commit message
    if request.message:
        commit_message = request.message.strip()
    else:
        # Default message: combine first and last commit messages
        oldest_msg = oldest_commit_msg_result.get("output", "").strip() if oldest_commit_msg_result["success"] else ""
        newest_msg = newest_commit_msg_result.get("output", "").strip() if newest_commit_msg_result["success"] else ""

        if oldest_msg and newest_msg and oldest_msg != newest_msg:
            commit_message = f"{oldest_msg} ... {newest_msg}"
        elif newest_msg:
            commit_message = newest_msg
        elif oldest_msg:
            commit_message = oldest_msg
        else:
            commit_message = f"Squashed {count} commits"

    # Step 1: Reset soft to HEAD~<count> (keeps changes staged)
    reset_result = run_git_command(
        ["reset", "--soft", f"HEAD~{count}"],
        project_path
    )

    if not reset_result["success"]:
        return {
            "success": False,
            "error": f"Failed to reset commits: {reset_result.get('error')}"
        }

    # Step 2: Create new commit with all the squashed changes
    commit_result = run_git_command(
        ["commit", "-m", commit_message],
        project_path
    )

    if not commit_result["success"]:
        # Try to recover by resetting back
        run_git_command(["reset", "ORIG_HEAD"], project_path)
        return {
            "success": False,
            "error": f"Failed to create squashed commit: {commit_result.get('error')}"
        }

    return {
        "success": True,
        "message": f"Successfully squashed {count} commits on branch '{current_branch}'",
        "commitMessage": commit_message
    }


class CreateWorktreeRequest(BaseModel):
    """Request model for creating a git worktree."""
    name: str = Field(..., min_length=1, max_length=100, description="Worktree name (used for directory and branch)")
    baseBranch: str | None = Field(None, description="Base branch to create worktree from (defaults to current branch)")
    createBranch: bool = Field(True, description="Whether to create a new branch for the worktree")


@project_router.post("/{projectId}/git/worktree")
async def create_worktree(projectId: str, request: CreateWorktreeRequest):
    """Create a git worktree for parallel task work.

    Git worktrees allow you to check out multiple branches simultaneously in different
    directories. This is useful for working on multiple features/tasks in parallel without
    switching branches in your main repository.

    The worktree will be created in:
    - Path: .pfactory/worktrees/tasks/{name}
    - Branch: pfactory/tasks/{name} (if createBranch is true)

    Args:
        projectId: Project ID
        request: Worktree creation request with name and options

    Returns:
        Success response with worktree path and branch information

    Raises:
        HTTPException: If project not found or git operations fail
    """
    import re

    from fastapi import HTTPException

    # Load projects to get project path
    try:
        from ..config import get_settings
        settings = get_settings()
        projects_file = settings.projects_file

        if not projects_file.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Project {projectId} not found"
            )

        with open(projects_file) as f:
            projects_data = json.loads(f.read())

        # Find project by ID
        project = None
        for proj in projects_data.get("projects", []):
            if proj.get("id") == projectId:
                project = proj
                break

        if not project:
            raise HTTPException(
                status_code=404,
                detail=f"Project {projectId} not found"
            )

        project_path = project.get("path")
        if not project_path:
            raise HTTPException(
                status_code=404,
                detail=f"Project path not found for {projectId}"
            )

    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": f"Failed to load project: {str(e)}"}

    # Validate worktree name (alphanumeric, dashes, underscores only)
    name = request.name.strip()
    if not name:
        return {"success": False, "error": "Worktree name cannot be empty"}

    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return {
            "success": False,
            "error": "Worktree name must contain only letters, numbers, dashes, and underscores"
        }

    # Determine base branch
    if request.baseBranch:
        base_branch = request.baseBranch.strip()
        # Verify base branch exists
        branch_check = run_git_command(
            ["rev-parse", "--verify", base_branch],
            project_path
        )
        if not branch_check["success"]:
            return {
                "success": False,
                "error": f"Base branch '{base_branch}' does not exist"
            }
    else:
        # Use current branch as base
        current_branch_result = run_git_command(
            ["branch", "--show-current"],
            project_path
        )
        if not current_branch_result["success"]:
            return {"success": False, "error": "Failed to get current branch"}
        base_branch = current_branch_result["output"]

    # Create worktree path: .pfactory/worktrees/tasks/{name}
    worktrees_base = Path(project_path) / ".pfactory" / "worktrees" / "tasks"
    worktree_path = worktrees_base / name

    # Check if worktree path already exists
    if worktree_path.exists():
        return {
            "success": False,
            "error": f"Worktree path already exists: {worktree_path}"
        }

    # Create parent directories
    try:
        worktrees_base.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to create worktree directory: {str(e)}"
        }

    # Build git worktree add command
    worktree_branch = f"pfactory/tasks/{name}" if request.createBranch else None

    if request.createBranch:
        # Check if branch already exists
        branch_exists_check = run_git_command(
            ["rev-parse", "--verify", worktree_branch],
            project_path
        )
        if branch_exists_check["success"]:
            return {
                "success": False,
                "error": f"Branch '{worktree_branch}' already exists. Use a different worktree name or set createBranch to false."
            }

        # Create worktree with new branch
        # git worktree add <path> -b <new-branch> <base-branch>
        worktree_result = run_git_command(
            ["worktree", "add", str(worktree_path), "-b", worktree_branch, base_branch],
            project_path
        )
    else:
        # Create worktree without new branch (checkout existing base branch)
        # git worktree add <path> <base-branch>
        worktree_result = run_git_command(
            ["worktree", "add", str(worktree_path), base_branch],
            project_path
        )

    if not worktree_result["success"]:
        # Clean up directory if it was created
        try:
            if worktree_path.exists():
                import shutil
                shutil.rmtree(worktree_path)
        except Exception:
            pass

        return {
            "success": False,
            "error": f"Failed to create worktree: {worktree_result.get('error')}"
        }

    return {
        "success": True,
        "message": f"Worktree '{name}' created successfully",
        "worktreePath": str(worktree_path),
        "branch": worktree_branch if request.createBranch else base_branch,
        "baseBranch": base_branch
    }


class PreflightRequest(BaseModel):
    version: str


@releases_router.post("/preflight")
async def run_release_preflight(projectId: str, request: PreflightRequest):
    """Run preflight checks for a release."""
    return {
        "success": True,
        "data": {
            "passed": True,
            "checks": []
        }
    }


class CreateReleaseRequest(BaseModel):
    projectId: str
    version: str
    releaseNotes: str
    platform: str = "github"


def run_gh_command(args: list[str], cwd: str) -> dict:
    """Run a gh CLI command and return result.

    Args:
        args: Command arguments (without 'gh' prefix)
        cwd: Working directory for command execution

    Returns:
        Dict with success status, output or error message
    """
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip()}
        return {"success": True, "output": result.stdout.strip()}
    except FileNotFoundError:
        return {"success": False, "error": "GitHub CLI (gh) not installed"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@releases_router.post("")
async def create_release(projectId: str, request: CreateReleaseRequest):
    """Create a release using GitHub (gh) CLI.

    This endpoint creates a release on GitHub by:
    1. Validating the project exists
    2. Getting the project path
    3. Validating the version and release notes
    4. Executing the gh CLI command
    5. Creating the release with the specified version and notes

    Args:
        projectId: Project ID
        request: Release request with version, notes, and platform

    Returns:
        Success response with confirmation message and release details

    Raises:
        HTTPException: If project not found or CLI command fails
    """
    from fastapi import HTTPException

    # Validate platform
    platform = request.platform.lower()
    if platform != "github":
        return {
            "success": False,
            "error": f"Invalid platform '{request.platform}'. Must be 'github'"
        }

    # Validate version
    version = request.version.strip() if request.version else ""
    if not version:
        return {"success": False, "error": "Version cannot be empty"}

    # Validate release notes
    release_notes = request.releaseNotes.strip() if request.releaseNotes else ""
    if not release_notes:
        return {"success": False, "error": "Release notes cannot be empty"}

    # Load projects to get project path
    try:
        from ..config import get_settings
        settings = get_settings()
        projects_file = settings.projects_file

        if not projects_file.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Project {projectId} not found"
            )

        with open(projects_file) as f:
            projects_data = json.loads(f.read())

        # Find project by ID
        project = None
        for proj in projects_data.get("projects", []):
            if proj.get("id") == projectId:
                project = proj
                break

        if not project:
            raise HTTPException(
                status_code=404,
                detail=f"Project {projectId} not found"
            )

        project_path = project.get("path")
        if not project_path:
            raise HTTPException(
                status_code=404,
                detail=f"Project path not found for {projectId}"
            )

    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": f"Failed to load project: {str(e)}"}

    # Ensure version starts with 'v' if not already present (conventional)
    if not version.startswith('v'):
        version_tag = f"v{version}"
    else:
        version_tag = version

    # Create release
    try:
        # GitHub: gh release create <tag> --notes <notes>
        result = run_gh_command(
            ["release", "create", version_tag, "--notes", release_notes],
            project_path
        )

        if not result["success"]:
            return {
                "success": False,
                "error": f"Failed to create GitHub release: {result.get('error', 'Unknown error')}"
            }

        return {
            "success": True,
            "message": f"Successfully created GitHub release {version_tag}",
            "version": version_tag,
            "platform": "github",
            "output": result.get("output", "")
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to create GitHub release: {str(e)}"
        }
