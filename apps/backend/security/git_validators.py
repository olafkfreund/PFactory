"""
Git Validators
==============

Validators for git operations (commit with secret scanning).
"""

import logging
import shlex
import subprocess
from pathlib import Path

from .scan_secrets import SecretMatch
from .validation_models import ValidationResult

logger = logging.getLogger(__name__)


def _format_secret_error(matches: list[SecretMatch]) -> ValidationResult:
    """Format secret scan matches into an actionable error message."""
    try:
        from scan_secrets import redacted_fingerprint
    except ImportError:
        return False, "Secrets detected in staged files"

    files_with_secrets: dict[str, list[SecretMatch]] = {}
    for match in matches:
        if match.file_path not in files_with_secrets:
            files_with_secrets[match.file_path] = []
        files_with_secrets[match.file_path].append(match)

    error_lines = [
        "SECRETS DETECTED - COMMIT BLOCKED",
        "",
        "The following potential secrets were found in staged files:",
        "",
    ]

    for file_path, file_matches in files_with_secrets.items():
        error_lines.append(f"File: {file_path}")
        for match in file_matches:
            fingerprint = redacted_fingerprint(match.matched_text)
            error_lines.append(f"  Line {match.line_number}: {match.pattern_name}")
            error_lines.append(f"    Found: {fingerprint}")
        error_lines.append("")

    error_lines.extend(
        [
            "ACTION REQUIRED:",
            "",
            "1. Move secrets to environment variables:",
            "   - Add the secret value to .env (create if needed)",
            "   - Update the code to use os.environ.get('VAR_NAME') or process.env.VAR_NAME",
            "   - Add the variable name (not value) to .env.example",
            "",
            "2. Example fix:",
            "   BEFORE: api_key = 'sk-abc123...'",
            "   AFTER:  api_key = os.environ.get('API_KEY')",
            "",
            "3. If this is a FALSE POSITIVE (test data, example, mock):",
            "   - Add the file pattern to .secretsignore",
            "   - Example: echo 'tests/fixtures/' >> .secretsignore",
            "",
            "After fixing, stage the changes with 'git add .' and retry the commit.",
        ]
    )

    return False, "\n".join(error_lines)


def _unstage_spec_artifacts() -> list[str]:
    """
    Check for and unstage any .pfactory/ files from the git staging area.

    Returns:
        List of file paths that were unstaged.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        staged_files = [f for f in result.stdout.strip().split("\n") if f]
        spec_files = [f for f in staged_files if f.startswith(".pfactory/")]

        if not spec_files:
            return []

        # Unstage spec artifacts
        subprocess.run(
            ["git", "reset", "HEAD", "--"] + spec_files,
            capture_output=True,
            text=True,
            timeout=10,
        )
        logger.warning(
            "Auto-unstaged .pfactory/ spec artifacts from commit: %s",
            spec_files,
        )
        return spec_files
    except Exception as e:
        logger.error("Failed to check/unstage spec artifacts: %s", e)
        return []


def validate_git_commit(command_string: str) -> ValidationResult:
    """
    Validate git commit commands - run secret scan before allowing commit.

    This provides autonomous feedback to the AI agent if secrets are detected,
    with actionable instructions on how to fix the issue.

    Also auto-unstages any .pfactory/ spec artifacts that the agent may
    have staged (defense-in-depth against spec files leaking into commits).

    Args:
        command_string: The full git command string

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        # Heredoc/complex quoting — can't parse tokens, but if it starts with
        # "git commit", allow it (secret scanning runs on staged files anyway)
        stripped = command_string.strip()
        if stripped.startswith("git commit") or stripped.startswith("git -c"):
            # Unstage spec artifacts before scanning/committing
            _unstage_spec_artifacts()
            # Still scan staged files for secrets
            try:
                from scan_secrets import get_staged_files, scan_files

                staged_files = get_staged_files()
                if staged_files:
                    matches = scan_files(staged_files, Path.cwd())
                    if matches:
                        return _format_secret_error(matches)
            except ImportError:
                pass
            return True, ""
        return False, "Could not parse git command"

    if not tokens or tokens[0] != "git":
        return True, ""

    # Only intercept 'git commit' commands (not git add, git push, etc.)
    if len(tokens) < 2 or tokens[1] != "commit":
        return True, ""

    # Defense-in-depth: auto-unstage any .pfactory/ spec artifacts
    unstaged = _unstage_spec_artifacts()
    if unstaged:
        logger.info("Removed %d spec artifact(s) from staging before commit", len(unstaged))

    # Import the secret scanner
    try:
        from scan_secrets import get_staged_files, scan_files
    except ImportError:
        # Scanner not available, allow commit (don't break the build)
        return True, ""

    # Get staged files and scan them
    staged_files = get_staged_files()
    if not staged_files:
        return True, ""  # No staged files, allow commit

    matches = scan_files(staged_files, Path.cwd())

    if not matches:
        return True, ""  # No secrets found, allow commit

    return _format_secret_error(matches)
