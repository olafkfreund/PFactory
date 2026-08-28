"""W4 (Factory #218): a plan session registers its target repo as a tracked
project so the portal's project dropdown isn't empty for plan-only work.

``ensure_tracked_project`` upserts a lightweight, repo-only record (no local
clone), reuses an existing project registered for the repo, and
``project_to_response`` tolerates the path-less record.
"""

from __future__ import annotations

import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.routes import projects as proj  # noqa: E402
from server.services import project_paths  # noqa: E402


def _point_projects_file(monkeypatch, tmp_path):
    f = tmp_path / "projects.json"
    monkeypatch.setattr(project_paths, "get_projects_file", lambda: f)
    return f


def test_registers_repo_only_project(monkeypatch, tmp_path):
    _point_projects_file(monkeypatch, tmp_path)
    pid = proj.ensure_tracked_project("olafkfreund/my-app")
    assert pid == "olafkfreund-my-app"
    saved = proj.load_projects()
    assert saved[pid]["repo"] == "olafkfreund/my-app"
    assert saved[pid]["name"] == "my-app"
    assert saved[pid]["source"] == "plan-session"


def test_idempotent_and_reuses_existing_repo(monkeypatch, tmp_path):
    _point_projects_file(monkeypatch, tmp_path)
    # A real local clone already registered for the repo under a different id.
    proj.save_projects({"existing": {"repo": "olafkfreund/my-app", "path": "/repos/my-app"}})
    pid = proj.ensure_tracked_project("olafkfreund/my-app")
    assert pid == "existing"  # reused, not clobbered
    assert proj.load_projects()["existing"]["path"] == "/repos/my-app"


def test_blank_repo_is_noop(monkeypatch, tmp_path):
    _point_projects_file(monkeypatch, tmp_path)
    assert proj.ensure_tracked_project("") is None
    assert proj.ensure_tracked_project("   ") is None


def test_response_tolerates_path_less_record():
    resp = proj.project_to_response(
        "olafkfreund-my-app",
        {"repo": "olafkfreund/my-app", "name": "my-app", "path": ""},
    )
    assert resp["id"] == "olafkfreund-my-app"
    assert resp["repo"] == "olafkfreund/my-app"
    assert resp["name"] == "my-app"
    assert resp["path"] == ""
    assert resp["autoBuildPath"] == ""  # no .pfactory on a path-less project


def test_analyze_project_handles_missing_path():
    assert proj.analyze_project("") == {
        "is_git_repo": False,
        "has_magestic_ai": False,
        "task_count": 0,
    }
    assert proj.analyze_project("/no/such/dir/xyz")["task_count"] == 0
