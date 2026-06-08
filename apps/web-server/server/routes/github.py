"""
GitHub integration routes.

Handles GitHub OAuth, repository management, issues, PRs, and releases.
"""

import asyncio
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path as FilePath

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


# ============================================
# Request/Response Models
# ============================================

class CreateRepoRequest(BaseModel):
    repoName: str
    description: str | None = None
    private: bool = False
    orgName: str | None = None


class AddRemoteRequest(BaseModel):
    projectPath: str
    repoFullName: str


class InvestigateRequest(BaseModel):
    # Comment IDs round-trip as opaque values — they're only used for `in`
    # filtering at the backend. Accept either int (GitLab numeric IDs, GitHub
    # REST API legacy) or str (GitHub GraphQL node IDs like 'IC_kwDOLB...').
    selectedCommentIds: list[int | str] | None = None


class ImportIssuesRequest(BaseModel):
    issueNumbers: list[int]


class PRReviewRequest(BaseModel):
    followup: bool = False


class PostPRReviewRequest(BaseModel):
    selectedFindingIds: list[str] | None = None


class PersistTokenRequest(BaseModel):
    projectId: str


class PostPRCommentRequest(BaseModel):
    body: str


class MergePRRequest(BaseModel):
    mergeMethod: str = "squash"  # merge, squash, rebase


class AssignPRRequest(BaseModel):
    username: str


class CreateReleaseRequest(BaseModel):
    version: str
    releaseNotes: str
    draft: bool = False
    prerelease: bool = False


class CopilotDispatchRequest(BaseModel):
    repo: str  # owner/name
    issueNumber: int


# ============================================
# GitHub CLI Helpers
# ============================================

def run_gh_command(args: list[str], cwd: str | None = None) -> dict:
    """Run a gh CLI command and return the result."""
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


def _persist_cli_token_to_project(project_id: str) -> bool:
    """Persist the gh CLI token to a project's .pfactory/.env file.

    Reads the current token from `gh auth token`, then inserts/updates
    GITHUB_TOKEN in the project env file with secure 0o600 permissions.
    Returns True on success.
    """
    from .projects import load_projects

    token_result = run_gh_command(["auth", "token"])
    if not token_result["success"] or not token_result["output"]:
        return False

    token = token_result["output"]

    projects = load_projects()
    if project_id not in projects:
        return False

    project_path = FilePath(projects[project_id]["path"])
    env_path = project_path / ".pfactory" / ".env"

    try:
        existing = {}
        if env_path.exists():
            for line in env_path.read_text().split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    existing[key.strip()] = value.strip()

        existing["GITHUB_TOKEN"] = token

        env_path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(f"{k}={v}" for k, v in existing.items())
        env_path.write_text(content)
        env_path.chmod(0o600)
        return True
    except Exception:
        return False


# ============================================
# AI Analysis Helper
# ============================================

async def analyze_issue_with_ai(issue_data: dict, comments: list, project_path: str) -> dict:
    """
    Analyze a GitHub issue using AI.

    Args:
        issue_data: Issue data from GitHub API
        comments: List of comments on the issue
        project_path: Path to the project directory

    Returns:
        Dictionary containing AI analysis results with keys:
        - summary: Brief summary of the issue
        - issue_type: Type of issue (bug, feature, documentation, etc.)
        - complexity: Complexity estimate (simple, standard, complex)
        - suggestions: List of suggested solutions or next steps
        - affected_areas: List of files/components that might need attention
        - risks: List of potential risks or concerns
    """
    # Add backend to Python path
    backend_path = FilePath(__file__).parent.parent.parent.parent / "backend"
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))

    try:
        from core.simple_client import create_simple_client
    except ImportError as e:
        raise RuntimeError(f"Failed to import simple_client: {e}")

    # Build analysis prompt
    prompt = _build_issue_analysis_prompt(issue_data, comments)

    # Run AI analysis using Claude Agent SDK async context manager
    try:
        client = create_simple_client(
            agent_type="batch_analysis",
            model="claude-sonnet-4-20250514",
            cwd=FilePath(project_path),
            max_turns=1,
        )

        response_text = ""
        async with client:
            await client.query(prompt)
            async for msg in client.receive_response():
                msg_type = type(msg).__name__
                if msg_type == "AssistantMessage" and hasattr(msg, "content"):
                    for block in msg.content:
                        if hasattr(block, "text"):
                            response_text += block.text

        if not response_text:
            raise RuntimeError("Empty response from AI")

        return _parse_ai_analysis_response(response_text)

    except Exception as e:
        raise RuntimeError(f"AI analysis failed: {e}")


def _build_issue_analysis_prompt(issue_data: dict, comments: list) -> str:
    """Build the analysis prompt for the AI."""

    # Format comments for inclusion in prompt
    comments_text = ""
    if comments:
        comments_text = "\n\n## Comments\n\n"
        for comment in comments[:10]:  # Limit to first 10 comments
            author = comment.get("user", {}).get("login", "Unknown")
            body = comment.get("body", "")
            created_at = comment.get("created_at", "")
            comments_text += f"**{author}** ({created_at}):\n{body}\n\n"

    labels_text = ", ".join([label.get("name", "") for label in issue_data.get("labels", [])]) if issue_data.get("labels") else "None"

    prompt = f"""You are analyzing a GitHub issue to help understand what needs to be done and provide actionable insights.

## Issue Information

**Title:** {issue_data.get("title", "Unknown")}
**State:** {issue_data.get("state", "unknown")}
**Labels:** {labels_text}
**Author:** {issue_data.get("user", {}).get("login", "Unknown")}
**Created:** {issue_data.get("created_at", "Unknown")}
**Updated:** {issue_data.get("updated_at", "Unknown")}

## Description

{issue_data.get("body", "No description provided.")}
{comments_text}

## Your Task

Analyze this issue and provide structured insights in the following JSON format:

```json
{{
  "summary": "One paragraph summary of what this issue is about",
  "issue_type": "bug|feature|documentation|refactor|performance|security|other",
  "complexity": "simple|standard|complex",
  "suggestions": [
    "Specific, actionable suggestion for addressing this issue",
    "Another suggestion or next step"
  ],
  "affected_areas": [
    "File paths, components, or modules that might need changes",
    "API endpoints or functions that are relevant"
  ],
  "risks": [
    "Potential risk or concern to be aware of",
    "Another consideration"
  ]
}}
```

**Analysis Guidelines:**

1. **Issue Type Classification:**
   - bug: Something is broken or not working as expected
   - feature: New functionality request
   - documentation: Docs need to be added or updated
   - refactor: Code restructuring without behavior change
   - performance: Speed or efficiency improvements
   - security: Security vulnerability or concern
   - other: Doesn't fit other categories

2. **Complexity Levels:**
   - simple: Single file change, clear fix, < 1 hour
   - standard: Multiple files, moderate changes, 1-4 hours
   - complex: Architectural changes, many files, > 4 hours

3. **Suggestions:** Be specific and actionable. Focus on practical next steps.

4. **Affected Areas:** Identify specific files, components, or modules based on the issue description.

5. **Risks:** Consider backwards compatibility, breaking changes, edge cases, security implications.

Respond with ONLY the JSON object, no other text."""

    return prompt


