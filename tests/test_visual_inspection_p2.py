"""Tests for Visual Inspection P2 — correction plan + issues + store (#170 / #172)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.visual_inspection import (
    StepResult,
    build_correction_prompt,
    build_issue_specs,
    build_meta,
    finalize_run,
    issue_specs_to_dict,
    register_issues,
    render_correction_plan,
)
from agents.visual_inspection import store

_TARGET = {"name": "snow", "platform": "servicenow", "base_url": "https://acme.service-now.com"}


def _meta(fail: bool = True):
    steps = [StepResult(1, "login", "pass", screenshot="screenshots/01-login-pass.png")]
    if fail:
        steps.append(StepResult(2, "submit", "fail",
                                screenshot="screenshots/02-submit-fail.png",
                                error="expected 'Saved' — got 'Required field'"))
    return build_meta(run_id="snow-20260603130500", target=_TARGET, steps=steps,
                      created_at="2026-06-03T13:05:00Z")


# ── correction plan ──────────────────────────────────────────────────────────


def test_prompt_lists_failures() -> None:
    p = build_correction_prompt(_meta())
    assert "submit" in p and "Required field" in p and "correction plan" in p.lower()


def test_plan_uses_injected_llm() -> None:
    out = render_correction_plan(_meta(), generate=lambda prompt: "# LLM plan\nfix it")
    assert out.startswith("# LLM plan")


def test_plan_falls_back_when_no_llm() -> None:
    out = render_correction_plan(_meta())  # no generate
    assert "Correction plan" in out and "submit" in out
    assert "AIFactory-ready" in out


def test_plan_falls_back_when_llm_raises() -> None:
    def boom(_p):
        raise RuntimeError("model down")

    out = render_correction_plan(_meta(), generate=boom)
    assert "Correction plan" in out  # deterministic fallback, not an exception


def test_plan_clean_run() -> None:
    out = render_correction_plan(_meta(fail=False))
    assert "nothing to correct" in out.lower()


# ── issues ───────────────────────────────────────────────────────────────────


def test_build_issue_specs_epic_and_child_per_failure() -> None:
    epic, children = build_issue_specs(_meta())
    assert "Visual inspection: snow" in epic.title and "epic" in epic.labels
    assert len(children) == 1
    body = children[0].body
    assert "## What's wrong" in body and "## How to fix" in body
    assert "02-submit-fail.png" in body


def test_register_dry_run_makes_no_calls() -> None:
    epic, children = build_issue_specs(_meta())
    calls: list = []
    r = register_issues(epic, children, "o/r", create=False, gh_runner=lambda a: calls.append(a))
    assert r["dry_run"] is True and r["count"] == 1 and calls == []


def test_register_create_links_epic() -> None:
    epic, children = build_issue_specs(_meta())
    seen = []

    def fake_gh(argv):
        seen.append(argv)
        return 0, f"https://github.com/o/r/issues/{len(seen)}"

    r = register_issues(epic, children, "o/r", create=True, gh_runner=fake_gh)
    assert r["dry_run"] is False and r["epic"].endswith("/issues/1")
    child_argv = seen[1]
    assert "Part of epic #1." in child_argv[child_argv.index("--body") + 1]


def test_issue_specs_to_dict() -> None:
    d = issue_specs_to_dict(*build_issue_specs(_meta()))
    assert d["epic"]["title"] and d["children"][0]["labels"]


# ── store ────────────────────────────────────────────────────────────────────


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    root = tmp_path / "visual-inspections"
    root.mkdir()
    monkeypatch.setenv("PFACTORY_VISUAL_INSPECTION_ROOT", str(root))
    return root


def _packaged(tmp: Path) -> Path:
    d = tmp / "snow-20260603130500"
    (d / "screenshots").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps(_meta().to_dict()))
    (d / "report.md").write_text("# report")
    (d / "correction-plan.md").write_text("# plan")
    (d / "issues.json").write_text('{"epic": {}, "children": []}')
    return d


def test_store_write_list_read_download(store_root, tmp_path) -> None:
    run = _packaged(tmp_path)
    dst = store.write_run(run)
    assert dst == store_root / "snow-20260603130500"

    runs = store.list_runs()
    assert len(runs) == 1 and runs[0]["id"] == "snow-20260603130500"
    assert runs[0]["verdict"] == "fail" and runs[0]["target"]["name"] == "snow"

    detail = store.read_run("snow-20260603130500")
    assert detail["reportMarkdown"] == "# report"
    assert detail["correctionPlanMarkdown"] == "# plan"

    assert store.download_path("snow-20260603130500", "report.md").name == "report.md"
    assert store.download_path("snow-20260603130500", "issues.json").is_file()
    assert store.download_path("snow-20260603130500", "bogus") is None


def test_store_traversal_guard(store_root) -> None:
    assert store.read_run("../etc") is None
    assert store.download_path("..", "report.md") is None


# ── finalize_run (writes P2 artifacts into the run dir) ───────────────────────


def test_finalize_run_writes_plan_and_issues(tmp_path) -> None:
    run = tmp_path / "snow-x"
    run.mkdir()
    finalize_run(run, _meta(), generate=lambda p: "# plan from llm")
    assert (run / "correction-plan.md").read_text().startswith("# plan from llm")
    issues = json.loads((run / "issues.json").read_text())
    assert len(issues["children"]) == 1
