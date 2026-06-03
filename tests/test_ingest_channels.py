"""Tests for the plan intake channels (issue #4).

Covers the unifying channel layer (text / bytes / GitHub body), the CLI entry
point, and the MCP-tool callable. PDF / DOCX deps (``pypdf`` / ``python-docx``)
may be absent, so path / bytes tests use markdown content, which needs no binary
loader. The FastAPI route is tested separately, guarded by
``pytest.importorskip("fastapi")`` (the conftest also auto-skips
fastapi-importing test modules when the dep is absent).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pydantic")

# Make the backend importable (mirrors conftest, but keep this module standalone).
_BACKEND = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.ingest import channels  # noqa: E402
from plan.ingest.cli import main as cli_main  # noqa: E402
from plan.ingest.mcp_tool import plan_ingest  # noqa: E402

MARKDOWN_PLAN = """# Login Feature

## Acceptance Criteria

- User can log in with a valid email and password
- Invalid credentials show an error message
- A locked account cannot log in
"""


# ── ingest_text ────────────────────────────────────────────────────────

def test_ingest_text_markdown_builds_plan():
    plan = channels.ingest_text(MARKDOWN_PLAN, source_channel="mcp")

    assert plan.title == "Login Feature"
    assert plan.source_channel == "mcp"
    assert plan.source_format == "markdown"
    assert [c.text for c in plan.criteria] == [
        "User can log in with a valid email and password",
        "Invalid credentials show an error message",
        "A locked account cannot log in",
    ]
    assert [c.id for c in plan.criteria] == ["AC#1", "AC#2", "AC#3"]
    assert plan.raw_text == MARKDOWN_PLAN
    # content_hash is set on construction and matches the canonical content.
    assert plan.content_hash
    assert plan.hash_matches()


def test_ingest_text_records_channel():
    plan = channels.ingest_text(MARKDOWN_PLAN, source_channel="cli")
    assert plan.source_channel == "cli"


# ── ingest_bytes ───────────────────────────────────────────────────────

def test_ingest_bytes_markdown():
    data = MARKDOWN_PLAN.encode("utf-8")
    plan = channels.ingest_bytes(data, filename="plan.md")

    assert plan.source_channel == "portal"
    assert plan.source_format == "markdown"
    assert len(plan.criteria) == 3
    assert plan.raw_text == MARKDOWN_PLAN
    assert plan.hash_matches()


# ── ingest_path ────────────────────────────────────────────────────────

def test_ingest_path_markdown(tmp_path: Path):
    f = tmp_path / "plan.md"
    f.write_text(MARKDOWN_PLAN)

    plan = channels.ingest_path(f)

    assert plan.source_channel == "cli"
    assert plan.source_format == "markdown"
    assert len(plan.criteria) == 3
    assert plan.raw_text == MARKDOWN_PLAN


# ── ingest_github_body ─────────────────────────────────────────────────

def test_ingest_github_body_issue_channel():
    plan = channels.ingest_github_body(MARKDOWN_PLAN)
    assert plan.source_channel == "github_issue"
    assert len(plan.criteria) == 3


def test_ingest_github_body_discussion_channel():
    plan = channels.ingest_github_body(MARKDOWN_PLAN, discussion=True)
    assert plan.source_channel == "github_discussion"


# ── CLI ────────────────────────────────────────────────────────────────

def test_cli_main_prints_json(tmp_path: Path, capsys):
    f = tmp_path / "plan.md"
    f.write_text(MARKDOWN_PLAN)

    rc = cli_main([str(f), "--json"])
    assert rc == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["plan_id"].endswith("login-feature")
    assert len(payload["criteria"]) == 3
    assert payload["source_channel"] == "cli"


def test_cli_main_error_exit_code(tmp_path: Path, capsys):
    f = tmp_path / "empty.md"
    f.write_text("no acceptance criteria here, just prose\n")

    rc = cli_main([str(f)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "error:" in err


# ── MCP tool ───────────────────────────────────────────────────────────

def test_plan_ingest_with_text_returns_dict():
    result = plan_ingest(text=MARKDOWN_PLAN)
    assert isinstance(result, dict)
    assert result["source_channel"] == "mcp"
    assert len(result["criteria"]) == 3
    assert result["content_hash"]


def test_plan_ingest_requires_exactly_one_source():
    with pytest.raises(ValueError):
        plan_ingest()
    with pytest.raises(ValueError):
        plan_ingest(text=MARKDOWN_PLAN, path="/tmp/x.md")


def test_plan_ingest_with_path(tmp_path: Path):
    f = tmp_path / "plan.md"
    f.write_text(MARKDOWN_PLAN)
    result = plan_ingest(path=str(f))
    assert result["source_channel"] == "mcp"
    assert len(result["criteria"]) == 3


# ── DOCX (only if python-docx is importable) ───────────────────────────

def test_ingest_bytes_docx_if_available():
    docx = pytest.importorskip("docx")

    from io import BytesIO

    document = docx.Document()
    document.add_heading("Login Feature", level=1)
    document.add_heading("Acceptance Criteria", level=2)
    document.add_paragraph("User can log in", style="List Bullet")
    document.add_paragraph("Invalid creds error", style="List Bullet")
    buf = BytesIO()
    document.save(buf)

    plan = channels.ingest_bytes(buf.getvalue(), filename="plan.docx")
    assert plan.source_channel == "portal"
    assert len(plan.criteria) >= 2


# ── FastAPI route (only if fastapi is importable) ──────────────────────

def test_plan_intake_router_importorskip():
    pytest.importorskip("fastapi")

    # The route module lives in the web-server app; import it directly by path
    # so we don't depend on the whole `server` package importing cleanly.
    import importlib.util

    web_server = Path(__file__).resolve().parent.parent / "apps" / "web-server"
    if str(web_server) not in sys.path:
        sys.path.insert(0, str(web_server))

    route_path = web_server / "server" / "routes" / "plan_intake.py"
    spec = importlib.util.spec_from_file_location(
        "server.routes.plan_intake", route_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.router.prefix == "/api/plan"
    paths = {route.path for route in module.router.routes}
    assert "/api/plan/ingest-text" in paths
    assert "/api/plan/ingest" in paths
