"""Portal-managed project workspaces (#82 PR-A).

When PFactory runs on a developer laptop, the user's git repo lives on
the same filesystem as the portal and the existing ``POST /api/projects
{path}`` route just registers that directory. That model breaks for
every other deployment shape:

- **Single-user VPS** — repo is on the laptop, portal on the VPS, no
  shared filesystem.
- **Kubernetes** — portal pod has no view into the user's machine.
- **Shared/SaaS** — the path concept doesn't even map.

This service backs the alternative path: the portal accepts a Git URL
and clones it into a local workspace directory. The workspace root is
configurable via ``PROJECT_WORKSPACE_ROOT`` (defaults to
``~/.pfactory/workspaces/`` on laptop installs, expected to be a
mounted PVC in K8s installs). The returned path is what the rest of
PFactory (Auto-Fix, agent_service, etc.) uses as the project's
on-disk root — they don't need to know whether the project was added
via path or URL.

Auth in PR-A is whatever the host's git config already provides —
i.e. public HTTPS URLs and SSH keys configured in ``~/.ssh/``. Stored
git credentials (Deploy Keys, GitHub App install IDs, PATs) land in
PR-C.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from factory_common.logsafe import sanitize_log
from server.services.git_utils import safe_spec_component

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_ROOT = Path.home() / ".pfactory" / "workspaces"

# Default git operation timeout — long enough for a fresh clone of a
# medium-sized repo over a slow link, short enough that a hung remote
# doesn't lock up the portal forever.
DEFAULT_GIT_TIMEOUT_SECONDS = 600


def workspace_root() -> Path:
    """Resolve the directory under which all portal-managed clones live.

    Looks at ``PROJECT_WORKSPACE_ROOT`` env first (the K8s/SaaS path),
    falls back to ``~/.pfactory/workspaces/`` (laptop path).
    """
    env = os.environ.get("PROJECT_WORKSPACE_ROOT")
    if env:
        return Path(env).expanduser()
    return DEFAULT_WORKSPACE_ROOT


def slug_from_git_url(git_url: str) -> str:
    """Turn a git URL into a filesystem-safe directory slug.

    ``git@github.com:olaf/PFactory.git`` → ``olaf-PFactory``
    ``https://github.com/olaf/PFactory.git`` → ``olaf-PFactory``
    ``https://gitlab.com/group/sub/repo`` → ``group-sub-repo``

    The slug is used as the directory name under ``workspace_root()``, so the
    return value must be a single literal path component. ``.`` is inside the
    permitted charset below, which means a URL path of ``..`` survives the
    substitution intact and names the PARENT of the workspace root. The
    substitution alone therefore does not make the result filesystem-safe, and
    this function refuses such a URL (``ValueError``) rather than silently
    rewriting it -- registering a project under a name the caller did not ask
    for is its own surprise. Path separators cannot survive (they hyphenate),
    so ``.`` and ``..`` are the whole hazard.

    Raises:
        ValueError: if the URL yields a slug that is not a safe component.
    """
    # SCP-style ("git@host:owner/repo.git") — split on the colon, drop the host.
    if git_url.startswith("git@") and ":" in git_url:
        _, path = git_url.split(":", 1)
    else:
        parsed = urlparse(git_url)
        path = parsed.path.lstrip("/")
    # Strip .git suffix + lowercase + replace path separators with hyphens.
    if path.endswith(".git"):
        path = path[:-4]
    # Replace any non-alnum/hyphen char with hyphen; collapse repeats.
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", path).strip("-")
    # Reuse the repo's existing component barrier rather than re-deriving the
    # rule here -- it already rejects "." / ".." and over-long components.
    return safe_spec_component(slug or "workspace", field="workspace_slug")


def _inject_credential(git_url: str, username: str, token: str) -> str:
    """Rewrite an HTTPS git URL to embed a PAT (#82 PR-C).

    ``https://github.com/owner/repo.git`` →
    ``https://oauth2:<token>@github.com/owner/repo.git``

    SSH URLs (``git@host:...``) are returned unchanged — they auth via
    keys, not URLs; stored Deploy Keys are a separate path (out of
    scope for V1 of PR-C).
    """
    if not git_url.startswith("https://"):
        return git_url
    rest = git_url[len("https://") :]
    return f"https://{username}:{token}@{rest}"


async def clone_or_update(
    git_url: str,
    branch: str | None = None,
    slug: str | None = None,
    *,
    root: Path | None = None,
    timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    credential: tuple[str, str] | None = None,
) -> Path:
    """Clone the repo into the workspace root, or fast-forward an existing clone.

    Args:
        git_url: HTTPS or SSH URL to the repository.
        branch: Optional branch to checkout after clone. ``None`` uses
            the remote's HEAD.
        slug: Optional override for the workspace directory name.
            Defaults to ``slug_from_git_url(git_url)``.
        root: Optional override for the workspace root. Defaults to
            ``workspace_root()`` (PROJECT_WORKSPACE_ROOT env or
            ``~/.pfactory/workspaces/``).
        timeout_seconds: Per-operation timeout.
        credential: Optional ``(username, token)`` tuple. When provided
            and ``git_url`` is HTTPS, the credential is injected into
            the URL for the network operation only — never persisted to
            git's config (the workspace dir gets a sanitized origin
            via ``git remote set-url`` after the fetch). Use this with
            credentials from the ``git_credentials`` table (#82 PR-C).

    Returns:
        Absolute path to the local clone.

    Raises:
        GitOperationError: On any non-zero ``git`` exit code or timeout.
    """
    # #335: barrier the workspace dir component (caller slug or url-derived)
    # before it becomes a path — dominates the mkdir + .git checks below.
    slug = safe_spec_component(slug or slug_from_git_url(git_url), field="workspace_slug")
    workspace = (root or workspace_root()) / slug
    workspace.parent.mkdir(parents=True, exist_ok=True)

    # Build the URL that actually gets passed to ``git`` for network ops.
    # Note: ``credential`` is the secret material — never log it.
    fetch_url = git_url
    if credential is not None:
        username, token = credential
        fetch_url = _inject_credential(git_url, username, token)

    if (workspace / ".git").is_dir():
        # Existing clone — fetch + reset/fast-forward.
        # For credentialed pulls, point origin at the URL-with-token
        # FOR THIS OPERATION ONLY, then restore the sanitized origin so
        # the credential doesn't end up in ``.git/config``.
        if credential is not None:
            await _run_git(
                ["remote", "set-url", "origin", fetch_url],
                cwd=workspace,
                timeout=timeout_seconds,
            )
        try:
            await _run_git(
                ["fetch", "--prune", "origin"],
                cwd=workspace,
                timeout=timeout_seconds,
            )
            if branch:
                await _run_git(
                    ["checkout", branch],
                    cwd=workspace,
                    timeout=timeout_seconds,
                )
            await _run_git(
                ["pull", "--ff-only"],
                cwd=workspace,
                timeout=timeout_seconds,
            )
        finally:
            if credential is not None:
                # Restore origin to the sanitized URL so credentials
                # don't leak via ``git config``.
                try:
                    await _run_git(
                        ["remote", "set-url", "origin", git_url],
                        cwd=workspace,
                        timeout=timeout_seconds,
                    )
                except GitOperationError:
                    pass
        logger.info("[workspace] pulled latest into %s", sanitize_log(workspace))
        return workspace

    # Fresh clone
    cmd = ["clone"]
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([fetch_url, str(workspace)])
    await _run_git(cmd, cwd=workspace.parent, timeout=timeout_seconds)
    if credential is not None:
        # Strip the credential from origin so it isn't persisted in
        # the workspace's ``.git/config``.
        try:
            await _run_git(
                ["remote", "set-url", "origin", git_url],
                cwd=workspace,
                timeout=timeout_seconds,
            )
        except GitOperationError:
            pass
    logger.info("[workspace] cloned %s → %s", sanitize_log(git_url), sanitize_log(workspace))
    return workspace


class GitOperationError(RuntimeError):
    """Raised when a git operation fails or times out."""


async def _run_git(args: list[str], *, cwd: Path, timeout: float) -> str:
    """Run ``git <args>`` with a timeout. Returns stdout on success.

    PFactory#576: when ``args`` carries a credentialed URL (a clone/fetch/
    remote-set-url against a ``https://user:TOKEN@host/...`` origin --
    ``clone_or_update`` builds exactly that), the token is an argv element
    AND git itself echoes the remote URL back on an auth failure, so it can
    be in ``stderr`` independently of argv. GitOperationError's message used
    to interpolate both; a caller (``routes/projects.py``) puts that message
    straight into an HTTPException detail. A wrong or revoked token -- the
    most likely trigger, since it always exits non-zero -- disclosed the
    token being tested back to whoever tested it.

    Neither the failing subcommand name (``clone``, ``fetch``, ``pull``,
    ``remote``) nor the exit code is secret, so those still identify the
    failure; the full argv and stderr go to the debug/warning log only,
    which callers never put in a response (this module's own comment a few
    lines up already treats the log as the credential's one write path:
    "the workspace dir gets a sanitized origin ... never persisted to git's
    config"). This does not remove the credential from argv -- it is still
    world-readable on the host via ps/proc while the process runs -- only
    from client-visible text; keeping it out of argv entirely (GIT_ASKPASS
    or a credential helper) is a separate, larger change (PFactory#576).
    """
    cmd = ["git", *args]
    subcommand = args[0] if args else "git"
    logger.debug(
        "[workspace] running: git %s (cwd=%s)", sanitize_log(" ".join(args)), sanitize_log(cwd)
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise GitOperationError("git executable not found on PATH") from e

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as e:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise GitOperationError(f"git {subcommand} timed out after {timeout}s") from e

    if proc.returncode != 0:
        stderr_text = stderr.decode("utf-8", "replace").strip() or "no stderr"
        # Full argv + stderr (which may carry the credentialed URL either
        # way) go to the log only -- never into the exception message below,
        # which a caller may put straight into a client response.
        logger.warning(
            "[workspace] git %s failed (exit %s): %s",
            sanitize_log(subcommand),
            proc.returncode,
            sanitize_log(stderr_text),
        )
        raise GitOperationError(f"git {subcommand} failed (exit {proc.returncode})")
    return stdout.decode("utf-8", "replace")
