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
import contextlib
import logging
import os
import re
import stat
import tempfile
from collections.abc import Iterator
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


def _inject_credential(git_url: str, username: str) -> str:
    """Rewrite an HTTPS git URL to carry the *username only* (#82 PR-C).

    ``https://github.com/owner/repo.git`` →
    ``https://oauth2@github.com/owner/repo.git``

    The token is deliberately NOT embedded (PFactory#602, converging on
    TFactory's fork). A URL handed to ``git`` becomes an argv element, and
    argv is world-readable via ``/proc/<pid>/cmdline`` for the lifetime of
    the child. Everything downstream of that -- the exception message, the
    log line, git's own stderr -- was a *consequence* of the token being in
    argv, and each had to be defended separately (PFactory#576, #599). It
    is not in argv now, so there is nothing left to defend: the password is
    supplied out-of-band via ``GIT_ASKPASS`` (see :func:`_git_askpass_env`),
    which git asks for because this URL carries a username and no password.

    SSH URLs (``git@host:...``) are returned unchanged — they auth via
    keys, not URLs; stored Deploy Keys are a separate path (out of
    scope for V1 of PR-C).
    """
    if not git_url.startswith("https://"):
        return git_url
    rest = git_url[len("https://") :]
    return f"https://{username}@{rest}"


# Tiny POSIX askpass helper. git invokes it as ``<script> "<prompt>"`` and
# reads the answer from stdout. We branch on the prompt: git asks for the
# username first ("Username for '...'"), then the password. Both values come
# from the environment (``GIT_USER`` / ``GIT_PASS``) — never argv — so the
# token never appears in any process command line.
_GIT_ASKPASS_SCRIPT = """#!/bin/sh
case "$1" in
  Username*) printf '%s' "$GIT_USER" ;;
  *)         printf '%s' "$GIT_PASS" ;;
esac
"""