def _parse_ai_analysis_response(response_content: str) -> dict:
    """Parse the AI's analysis response and extract structured data."""

    # Try to extract JSON from the response
    try:
        # Look for JSON code block
        if "```json" in response_content:
            start = response_content.find("```json") + 7
            end = response_content.find("```", start)
            json_text = response_content[start:end].strip()
        elif "```" in response_content:
            start = response_content.find("```") + 3
            end = response_content.find("```", start)
            json_text = response_content[start:end].strip()
        else:
            # Try to parse the whole response as JSON
            json_text = response_content.strip()

        analysis = json.loads(json_text)

        # Validate required fields
        required_fields = ["summary", "issue_type", "complexity", "suggestions", "affected_areas", "risks"]
        for field in required_fields:
            if field not in analysis:
                analysis[field] = [] if field in ["suggestions", "affected_areas", "risks"] else None

        return analysis

    except json.JSONDecodeError:
        # If JSON parsing fails, return a basic structure
        return {
            "summary": "AI analysis completed but response format was invalid.",
            "issue_type": "unknown",
            "complexity": "unknown",
            "suggestions": [],
            "affected_areas": [],
            "risks": []
        }


# ============================================
# GitHub CLI Check & Auth
# ============================================

@router.get("/cli/check")
async def check_github_cli():
    """Check if GitHub CLI is installed."""
    result = run_gh_command(["--version"])
    version = None
    if result["success"]:
        # Parse version from "gh version 2.x.x (2024-...)"
        output = result.get("output", "")
        import re as _re
        m = _re.search(r"(\d+\.\d+\.\d+)", output)
        if m:
            version = m.group(1)
    return {"success": True, "data": {"installed": result["success"], "version": version}}


@router.get("/models")
async def list_github_models():
    """List the GitHub Models catalog (epic #87 / #88).

    Passthrough to ``GET https://models.github.ai/catalog/models`` via the gh
    CLI (reuses the existing gh auth token). Each entry can be selected in the
    portal as ``github-models/{publisher}/{name}`` — free OpenAI-compatible
    inference routed through PFactory's provider factory.
    """
    result = run_gh_command(["api", "https://models.github.ai/catalog/models"])
    if not result["success"]:
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": result["error"], "models": []},
        )
    try:
        catalog = json.loads(result["output"])
    except json.JSONDecodeError as exc:
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": f"bad catalog JSON: {exc}", "models": []},
        )
    # Normalise to the github-models/<publisher>/<name> model strings the
    # provider factory understands.
    models = []
    for entry in catalog if isinstance(catalog, list) else []:
        publisher = entry.get("publisher") or entry.get("owner") or ""
        name = entry.get("name") or entry.get("id") or ""
        if not name:
            continue
        slug = f"{publisher}/{name}".strip("/")
        models.append({
            "id": f"github-models/{slug}",
            "model": slug,
            "name": entry.get("friendly_name") or name,
            "publisher": publisher,
            "summary": entry.get("summary", ""),
        })
    return {"success": True, "data": {"models": models, "count": len(models)}}


@router.get("/copilot/config")
async def get_copilot_dispatch_config():
    """Report whether Copilot cloud-agent dispatch is enabled (epic #87 / #88)."""
    from ..services.copilot_dispatch_service import CopilotDispatchService

    return {
        "success": True,
        "data": {
            "enabled": CopilotDispatchService.is_enabled(),
            "dispatch_label": CopilotDispatchService.DISPATCH_LABEL,
            "agent_handle": CopilotDispatchService.AGENT_HANDLE,
        },
    }


@router.post("/copilot/dispatch")
async def dispatch_to_copilot(request: CopilotDispatchRequest):
    """Assign a PFactory planning issue to the Copilot cloud agent (#88).

    Opt-in: returns 409 when ``PFACTORY_COPILOT_DISPATCH_ENABLED`` is off so the
    caller falls back to the normal PFactory flow instead of silently ignoring
    the label.
    """
    from ..services.copilot_dispatch_service import SERVICE, CopilotDispatchService

    if not CopilotDispatchService.is_enabled():
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "error": (
                    "Copilot dispatch disabled — set "
                    "PFACTORY_COPILOT_DISPATCH_ENABLED=1 to enable."
                ),
            },
        )
    try:
        meta = SERVICE.dispatch(request.repo, request.issueNumber)
    except RuntimeError as exc:
        return JSONResponse(
            status_code=502, content={"success": False, "error": str(exc)}
        )
    return {"success": True, "data": meta}


@router.get("/copilot/pr")
async def find_copilot_pr(
    repo: str = Query(...), issueNumber: int = Query(...)
):
    """Find the Copilot-opened plan-draft PR for an issue, if any (#88)."""
    from ..services.copilot_dispatch_service import SERVICE

    pr_number = SERVICE.find_copilot_pr(repo, issueNumber)
    return {"success": True, "data": {"pr_number": pr_number}}


