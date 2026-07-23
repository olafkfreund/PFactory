"""
Terminal Worktree Service

Manages terminal worktrees - isolated git worktrees for manual development
separate from automated task spec worktrees.
"""

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .git_utils import assert_safe_git_ref, confine_to_workspace, safe_spec_component


class TerminalWorktreeService:
    """Service for managing terminal worktrees."""

    WORKTREE_NAME_PATTERN = re.compile(r"^[a-z0-9-_]+$")
    MAX_NAME_LENGTH = 100

    def __init__(self, project_path: str):
        """Initialize the service for a project.

        Args:
            project_path: Absolute path to the project root

        Raises:
            ValueError: If project_path is not a valid directory
        """
        # #335 confine the caller-supplied absolute path once; every
        # self.project_path-derived join below is dominated by this barrier.
        self.project_path = confine_to_workspace(project_path)
        if not self.project_path.is_dir():
            raise ValueError(f"Project path does not exist: {project_path}")

        self.worktrees_dir = self.project_path / ".pfactory" / "worktrees" / "terminal"
        self.config_file = self.project_path / ".pfactory" / "terminal-worktrees.json"

    def create_worktree(
        self,
        name: str,
        terminal_id: str,
        task_id: Optional[str],
        create_git_branch: bool,
        base_branch: str,
    ) -> Dict:
        """Create a new terminal worktree.

        Args:
            name: Worktree name (lowercase, alphanumeric, dashes, underscores)
            terminal_id: Terminal session ID
            task_id: Optional task ID association
            create_git_branch: Whether to create a git branch
            base_branch: Base branch to branch from

        Returns:
            TerminalWorktreeConfig dict

        Raises:
            ValueError: If name is invalid or already exists
            subprocess.CalledProcessError: If git command fails
        """
        # Validate name
        self._validate_name(name)
        # #335 barrier the caller-supplied component at its source so every
        # `worktrees_dir / name` join downstream is dominated by the sanitizer.
        name = safe_spec_component(name, field="worktree_name")

        # Check if worktree already exists
        existing = self.get_worktree(name)
        if existing:
            raise ValueError(f"Worktree '{name}' already exists")

        # Ensure .pfactory/worktrees/terminal/ directory exists
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)

        worktree_path = self.worktrees_dir / name
        branch_name = f"terminal/{name}" if create_git_branch else None

        # Check if project is a git repository
        if create_git_branch and not self._is_git_repo():
            raise ValueError("Project is not a git repository")

        # Validate the caller-supplied base branch before it reaches any git
        # argv. `_branch_exists` happens to be safe by construction (it prefixes
        # `refs/heads/`), but `git worktree add` below takes the raw value, and
        # relying on an incidental property of a different method is how these
        # holes reopen.
        if create_git_branch:
            base_branch = assert_safe_git_ref(base_branch, "base_branch")
            if not self._branch_exists(base_branch):
                raise ValueError(f"Base branch '{base_branch}' does not exist")

        # Create the worktree
        if create_git_branch:
            # Create worktree with new branch
            self._run_git_command([
                "git", "worktree", "add",
                "-b", branch_name,
                str(worktree_path),
                base_branch
            ])
        else:
            # Create worktree directory without git branch (just a regular directory)
            worktree_path.mkdir(parents=True, exist_ok=True)

        # Create worktree config
        config = {
            "name": name,
            "path": str(worktree_path),
            "branch": branch_name,
            "baseBranch": base_branch,
            "taskId": task_id,
            "createdAt": datetime.utcnow().isoformat() + "Z",
            "terminalId": terminal_id,
        }

        # Save to config file
        self._add_worktree_to_config(config)

        return config

    def list_worktrees(self) -> List[Dict]:
        """List all terminal worktrees for this project.

        Returns:
            List of TerminalWorktreeConfig dicts

        Side effects:
            Removes stale entries (worktrees whose directories no longer exist)
        """
        config = self._load_config()
        worktrees = config.get("worktrees", [])

        # Filter out stale entries and validate existing ones
        valid_worktrees = []
        for wt in worktrees:
            wt_path = Path(wt.get("path", ""))
            if wt_path.exists():
                valid_worktrees.append(wt)

        # Update config if we removed any stale entries
        if len(valid_worktrees) < len(worktrees):
            config["worktrees"] = valid_worktrees
            self._save_config(config)

        return valid_worktrees

    def remove_worktree(self, name: str, delete_branch: bool = False) -> bool:
        """Remove a terminal worktree.

        Args:
            name: Worktree name
            delete_branch: Whether to also delete the git branch

        Returns:
            True if successful

        Raises:
            ValueError: If worktree not found
            subprocess.CalledProcessError: If git command fails
        """
        # Find worktree in config
        worktree = self.get_worktree(name)
        if not worktree:
            raise ValueError(f"Worktree '{name}' not found")

        worktree_path = Path(worktree["path"])
        branch = worktree.get("branch")

        # Remove the worktree
        if branch and self._is_git_repo():
            # Use git worktree remove if it's a git worktree
            try:
                self._run_git_command(["git", "worktree", "remove", str(worktree_path)])
            except subprocess.CalledProcessError:
                # If git worktree remove fails, force remove the directory
                if worktree_path.exists():
                    import shutil
                    shutil.rmtree(worktree_path)

            # Delete branch if requested
            if delete_branch and branch:
                try:
                    self._run_git_command(["git", "branch", "-D", branch])
                except subprocess.CalledProcessError:
                    pass  # Ignore if branch deletion fails

            # Prune worktrees
            try:
                self._run_git_command(["git", "worktree", "prune"])
            except subprocess.CalledProcessError:
                pass  # Ignore prune errors
        else:
            # Just remove the directory if it's not a git worktree
            if worktree_path.exists():
                import shutil
                shutil.rmtree(worktree_path)

        # Remove from config
        self._remove_worktree_from_config(name)

        return True

    def get_worktree(self, name: str) -> Optional[Dict]:
        """Get a specific worktree config by name.

        Args:
            name: Worktree name

        Returns:
            TerminalWorktreeConfig dict or None if not found
        """
        config = self._load_config()
        worktrees = config.get("worktrees", [])
        for wt in worktrees:
            if wt.get("name") == name:
                return wt
        return None

    def _validate_name(self, name: str):
        """Validate worktree name.

        Args:
            name: Worktree name to validate

        Raises:
            ValueError: If name is invalid
        """
        if not name:
            raise ValueError("Worktree name cannot be empty")

        if len(name) > self.MAX_NAME_LENGTH:
            raise ValueError(f"Worktree name cannot exceed {self.MAX_NAME_LENGTH} characters")

        if not self.WORKTREE_NAME_PATTERN.match(name):
            raise ValueError(
                "Worktree name must be lowercase alphanumeric with dashes/underscores only"
            )

    def _load_config(self) -> Dict:
        """Load terminal-worktrees.json.

        Returns:
            Config dict with "version" and "worktrees" keys
        """
        if not self.config_file.exists():
            return {"version": "1.0", "worktrees": []}

        try:
            with open(self.config_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"version": "1.0", "worktrees": []}

    def _save_config(self, config: Dict):
        """Save terminal-worktrees.json.

        Args:
            config: Config dict to save
        """
        # Ensure parent directory exists
        self.config_file.parent.mkdir(parents=True, exist_ok=True)

        with open(self.config_file, "w") as f:
            json.dump(config, f, indent=2)

    def _add_worktree_to_config(self, worktree_config: Dict):
        """Add a worktree to the config file.

        Args:
            worktree_config: TerminalWorktreeConfig dict
        """
        config = self._load_config()
        worktrees = config.get("worktrees", [])
        worktrees.append(worktree_config)
        config["worktrees"] = worktrees
        self._save_config(config)

    def _remove_worktree_from_config(self, name: str):
        """Remove a worktree from the config file.

        Args:
            name: Worktree name
        """
        config = self._load_config()
        worktrees = config.get("worktrees", [])
        config["worktrees"] = [wt for wt in worktrees if wt.get("name") != name]
        self._save_config(config)

    def _is_git_repo(self) -> bool:
        """Check if project is a git repository.

        Returns:
            True if project has .git directory
        """
        git_dir = self.project_path / ".git"
        return git_dir.exists()

    def _branch_exists(self, branch_name: str) -> bool:
        """Check if a git branch exists.

        Args:
            branch_name: Branch name to check

        Returns:
            True if branch exists
        """
        try:
            result = self._run_git_command(
                ["git", "rev-parse", "--verify", f"refs/heads/{branch_name}"],
                check=False
            )
            return result.returncode == 0
        except subprocess.CalledProcessError:
            return False

    def _run_git_command(
        self,
        cmd: List[str],
        check: bool = True
    ) -> subprocess.CompletedProcess:
        """Run a git command in the project directory.

        Args:
            cmd: Command and arguments as list
            check: Whether to raise on non-zero exit code

        Returns:
            CompletedProcess instance

        Raises:
            subprocess.CalledProcessError: If check=True and command fails
        """
        return subprocess.run(
            cmd,
            cwd=self.project_path,
            capture_output=True,
            check=check,
            text=True
        )
