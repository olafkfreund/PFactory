"""Cloudflare-friendly stdlib HTTP/JSON client for the Factory fleet.

Two different hub scripts each hand-rolled the same urllib JSON helper:

* ``scripts/parr_regression.py::_call`` - the live-fleet seam probe needed a
  non-default User-Agent (Cloudflare 403s ``Python-urllib`` as a bot), bearer
  auth, a timeout, and a bounded retry on 5xx.
* ``scripts/sync_labels.py::_http_json`` - the cross-tracker label sync needed
  the same urllib request/parse, with GitLab ``PRIVATE-TOKEN`` / Azure ``Basic``
  auth instead of bearer.

Both are the same primitive: "make a JSON request to an authenticated host that
sits behind Cloudflare, with a timeout and a sane retry". This module is that
primitive, once, typed and tested.

Design (matches the rest of the deduped hub layer):

* **stdlib-only** (``urllib``) - importable anywhere with no third-party dep.
* **Cloudflare-friendly UA by default** - the default ``Python-urllib`` UA is
  bot-blocked by the live fleet; a Mozilla UA is sent unless overridden.
* **Pluggable auth** - bearer / basic / GitLab private-token, as small
  :class:`Auth` factories, so each caller keeps its own credential style.
* **Bounded retry on 5xx / network error** - the transient class the live fleet
  hit; 4xx is returned immediately (it will not get better on retry).
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

# The default Python-urllib UA is 403'd by Cloudflare as a bot; the live fleet
# requires a browser-shaped UA. This is the lesson from the first benchmark run.
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) factory-common-http/1.0"

# A 5xx is transient (the live fleet hit this); retry it. A 4xx will not improve
# on retry, so it is returned immediately.
_SERVER_ERROR_FLOOR = 500

_SUCCESS_FLOOR = 200
_SUCCESS_CEILING = 300


def is_success(status: int) -> bool:
    """True for a 2xx HTTP status (a successful response)."""
    return _SUCCESS_FLOOR <= status < _SUCCESS_CEILING


JsonBody = dict[str, Any] | list[Any]

# An Auth is a function that contributes header(s) to a request.
Auth = Callable[[], dict[str, str]]


def bearer_auth(token: str) -> Auth:
    """``Authorization: Bearer <token>`` (the PARR services)."""

    def _apply() -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"} if token else {}

    return _apply


def basic_auth(username: str, password: str) -> Auth:
    """``Authorization: Basic <base64(user:pass)>`` (Azure DevOps uses ``:PAT``)."""

    def _apply() -> dict[str, str]:
        raw = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {raw}"}

    return _apply


def private_token_auth(token: str) -> Auth:
    """``PRIVATE-TOKEN: <token>`` (GitLab)."""

    def _apply() -> dict[str, str]:
        return {"PRIVATE-TOKEN": token} if token else {}

    return _apply


def no_auth() -> Auth:
    """No auth header (for unauthenticated probes)."""

    def _apply() -> dict[str, str]:
        return {}

    return _apply


def _default_sleep(seconds: float) -> None:
    # Indirection through the module-level reference so a test that patches
    # ``factory_common.http.time.sleep`` takes effect (a bound ``time.sleep``
    # default would not), while callers can still pass their own ``_sleep``.
    time.sleep(seconds)


@dataclass(frozen=True)
class HttpResponse:
    """Result of a request: HTTP status (0 = network failure) + parsed body.

    ``json`` is the decoded JSON when the body was a JSON object/array, otherwise
    ``{"raw": <text>}`` (or ``{"error": <text>}`` for an error response), so a
    caller can always index it without a type check.
    """

    status: int
    json: dict[str, Any]


def _parse_body(raw: str) -> dict[str, Any]:
    text = raw or "{}"
    if text.strip().startswith(("{", "[")):
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {"items": parsed}
    return {"raw": raw}


@dataclass
class HttpClient:
    """Typed stdlib JSON client with a Cloudflare-friendly UA + bounded retry.

    One client carries a base URL, an auth strategy, and retry/timeout policy;
    :meth:`request` (and the verb helpers) issue calls against it.
    """

    base_url: str = ""
    auth: Auth = field(default_factory=no_auth)
    user_agent: str = DEFAULT_USER_AGENT
    timeout: int = 30
    max_attempts: int = 3
    backoff_seconds: float = 5.0
    # Follow 3xx? Default True preserves existing behaviour for callers that
    # want it (sync_labels.py talks to GitHub, which legitimately redirects a
    # renamed repo). Set False when probing an API: a service behind an OIDC
    # proxy answers an unauthenticated API call with a 302 to the identity
    # provider, and FOLLOWING that turns "you are not authenticated" into the
    # login page's 200. The PARR seam probe recorded exactly that as
    # `cfactory:cockpit-api -> 200` while never reaching CFactory at all.
    follow_redirects: bool = True
    # Indirection so tests can stub sleeping without real delay.
    _sleep: Callable[[float], None] = field(default=_default_sleep, repr=False)

    def _no_redirect_opener(self) -> urllib.request.OpenerDirector:
        """Opener that surfaces a 3xx instead of following it.

        Returning None from ``redirect_request`` makes urllib raise the 3xx as
        an HTTPError, which :meth:`request` already reports verbatim -- so the
        caller sees `302`, not the status of whatever page it landed on.
        """

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            # Signature is urllib's, not ours; returning None is the
            # documented way to refuse a redirect.
            def redirect_request(self, *_args: object, **_kw: object) -> None:
                return None

        return urllib.request.build_opener(_NoRedirect)

    def _url(self, path: str) -> str:
        if not self.base_url:
            return path
        return f"{self.base_url.rstrip('/')}{path}"

    def _headers(self, extra: Mapping[str, str] | None, has_body: bool) -> dict[str, str]:
        headers = {"User-Agent": self.user_agent}
        if has_body:
            headers["Content-Type"] = "application/json"
        headers.update(self.auth())
        if extra:
            headers.update(extra)
        return headers

    def request(
        self,
        method: str,
        path: str,
        body: JsonBody | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: int | None = None,
    ) -> HttpResponse:
        """Issue one JSON request, retrying transient (5xx / network) failures.

        4xx responses are returned immediately (they will not improve on retry);
        5xx and network errors are retried up to ``max_attempts`` with a linear
        backoff. A network failure that never succeeds returns status ``0``.
        """
        url = self._url(path)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(  # noqa: S310 — trusted, caller-built host
            url,
            data=data,
            method=method,
            headers=self._headers(headers, has_body=data is not None),
        )
        effective_timeout = self.timeout if timeout is None else timeout
        last_error = "unreachable"
        for attempt in range(self.max_attempts):
            try:
                # Default path stays urlopen: it is the seam existing tests
                # stub, and following redirects is urllib's own behaviour, so
                # not-following is the only case that needs a custom opener.
                _open = (
                    urllib.request.urlopen
                    if self.follow_redirects
                    else self._no_redirect_opener().open
                )
                with _open(req, timeout=effective_timeout) as resp:
                    return HttpResponse(resp.status, _parse_body(resp.read().decode()))
            except urllib.error.HTTPError as exc:
                if exc.code >= _SERVER_ERROR_FLOOR and attempt < self.max_attempts - 1:
                    self._sleep(self.backoff_seconds * (attempt + 1))
                    continue
                return HttpResponse(exc.code, {"error": exc.read().decode()[:300]})
            except urllib.error.URLError as exc:
                last_error = str(exc)
                if attempt < self.max_attempts - 1:
                    self._sleep(self.backoff_seconds * (attempt + 1))
                    continue
        return HttpResponse(0, {"error": last_error})

    def get(self, path: str, **kwargs: Any) -> HttpResponse:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, body: JsonBody | None = None, **kwargs: Any) -> HttpResponse:
        return self.request("POST", path, body=body, **kwargs)

    def put(self, path: str, body: JsonBody | None = None, **kwargs: Any) -> HttpResponse:
        return self.request("PUT", path, body=body, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> HttpResponse:
        return self.request("DELETE", path, **kwargs)
