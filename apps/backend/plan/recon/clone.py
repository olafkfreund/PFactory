"""Safe, read-only checkout of the target repo for reconnaissance (RFC-0010).

The bright line (RFC-0010 §2): the planner reads target-repo code **statically**
and **never executes** it. This module clones the repo into a throwaway temp dir
with every code-execution vector disabled, hands the path to the caller for
static parsing, and tears the clone down afterwards.

Disabled execution vectors:

* **git hooks** — ``core.hooksPath=/dev/null`` (a malicious repo can ship a
  ``post-checkout`` hook; we never let it run).
* **system/global git config** — ``GIT_CONFIG_NOSYSTEM=1`` + an empty
  ``HOME`` so a repo can't lean on host config / aliases.
* **submodules** — never fetched/updated (their URLs and hooks are untrusted);
  their presence is recorded as a fact by the reconnoiter, not acted on.
* **no build/install** — this module only clones; nothing in reconnaissance
  runs ``npm install`` / ``terraform init`` / build scripts.

Everything is best-effort: a failure (private repo, bad ref, network, oversize)
yields ``CloneResult(ok=False, error=...)`` so planning degrades to greenfield
rather than raising.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Guardrails: reconnaissance must stay cheap and bounded.
_CLONE_TIMEOUT_S = 90
_MAX_REPO_MB = 500
_MAX_FILES = 50_000


@dataclass
class CloneResult:
    """Outcome of a reconnaissance clone."""

    ok: bool
    path: Path | None = None
    commit: str | None = None  # resolved HEAD SHA
    error: str | None = None


def _git_url(repo: str) -> str:
    """Build an https clone URL, injecting a scoped read-only token if present.

    ``repo`` is ``owner/name`` (the contract's ``provenance.repo``) or already a
    URL. A token from ``PFACTORY_RECON_TOKEN`` / ``GH_TOKEN`` / ``GITHUB_TOKEN``
    enables private-repo reconnaissance; without one, public repos still work and
    private repos degrade to greenfield.
    """
    if "://" in repo:
        return repo
    host = os.environ.get("PFACTORY_RECON_GIT_HOST", "github.com").strip()
    token = (
        os.environ.get("PFACTORY_RECON_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or ""
    ).strip()
    if token:
        return f"https://x-access-token:{token}@{host}/{repo}.git"
    return f"https://{host}/{repo}.git"


def _hardened_env(home: str) -> dict[str, str]:
    """A git env with host config and credential prompts neutralised."""
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"  # never block on a credential prompt
    env["GIT_ALLOW_PROTOCOL"] = "https"  # no file://, ext::, ssh shell-outs
    env["HOME"] = home  # isolate from ~/.gitconfig aliases/hooks
    return env


def _run_git(args: list[str], *, cwd: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run git with no shell, hooks disabled, bounded time. Raises on failure."""
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, hardened env
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=_CLONE_TIMEOUT_S,
        check=True,
    )


def _within_limits(root: Path) -> str | None:
    """Return an error string if the checkout is too big to reconnoiter cheaply."""
    total_bytes = 0
    files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames:
            dirnames.remove(".git")  # don't count pack files
        for name in filenames:
            files += 1
            if files > _MAX_FILES:
                return f"repo exceeds {_MAX_FILES} files"
            fp = Path(dirpath) / name
            with contextlib.suppress(OSError):
                if not fp.is_symlink():
                    total_bytes += fp.stat().st_size
    if total_bytes > _MAX_REPO_MB * 1024 * 1024:
        return f"repo exceeds {_MAX_REPO_MB} MB"
    return None


@contextlib.contextmanager
def clone_for_recon(repo: str, base_ref: str | None = None) -> Iterator[CloneResult]:
    """Context manager yielding a read-only checkout of ``repo`` at ``base_ref``.

    Shallow, single-branch, blobless-history clone (only the working tree we
    parse). The temp dir is always removed on exit. Never raises — failures are
    reported on the yielded :class:`CloneResult`.

    Usage::

        with clone_for_recon("owner/name", "main") as c:
            if c.ok:
                ...  # statically parse files under c.path
    """
    tmp = tempfile.mkdtemp(prefix="pfactory-recon-")
    work = Path(tmp) / "repo"
    env = _hardened_env(home=tmp)
    try:
        url = _git_url(repo)
        args = ["clone", "--depth", "1", "--single-branch", "--filter=blob:none"]
        if base_ref:
            args += ["--branch", base_ref]
        args += [url, str(work)]
        try:
            _run_git(args, cwd=tmp, env=env)
        except subprocess.TimeoutExpired:
            yield CloneResult(ok=False, error="clone timed out")
            return
        except subprocess.CalledProcessError as exc:
            # stderr may contain the token-bearing URL; never surface it.
            yield CloneResult(ok=False, error="clone failed (repo unreachable or ref not found)")
            logger.info("recon clone failed for %s@%s: rc=%s", repo, base_ref, exc.returncode)
            return

        limit_err = _within_limits(work)
        if limit_err:
            yield CloneResult(ok=False, error=limit_err)
            return

        commit: str | None = None
        with contextlib.suppress(subprocess.SubprocessError):
            commit = _run_git(["rev-parse", "HEAD"], cwd=str(work), env=env).stdout.strip() or None

        yield CloneResult(ok=True, path=work, commit=commit)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