@router.post("/cli/install")
def install_github_cli():
    """Install GitHub CLI (gh) from the official GitHub repository.

    Uses the official install script from https://cli.github.com/ which:
    1. Adds the GitHub CLI apt repository
    2. Installs the gh package

    Requires root access (runs in Docker container as root entrypoint).
    Uses sync def to avoid blocking the event loop with subprocess.run.
    """
    import logging
    import shlex

    log = logging.getLogger(__name__)

    def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
        """Run a command inside a login shell."""
        safe_cmd = " ".join(shlex.quote(a) for a in args)
        return subprocess.run(
            ["bash", "-l", "-c", safe_cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    # Step 1: Check if gh is already installed
    try:
        result = _run(["gh", "--version"], timeout=10)
        if result.returncode == 0:
            return {
                "success": True,
                "data": {
                    "message": "GitHub CLI is already installed",
                    "version": result.stdout.strip(),
                    "steps_completed": ["already-installed"],
                },
            }
    except Exception:
        pass

    # Step 2: Install gh using the official apt repository method
    # This works in the Docker container (Ubuntu-based)
    steps_completed: list[str] = []

    try:
        log.info("Installing GitHub CLI via official apt repository...")

        # Add GitHub CLI apt repo and install
        install_script = (
            "apt-get update -qq"
            " && apt-get install -y -qq --no-install-recommends"
            " gpg wget"
            " && wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg"
            " | tee /usr/share/keyrings/githubcli-archive-keyring.gpg > /dev/null"
            " && echo 'deb [arch=$(dpkg --print-architecture)"
            " signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg]"
            " https://cli.github.com/packages stable main'"
            " | tee /etc/apt/sources.list.d/github-cli.list > /dev/null"
            " && apt-get update -qq"
            " && apt-get install -y -qq --no-install-recommends gh"
            " && rm -rf /var/lib/apt/lists/*"
        )

        result = subprocess.run(
            ["bash", "-c", install_script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return {
                "success": False,
                "error": f"Failed to install GitHub CLI: {result.stderr.strip()[-500:]}",
            }
        steps_completed.append("gh-installed")
        log.info("GitHub CLI installed successfully")
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Installation timed out (120s)"}
    except Exception as e:
        return {"success": False, "error": f"Installation failed: {e}"}

    # Step 3: Verify installation
    version_str = "unknown"
    try:
        result = _run(["gh", "--version"], timeout=10)
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
            "message": "GitHub CLI installed successfully",
            "version": version_str,
            "steps_completed": steps_completed,
        },
    }


@router.get("/auth/check")
async def check_github_auth():
    """Check if user is authenticated with GitHub CLI."""
    result = run_gh_command(["auth", "status"])
    output = result.get("output", "")
    authenticated = result["success"] and "Logged in" in output
    username = ""
    if authenticated:
        user_result = run_gh_command(["api", "user", "-q", ".login"])
        if user_result["success"]:
            username = user_result["output"]
    return {"success": True, "data": {"authenticated": authenticated, "username": username}}


@router.get("/auto-detect")
async def auto_detect_github(projectId: str | None = Query(None)):
    """Auto-detect GitHub CLI authentication and username in one call.

    If projectId is provided, the CLI token is persisted directly to the
    project's .pfactory/.env file (server-side). The raw token is never
    included in the response.
    """
    # Check gh CLI is installed
    if not shutil.which("gh"):
        return {"success": True, "data": {"authenticated": False, "reason": "gh_not_installed"}}

    # Check auth status
    auth_result = run_gh_command(["auth", "status"])
    output = auth_result.get("output", "")
    if not (auth_result["success"] and "Logged in" in output):
        return {"success": True, "data": {"authenticated": False, "reason": "not_logged_in"}}

    # Get username
    username = ""
    user_result = run_gh_command(["api", "user", "-q", ".login"])
    if user_result["success"]:
        username = user_result["output"]

    # Persist token server-side if projectId provided
    token_persisted = False
    if projectId:
        token_persisted = _persist_cli_token_to_project(projectId)

    return {
        "success": True,
        "data": {
            "authenticated": True,
            "username": username,
            "tokenPersisted": token_persisted,
        }
    }


# Background state for GitHub auth flow
_gh_auth_proc: asyncio.subprocess.Process | None = None
_gh_auth_status: dict | None = None


async def _monitor_gh_auth(proc: asyncio.subprocess.Process):
    """Monitor gh auth login process in background and broadcast result."""
    global _gh_auth_status, _gh_auth_proc
    import logging
    log = logging.getLogger(__name__)

    try:
        await asyncio.wait_for(proc.wait(), timeout=300)

        if proc.returncode == 0:
            _gh_auth_status = {"complete": True, "success": True}
            log.info("[GitHub Auth] Authentication completed successfully")
        else:
            _gh_auth_status = {"complete": True, "success": False,
                               "error": "Authentication flow did not complete. Please try again."}
            log.warning(f"[GitHub Auth] Process exited with code {proc.returncode}")
    except asyncio.TimeoutError:
        _gh_auth_status = {"complete": True, "success": False,
                           "error": "Authentication timed out after 5 minutes."}
        try:
            proc.kill()
        except Exception:
            pass
    except Exception as e:
        _gh_auth_status = {"complete": True, "success": False,
                           "error": f"Authentication failed: {e}"}
    finally:
        _gh_auth_proc = None

    # Broadcast completion via WebSocket
    try:
        from ..websockets.events import broadcast_event
        await broadcast_event("github:auth-complete", _gh_auth_status)
    except Exception:
        pass


@router.post("/auth/start")
async def start_github_auth():
    """Start GitHub CLI authentication flow using device code.

    Returns the device code and URL immediately so the user can complete
    auth on any device. The gh process continues running in the background.
    Poll GET /auth/status or listen for the github:auth-complete WebSocket event.
    """
    global _gh_auth_proc, _gh_auth_status
    gh_path = shutil.which("gh")
    if not gh_path:
        return {
            "success": True,
            "data": {
                "success": False,
                "message": "GitHub CLI (gh) is not installed."
            }
        }

    # Kill any existing auth process
    if _gh_auth_proc is not None:
        try:
            _gh_auth_proc.kill()
        except Exception:
            pass
        _gh_auth_proc = None

    _gh_auth_status = None

    try:
        proc = await asyncio.create_subprocess_exec(
            gh_path, "auth", "login",
            "--hostname", "github.com",
            "--git-protocol", "https",
            "--web",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
        )
        _gh_auth_proc = proc

        device_code = None
        auth_url = None

        # Read stderr line by line until we get the device code + URL
        # gh outputs to stderr: "First, copy your one-time code: XXXX-XXXX"
        # then "... open ... https://github.com/login/device"
        async def extract_device_code():
            nonlocal device_code, auth_url
            for stream in [proc.stderr, proc.stdout]:
                if stream is None:
                    continue
                while True:
                    try:
                        line = await asyncio.wait_for(stream.readline(), timeout=15)
                    except asyncio.TimeoutError:
                        break
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").strip()
                    code_match = re.search(r"one-time code:\s*([A-Z0-9]+-[A-Z0-9]+)", text)
                    if code_match:
                        device_code = code_match.group(1)
                    url_match = re.search(r"(https://github\.com/login/device\S*)", text)
                    if url_match:
                        auth_url = url_match.group(1)
                    if device_code and auth_url:
                        return

        await extract_device_code()

        if not device_code:
            # Process may have exited already (e.g., already authenticated)
            if proc.returncode is not None and proc.returncode == 0:
                _gh_auth_status = {"complete": True, "success": True}
                return {
                    "success": True,
                    "data": {
                        "success": True,
                        "message": "Already authenticated with GitHub.",
                    }
                }
            return {
                "success": True,
                "data": {
                    "success": False,
                    "message": "Could not extract device code from gh CLI output.",
                }
            }

        # Start background monitor for process completion
        asyncio.create_task(_monitor_gh_auth(proc))

        return {
            "success": True,
            "data": {
                "success": True,
                "deviceCode": device_code,
                "authUrl": auth_url or "https://github.com/login/device",
                "awaiting": True,
            }
        }

    except Exception as e:
        _gh_auth_proc = None
        return {
            "success": True,
            "data": {
                "success": False,
                "message": f"Failed to start authentication: {e}"
            }
        }


@router.get("/auth/status")
async def check_github_auth_status():
    """Poll for GitHub auth flow completion.

    Returns the current status of the background gh auth login process.
    """
    global _gh_auth_status

    if _gh_auth_status and _gh_auth_status.get("complete"):
        result = {**_gh_auth_status}
        _gh_auth_status = None  # Clear after reading
        return {"success": True, "data": result}

    return {"success": True, "data": {"complete": False}}


@router.get("/token")
async def get_github_token():
    """Check if a GitHub auth token is available from CLI.

    Returns only a boolean flag -- the raw token is never exposed.
    """
    result = run_gh_command(["auth", "token"])
    has_token = result["success"] and bool(result.get("output"))
    return {"success": True, "data": {"hasToken": has_token}}


@router.post("/persist-token")
async def persist_github_token(request: PersistTokenRequest):
    """Persist the gh CLI token to a project's .pfactory/.env file.

    The raw token never appears in the response.
    """
    persisted = _persist_cli_token_to_project(request.projectId)
    if persisted:
        return {"success": True, "data": {"tokenPersisted": True}}
    return {"success": False, "error": "Failed to persist token"}


@router.get("/user")
async def get_github_user():
    """Get authenticated GitHub username."""
    result = run_gh_command(["api", "user", "-q", ".login"])
    if result["success"]:
        return {"success": True, "data": {"username": result["output"]}}
    return {"success": True, "data": {"username": ""}}


@router.get("/repos")
async def list_github_user_repos():
    """List repositories for authenticated user."""
    result = run_gh_command([
        "repo", "list", "--json", "name,nameWithOwner,description,isPrivate,url",
        "--limit", "100"
    ])
    if result["success"]:
        try:
            raw_repos = json.loads(result["output"])
            repos = [
                {
                    "fullName": r.get("nameWithOwner", r.get("name", "")),
                    "description": r.get("description"),
                    "isPrivate": r.get("isPrivate", False),
                }
                for r in raw_repos
            ]
            return {"success": True, "data": {"repos": repos}}
        except json.JSONDecodeError:
            return {"success": True, "data": {"repos": []}}
    return {"success": True, "data": {"repos": []}}


@router.get("/orgs")
async def list_github_orgs():
    """List organizations for authenticated user."""
    result = run_gh_command(["api", "user/orgs", "-q", ".[].login"])
    if result["success"]:
        orgs = [{"login": org} for org in result["output"].split("\n") if org]
        return {"success": True, "data": {"orgs": orgs}}
    return {"success": True, "data": {"orgs": []}}


@router.get("/detect-repo")
async def detect_github_repo(path: str = Query(...)):
    """Detect GitHub remote for a local repository."""
    result = run_gh_command(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=path)
    if result["success"]:
        return {"success": True, "data": result["output"]}
    return {"success": True, "data": ""}


@router.get("/branches")
async def get_github_branches(
    repo: str = Query(...),
):
    """Get branches for a GitHub repository."""
    result = run_gh_command([
        "api", f"repos/{repo}/branches", "--jq", ".[].name"
    ])
    if result["success"]:
        branches = result["output"].split("\n") if result["output"] else []
        return {"success": True, "data": branches}
    return {"success": True, "data": []}


@router.post("/repos")
async def create_github_repo(request: CreateRepoRequest):
    """Create a new GitHub repository."""
    args = ["repo", "create", request.repoName, "--confirm"]
    if request.description:
        args.extend(["--description", request.description])
    if request.private:
        args.append("--private")
    else:
        args.append("--public")
    if request.orgName:
        args[2] = f"{request.orgName}/{request.repoName}"

    result = run_gh_command(args)
    if result["success"]:
        return {
            "success": True,
            "data": {
                "fullName": request.repoName,
                "url": f"https://github.com/{request.repoName}"
            }
        }
    return {"success": False, "error": result.get("error", "Failed to create repo")}


@router.post("/remote")
async def add_git_remote(request: AddRemoteRequest):
    """Add GitHub remote to local repository."""
    remote_url = f"https://github.com/{request.repoFullName}.git"
    try:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=request.projectPath,
            check=True,
            capture_output=True
        )
        return {"success": True, "data": {"remoteUrl": remote_url}}
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": e.stderr.decode() if e.stderr else "Failed to add remote"}


# ============================================
# Project-specific GitHub Routes
# These are mounted under /api/projects/{projectId}/github
# ============================================

project_router = APIRouter()


def _resolve_project_path(projectId: str) -> FilePath | None:
    """Resolve a project ID to its filesystem path."""
    from .projects import load_projects
    projects = load_projects()
    if projectId not in projects:
        return None
    return FilePath(projects[projectId]["path"])


def _map_gh_issue(issue: dict, repo_full_name: str = "") -> dict:
    """Map gh CLI issue JSON to the frontend GitHubIssue shape."""
    comments = issue.get("comments", [])
    author = issue.get("author", {}) or {}
    assignees = issue.get("assignees", []) or []
    labels = issue.get("labels", []) or []
    milestone = issue.get("milestone", None)

    return {
        "id": issue.get("number", 0),
        "number": issue.get("number", 0),
        "title": issue.get("title", ""),
        "body": issue.get("body", ""),
        "state": (issue.get("state", "OPEN") or "OPEN").lower(),
        "labels": [
            {"id": i, "name": lbl.get("name", "") if isinstance(lbl, dict) else str(lbl), "color": lbl.get("color", "") if isinstance(lbl, dict) else ""}
            for i, lbl in enumerate(labels)
        ],
        "assignees": [
            {"login": a.get("login", "") if isinstance(a, dict) else str(a), "avatar_url": a.get("avatarUrl", "") if isinstance(a, dict) else ""}
            for a in assignees
        ],
        "author": {
            "login": author.get("login", "") if isinstance(author, dict) else str(author),
            "avatar_url": author.get("avatarUrl", "") if isinstance(author, dict) else "",
        },
        "milestone": {"title": milestone.get("title", ""), "number": milestone.get("number", 0)} if milestone else None,
        "commentsCount": len(comments) if isinstance(comments, list) else 0,
        "htmlUrl": issue.get("url", ""),
        "repoFullName": repo_full_name,
        "createdAt": issue.get("createdAt", ""),
        "updatedAt": issue.get("updatedAt", ""),
        "closedAt": issue.get("closedAt", None),
    }


def _get_repo_full_name(project_path: str) -> str:
    """Get the repo full name (owner/repo) from the project path."""
    result = run_gh_command(
        ["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        cwd=project_path,
    )
    return result.get("output", "") if result["success"] else ""


def _use_provider_api(projectId: str) -> bool:
    """Check if the project is configured to use a custom GitProvider REST API."""
    from .projects import load_projects
    projects = load_projects()
    if projectId not in projects:
        return False
    project = projects[projectId]
    settings = project.get("settings", {})
    provider = settings.get("gitProvider", "github").lower()
    token = settings.get("gitToken")
    # If using gitlab or azure_devops, or github with a custom token configured, use the provider API
    return provider in ("gitlab", "azure_devops") or (provider == "github" and bool(token))


def _get_project_provider(projectId: str):
    """Get the appropriate GitProvider instance for the project based on settings."""
    # Ensure backend is in Python path
    backend_path = FilePath(__file__).parent.parent.parent.parent / "backend"
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))

    from .projects import load_projects
    projects = load_projects()
    if projectId not in projects:
        raise ValueError(f"Project {projectId} not found")

    project = projects[projectId]
    settings = project.get("settings", {})
    provider_type = settings.get("gitProvider", "github").lower()
    project_path = project.get("path", "")

    # Import factory and protocol
    from runners.github.providers.factory import get_provider
    from runners.github.providers.protocol import ProviderType

    # Map settings fields
    token = settings.get("gitToken")
    base_url = settings.get("gitBaseUrl")
    org = settings.get("gitOrg")
    proj_name = settings.get("gitProject")
    repo_name = settings.get("gitRepo")

    # If repo name is not configured, try to auto-detect from the folder or settings
    if not repo_name:
        # Detect repo from path
        repo_name = _get_repo_full_name(project_path) or ""

    if provider_type == "gitlab":
        kwargs = {}
        if token:
            kwargs["_token"] = token
        if base_url:
            kwargs["_base_url"] = base_url
        if project_path:
            kwargs["_project_dir"] = project_path
        return get_provider(ProviderType.GITLAB, repo=repo_name, **kwargs)

    elif provider_type == "azure_devops":
        kwargs = {}
        if token:
            kwargs["_pat"] = token
        if org:
            kwargs["_organization"] = org
        if proj_name:
            kwargs["_project"] = proj_name
        if base_url:
            kwargs["_base_url"] = base_url
        if project_path:
            kwargs["_project_dir"] = project_path
        return get_provider(ProviderType.AZURE_DEVOPS, repo=repo_name, **kwargs)

    else:
        # Default to github
        kwargs = {}
        if project_path:
            kwargs["_project_dir"] = project_path
        # Pass token if present
        if token:
            kwargs["_token"] = token
        return get_provider(ProviderType.GITHUB, repo=repo_name, **kwargs)


