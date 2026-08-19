"""Module-owned runtime invariants for the vendored hub layer (Factory#818).

WHY THIS FILE IS NOT INSIDE THE PACKAGE. ``apps/web-server/factory_common/`` is
vendored byte-identically from the hub canonical
(``shared/factory-common/factory_common/``) and
``.github/workflows/factory-common-drift.yml`` derives the expected module set
by LISTING that canonical directory. Dropping a companion into the package
would therefore fail the drift gate as an unexpected module, and landing it in
the hub first would force the stdlib-only hub layer to import this repo's
registry. So the companion sits beside the package instead.

That is not a workaround, it is the first evidence the pattern generalises:
**a package can be watched by a companion that does not own its source.** The
credential-seam pilot only ever declared for first-party code.

Three seams are declared here, all of them cross-cutting relations that no
single-file linter and no type checker can see:

* the SSRF guard seam -- ``url_safety`` is the fleet canonical, registered BY
  NAME as a CodeQL barrier in every consumer, so its behaviour and its exported
  name are both load-bearing.
* the vendored-surface seam -- a module can be vendored and never imported,
  which is exactly how ``logsafe.py`` (Factory#717) and ``url_safety.py``
  (Factory#734) spent months vendored-but-ungated.
* the sanitiser seam -- ``secrets`` and ``logsafe`` are asserted on OUTPUT, not
  on source, because a rule change that quietly stopped matching leaves the
  source looking correct.

Every check here is asserted against behaviour with fixed, synthetic probes.
None of them touches the network: every URL probe uses a literal IP address, so
``getaddrinfo`` answers from the string rather than from DNS.

WHAT IS DELIBERATELY NOT CHECKED HERE, recorded rather than implied:

* that ``logsafe`` keeps its literal ``.replace`` chain. CodeQL recognises that
  chain as the ``py/log-injection`` barrier, so swapping it for ``str.translate``
  would be behaviourally identical and would silently un-register the barrier.
  A runtime check cannot see the difference. It belongs in CI, on the source.
* that ``.hub-sha`` names a squash-merge commit on hub ``main``. That needs the
  hub, so it stays the drift workflow's job; only the SHAPE of the pin is
  checked here, because a truncated or empty pin makes that workflow resolve
  nothing.

Importing this module registers; it runs nothing. Verification happens only when
a caller asks (``registry.verify_all()``), and only when FACTORY_INVARIANTS=1.
"""

# The product imports are lazy ON PURPOSE: registration must not drag in the
# modules being watched, so that importing this companion cannot fail because a
# module it watches is broken. A check that cannot be registered watches nothing.
# ruff: noqa: PLC0415

from __future__ import annotations

import re
import urllib.request
from collections.abc import Iterator
from pathlib import Path

# The registry lives under apps/backend, this companion under apps/web-server.
# Both roots are on sys.path at runtime, and the lint ratchet puts the sibling
# app packages on MYPYPATH for exactly this reason, so the static import
# resolves in both. That it needed checking rather than assuming is itself a
# finding for the RFC: where the registry lives is the first thing the RFC has
# to decide, because a companion for a package in another source root is not a
# corner case -- it is what watching vendored code looks like.
from factory_invariants import registry

_PKG = "factory_common"

# The cloud instance-credentials endpoint. Refused in BOTH postures -- there is
# no deployment in which a user's own Ollama or MCP server lives at 169.254.x.x.
_METADATA_URL = "http://169.254.169.254/latest/meta-data/"
# A literal public IP, so the probe resolves without DNS and without a network.
_PUBLIC_URL = "http://93.184.216.34/probe"
# The scheme probe. It carries a HOST, and a public literal one, so the only
# thing that can reject it is the scheme check. The first version of this probe
# was ``file:///etc/passwd`` and its own mutation test failed it: with ``file``
# added to the allowlist the URL was still refused, by the no-host branch, so
# the check reported a pass for a reason that had nothing to do with schemes.
_BAD_SCHEME_URL = "gopher://93.184.216.34/probe"
# A literal loopback address: refused by the strict posture, allowed by the
# operator-configured-service posture.
_LOOPBACK_URL = "http://127.0.0.1:9/probe"

# A redirect chain long enough to be a real site is short; a chain long enough
# to be an amplifier is not. Outside this band the constant has stopped
# describing the web.
_HOPS_FLOOR = 1
_HOPS_CEILING = 20

# A log line long enough to be a denial of service against the sink, short
# enough that truncation is an anomaly rather than routine.
_LOG_CAP_FLOOR = 200
_LOG_CAP_CEILING = 100_000

_HUB_SHA = re.compile(r"^[0-9a-f]{40}$")