@contextlib.contextmanager
def _git_askpass_env(username: str, token: str) -> Iterator[dict[str, str]]:
    """Yield env vars that feed a git credential via ``GIT_ASKPASS``.

    Writes the askpass helper to a ``0700`` temp file and points
    ``GIT_ASKPASS`` at it. The token travels in ``GIT_PASS`` (read by the
    script), so it never lands in argv or in git's persisted config.
    ``/proc/<pid>/environ`` is owner-only; ``/proc/<pid>/cmdline`` is
    world-readable -- that asymmetry is the whole point of the move. The
    script is removed when the context exits.
    """
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed explicitly below
        mode="w", prefix="git-askpass-", suffix=".sh", delete=False
    )
    try:
        handle.write(_GIT_ASKPASS_SCRIPT)
        handle.close()
        Path(handle.name).chmod(stat.S_IRWXU)  # 0700 — owner-only rwx
        yield {
            "GIT_ASKPASS": handle.name,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_USER": username,
            "GIT_PASS": token,
        }
    finally:
        with contextlib.suppress(OSError):
            Path(handle.name).unlink()


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
            and ``git_url`` is HTTPS, the USERNAME is injected into the
            URL for the network operation only and the TOKEN is fed to
            git out-of-band via ``GIT_ASKPASS`` (PFactory#602), so it
            never enters argv or git's config. The workspace dir gets a
            bare origin back via ``git remote set-url`` after the fetch,
            so not even the username lingers. Use this with credentials
            from the ``git_credentials`` table (#82 PR-C).

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

    # Build the URL that actually gets passed to ``git`` for network ops, plus
    # the credential env. The token is NEVER embedded in the URL/argv
    # (PFactory#602): the URL carries only the username and the password is fed
    # via GIT_ASKPASS, so it can't be read from ``/proc/<pid>/cmdline``.
    # Note: ``credential`` is the secret material — never log it.
    fetch_url = git_url
    askpass_ctx: contextlib.AbstractContextManager[dict[str, str]]
    if credential is not None:
        username, token = credential
        fetch_url = _inject_credential(git_url, username)
        askpass_ctx = _git_askpass_env(username, token)
    else:
        askpass_ctx = contextlib.nullcontext({})

    with askpass_ctx as cred_env:
        if (workspace / ".git").is_dir():
            # Existing clone — fetch + reset/fast-forward.
            # For credentialed pulls, point origin at the username-only URL
            # FOR THIS OPERATION ONLY, then restore the bare origin so not
            # even the username ends up in ``.git/config``.
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
                    extra_env=cred_env,
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
                    extra_env=cred_env,
                )
            finally:
                if credential is not None:
                    # Restore origin to the bare URL so the username doesn't
                    # linger in ``git config``.
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
        await _run_git(cmd, cwd=workspace.parent, timeout=timeout_seconds, extra_env=cred_env)
        if credential is not None:
            # Strip the username from origin so it isn't persisted in
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


#: Every git subcommand this module invokes. ``args[0]`` is a hard-coded
#: literal at all seven ``_run_git`` call sites, but the value that reaches a
#: log line and an exception message must be provably one of these rather than
#: "element 0 of a caller-supplied list" -- which is all the code (and all
#: CodeQL) can otherwise say about it.
_GIT_SUBCOMMANDS = ("clone", "fetch", "checkout", "pull", "remote")


def _safe_subcommand(args: list[str]) -> str:
    """Return ``args[0]`` as one of :data:`_GIT_SUBCOMMANDS`, else ``"unknown"``.

    Returns the matching *constant*, not the caller's string. That is the whole
    point: the returned object is a literal defined in this module, so no value
    derived from ``args`` -- a clone URL, a branch name, anything else a
    caller put there -- can reach the log sink or the exception message
    through it. (Since PFactory#602 the argv carries no credential either,
    but this barrier is about caller-controlled text in general, not only
    about the token.)

    ``"unknown"`` rather than echoing an unrecognised value back: a new
    subcommand added at a call site without being added here should read as
    unrecognised, not smuggle arbitrary argv text into a log line.
    """
    head = args[0] if args else ""
    for known in _GIT_SUBCOMMANDS:
        if head == known:
            return known
    return "unknown"


async def _run_git(
    args: list[str],
    *,
    cwd: Path,
    timeout: float,
    extra_env: dict[str, str] | None = None,
) -> str:
    """Run ``git <args>`` with a timeout. Returns stdout on success.

    ``extra_env`` is merged on top of the process environment. It is how the
    ``GIT_ASKPASS`` credential vars reach a network operation (PFactory#602)
    without the token ever being on the command line.

    There is no ``credentialed`` flag any more, and nothing here branches on
    whether a credential is in play. That flag existed because the token WAS
    an argv element -- ``_inject_credential`` used to build
    ``https://user:TOKEN@host/...`` and hand it here -- so every text derived
    from the operation (the exception message, the DEBUG argv line, git's
    stderr) was potentially credential-bearing, and each of the seven call
    sites had to declare by hand which of those to withhold. PFactory#576 and
    #599 closed the demonstrated leaks that followed from it; #602 removes the
    cause. The token now travels in ``GIT_PASS`` and git reads it through an
    askpass helper, so:

    * argv is credential-free, which also takes it out of
      ``/proc/<pid>/cmdline`` -- the residual #576 explicitly deferred; and
    * stderr is logged in full again on EVERY failure. Withholding it cost
      operators the real git error on ordinary public-repo failures, and it
      bought nothing that removing the credential from argv does not buy
      outright.

    The argv is still NEVER logged, and the exception message still carries
    only ``_safe_subcommand``'s module constant plus the exit code -- a caller
    (``routes/projects.py``) puts that message straight into an HTTPException
    detail, and argv can carry other caller-controlled text (a URL, a branch)
    that has no business in a client response or in an off-host-forwarded log
    even when it is not secret. That property is PFactory#599's and it is
    unchanged here.
    """
    cmd = ["git", *args]
    subcommand = _safe_subcommand(args)
    # The argv is NEVER logged. It used to be, and that made "is the token in
    # the log?" a property of a boolean each of the seven call sites set by
    # hand, three lines away from the `fetch_url` that carried it, rather than
    # a property of this code. Driving the real pipeline (setup_logging ->
    # server.log) with that pairing inverted wrote the full PAT to a DEBUG
    # line, and this fleet forwards application logs off-host. The subcommand
    # and cwd identify the operation without the argv, so it stays out even
    # now that argv carries no credential.
    logger.debug("[workspace] running: git %s (cwd=%s)", subcommand, sanitize_log(cwd))
    env = {**os.environ, **extra_env} if extra_env else None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            env=env,
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
        # Full stderr goes to the log on EVERY failure now -- there is no
        # credential in this operation's argv for git to echo back. It still
        # never goes into the exception message below, which a caller may put
        # straight into a client response: stderr is remote-controlled text
        # (a server's own `remote:` lines are printed verbatim by git) and
        # belongs in the operator's log, not in an HTTP response body.
        stderr_text = stderr.decode("utf-8", "replace").strip() or "no stderr"
        logger.warning(
            "[workspace] git %s failed (exit %s): %s",
            sanitize_log(subcommand),
            proc.returncode,
            sanitize_log(stderr_text),
        )
        raise GitOperationError(f"git {subcommand} failed (exit {proc.returncode})")
    return stdout.decode("utf-8", "replace")