def _map_provider_issue(issue, repo_full_name: str = "") -> dict:
    """Map IssueData from GitProvider to frontend shape."""
    from datetime import datetime
    return {
        "id": issue.number,
        "number": issue.number,
        "title": issue.title,
        "body": issue.body,
        "state": issue.state.lower(),
        "labels": [
            {"id": i, "name": name, "color": ""}
            for i, name in enumerate(issue.labels)
        ],
        "assignees": [
            {"login": a, "avatar_url": ""}
            for a in issue.assignees
        ],
        "author": {
            "login": issue.author,
            "avatar_url": "",
        },
        "milestone": {"title": issue.milestone, "number": 0} if issue.milestone else None,
        "commentsCount": 0,
        "htmlUrl": issue.url,
        "repoFullName": repo_full_name,
        "createdAt": issue.created_at.isoformat() if isinstance(issue.created_at, datetime) else str(issue.created_at),
        "updatedAt": issue.updated_at.isoformat() if isinstance(issue.updated_at, datetime) else str(issue.updated_at),
        "closedAt": None,
    }


def _map_provider_pr(pr) -> dict:
    """Map PRData from GitProvider to frontend shape."""
    from datetime import datetime
    files = pr.files or []
    return {
        "number": pr.number,
        "title": pr.title,
        "body": pr.body,
        "state": pr.state.lower(),
        "author": {
            "login": pr.author,
        },
        "headRefName": pr.source_branch,
        "baseRefName": pr.target_branch,
        "additions": pr.additions,
        "deletions": pr.deletions,
        "changedFiles": pr.changed_files,
        "assignees": [],
        "files": [
            {
                "path": f.get("path", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "status": f.get("status", ""),
            }
            for f in files
        ],
        "createdAt": pr.created_at.isoformat() if isinstance(pr.created_at, datetime) else str(pr.created_at),
        "updatedAt": pr.updated_at.isoformat() if isinstance(pr.updated_at, datetime) else str(pr.updated_at),
        "htmlUrl": pr.url,
    }


async def _get_provider_issue_comments(provider, issueNumber: int) -> list[dict]:
    """Fetch and map issue/PR comments using GitProvider API endpoints."""
    from runners.github.providers.protocol import ProviderType
    comments = []
    
    if provider.provider_type == ProviderType.GITLAB:
        try:
            notes = await provider.api_get(f"/api/v4/projects/{provider._project_id}/issues/{issueNumber}/notes")
        except Exception:
            try:
                notes = await provider.api_get(f"/api/v4/projects/{provider._project_id}/merge_requests/{issueNumber}/notes")
            except Exception:
                notes = []
        
        for note in notes:
            if note.get("system"):
                continue
            author = note.get("author", {})
            comments.append({
                "id": note.get("id", 0),
                "body": note.get("body", ""),
                "user": {
                    "login": author.get("username", "") or author.get("name", ""),
                    "avatar_url": author.get("avatar_url", ""),
                },
                "created_at": note.get("created_at", ""),
                "updated_at": note.get("updated_at", ""),
            })

    elif provider.provider_type == ProviderType.AZURE_DEVOPS:
        try:
            url = f"{provider._base_url}/{provider._org}/{provider._proj}/_apis/wit/workItems/{issueNumber}/comments?api-version=7.1-preview.3"
            resp = await provider.api_get(url)
            comments_raw = resp.get("comments", [])
            for c in comments_raw:
                author = c.get("createdBy", {})
                comments.append({
                    "id": c.get("id", 0),
                    "body": c.get("text", ""),
                    "user": {
                        "login": author.get("uniqueName", "") or author.get("displayName", ""),
                        "avatar_url": author.get("_links", {}).get("avatar", {}).get("href", ""),
                    },
                    "created_at": c.get("createdDate", ""),
                    "updated_at": c.get("modifiedDate", ""),
                })
        except Exception:
            try:
                url = f"{provider._base_url}/{provider._org}/{provider._proj}/_apis/git/repositories/{provider._repo_id}/pullRequests/{issueNumber}/threads?api-version=7.1"
                resp = await provider.api_get(url)
                for thread in resp.get("value", []):
                    if thread.get("isDeleted"):
                        continue
                    for c in thread.get("comments", []):
                        if c.get("isDeleted"):
                            continue
                        author = c.get("author", {})
                        comments.append({
                            "id": c.get("id", 0),
                            "body": c.get("content", ""),
                            "user": {
                                "login": author.get("uniqueName", "") or author.get("displayName", ""),
                                "avatar_url": author.get("_links", {}).get("avatar", {}).get("href", ""),
                            },
                            "created_at": c.get("publishedDate", ""),
                            "updated_at": c.get("lastContentUpdatedDate", ""),
                        })
            except Exception:
                pass
    else:
        # GitHub api-based fallback
        try:
            url = f"repos/{provider.repo}/issues/{issueNumber}/comments"
            comments_raw = await provider.api_get(url)
            for c in comments_raw:
                user = c.get("user", {})
                comments.append({
                    "id": c.get("id", 0),
                    "body": c.get("body", ""),
                    "user": {
                        "login": user.get("login", ""),
                        "avatar_url": user.get("avatar_url", ""),
                    },
                    "created_at": c.get("created_at", ""),
                    "updated_at": c.get("updated_at", ""),
                })
        except Exception:
            pass

    return comments


@project_router.get("/repositories")
async def get_project_github_repositories(projectId: str):
    """Get GitHub repositories for a project."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return {"success": False, "error": f"Project {projectId} not found"}

    if _use_provider_api(projectId):
        try:
            provider = _get_project_provider(projectId)
            repo_info = await provider.get_repository_info()
            mapped_repo = {
                "nameWithOwner": repo_info.get("nameWithOwner") or repo_info.get("name") or provider.repo,
                "description": repo_info.get("description", ""),
                "url": repo_info.get("url", ""),
                "isPrivate": repo_info.get("isPrivate", False)
            }
            return {"success": True, "data": [mapped_repo]}
        except Exception as e:
            return {"success": True, "data": []}

    result = run_gh_command(
        ["repo", "view", "--json", "nameWithOwner,description,url,isPrivate"],
        cwd=str(project_path),
    )
    if not result["success"]:
        return {"success": True, "data": []}

    try:
        repo = json.loads(result["output"])
        return {"success": True, "data": [repo]}
    except json.JSONDecodeError:
        return {"success": True, "data": []}


@project_router.get("/status")
async def check_project_github_connection(projectId: str):
    """Check GitHub connection status for a project."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return {"success": True, "data": {"connected": False, "repoFullName": None, "error": f"Project {projectId} not found"}}

    if _use_provider_api(projectId):
        try:
            provider = _get_project_provider(projectId)
            repo_info = await provider.get_repository_info()
            
            # Fetch issues count
            try:
                issues = await provider.fetch_issues()
                issue_count = len(issues)
            except Exception:
                issue_count = 0
                
            return {
                "success": True,
                "data": {
                    "connected": True,
                    "repoFullName": repo_info.get("nameWithOwner") or repo_info.get("name") or provider.repo,
                    "repoDescription": repo_info.get("description", ""),
                    "issueCount": issue_count,
                    "error": None,
                }
            }
        except Exception as e:
            return {
                "success": True,
                "data": {
                    "connected": False,
                    "repoFullName": None,
                    "repoDescription": None,
                    "issueCount": 0,
                    "error": f"Connection failed: {str(e)}",
                }
            }

    # Check gh auth
    auth_result = run_gh_command(["auth", "status"])
    if not auth_result["success"]:
        return {"success": True, "data": {"connected": False, "repoFullName": None, "error": "GitHub CLI not authenticated. Run 'gh auth login' in terminal."}}

    # Check repo detection
    repo_result = run_gh_command(
        ["repo", "view", "--json", "nameWithOwner,description"],
        cwd=str(project_path),
    )
    if not repo_result["success"]:
        return {"success": True, "data": {"connected": False, "repoFullName": None, "error": "No GitHub remote detected for this project."}}

    try:
        repo_data = json.loads(repo_result["output"])
    except json.JSONDecodeError:
        return {"success": True, "data": {"connected": False, "repoFullName": None, "error": "Failed to parse repo info."}}

    repo_full_name = repo_data.get("nameWithOwner", "")
    repo_description = repo_data.get("description", "")

    # Get issue count
    issue_count = 0
    count_result = run_gh_command(
        ["issue", "list", "--state", "all", "--json", "number", "--jq", "length"],
        cwd=str(project_path),
    )
    if count_result["success"]:
        try:
            issue_count = int(count_result["output"])
        except (ValueError, TypeError):
            pass

    return {
        "success": True,
        "data": {
            "connected": True,
            "repoFullName": repo_full_name,
            "repoDescription": repo_description,
            "issueCount": issue_count,
            "error": None,
        }
    }


@project_router.get("/issues")
async def get_project_github_issues(
    projectId: str,
    state: str | None = Query(None)
):
    """Get GitHub issues for a project."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return {"success": False, "error": f"Project {projectId} not found"}

    if _use_provider_api(projectId):
        try:
            provider = _get_project_provider(projectId)
            from runners.github.providers.protocol import IssueFilters
            
            # Map state
            query_state = "open"
            if state and state in ("open", "closed", "all"):
                query_state = state
                
            filters = IssueFilters(state=query_state)
            issues_raw = await provider.fetch_issues(filters)
            issues = [_map_provider_issue(issue, provider.repo) for issue in issues_raw]
            return {"success": True, "data": issues}
        except Exception as e:
            return {"success": False, "error": str(e)}

    args = [
        "issue", "list",
        "--json", "number,title,body,state,labels,assignees,author,milestone,createdAt,updatedAt,closedAt,comments,url",
        "--limit", "100",
    ]
    if state and state in ("open", "closed", "all"):
        args.extend(["--state", state])

    result = run_gh_command(args, cwd=str(project_path))
    if not result["success"]:
        return {"success": False, "error": result.get("error", "Failed to fetch issues")}

    try:
        issues_raw = json.loads(result["output"])
    except json.JSONDecodeError:
        return {"success": True, "data": []}

    repo_full_name = _get_repo_full_name(str(project_path))
    issues = [_map_gh_issue(issue, repo_full_name) for issue in issues_raw]
    return {"success": True, "data": issues}


@project_router.get("/issues/{issueNumber}")
async def get_project_github_issue(projectId: str, issueNumber: int):
    """Get a specific GitHub issue."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return {"success": False, "error": f"Project {projectId} not found"}

    if _use_provider_api(projectId):
        try:
            provider = _get_project_provider(projectId)
            issue = await provider.fetch_issue(issueNumber)
            return {"success": True, "data": _map_provider_issue(issue, provider.repo)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    result = run_gh_command(
        ["issue", "view", str(issueNumber), "--json", "number,title,body,state,labels,assignees,author,milestone,createdAt,updatedAt,closedAt,comments,url"],
        cwd=str(project_path),
    )
    if not result["success"]:
        return {"success": False, "error": result.get("error", f"Failed to fetch issue #{issueNumber}")}

    try:
        issue_raw = json.loads(result["output"])
    except json.JSONDecodeError:
        return {"success": False, "error": "Failed to parse issue data"}

    repo_full_name = _get_repo_full_name(str(project_path))
    return {"success": True, "data": _map_gh_issue(issue_raw, repo_full_name)}


@project_router.get("/issues/{issueNumber}/comments")
async def get_project_github_issue_comments(projectId: str, issueNumber: int):
    """Get comments for a GitHub issue."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return {"success": False, "error": f"Project {projectId} not found"}

    if _use_provider_api(projectId):
        try:
            provider = _get_project_provider(projectId)
            comments = await _get_provider_issue_comments(provider, issueNumber)
            return {"success": True, "data": comments}
        except Exception as e:
            return {"success": False, "error": str(e)}

    result = run_gh_command(
        ["issue", "view", str(issueNumber), "--json", "comments"],
        cwd=str(project_path),
    )
    if not result["success"]:
        return {"success": False, "error": result.get("error", f"Failed to fetch comments for issue #{issueNumber}")}

    try:
        data = json.loads(result["output"])
    except json.JSONDecodeError:
        return {"success": True, "data": []}

    raw_comments = data.get("comments", [])
    comments = []
    for c in raw_comments:
        author = c.get("author", {}) or {}
        comments.append({
            "id": c.get("id", 0),
            "body": c.get("body", ""),
            "user": {
                "login": author.get("login", "") if isinstance(author, dict) else str(author),
                "avatar_url": author.get("avatarUrl", "") if isinstance(author, dict) else "",
            },
            "created_at": c.get("createdAt", ""),
            "updated_at": c.get("updatedAt", ""),
        })

    return {"success": True, "data": comments}


@project_router.post("/issues/{issueNumber}/investigate")
async def investigate_github_issue(
    projectId: str,
    issueNumber: int,
    request: InvestigateRequest
):
    """Investigate an issue using AI (supports GitHub, GitLab, Azure DevOps)."""
    try:
        # Load projects and validate project exists
        from .projects import load_projects

        projects = load_projects()
        if projectId not in projects:
            return {"success": False, "error": f"Project {projectId} not found"}

        project_path = FilePath(projects[projectId]["path"])

        # Provider-aware fetch: GitLab / Azure DevOps / GitHub-with-PAT
        # use the GitProvider abstraction; only gh-CLI-authed GitHub falls
        # through to the legacy `run_gh_command` path below.
        if _use_provider_api(projectId):
            try:
                provider = _get_project_provider(projectId)
                issue_obj = await provider.fetch_issue(issueNumber)
                issue_data = {
                    "number": issue_obj.number,
                    "title": issue_obj.title,
                    "body": issue_obj.body,
                    "state": issue_obj.state,
                    "labels": [{"name": lbl} for lbl in issue_obj.labels],
                    "author": {"login": issue_obj.author},
                    "createdAt": issue_obj.created_at.isoformat() if issue_obj.created_at else None,
                    "updatedAt": issue_obj.updated_at.isoformat() if issue_obj.updated_at else None,
                    "url": issue_obj.url,
                }
                try:
                    all_comments = await _get_provider_issue_comments(provider, issueNumber)
                except Exception:
                    all_comments = []
            except Exception as exc:
                return {
                    "success": False,
                    "error": f"Failed to fetch issue: {exc}",
                }
        else:
            # Fetch issue details using gh CLI
            issue_result = run_gh_command(
                ["issue", "view", str(issueNumber), "--json", "number,title,body,state,labels,author,createdAt,updatedAt,url"],
                cwd=str(project_path)
            )

            if not issue_result["success"]:
                return {
                    "success": False,
                    "error": f"Failed to fetch issue: {issue_result.get('error', 'Unknown error')}"
                }

            try:
                issue_data = json.loads(issue_result["output"])
            except json.JSONDecodeError:
                return {"success": False, "error": "Failed to parse issue data"}

            # Fetch all comments for the issue
            comments_result = run_gh_command(
                ["issue", "view", str(issueNumber), "--json", "comments"],
                cwd=str(project_path)
            )

            all_comments = []
            if comments_result["success"]:
                try:
                    comments_data = json.loads(comments_result["output"])
                    all_comments = comments_data.get("comments", [])
                except json.JSONDecodeError:
                    pass

        # Filter comments if specific IDs were selected
        selected_comments = []
        if request.selectedCommentIds:
            selected_comments = [
                comment for comment in all_comments
                if comment.get("id") in request.selectedCommentIds
            ]
        else:
            # If no specific comments selected, include all comments
            selected_comments = all_comments

        # Prepare issue data for analysis
        issue_info = {
            "number": issue_data.get("number"),
            "title": issue_data.get("title"),
            "body": issue_data.get("body"),
            "state": issue_data.get("state"),
            "labels": issue_data.get("labels", []),
            "user": issue_data.get("author", {}),
            "created_at": issue_data.get("createdAt"),
            "updated_at": issue_data.get("updatedAt"),
            "url": issue_data.get("url"),
        }

        # Perform AI analysis
        try:
            analysis_result = await analyze_issue_with_ai(
                issue_info,
                selected_comments,
                str(project_path)
            )
            analysis_status = "completed"
            analysis_data = analysis_result
        except Exception as ai_error:
            # If AI analysis fails, still return the issue data
            analysis_status = "failed"
            analysis_data = {
                "error": f"AI analysis failed: {str(ai_error)}",
                "summary": None,
                "issue_type": None,
                "complexity": None,
                "suggestions": [],
                "affected_areas": [],
                "risks": []
            }

        # Prepare investigation data
        investigation_data = {
            "issue": issue_info,
            "comments": selected_comments,
            "analysis": {
                "status": analysis_status,
                **analysis_data
            }
        }

        return {
            "success": True,
            "data": investigation_data
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to investigate issue: {str(e)}"
        }


@project_router.post("/import")
async def import_github_issues(projectId: str, request: ImportIssuesRequest):
    """Import GitHub issues as tasks."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return {"success": False, "error": f"Project {projectId} not found"}

    imported = 0
    failed = 0
    issues = []

    for issue_number in request.issueNumbers:
        result = run_gh_command(
            ["issue", "view", str(issue_number), "--json", "number,title,body,state,labels,author,createdAt,url"],
            cwd=str(project_path),
        )
        if not result["success"]:
            failed += 1
            continue

        try:
            issue_data = json.loads(result["output"])
        except json.JSONDecodeError:
            failed += 1
            continue

        # Create a spec directory for the imported issue
        specs_dir = project_path / ".pfactory" / "specs"
        specs_dir.mkdir(parents=True, exist_ok=True)

        # Determine next spec number
        existing = sorted(specs_dir.iterdir()) if specs_dir.exists() else []
        next_num = 1
        for d in existing:
            if d.is_dir() and d.name[:3].isdigit():
                try:
                    next_num = max(next_num, int(d.name[:3]) + 1)
                except ValueError:
                    pass

        title_slug = (issue_data.get("title", "untitled") or "untitled").lower()
        title_slug = title_slug.replace(" ", "-")[:40]
        # Remove non-alphanumeric chars except hyphens
        title_slug = "".join(c for c in title_slug if c.isalnum() or c == "-")
        spec_name = f"{next_num:03d}-gh{issue_number}-{title_slug}"
        spec_dir = specs_dir / spec_name
        spec_dir.mkdir(parents=True, exist_ok=True)

        # Write requirements.json
        labels = issue_data.get("labels", []) or []
        label_names = [lbl.get("name", "") if isinstance(lbl, dict) else str(lbl) for lbl in labels]
        requirements = {
            "title": issue_data.get("title", f"GitHub Issue #{issue_number}"),
            "description": issue_data.get("body", ""),
            "source": "github",
            "githubIssue": {
                "number": issue_number,
                "url": issue_data.get("url", ""),
                "state": (issue_data.get("state", "") or "").lower(),
                "labels": label_names,
            },
        }
        (spec_dir / "requirements.json").write_text(json.dumps(requirements, indent=2))

        # Write spec.md
        body = issue_data.get("body", "") or ""
        spec_md = f"# {issue_data.get('title', f'Issue #{issue_number}')}\n\n"
        spec_md += f"**Source:** GitHub Issue [#{issue_number}]({issue_data.get('url', '')})\n"
        if label_names:
            spec_md += f"**Labels:** {', '.join(label_names)}\n"
        spec_md += f"\n## Description\n\n{body}\n"
        (spec_dir / "spec.md").write_text(spec_md)

        imported += 1
        issues.append({
            "number": issue_number,
            "title": issue_data.get("title", ""),
            "specId": spec_name,
        })

    return {
        "success": True,
        "data": {
            "success": True,
            "imported": imported,
            "failed": failed,
            "issues": issues,
        }
    }


@project_router.post("/issues/{issueNumber}/close")
async def close_github_issue(projectId: str, issueNumber: int):
    """Close a GitHub issue."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return {"success": False, "error": f"Project {projectId} not found"}

    if _use_provider_api(projectId):
        try:
            provider = _get_project_provider(projectId)
            success = await provider.close_issue(issueNumber)
            return {"success": success}
        except Exception as e:
            return {"success": False, "error": str(e)}

    result = run_gh_command(
        ["issue", "close", str(issueNumber)],
        cwd=str(project_path),
    )
    if not result["success"]:
        return {"success": False, "error": result.get("error", f"Failed to close issue #{issueNumber}")}

    return {"success": True}


@project_router.get("/prs")
async def get_project_github_prs(
    projectId: str,
    state: str | None = Query(None),
):
    """Get GitHub pull requests for a project."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return {"success": False, "error": f"Project {projectId} not found"}

    if _use_provider_api(projectId):
        try:
            provider = _get_project_provider(projectId)
            from runners.github.providers.protocol import PRFilters
            
            # Map state
            query_state = "open"
            if state and state in ("open", "closed", "merged", "all"):
                query_state = state
                
            filters = PRFilters(state=query_state)
            prs_raw = await provider.fetch_prs(filters)
            prs = [_map_provider_pr(pr) for pr in prs_raw]
            return {"success": True, "data": prs}
        except Exception as e:
            return {"success": False, "error": str(e)}

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    return service.list_prs(project_path, state=state)


@project_router.post("/prs/{prNumber}/review")
async def trigger_pr_review(
    projectId: str,
    prNumber: int,
    request: PRReviewRequest | None = None,
):
    """Trigger an async PR review.

    Launches the GitHub runner's review-pr (or followup-review-pr) command
    as a background subprocess. Progress is emitted via WebSocket events:
    - pr:review-progress
    - pr:review-complete
    - pr:review-error

    Returns 202 Accepted immediately.
    """
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    followup = request.followup if request else False

    from ..services.pr_review_service import get_pr_review_service

    service = get_pr_review_service()

    if service.is_running(projectId, prNumber):
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "error": f"A review is already running for PR #{prNumber}",
            },
        )

    started = await service.start_review(
        project_id=projectId,
        pr_number=prNumber,
        project_path=project_path,
        followup=followup,
    )

    if not started:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Failed to start PR review"},
        )

    return JSONResponse(
        status_code=202,
        content={
            "success": True,
            "data": {
                "message": f"PR #{prNumber} review started",
                "prNumber": prNumber,
                "followup": followup,
            },
        },
    )


