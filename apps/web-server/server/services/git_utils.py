"""Shared git command helper.

Extracted from routes/git.py so route modules (git.py, tasks.py) can share it
without importing each other.
"""

import logging
import re
import subprocess
import sys
from pathlib import Path

from server.error_ref import error_message
from server.services.project_paths import load_projects

_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from client_errors import InputRejectedError  # noqa: E402  (after sys.path insert)

logger = logging.getLogger(__name__)

# A git revision we are willing to place in a command line. Deliberately
# strict, and anchored so the first character is alphanumeric: there is no
# shell involved anywhere here, but git still reads its own argv as options,
# and `git log` accepts `--output=<file>`. A ref that may begin with "-" is
# therefore a file-write primitive, not merely a bad ref.
_GIT_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@/+^~{}-]{0,254}")


def assert_safe_git_ref(value: object, field: str = "ref") -> str:
    """Return ``value`` as a string if it is safe to pass to git as a revision.

    Rejects anything that could be parsed as an option, and anything carrying
    its own ``..`` -- callers join refs into ``a..b`` ranges, so an embedded
    range separator would let one field rewrite the range it lands in.

    Raises:
        ValueError: if the value is not a usable revision.
    """
    text = str(value)
    if not _GIT_REF_RE.fullmatch(text) or ".." in text:
        # Factory#718: echoes only the caller's own value, truncated -- safe
        # for InputRejectedError's client_message contract.
        raise InputRejectedError(f"invalid {field}: {text[:80]!r}")
    return text


def run_git_command(args: list[str], cwd: str | Path) -> dict:
    """Run a git command and return result.

    ``cwd`` accepts ``Path`` because ``subprocess.run`` does (it takes any
    ``os.PathLike``) and because 43 of the call sites in ``routes/tasks.py``
    already pass one. Annotating it ``str`` did not make them wrong -- they work
    -- it just made mypy report 43 arg-type errors against the callers for a
    narrowness the implementation never had (PFactory#468).
    """
    try:
        result = subprocess.run(
            ["git"] + args, check=False, capture_output=True, text=True, cwd=cwd, timeout=30
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip()}
        return {"success": True, "output": result.stdout.strip()}
    except Exception as e:
        # The caller returns this dict's "error" straight to the browser, so it
        # must not carry `str(e)` - that is an OSError naming the workspace path
        # on disk, or a decode error quoting bytes from the repo (CWE-209,
        # py/stack-trace-exposure). Full detail goes to the server log under a
        # correlation id the user can quote back.
        return {
            "success": False,
            "error": error_message(
                logger, f"git {args[:2]} failed in {cwd}", e, "the git command failed"
            ),
        }


