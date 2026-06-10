"""Plan → docs emit (P2): Backstage + Confluence targets (fakes, no network).

Builds a real processed PlanSession, then exercises the two new targets with
injected fakes — asserting the GitHub Contents writes, the Backstage sync calls,
Confluence create-vs-update, availability gating, and that the orchestrator wires
them when configured.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.emit.docs import emit_docs, render_plan_docs  # noqa: E402
from plan.emit.docs.targets.backstage import BackstageTarget  # noqa: E402
from plan.emit.docs.targets.confluence import ConfluenceTarget  # noqa: E402
from plan.emit.docs.targets.github_writer import GitHubContentsWriter  # noqa: E402
from plan.service import PlanService  # noqa: E402

_PLAN = """# Refund API
A REST endpoint with auth + a Kubernetes deploy.
## Acceptance Criteria
- User can request a refund
- The endpoint requires a valid JWT
"""


def _session():
    svc = PlanService()
    s = svc.ingest_text(_PLAN, title="Refund API", category="software")
    svc.process(s.session_id)
    return svc.get(s.session_id)


# ── GitHub Contents writer (fake gh api) ────────────────────────────────


class _FakeGh:
    """In-memory GitHub Contents API: tracks files by path."""

    def __init__(self):
        self.files: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, path, body):
        import base64
        self.calls.append((method, path))
        # contents path: /repos/o/r/contents/<file>?ref=...
        file = path.split("/contents/", 1)[1].split("?", 1)[0]
        if method == "GET":
            if file not in self.files:
                raise RuntimeError("404")
            return {"sha": f"sha-{file}",
                    "content": base64.b64encode(self.files[file].encode()).decode()}
        # PUT
        self.files[file] = base64.b64decode(body["content"]).decode()
        return {"content": {"path": file}}


def test_github_writer_create_then_update():
    gh = _FakeGh()
    w = GitHubContentsWriter("o/r", api=gh)
    w.put_file("techdocs/plans/x.md", "hello", "msg")
    assert gh.files["techdocs/plans/x.md"] == "hello"
    # second write reads sha then updates (idempotent path)
    w.put_file("techdocs/plans/x.md", "world", "msg2")
    assert gh.files["techdocs/plans/x.md"] == "world"
    assert ("GET", "/repos/o/r/contents/techdocs/plans/x.md?ref=main") in gh.calls


# ── Backstage target ────────────────────────────────────────────────────


def test_backstage_writes_repo_and_syncs():
    s = _session()
    bundle = render_plan_docs(s)
    gh = _FakeGh()
    synced: list[tuple[str, str]] = []

    def http(method, url):
        synced.append((method, url))
        return 200

    t = BackstageTarget(
        base_url="https://bs.example/backstage", repo="o/r", git_write=True,
        writer=GitHubContentsWriter("o/r", api=gh), http=http,
    )
    res = t.publish(bundle)
    assert res.status == "written"
    # wrote page + registry + index into techdocs/plans/
    assert f"techdocs/plans/{bundle.slug}.md" in gh.files
    assert "techdocs/plans/registry.json" in gh.files
    assert "techdocs/plans/index.md" in gh.files
    # triggered catalog refresh + techdocs sync
    assert ("POST", "https://bs.example/backstage/api/catalog/refresh") in synced
    assert any("techdocs/sync" in u for _, u in synced)


def test_backstage_dry_run_does_not_write():
    s = _session()
    bundle = render_plan_docs(s)
    gh = _FakeGh()
    t = BackstageTarget(base_url="https://bs.example", repo="o/r", git_write=False,
                        writer=GitHubContentsWriter("o/r", api=gh), http=lambda m, u: 200)
    res = t.publish(bundle)
    assert res.status == "written"
    assert res.detail.get("dry_run") is True
    assert gh.files == {}  # no git write without opt-in


def test_backstage_unavailable_without_base_url(monkeypatch):
    monkeypatch.delenv("BACKSTAGE_BASE_URL", raising=False)
    assert BackstageTarget().available() is False


# ── Confluence target ───────────────────────────────────────────────────


class _Resp:
    def __init__(self, code, payload):
        self.status_code = code
        self._p = payload

    def json(self):
        return self._p


class _FakeConfluence:
    def __init__(self, existing=None):
        self.existing = existing  # None => not found
        self.calls: list[str] = []

    def get(self, url, *, params=None):
        self.calls.append("get")
        if self.existing:
            return _Resp(200, {"results": [self.existing]})
        return _Resp(200, {"results": []})

    def post(self, url, *, json):
        self.calls.append("post:" + url.rsplit("/", 1)[-1])
        return _Resp(200, {"id": "new-id"})

    def put(self, url, *, json):
        self.calls.append("put")
        return _Resp(200, {"id": json["id"]})


def test_confluence_creates_when_absent():
    s = _session()
    bundle = render_plan_docs(s)
    c = _FakeConfluence(existing=None)
    t = ConfluenceTarget(base_url="https://x.atlassian.net", token="t", space="ENG", client=c)
    res = t.publish(bundle)
    assert res.status == "written"
    assert res.detail["action"] == "created"
    assert "post:content" in c.calls  # created the page


def test_confluence_updates_when_present():
    s = _session()
    bundle = render_plan_docs(s)
    c = _FakeConfluence(existing={"id": "p1", "version": {"number": 3}})
    t = ConfluenceTarget(base_url="https://x.atlassian.net", token="t", space="ENG", client=c)
    res = t.publish(bundle)
    assert res.detail["action"] == "updated"
    assert "put" in c.calls


def test_confluence_unavailable_without_config():
    assert ConfluenceTarget(base_url="", token="", space="").available() is False


# ── orchestrator wiring ─────────────────────────────────────────────────


def test_resolve_adds_backstage_and_confluence_when_configured(tmp_path, monkeypatch):
    from plan.emit.docs.emit_docs import _resolve_targets

    monkeypatch.setenv("BACKSTAGE_BASE_URL", "https://bs.example")
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://x.atlassian.net")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "t")
    monkeypatch.setenv("CONFLUENCE_SPACE", "ENG")
    # Resolve only (no publish ⇒ no network): the repo target is always present,
    # and the two remote targets are added because their env config is set.
    names = {t.name for t in _resolve_targets(tmp_path, "", repo="o/r")}
    assert names == {"repo", "backstage", "confluence"}


def test_resolve_repo_only_when_nothing_configured(tmp_path, monkeypatch):
    from plan.emit.docs.emit_docs import _resolve_targets

    for v in ("BACKSTAGE_BASE_URL", "CONFLUENCE_BASE_URL", "PFACTORY_DOCS_BACKSTAGE",
              "PFACTORY_DOCS_CONFLUENCE"):
        monkeypatch.delenv(v, raising=False)
    names = [t.name for t in _resolve_targets(tmp_path, "", repo=None)]
    assert names == ["repo"]  # GitHub/repo default when nothing else specified