@project_router.get("/prs/{prNumber}/review")
async def get_pr_review(projectId: str, prNumber: int):
    """Get stored PR review result.

    Reads the review result JSON from the project's
    .pfactory/github/pr/review_{prNumber}.json file.

    Returns PRReviewResult data or null if no review exists.
    """
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    result = service.get_review(project_path, prNumber)

    if not result["success"]:
        return JSONResponse(
            status_code=500,
            content=result,
        )
    return result


@project_router.delete("/prs/{prNumber}/review")
async def delete_pr_review(projectId: str, prNumber: int):
    """Delete a stored PR review result.

    Removes the review result JSON file and updates the index.
    """
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    result = service.delete_review(project_path, prNumber)

    if not result["success"]:
        return JSONResponse(
            status_code=500,
            content=result,
        )
    return result


@project_router.post("/prs/{prNumber}/post-review")
async def post_pr_review_to_github(
    projectId: str,
    prNumber: int,
    request: PostPRReviewRequest | None = None,
):
    """Post review findings as GitHub review comments.

    Reads the stored review result, filters by selectedFindingIds if provided,
    and posts each finding as a file-level review comment via gh CLI.
    """
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    selected_ids = request.selectedFindingIds if request else None
    result = service.post_review_to_github(project_path, prNumber, selected_finding_ids=selected_ids)

    if not result["success"]:
        # Determine appropriate status code based on error
        error_msg = result.get("error", "")
        if "No review found" in error_msg:
            status_code = 404
        elif "No findings to post" in error_msg:
            status_code = 400
        elif "Failed to read" in error_msg:
            status_code = 500
        else:
            status_code = 500
        return JSONResponse(status_code=status_code, content=result)

    return result


