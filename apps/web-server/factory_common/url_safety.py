"""One SSRF guard for outbound URLs a service fetches on a caller's behalf.

The fleet canonical. Vendored byte-identically into the services that use it;
edit it here and re-vendor, never the other way round.

There were three near-identical copies of this check in AIFactory before it was
consolidated (AIFactory#1265/#1266): ``apps/backend/security/url_guard.py``
(agent WebFetch, #370), ``routes/llm_endpoints.py::_assert_url_not_ssrf`` (BYO
LLM base_url, #323 H6), and nothing at all on the routes that actually take a
URL straight from a request body. Three copies is how the fourth caller ends up
unguarded, which is exactly what happened. It lives here (Factory#154/#161,
AIFactory#1270) so the next runtime that needs it imports rather than copies.

The public name ``assert_safe_outbound_url`` is load-bearing beyond Python: the
CodeQL barrier in each consumer's ``SsrfBarriers.qll`` registers it BY NAME.
Renaming it un-registers the barrier silently and reopens every alert it clears.

Two postures, because the fleet genuinely needs both:

``allow_private=False`` (default) -- the strict posture from #323. The host must
resolve to a PUBLIC address. Use it wherever the URL is attacker-supplied and
there is no legitimate reason to reach inside the network.

``allow_private=True`` -- for endpoints whose whole purpose is to reach an
operator-configured service on the local network: a self-hosted Ollama at
``http://localhost:11434``, an MCP server on a cluster-internal address. Here
blocking RFC-1918 would break the product, so this posture keeps the guards that
cost nothing legitimate -- an http(s)-only scheme, and a hard block on the cloud
metadata addresses, which are never a real Ollama or MCP server and are the
single highest-value SSRF target.

What neither posture fixes: DNS rebinding. The host is resolved here and
re-resolved by the transport at connect time, so a hostile DNS server can answer
differently the second time. Closing it needs IP-pinning at the socket layer.
Same residual as #370/#323, recorded rather than implied.
"""

from __future__ import annotations

import email.message
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import IO

from factory_common.client_errors import InputRejectedError

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
    check made before the request was sent (#323 H6)."""

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
    """An opener that refuses redirects. Pair with :func:`assert_safe_outbound_url`."""
    return urllib.request.build_opener(_NoRedirect)


def assert_safe_outbound_url(url: str, *, allow_private: bool = False) -> str:
    """Raise :class:`InputRejectedError` if ``url`` is unsafe to fetch server-side.

    Returns ``url`` unchanged, so a call site can wrap the value on its way into
    the request rather than checking one string and then fetching another. That
    is not decoration: the checked-then-ignored variant is the failure mode this
    module exists to stop, and it is also the shape static analysis can see --
    the barrier in ``.github/codeql/custom-queries/SsrfSanitized.ql`` is
    registered on this call, so only the value that flows OUT of it is cleared.

    ``InputRejectedError`` rather than a plain ``ValueError`` (TFactory#1073,
    landed independently in AIFactory's web-server before this module caught up
    -- TFactory#1111), and it subclasses ``ValueError``, so every existing
    ``except ValueError`` around a call site still catches it. What it adds is a
    mark: these messages are developer-written about the caller's own URL, so a
    handler may hand them back verbatim, while an unmarked ``ValueError``
    escaping some library this function calls gets a correlation id instead.

    Note what is NOT blessed: the resolve failure does not interpolate the
    ``socket.gaierror``. That would be third-party text riding out on a
    repo-owned exception, which is the shape that made CFactory's
    ``AdapterError`` leak an upstream host while looking safe. The host is the
    caller's own input and stays; the inner exception is kept on ``__cause__``.

    See the module docstring for what each posture does and does not promise.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise InputRejectedError(f"unsupported URL scheme {parsed.scheme!r} (only http/https)")
    host = parsed.hostname
    if not host:
        raise InputRejectedError("URL has no host")

    default_port = 443 if parsed.scheme == "https" else 80
    try:
        infos = socket.getaddrinfo(host, parsed.port or default_port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise InputRejectedError(f"cannot resolve host {host!r}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # Refused in both postures.
        if any(ip in net for net in _METADATA_NETS):
            raise InputRejectedError(
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
            raise InputRejectedError(f"refusing to fetch non-public address {ip} (SSRF)")

    return url


# A redirect chain long enough to be a real site is short. Five is more than
# http -> https -> www -> canonical -> trailing-slash, which is the longest
# legitimate chain anyone has produced an example of.
MAX_REDIRECT_HOPS = 5


def fetch_following_safe_redirects(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 15,
    allow_private: bool = False,
    max_hops: int = MAX_REDIRECT_HOPS,
) -> tuple[str, int, bytes]:
    """Fetch ``request``, following redirects only to URLs that pass the guard.

    Returns ``(final_url, status, body)``. Raises :class:`InputRejectedError`
    (a ``ValueError``) if any hop -- including the first -- fails
    :func:`assert_safe_outbound_url`.

    This exists because refusing redirects outright is the right answer only
    when *we* are the one fetching and can simply not follow. Redirects are
    ordinary on the public web (http->https, apex->www, shortened links), so a
    tool that refuses them is a tool that does not work. Following them without
    re-checking is the #1269 hole: the dangerous URL is the one nobody
    validated because nobody saw it.

    So every hop is validated before it is requested, using the same guard and
    the same posture as the first. ``Location`` is resolved against the URL it
    came from, because a relative ``Location`` is legal and ``urljoin`` is what
    decides where it actually points.

    Residual, stated rather than implied: the guard resolves a hostname and the
    socket resolves it again, so a hostile DNS server can answer differently the
    second time (DNS rebinding, #370). Closing that needs IP-pinning at the
    socket layer. It is the same residual every caller of this module carries,
    and it is NOT the redirect hole -- redirects are closed here.
    """
    opener = build_no_redirect_opener()
    for _ in range(max_hops + 1):
        assert_safe_outbound_url(url, allow_private=allow_private)
        # A fresh Request per hop. Reusing one and rewriting the url keeps the
        # previous host's Host header, which is its own small vulnerability.
        # S310 asks us to audit the scheme before opening a URL. The
        # assert_safe_outbound_url above IS that audit, and it is the strictest
        # one in the repo: http(s) only, enforced on this exact string, one
        # statement earlier, with no branch in between.
        hop = urllib.request.Request(  # noqa: S310
            url, headers=headers or {}, method="GET"
        )
        try:
            with opener.open(hop, timeout=timeout) as resp:
                return url, resp.getcode(), resp.read()
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") if exc.headers else None
            if not (exc.code in (301, 302, 303, 307, 308) and location):
                raise
            # _NoRedirect turned the 30x into this HTTPError, which is how the
            # hop is handed back to us unfollowed. Resolve and re-validate.
            url = urllib.parse.urljoin(url, location)
    raise InputRejectedError(f"too many redirects (>{max_hops})")