# The vendored layer is a layer, not a file. If it ever holds one module the
# loops below are measuring almost nothing, and a vacuous loop looks identical
# to a clean one.
_MIN_VENDORED_MODULES = 2

_SUCCESS_CASES = ((200, True), (299, True), (199, False), (300, False))


def _opener_refuses_redirects(opener: urllib.request.OpenerDirector, what: str) -> Iterator[str]:
    """The opener must carry a redirect handler that OVERRIDES urllib's follow.

    ``build_opener`` substitutes a subclass for a default handler silently. If
    the subclass is dropped the opener still works, still returns 200s, and
    follows every 30x -- which is how a checked public URL ends up fetching
    169.254.169.254 (#323 H6). Nothing about that failure is visible without
    inspecting the handler chain or making a real redirecting request.
    """
    base = urllib.request.HTTPRedirectHandler
    # typeshed does not declare ``OpenerDirector.handlers``, though urllib has
    # built it since 2.x. Read it defensively rather than cast a claim into the
    # types: if it ever stops existing this reports an empty chain, and the
    # branch below names that as a finding rather than passing quietly.
    chain: list[object] = getattr(opener, "handlers", [])
    handlers = [h for h in chain if isinstance(h, urllib.request.HTTPRedirectHandler)]
    if not handlers:
        yield f"{what} carries no redirect handler at all, so urllib's default follow applies"
        return
    if any(type(h).redirect_request is base.redirect_request for h in handlers):
        yield (
            f"{what} carries urllib's stock HTTPRedirectHandler: 30x responses are FOLLOWED, "
            "not refused"
        )


def _url_safety_refuses_what_it_promises_to_refuse() -> Iterator[str]:
    """The SSRF guard actually rejects, in both postures, and passes a value THROUGH.

    Asserted on BEHAVIOUR with fixed probes rather than on the source of
    ``_METADATA_NETS``. The guard is the fleet canonical and its name is
    registered as a CodeQL barrier in four repos, so an edit that narrows what
    it rejects keeps clearing alerts in all four while looking correct in
    review -- the barrier is registered on the call, not on what the call does.

    The probes are fixed rather than derived from ``_METADATA_NETS`` for the
    reason the pilot's redaction check recorded: a probe derived from the table
    adapts to a mutation of the table and can no longer see it.
    """
    from factory_common import url_safety
    from factory_common.client_errors import InputRejectedError

    def refused(url: str, *, allow_private: bool) -> str | None:
        """None if the guard refused ``url``, else what it did instead."""
        try:
            url_safety.assert_safe_outbound_url(url, allow_private=allow_private)
        except InputRejectedError:
            return None
        except Exception as exc:  # noqa: BLE001 - the wrong type is itself the finding
            return (
                f"raised {type(exc).__name__} rather than InputRejectedError, so every "
                "`except ValueError` handler around a call site stops catching it"
            )
        return "was ACCEPTED"

    for posture in (False, True):
        why = refused(_METADATA_URL, allow_private=posture)
        if why:
            yield f"the cloud metadata address {why} with allow_private={posture}"

    why = refused(_BAD_SCHEME_URL, allow_private=True)
    if why:
        yield (
            f"a non-http(s) scheme {why}: the http(s)-only check is the one guard "
            "both postures share"
        )

    why = refused(_LOOPBACK_URL, allow_private=False)
    if why:
        yield (
            f"a loopback address {why} by the STRICT posture, which is the posture "
            "for attacker-supplied URLs"
        )

    # The permissive posture must still work, or every caller routes around it.
    try:
        url_safety.assert_safe_outbound_url(_LOOPBACK_URL, allow_private=True)
    except InputRejectedError as exc:
        yield (
            f"allow_private=True refused a loopback address ({exc.client_message}); the "
            "operator-configured-service posture is unusable and callers will fork the guard"
        )

    # The value must come back out. A call site that checks one string and then
    # fetches another is the failure this module exists to stop, and it is also
    # the only shape the CodeQL barrier can follow.
    try:
        returned = url_safety.assert_safe_outbound_url(_PUBLIC_URL)
    except InputRejectedError as exc:
        yield (
            f"a public literal address was refused ({exc.client_message}); the guard is "
            "blocking legitimate traffic"
        )
    else:
        if returned != _PUBLIC_URL:
            yield (
                f"the guard returned {returned!r} for {_PUBLIC_URL!r}: a call site wrapping "
                "the value now fetches something other than what was checked"
            )

    if not _HOPS_FLOOR <= url_safety.MAX_REDIRECT_HOPS <= _HOPS_CEILING:
        yield (
            f"MAX_REDIRECT_HOPS is {url_safety.MAX_REDIRECT_HOPS}: outside the band where it "
            "describes real redirect chains rather than disabling the tool or amplifying a fetch"
        )

    yield from _opener_refuses_redirects(
        url_safety.build_no_redirect_opener(), "build_no_redirect_opener()"
    )


