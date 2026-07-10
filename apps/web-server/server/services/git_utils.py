"""Shared git command helper.

Extracted from routes/git.py so route modules (git.py, tasks.py) can share it
without importing each other.
"""

import subprocess


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
