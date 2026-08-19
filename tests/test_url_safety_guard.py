"""The outbound-URL guard, in both postures (PFactory#517 follow-up).

Ported from AIFactory's ``tests/test_url_safety_guard.py`` so the shared module
carries the same proof in both repos, plus the PFactory-specific cases: the
regression that motivated replacing the local copy (``fd00:ec2::254``), and the
LLM-provider call sites that were reaching ``urlopen``/``httpx`` with an
unchecked base URL (CodeQL py/partial-ssrf).

The point of the strict/permissive split is that the fleet runs a self-hosted
Ollama on a private address. A guard that blocks RFC-1918 outright is not
"more secure" here -- it breaks the product, gets reverted, and leaves nothing.
So the permissive posture keeps only the guards that cost no legitimate use.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

import pytest

_WEB = Path(__file__).resolve().parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from pydantic import SecretStr  # noqa: E402

from factory_common.url_safety import (  # noqa: E402
    assert_safe_outbound_url,
    build_no_redirect_opener,
)
from server.routes import settings as settings_mod  # noqa: E402
from server.routes.git import (  # noqa: E402
    UnsafeProbeURLError,
    assert_safe_probe_url,
    check_ollama_running,
)
from server.services.ollama_utils import fetch_ollama_models  # noqa: E402

# 169.254.169.254 is the cloud metadata address. Both postures must refuse it;
# that is the whole reason the permissive posture is not simply "no check".
METADATA_HOST = "http://169.254.169.254"
METADATA = f"{METADATA_HOST}/latest/meta-data/iam/security-credentials/"


@pytest.mark.parametrize("allow_private", [False, True])
def test_metadata_is_refused_in_both_postures(allow_private: bool) -> None:
    with pytest.raises(ValueError, match="link-local/metadata"):
        assert_safe_outbound_url(METADATA, allow_private=allow_private)


@pytest.mark.parametrize("allow_private", [False, True])
def test_ipv6_metadata_is_refused_in_both_postures(allow_private: bool) -> None:
    """The regression that justified replacing the local copy.

    ``fd00:ec2::254`` is IMDS over IPv6. It is unique-local, so it is neither
    ``is_link_local`` nor ``is_reserved`` -- the guard this module replaced
    checked exactly those two and let it through.
    """
    with pytest.raises(ValueError, match="link-local/metadata"):
        assert_safe_outbound_url(
            "http://[fd00:ec2::254]/latest/meta-data/", allow_private=allow_private
        )


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://h/f"])
@pytest.mark.parametrize("allow_private", [False, True])
def test_non_http_schemes_are_refused_in_both_postures(url: str, allow_private: bool) -> None:
    with pytest.raises(ValueError, match="unsupported URL scheme"):
        assert_safe_outbound_url(url, allow_private=allow_private)


def test_strict_posture_refuses_loopback() -> None:
    with pytest.raises(ValueError, match="non-public"):
        assert_safe_outbound_url("http://127.0.0.1:8080/v1/models")


def test_permissive_posture_allows_the_self_hosted_ollama_case() -> None:
    """The regression this split exists to prevent: a local Ollama must work."""
    assert_safe_outbound_url("http://127.0.0.1:11434/api/tags", allow_private=True)
    assert_safe_outbound_url("http://10.0.0.5:11434/api/tags", allow_private=True)


def test_the_checked_url_is_returned_unchanged() -> None:
    """Call sites request the value they validated, not the original expression."""
    url = "http://127.0.0.1:11434/api/tags"
    assert assert_safe_outbound_url(url, allow_private=True) == url


def test_a_url_with_no_host_is_refused() -> None:
    with pytest.raises(ValueError, match="no host"):
        assert_safe_outbound_url("http:///nohost")


def test_an_unresolvable_host_is_refused_rather_than_attempted() -> None:
    with pytest.raises(ValueError, match="cannot resolve host"):
        assert_safe_outbound_url("http://no-such-host.invalid/x", allow_private=True)


def test_the_opener_does_not_follow_redirects() -> None:
    """A guard that runs before the request is defeated by a 302 to 169.254.169.254."""
    handlers = build_no_redirect_opener().handlers
    assert not any(type(h) is urllib.request.HTTPRedirectHandler for h in handlers)


# --------------------------------------------------------------------------
# The call sites the alerts pointed at. Remove a guard call and the matching
# case goes red instead of silently making a live request.
# --------------------------------------------------------------------------


async def test_fetch_ollama_models_refuses_a_metadata_base_url() -> None:
    """httpx call site (services/ollama_utils.py)."""
    with pytest.raises(ValueError, match="link-local/metadata"):
        await fetch_ollama_models(METADATA_HOST)


def test_check_ollama_running_refuses_a_non_http_base_url(caplog) -> None:
    """urllib call site (routes/git.py). Refusal reads as 'not running'.

    The log assertion is the load-bearing half: ``False`` alone would also be
    returned by a failed request, so without it the case passes with the guard
    deleted.
    """
    with caplog.at_level("WARNING"):
        assert check_ollama_running("file:///etc") is False
    assert "refusing to probe Ollama" in caplog.text


def _settings_probes() -> dict:
    """One entry per ``routes/settings.py`` sink the alerts pointed at."""
    s = settings_mod
    key = SecretStr("x" * 24)
    return {
        "openai_compat_list": lambda: s.list_openai_compat_models(baseUrl=METADATA_HOST),
        "openai_compat_test": lambda: s.test_openai_compat_connection(
            s.OpenAICompatTestRequest(baseUrl=METADATA_HOST)
        ),
        "ollama_pull": lambda: s.pull_ollama_model(modelName="m", ollamaBaseUrl=METADATA_HOST),
        "ollama_test": lambda: s.test_ollama_connection(ollamaBaseUrl=METADATA_HOST, modelName="m"),
        "api_profile_test": lambda: s.test_api_connection(
            s.TestConnectionRequest(baseUrl=METADATA_HOST, apiKey=key)
        ),
        "api_profile_discover": lambda: s.discover_api_models(
            s.TestConnectionRequest(baseUrl=METADATA_HOST, apiKey=key)
        ),
    }


@pytest.mark.parametrize("probe", list(_settings_probes()))
async def test_settings_provider_probes_refuse_the_metadata_address(probe, caplog) -> None:
    """Each handler already wraps its body in ``except Exception`` and returns an
    error envelope, so the envelope alone proves nothing — a request that simply
    failed looks identical. The assertion is on the logged reason, which only
    appears if the guard ran.
    """
    with caplog.at_level("ERROR"):
        result = await _settings_probes()[probe]()
    assert result["success"] is False
    assert "link-local/metadata" in caplog.text


def test_the_mcp_probe_guard_still_raises_its_own_error_type() -> None:
    """``check_mcp_health`` branches on ``UnsafeProbeURLError``; keep it distinct."""
    with pytest.raises(UnsafeProbeURLError):
        assert_safe_probe_url(METADATA)
    assert assert_safe_probe_url("http://127.0.0.1:3000/health")
