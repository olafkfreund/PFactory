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