@project_router.post("/prs/{prNumber}/comment")
async def post_pr_comment(
    projectId: str,
    prNumber: int,
    request: PostPRCommentRequest,
):
    """Post a general comment on a PR via gh pr comment."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    if _use_provider_api(projectId):
        try:
            provider = _get_project_provider(projectId)
            comment_id = await provider.add_comment(prNumber, request.body)
            return {"success": True, "data": {"commentId": comment_id}}
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    result = service.post_comment(project_path, prNumber, request.body)

    if not result["success"]:
        error_msg = result.get("error", "")
        status_code = 400 if "cannot be empty" in error_msg else 500
        return JSONResponse(status_code=status_code, content=result)

    return result


@project_router.post("/prs/{prNumber}/approve")
async def approve_pr(
    projectId: str,
    prNumber: int,
    request: PostPRCommentRequest | None = None,
):
    """Approve a PR via gh pr review --approve."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    if _use_provider_api(projectId):
        try:
            provider = _get_project_provider(projectId)
            from runners.github.providers.protocol import ReviewData
            review = ReviewData(pr_number=prNumber, event="approve", body=request.body if request else "Approved")
            await provider.post_review(prNumber, review)
            return {"success": True}
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    body = request.body if request else ""
    result = service.approve_pr(project_path, prNumber, body=body)

    if not result["success"]:
        return JSONResponse(status_code=500, content=result)

    return result


