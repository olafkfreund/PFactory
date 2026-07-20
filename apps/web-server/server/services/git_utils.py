"""Shared git command helper.

Extracted from routes/git.py so route modules (git.py, tasks.py) can share it
without importing each other.
"""

import re
import subprocess

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


def run_git_command(args: list[str], cwd: str) -> dict:
    """Run a git command and return result."""
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
