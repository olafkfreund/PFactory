"""RFC-0007 (#86 PR-g): the approve-access CLI (thin client over the PR-f route)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.access_cli import build_request, format_result, main  # noqa: E402


def test_build_request_shapes_url_body_headers():
    url, body, headers = build_request(
        "http://h:3114/",
        "s1",
        resource="web",
        approved_by="olaf",
        scope="staging",
        token="tok",
    )
    assert url == "http://h:3114/api/plan/sessions/s1/access/approve"
    assert body == {"resource": "web", "approved_by": "olaf", "scope": "staging"}
    assert headers["Authorization"] == "Bearer tok"


def test_build_request_omits_optional_and_token():
    _url, body, headers = build_request(
        "http://h", "s", resource="r", approved_by="a", scope="x"
    )
    assert "approved_at" not in body and "Authorization" not in headers
    _u2, body2, _h = build_request(
        "http://h",
        "s",
        resource="r",
        approved_by="a",
        scope="x",
        approved_at="2026-06-16",
    )
    assert body2["approved_at"] == "2026-06-16"


def test_format_result_ok_and_refused():
    assert "approved: web -> curated" in format_result(
        {"ok": True, "resource": "web", "state": "curated"}
    )
    assert "NOT approved: mfa" in format_result(
        {"ok": False, "resource": "mfa", "reason": "class D"}
    )


def test_main_posts_and_returns_exit_code(monkeypatch):
    monkeypatch.delenv("APP_API_TOKEN", raising=False)
    monkeypatch.delenv("PFACTORY_MCP_SECRET", raising=False)
    sent = {}

    def fake_post(url, body, headers):
        sent.update(url=url, body=body)
        return {"ok": True, "resource": "web", "state": "curated"}

    rc = main(
        [
            "s1",
            "--resource",
            "web",
            "--approved-by",
            "olaf",
            "--scope",
            "staging",
            "--base-url",
            "http://h:3114",
        ],
        post=fake_post,
    )
    assert rc == 0
    assert sent["url"].endswith("/api/plan/sessions/s1/access/approve")
    assert sent["body"]["resource"] == "web"


def test_main_nonzero_exit_on_refusal():
    rc = main(
        ["s1", "--resource", "mfa", "--approved-by", "o", "--scope", "s"],
        post=lambda u, b, h: {"ok": False, "resource": "mfa", "reason": "class D"},
    )
    assert rc == 1