@project_router.post("/prs/{prNumber}/merge")
async def merge_pr(
    projectId: str,
    prNumber: int,
    request: MergePRRequest | None = None,
):
    """Merge a PR with configurable merge method (merge/squash/rebase)."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    if _use_provider_api(projectId):
        try:
            provider = _get_project_provider(projectId)
            merge_method = request.mergeMethod if request else "squash"
            success = await provider.merge_pr(prNumber, merge_method=merge_method)
            if success:
                return {"success": True}
            else:
                return JSONResponse(status_code=500, content={"success": False, "error": "Failed to merge PR"})
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    merge_method = request.mergeMethod if request else "squash"
    result = service.merge_pr(project_path, prNumber, method=merge_method)

    if not result["success"]:
        error_msg = result.get("error", "")
        status_code = 400 if "Invalid merge method" in error_msg else 500
        return JSONResponse(status_code=status_code, content=result)

    return result


@project_router.post("/prs/{prNumber}/assign")
async def assign_pr(
    projectId: str,
    prNumber: int,
    request: AssignPRRequest,
):
    """Assign a user to a PR via gh pr edit --add-assignee."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    result = service.assign_pr(project_path, prNumber, request.username)

    if not result["success"]:
        error_msg = result.get("error", "")
        status_code = 400 if "cannot be empty" in error_msg else 500
        return JSONResponse(status_code=status_code, content=result)

    return result