def run_gh_command(args: list[str], cwd: str | None = None) -> dict:
    """Run a gh CLI command and return the result."""
    try:
        result = subprocess.run(
            ["gh"] + args, check=False, capture_output=True, text=True, cwd=cwd, timeout=30
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip()}
        return {"success": True, "output": result.stdout.strip()}
    except FileNotFoundError:
        return {"success": False, "error": "GitHub CLI (gh) not installed"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timed out"}
    except Exception as e:
        # Same reasoning as run_git_command above: routes/github.py returns this
        # dict's "error" to the client in ~28 places.
        return {
            "success": False,
            "error": error_message(
                logger, f"gh {args[:2]} failed in {cwd}", e, "the gh command failed"
            ),
        }


# A single path component we are willing to join onto a trusted root. No
# separators, no traversal, no absolute paths, no null bytes. Deliberately the
# same regex-allowlist shape as routes/pfactory_tasks.py's _validate_spec_id,
# which stock CodeQL already recognises as a path-injection barrier -- that
# module carries zero path-injection alerts while every module lacking such a
# check carries them all.
_SPEC_COMPONENT_RE = re.compile(r"[A-Za-z0-9._-]{1,255}")

# Rejected outright even though the character class permits them: "." and ".."
# are the traversal primitives, and the existing _validate_spec_id lets both
# through (".." matches ^[A-Za-z0-9._-]+$). A lone ".." resolves to the parent
# of the root it is joined to, which is exactly the escape the check exists to
# prevent.
_RESERVED_COMPONENTS = frozenset({".", ".."})


def safe_spec_component(value: object, field: str = "spec_id") -> str:
    """Return ``value`` if it is safe to join onto a trusted directory root.

    Caller-supplied identifiers (``spec_id``, ``task_id``, worktree names) are
    joined onto project roots and then read from and written to. ``Path`` joins
    collapse traversal silently -- ``Path("/srv/specs") / "../../etc"`` is
    ``/etc`` -- so the component must be validated before it is joined, not
    after.

    Args:
        value: The untrusted component.
        field: Name used in the error message.

    Returns:
        The validated component, unchanged.

    Raises:
        ValueError: if the component could escape the root it is joined to.
    """
    text = str(value)
    if text in _RESERVED_COMPONENTS or not _SPEC_COMPONENT_RE.fullmatch(text):
        # Factory#718: echoes only the caller's own value, truncated -- safe
        # for InputRejectedError's client_message contract.
        raise InputRejectedError(f"invalid {field}: {text[:80]!r}")
    return text


def registered_project_roots() -> list[Path]:
    """Resolved roots of the currently-registered projects, and nothing else.

    The STRICT tier (Factory#553, mirroring AIFactory#1306). Use it where the
    caller names a directory that must ALREADY be a project: reading file
    content, unlinking session state, launching a worktree. It deliberately
    omits the workspace root, so a request cannot reach a sibling clone that
    merely happens to live under the same workspace directory.

    Entries with an empty ``path`` are skipped rather than resolved: a
    repo-only "tracked" project stores ``""``, and ``Path("").resolve()`` is
    the server's CWD, which would silently authorise it. Any lookup failure
    degrades to fewer roots -- fail-closed, nothing is silently allowed.
    """
    roots: list[Path] = []
    try:
        for entry in load_projects().values():
            rp = entry.get("path")
            if rp:
                try:
                    roots.append(Path(rp).expanduser().resolve())
                except Exception:
                    pass
    except Exception:
        pass
    return roots


def browse_roots() -> list[Path]:
    """The workspace root (where clone-mode projects land) plus every
    registered project root.

    The PERMISSIVE tier. Use it where a not-yet-registered directory is a
    legitimate answer -- browsing for a project to add, scanning, registering
    one, or opening a shell. It has to contain the registry, because that is
    how a project registered outside the workspace stays reachable at all.

    That containment is also why this tier cannot reject a value that CAME
    from the registry: such a value matches on the first iteration by
    construction. That is a property of the tier, not a bug in the caller, and
    it is the reason the strict tier above exists as a separate helper.
    """
    roots: list[Path] = []
    try:
        from server.services.project_workspace_service import workspace_root

        roots.append(workspace_root().expanduser().resolve())
    except Exception:
        pass
    roots.extend(registered_project_roots())
    return roots


def _confine(value: object, roots: list[Path], field: str) -> Path:
    """Resolve ``value`` and require the result to sit inside one of ``roots``.

    ``resolve()`` collapses ``..`` and symlinks FIRST, then containment is
    checked on the collapsed result (like ``_safe_launch_path``), so traversal
    cannot escape. An empty ``roots`` rejects everything -- fail-closed.
    """
    resolved = Path(str(value)).expanduser().resolve()
    for root in roots:
        if resolved == root or root in resolved.parents:
            return resolved
    # Factory#718: echoes only the caller's own value, truncated -- safe for
    # InputRejectedError's client_message contract.
    raise InputRejectedError(
        f"invalid {field}: {str(value)[:120]!r} is outside the allowed workspace"
    )


def confine_to_workspace(value: object, field: str = "path") -> Path:
    """Confine a caller-supplied path to :func:`browse_roots` (permissive tier).

    The file-browser / project-register endpoints take a full path straight
    from the request. On a hosted server that is arbitrary-filesystem
    read/scan -- an attacker-supplied ``path`` can reach ``/etc``, secrets, or
    another tenant's data. This confines it and returns the resolved path.

    Reach for :func:`confine_to_project` instead whenever the path must name an
    already-registered project; this tier admits the whole workspace root.

    Raises:
        ValueError: if the resolved path is outside every allowed root, or if
            no allowed root could be determined (fail-closed).
    """
    return _confine(value, browse_roots(), field)


def confine_to_project(value: object, field: str = "path") -> Path:
    """Confine a caller-supplied path to :func:`registered_project_roots`.

    The strict tier. Same barrier body as :func:`confine_to_workspace`, one
    root set narrower: the workspace root alone does not authorise the path,
    so a caller cannot read out of, or delete inside, a directory that is
    merely a neighbour of their project under a shared workspace root.

    Raises:
        ValueError: if the resolved path is inside no registered project. With
            no projects registered that is every path, which is correct: there
            is nothing yet to address.
    """
    return _confine(value, registered_project_roots(), field)
