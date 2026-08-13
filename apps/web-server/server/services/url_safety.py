"""One SSRF guard for outbound URLs the web-server fetches on a caller's behalf.

Ported from AIFactory's ``server/services/url_safety.py`` deliberately: same
module path, same public API, so the two repos share one guard rather than
growing a second dialect of it. PFactory already had a fourth copy of the idea
(``routes/git.py::assert_safe_probe_url``), which now delegates here — that copy
allowed ``fd00:ec2::254``, the IMDSv2 IPv6 metadata address, because it is
neither link-local nor reserved. Copies drift; this is what that drift looks
like.

Two postures, because the fleet genuinely needs both:

``allow_private=False`` (default) -- the strict posture. The host must resolve to
a PUBLIC address. Use it wherever the URL is attacker-supplied and there is no
legitimate reason to reach inside the network.

``allow_private=True`` -- for endpoints whose whole purpose is to reach an
operator-configured service on the local network: a self-hosted Ollama at
``http://localhost:11434``, an OpenAI-compatible server at
``http://localhost:8080`` (the placeholder the profile editor itself suggests),
an MCP server on a cluster-internal address. Here blocking RFC-1918 would break
the product, so this posture keeps the guards that cost nothing legitimate -- an
http(s)-only scheme, and a hard block on the cloud metadata addresses, which are
never a real Ollama or MCP server and are the single highest-value SSRF target.

What neither posture fixes: DNS rebinding. The host is resolved here and
re-resolved by the transport at connect time, so a hostile DNS server can answer
differently the second time. Closing it needs IP-pinning at the socket layer.
Recorded rather than implied (PFactory#517).
"""

from __future__ import annotations

import email.message
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import IO

# Link-local carries the cloud metadata services (169.254.169.254 on AWS/Azure,
# and GCP's metadata.google.internal resolves into the same /16). Reaching one
# of these yields instance credentials, so it is refused in BOTH postures --
# there is no deployment in which a user's own Ollama lives at 169.254.x.x.
_METADATA_NETS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fd00:ec2::254/128"),
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse 30x. A public URL that redirects to 169.254.169.254 defeats any
    check made before the request was sent."""

    def redirect_request(  # noqa: PLR0913 - signature is urllib's, not ours
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,  # noqa: ARG002 - part of the urllib override contract
        headers: email.message.Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Refuse the redirect instead of following it."""
        raise urllib.error.HTTPError(
            req.full_url, code, f"Redirect blocked (SSRF guard): {newurl}", headers, fp
        )


def build_no_redirect_opener() -> urllib.request.OpenerDirector:
    """An opener that refuses redirects. Pair with :func:`assert_safe_outbound_url`.

    ``httpx`` callers need no equivalent: it does not follow redirects unless
    asked (``follow_redirects=True``). ``urllib.request.urlopen`` does.
    """
    return urllib.request.build_opener(_NoRedirect)


def assert_safe_outbound_url(url: str, *, allow_private: bool = False) -> str:
    """Raise ``ValueError`` if ``url`` is unsafe to fetch server-side; else return it.

    See the module docstring for what each posture does and does not promise.

    Returns the checked value so call sites can request the URL they validated
    rather than re-reading the original expression. The two are the same string
    today, but validating one expression and using another is how a guard ends up
    decorative — and it is also the shape a dataflow barrier can see. (AIFactory's
    copy returns ``None``; ignoring the return value is still valid here, so the
    two remain interchangeable.)
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported URL scheme {parsed.scheme!r} (only http/https)")
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")

    default_port = 443 if parsed.scheme == "https" else 80
    try:
        infos = socket.getaddrinfo(host, parsed.port or default_port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve host {host!r}: {exc}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # Refused in both postures.
        if any(ip in net for net in _METADATA_NETS):
            raise ValueError(
                f"refusing to fetch link-local/metadata address {ip} — this is the "
                "cloud instance-credentials endpoint, never a real service URL"
            )
        if allow_private:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(f"refusing to fetch non-public address {ip} (SSRF)")
    return url
