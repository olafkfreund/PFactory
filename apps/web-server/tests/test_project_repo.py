"""W5 (Factory #218): derive a task's target repo (owner/name) from its project."""

from __future__ import annotations

import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.routes.tasks import project_repo  # noqa: E402


def test_tracked_project_repo_field():
    # W4: a tracked project stamps ``repo`` directly; prefer it.
    assert project_repo({"repo": "olafkfreund/app"}) == "olafkfreund/app"


def test_github_repo_setting():
    assert project_repo({"settings": {"githubRepo": "olafkfreund/app"}}) == "olafkfreund/app"


def test_git_repo_setting():
    assert project_repo({"settings": {"gitRepo": "owner/name"}}) == "owner/name"


def test_org_plus_project():
    assert project_repo({"settings": {"gitOrg": "acme", "gitProject": "widget"}}) == "acme/widget"


def test_parses_https_git_url():
    assert project_repo({"gitUrl": "https://github.com/owner/name.git"}) == "owner/name"


def test_parses_ssh_git_url():
    assert project_repo({"git_url": "git@github.com:owner/name.git"}) == "owner/name"


def test_none_when_unconfigured():
    assert project_repo({"path": "/local/dir", "settings": {}}) is None
    assert project_repo({}) is None


def test_ignores_blank_repo_setting():
    # An empty gitRepo (seen in real records) must not be returned.
    assert project_repo({"settings": {"gitRepo": ""}}) is None
