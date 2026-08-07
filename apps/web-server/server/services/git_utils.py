"""Shared git command helper.

Extracted from routes/git.py so route modules (git.py, tasks.py) can share it
without importing each other.
"""

import re
import subprocess
from pathlib import Path

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
        raise ValueError(f"invalid {field}: {text[:80]!r}")
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
        return {"success": False, "error": str(e)}


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
        return {"success": False, "error": str(e)}


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
        raise ValueError(f"invalid {field}: {text[:80]!r}")
    return text


def _allowed_roots() -> list[Path]:
    """Resolved filesystem roots the server may legitimately touch: the workspace
    root (where clone-mode projects live) plus every currently-registered project
    root. Lazy-imported to keep this leaf module import-cycle free; any lookup
    failure degrades to fewer roots (fail-closed — nothing is silently allowed)."""
    roots: list[Path] = []
    try:
        from server.services.project_workspace_service import workspace_root

        roots.append(workspace_root().expanduser().resolve())
    except Exception:
        pass
    try:
        from server.routes.projects import load_projects

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


def confine_to_workspace(value: object, field: str = "path") -> Path:
    """Resolve a caller-supplied absolute filesystem path and require it to live
    under an allowed root (the workspace root or a registered project root).

    The file-browser / project-register endpoints take a full path straight from
    the request. On a hosted server that is arbitrary-filesystem read/scan — an
    attacker-supplied ``path`` can reach ``/etc``, secrets, or another tenant's
    data. This CONFINES it: ``resolve()`` collapses ``..`` and symlinks, then the
    result must sit inside one of :func:`_allowed_roots`. Containment is checked
    AFTER ``resolve()`` (like ``_safe_launch_path``), so traversal cannot escape.
    Returns the resolved, confined path for the caller to use.

    Raises:
        ValueError: if the resolved path is outside every allowed root, or if no
            allowed root could be determined (fail-closed).
    """
    resolved = Path(str(value)).expanduser().resolve()
    roots = _allowed_roots()
    for root in roots:
        if resolved == root or root in resolved.parents:
            return resolved
    raise ValueError(
        f"invalid {field}: {str(value)[:120]!r} is outside the allowed workspace"
    )