def _the_vendored_surface_is_whole() -> Iterator[str]:
    """Every vendored module is actually imported, and the hub pin has a shape.

    THE FAILURE THIS WATCHES: a module can be vendored into the tree and never
    referenced. ``logsafe.py`` and ``url_safety.py`` each spent months in
    exactly that state (Factory#717, #734) -- present on disk, absent from every
    list that named the package's contents, so the gates that scanned "the
    package" scanned around them.

    ``from factory_common.http import ...`` in ``__init__`` binds ``http`` as an
    attribute of the package, so attribute presence is a faithful proxy for
    "``__init__`` pulls this module in" without parsing anything.
    """
    import factory_common

    pkg_dir = Path(factory_common.__file__).resolve().parent
    modules = sorted(p for p in pkg_dir.glob("*.py") if p.stem != "__init__")
    if len(modules) < _MIN_VENDORED_MODULES:
        # Named as an empty measurement rather than reported as clean: the loop
        # below is vacuously green on an empty directory.
        yield (
            f"{pkg_dir} holds {len(modules)} module(s) beside __init__.py — too few for this to "
            "be the vendored hub layer, so the check below examined almost nothing"
        )
    for path in modules:
        if not hasattr(factory_common, path.stem):
            yield (
                f"{path.name} is vendored but __init__.py never imports it — vendored-but-ungated, "
                "the shape that hid logsafe.py and url_safety.py"
            )

    exported = list(getattr(factory_common, "__all__", ()))
    if not exported:
        yield "factory_common declares no __all__, so no export can be checked against it"
    for name in exported:
        if not hasattr(factory_common, name):
            yield f"__all__ exports {name!r}, which the package does not provide — a partial vendor"

    # Load-bearing beyond Python: each consumer's SsrfBarriers.qll registers this
    # barrier BY NAME. A rename un-registers it silently and reopens every alert
    # it was clearing.
    if "assert_safe_outbound_url" not in exported:
        yield (
            "assert_safe_outbound_url is no longer exported under that exact name; the CodeQL "
            "SSRF barrier is registered by name in every consumer and is now un-registered"
        )

    pin = pkg_dir / ".hub-sha"
    if not pin.is_file():
        yield f"{pin} is missing: the drift gate has nothing to pin the canonical to"
    elif not _HUB_SHA.match(pin.read_text().strip()):
        yield (
            f"{pin} does not hold a 40-character lowercase commit SHA; a truncated pin is not an "
            "input the drift gate can resolve, so it would compare against nothing"
        )


def _the_secret_table_masks_and_agrees_with_itself() -> Iterator[str]:
    """``scan``, ``redact`` and ``contains_secret`` must say the same thing.

    Each walks ``SECRET_PATTERNS`` independently. A pattern that matches in one
    and not another is invisible to every single-function test, and the visible
    symptom is a leak gate reporting clean on text ``redact`` would have masked.
    """
    from factory_common import secrets

    if not secrets.SECRET_PATTERNS:
        yield (
            "SECRET_PATTERNS is empty: redact() masks nothing while every call still returns "
            "a string, so clean and scanned-nothing look identical"
        )
        return

    # Built by concatenation so this source file does not itself read as a
    # credential to a secret scanner -- a published test vector tripped gitleaks
    # in this fleet for exactly that reason.
    token = "gh" + "p_" + "A" * 36
    line = f"cloning https://x-access-token:{token}@example.invalid/r.git"

    masked = secrets.redact(line)
    if token in masked:
        yield "a GitHub-PAT-shaped token survived redact() unmasked"
    if secrets.PLACEHOLDER not in masked:
        yield (
            "redact() changed the text without leaving its placeholder, so a reader cannot tell "
            "masking happened"
        )
    if not secrets.contains_secret(line):
        yield "contains_secret() says clean about text redact() masks — a leak gate would pass it"
    if not secrets.scan(line):
        yield "scan() found nothing in text redact() masks — a leak gate would report zero findings"

    innocuous = "GET /projects/42 200 in 13ms"
    if secrets.redact(innocuous) != innocuous:
        yield (
            "redact() rewrote text containing no credential; the table has grown a pattern that "
            "matches ordinary output"
        )


