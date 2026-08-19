"""SSRF guard on the per-project ``gitBaseUrl`` setting (#610).

``gitBaseUrl`` is stored per project, settable by any authenticated caller via
``PATCH /api/projects/{project_id}/settings``, and becomes the git provider's
``_base_url`` -- the address of requests that carry a real credential. Two
independent properties are asserted here, because the first alone does not close
the leak:

  1. The two readers of ``gitBaseUrl`` refuse a metadata-range URL, and accept
     both a legitimate private-range (self-hosted) host and a public one.
  2. The provider HTTP clients do not follow redirects, so a host that passes
     the check cannot 302 the credential onward to one that would not.

The redirect case is proven against a real local HTTP server: the first hop
answers 302, the second records the credential header of anything that arrives,
and the test asserts the second hop was never reached.

Ported from TFactory's ``tests/test_git_provider_base_url_guard.py``
(TFactory#1116), which closed the identical defect there.
"""

from __future__ import annotations

import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

for _p in (
    Path(__file__).parent.parent / "apps" / "web-server",
    Path(__file__).parent.parent / "apps" / "backend",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytest.importorskip("httpx")

from server.services.git_base_url import safe_git_base_url  # noqa: E402


def _patch_resolve(monkeypatch, ip: str) -> None:
    """Force resolution of any hostname to ``ip`` (keeps the test hermetic)."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# ---------------------------------------------------------------------------
# 1. The URL check
# ---------------------------------------------------------------------------


def test_metadata_base_url_is_refused():
    """The cloud-metadata address is refused -- the core case."""
    with pytest.raises(ValueError):
        safe_git_base_url("http://169.254.169.254/latest/meta-data/")


def test_metadata_behind_a_hostname_is_refused(monkeypatch):
    """A public-looking hostname that resolves to metadata is still refused."""
    _patch_resolve(monkeypatch, "169.254.169.254")
    with pytest.raises(ValueError):
        safe_git_base_url("https://gitlab.internal.example.com")


def test_self_hosted_private_base_url_is_accepted():
    """A LAN self-hosted GitLab is the case allow_private=True exists for.

    If this breaks, real self-hosted deployments break with it.
    """
    assert safe_git_base_url("https://10.1.2.3") == "https://10.1.2.3"


def test_public_base_url_is_accepted(monkeypatch):
    """An ordinary public base URL passes.

    A genuinely global address: 203.0.113.0/24 (TEST-NET-3) reads as private to
    ``ipaddress``, so it would have proved nothing about the public case.
    """
    import ipaddress

    assert ipaddress.ip_address("140.82.121.4").is_global
    _patch_resolve(monkeypatch, "140.82.121.4")
    assert safe_git_base_url("https://gitlab.example.com") == "https://gitlab.example.com"


def test_unset_base_url_is_left_alone():
    """No configured base URL means the provider default -- nothing to check."""
    assert safe_git_base_url(None) is None
    assert safe_git_base_url("") == ""


def test_non_http_scheme_is_refused():
    """file:// and friends are not a git base URL."""
    with pytest.raises(ValueError):
        safe_git_base_url("file:///etc/passwd")


def test_unresolvable_base_url_fails_closed(monkeypatch):
    """DNS failure is unsafe, not a pass-through."""

    def boom(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(ValueError):
        safe_git_base_url("https://gitlab.nonexistent.invalid")


# ---------------------------------------------------------------------------
# 1b. The guard is WIRED -- both readers actually route through it
#
# Calling safe_git_base_url directly only proves the guard works, not that
# anything uses it. These drive the real provider-construction functions with a
# hostile project record, which is the property that fails if a reader stops
# calling the guard.
# ---------------------------------------------------------------------------


def _project_with_base_url(monkeypatch, provider: str, base_url: str, tmp_path) -> None:
    """Make both readers see one project whose gitBaseUrl is ``base_url``."""
    from server.routes import projects as projects_module

    record = {
        "p1": {
            "path": str(tmp_path),
            "settings": {
                "gitProvider": provider,
                "gitToken": "glpat-secret",
                "gitRepo": "owner/repo",
                "gitOrg": "org",
                "gitProject": "proj",
                "gitBaseUrl": base_url,
            },
        }
    }
    monkeypatch.setattr(projects_module, "load_projects", lambda: record)


@pytest.mark.parametrize("provider", ["gitlab", "azure_devops"])
def test_routes_github_reader_refuses_metadata(monkeypatch, tmp_path, provider):
    """routes/github.py must not build a provider aimed at the metadata range."""
    from server.routes.github import _get_project_provider

    _project_with_base_url(monkeypatch, provider, "http://169.254.169.254/latest/", tmp_path)
    with pytest.raises(ValueError):
        _get_project_provider("p1")


def test_auto_fix_reader_refuses_metadata(monkeypatch, tmp_path):
    """auto_fix_service reads gitBaseUrl too, and must route through the guard."""
    from server.services.auto_fix_service import _provider_for

    _project_with_base_url(monkeypatch, "gitlab", "http://169.254.169.254/latest/", tmp_path)
    with pytest.raises(ValueError):
        _provider_for("p1")


def test_routes_github_reader_accepts_self_hosted(monkeypatch, tmp_path):
    """The private-range self-hosted case still builds a working provider."""
    from server.routes.github import _get_project_provider

    _project_with_base_url(monkeypatch, "gitlab", "https://10.1.2.3", tmp_path)
    provider = _get_project_provider("p1")
    assert provider._base_url == "https://10.1.2.3"


def test_auto_fix_reader_accepts_self_hosted(monkeypatch, tmp_path):
    """Same for auto_fix_service -- the guard must not break self-hosted there."""
    from server.services.auto_fix_service import _provider_for

    _project_with_base_url(monkeypatch, "gitlab", "https://10.1.2.3", tmp_path)
    assert _provider_for("p1")._base_url == "https://10.1.2.3"


# ---------------------------------------------------------------------------
# 2. The redirect check (the part that actually closes the credential leak)
# ---------------------------------------------------------------------------


class _RedirectHandler(BaseHTTPRequestHandler):
    """First hop: 302 everything to the recorder."""

    target = ""

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's required name
        self.send_response(302)
        self.send_header("Location", self.target)
        self.end_headers()

    def log_message(self, *args):
        pass


class _RecorderHandler(BaseHTTPRequestHandler):
    """Second hop: records the credential header of anything that arrives."""

    received: list[str | None] = []

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's required name
        type(self).received.append(
            self.headers.get("PRIVATE-TOKEN") or self.headers.get("Authorization")
        )
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def _serve(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.asyncio
async def test_redirect_does_not_re_send_the_credential():
    """A 302 from a passing host must not carry the PAT to the redirect target.

    httpx strips ``Authorization`` across origins but does NOT strip GitLab's
    ``PRIVATE-TOKEN``, so if these clients ever followed redirects the PAT would
    go to the second host in full. This is what the URL check alone cannot do:
    the first host passed it.
    """
    _RecorderHandler.received = []
    recorder = _serve(_RecorderHandler)
    try:
        _RedirectHandler.target = f"http://127.0.0.1:{recorder.server_port}/stolen"
        redirector = _serve(_RedirectHandler)
        try:
            from runners.github.providers.gitlab_provider import GitLabProvider

            provider = GitLabProvider(
                _repo="owner/repo",
                _token="glpat-secret",
                _base_url=f"http://127.0.0.1:{redirector.server_port}",
            )
            async with provider._client() as client:
                resp = await client.get("/api/v4/projects")

            # The credential assertion comes first: it is the one that shows what
            # an attacker actually gets.
            assert _RecorderHandler.received == [], (
                "credential-bearing request followed the redirect and carried: "
                f"{_RecorderHandler.received}"
            )
            # The client stops at the 302 instead of chasing it.
            assert resp.status_code == 302
        finally:
            redirector.shutdown()
    finally:
        recorder.shutdown()


def test_every_provider_client_declines_redirects():
    """All three credential-carrying provider clients are no-redirect.

    These clients live in the vendored factory-github canonical, so PFactory
    cannot set the flag itself without drifting the shared layer -- httpx's
    default supplies it today. That makes this a characterisation test on a
    property PFactory's credential safety depends on: if the canonical ever
    starts following redirects, this fails here rather than leaking a PAT in
    production. Making the posture explicit upstream is tracked in Factory#825.
    """
    from runners.github.providers.azure_devops_provider import AzureDevOpsProvider
    from runners.github.providers.gitlab_provider import GitLabProvider
    from runners.github.providers.http_github_provider import HttpGitHubProvider

    for provider in (
        GitLabProvider(_repo="o/r", _token="t"),
        AzureDevOpsProvider(_repo="o/r", _pat="t"),
        HttpGitHubProvider(_repo="o/r", _token="t"),
    ):
        client = provider._client()
        assert client.follow_redirects is False, type(provider).__name__


def test_no_provider_http_client_enables_redirects():
    """The ad-hoc clients count too, and no provider instance exposes them.

    Several ``httpx.AsyncClient`` call sites are constructed inline, so an
    attribute check on the three ``_client()`` factories cannot see them. This
    parses the modules and asserts none of them turns redirects on.
    """
    import ast

    import runners.github.providers as providers_pkg

    checked = 0
    for module in sorted(Path(providers_pkg.__file__).parent.glob("*_provider.py")):
        for node in ast.walk(ast.parse(module.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "AsyncClient"):
                continue
            checked += 1
            for kw in node.keywords:
                if kw.arg == "follow_redirects":
                    assert kw.value.value is False, (
                        f"{module.name}:{node.lineno} follows redirects while carrying a credential"
                    )
    assert checked >= 8, f"expected the 8 known client sites, found {checked}"