@project_router.post("/prs/{prNumber}/cancel")
async def cancel_pr_review(
    projectId: str,
    prNumber: int,
):
    """Cancel an ongoing PR review process."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_review_service import get_pr_review_service

    service = get_pr_review_service()

    if not service.is_running(projectId, prNumber):
        return {"success": True, "data": {"cancelled": False, "reason": "No review is running"}}

    cancelled = await service.cancel_review(projectId, prNumber)

    if not cancelled:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Failed to cancel review"},
        )

    return {"success": True, "data": {"cancelled": True}}


@project_router.get("/prs/{prNumber}/new-commits")
async def check_pr_new_commits(projectId: str, prNumber: int):
    """Check if there are new commits since the last review.

    Compares the reviewed_commit_sha stored in the review result JSON against
    the current HEAD SHA of the PR (fetched via gh pr view).

    Returns NewCommitsCheck: hasNewCommits, newCommitCount,
    lastReviewedCommit, currentHeadCommit.
    """
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    return service.check_new_commits(project_path, prNumber)


@project_router.get("/prs/{prNumber}/logs")
async def get_pr_review_logs(projectId: str, prNumber: int):
    """Get PR review execution logs.

    Reads phase-level review logs from the project's
    .pfactory/github/pr/review_{prNumber}_logs.json file.

    Returns PRLogs data with per-phase timing and entries, or null
    if no logs are available.
    """
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": f"Project {projectId} not found"},
        )

    from ..services.pr_data_service import get_pr_data_service

    service = get_pr_data_service()
    result = service.get_review_logs(project_path, prNumber)

    if not result["success"]:
        return JSONResponse(
            status_code=500,
            content=result,
        )
    return result


@project_router.post("/releases")
async def create_github_release(projectId: str, request: CreateReleaseRequest):
    """Create a GitHub release."""
    project_path = _resolve_project_path(projectId)
    if not project_path:
        return {"success": False, "error": f"Project {projectId} not found"}

    args = ["release", "create", request.version, "--title", request.version, "--notes", request.releaseNotes]
    if request.draft:
        args.append("--draft")
    if request.prerelease:
        args.append("--prerelease")

    result = run_gh_command(args, cwd=str(project_path))
    if not result["success"]:
        return {"success": False, "error": result.get("error", "Failed to create release")}

    # gh release create outputs the release URL
    release_url = result.get("output", "")
    return {"success": True, "data": {"url": release_url}}


@router.get("/fork-info")
async def get_fork_info(project_path: str = Query(..., description="Absolute path to the project")):
    """Detect if repo is a fork and return origin + upstream info."""
    result = run_gh_command(
        ["repo", "view", "--json", "nameWithOwner,parent,isFork,defaultBranchRef"],
        cwd=project_path
    )
    if not result["success"]:
        return {"success": False, "error": result.get("error")}

    try:
        data = json.loads(result["output"])
    except (json.JSONDecodeError, TypeError):
        return {"success": False, "error": "Failed to parse repository info"}

    info = {
        "isFork": data.get("isFork", False),
        "origin": data.get("nameWithOwner"),
        "defaultBranch": data.get("defaultBranchRef", {}).get("name", "main"),
    }
    if info["isFork"] and data.get("parent"):
        parent = data["parent"]
        info["upstream"] = parent.get("nameWithOwner")
        info["upstreamDefaultBranch"] = parent.get("defaultBranchRef", {}).get("name", "main")

    return {"success": True, "data": info}