def _log_values_cannot_forge_a_record() -> Iterator[str]:
    """``sanitize_log`` escapes rather than strips, and keeps the value readable.

    Asserted on output. A sanitiser that started deleting characters would still
    stop the injection while destroying the value you were debugging with, and
    two different inputs would collapse to the same line -- neither is visible
    from the source of the replace chain.
    """
    from factory_common import logsafe

    forged = "task-42\nERROR:server:admin login from 10.0.0.1"
    safe = logsafe.sanitize_log(forged)
    if "\n" in safe or "\r" in safe:
        yield (
            "a record separator survived sanitize_log(): a caller's value can still forge "
            "a log line"
        )
    if "admin login from 10.0.0.1" not in safe:
        yield (
            "sanitize_log() dropped the payload instead of escaping it; two different inputs now "
            "collapse to the same line and the value being debugged with is gone"
        )
    if "\x00" in logsafe.sanitize_log("a\x00b"):
        yield "a NUL survived sanitize_log(), which corrupts a syslog frame downstream"
    if "\t" not in logsafe.sanitize_log("a\tb"):
        yield (
            "sanitize_log() now escapes tab, which is not a record separator — aligned output is "
            "being mangled for no security gain"
        )
    if not _LOG_CAP_FLOOR <= logsafe.DEFAULT_MAX_LENGTH <= _LOG_CAP_CEILING:
        yield (
            f"DEFAULT_MAX_LENGTH is {logsafe.DEFAULT_MAX_LENGTH}: outside the band where "
            "truncation is an anomaly rather than routine"
        )


def _http_client_policy_holds() -> Iterator[str]:
    """The policies this client exists to carry, checked rather than assumed.

    The user agent is not cosmetic: Cloudflare 403s ``Python-urllib`` as a bot,
    and that is what the first live-fleet benchmark run spent its time on. The
    non-following opener is not cosmetic either: a service behind an OIDC proxy
    answers an unauthenticated API call with a 302 to the login page, and
    FOLLOWING it turned "you are not authenticated" into a recorded 200.
    """
    from factory_common import http

    if not http.DEFAULT_USER_AGENT.strip() or "Python-urllib" in http.DEFAULT_USER_AGENT:
        yield (
            f"DEFAULT_USER_AGENT is {http.DEFAULT_USER_AGENT!r}: the live fleet sits behind "
            "Cloudflare, which 403s the stdlib agent as a bot"
        )

    wrong = [s for s, expected in _SUCCESS_CASES if http.is_success(s) is not expected]
    if wrong:
        yield f"is_success() no longer describes the 2xx band; it disagrees on {wrong}"

    client = http.HttpClient(follow_redirects=False)
    yield from _opener_refuses_redirects(
        client._no_redirect_opener(), "HttpClient(follow_redirects=False)"
    )


def _the_caller_safe_error_stays_caller_safe() -> Iterator[str]:
    """``InputRejectedError`` keeps its base class and its single-writer message.

    Both properties are invisible to every other tool in the toolchain. Dropping
    the ``ValueError`` base turns each ``except ValueError`` around a guard call
    -- in four repos -- into an unhandled 500, and nothing type-checks that
    relation because the handlers live in other repos entirely. Reading the
    message back out of ``str(exc)`` instead of the constructor argument is the
    other half: ``args`` is written by every exception in the process, so a
    handler forwarding it forwards text of unknown authorship.
    """
    from factory_common.client_errors import InputRejectedError

    safe_sentence = "unsupported URL scheme 'ftp' (only http/https)"

    if not issubclass(InputRejectedError, ValueError):
        yield (
            "InputRejectedError no longer subclasses ValueError: every `except ValueError` around "
            "a guard call site in the fleet has just become an unhandled 500"
        )

    exc = InputRejectedError(safe_sentence)
    if exc.client_message != safe_sentence:
        yield "client_message does not carry the string the constructor was given"

    # Rewrite args underneath it. client_message must not move: if it does, it is
    # being derived from str(exc), which any exception in the process can write.
    exc.args = ("upstream host db-prod-7.internal refused the connection",)
    if exc.client_message != safe_sentence:
        yield (
            "client_message tracks args/str(exc) rather than the constructor argument, so text of "
            "unknown authorship can be handed back to a caller verbatim"
        )


registry.register(f"{_PKG}.url_safety", _url_safety_refuses_what_it_promises_to_refuse)
registry.register(f"{_PKG}.__init__", _the_vendored_surface_is_whole)
registry.register(f"{_PKG}.secrets", _the_secret_table_masks_and_agrees_with_itself)
registry.register(f"{_PKG}.logsafe", _log_values_cannot_forge_a_record)
registry.register(f"{_PKG}.http", _http_client_policy_holds)
registry.register(f"{_PKG}.client_errors", _the_caller_safe_error_stays_caller_safe)

# NO DECLARED-EMPTY ENTRIES, and that is a finding rather than an omission. The
# credential-seam pilot came out 4 executable / 13 empty because most of that
# package wraps a remote API per call and owns nothing of its own. This package
# is the opposite: every module in it IS a cross-cutting relation, which is why
# it was deduped into a shared layer in the first place. A written reason for
# any of them would have been filler.
