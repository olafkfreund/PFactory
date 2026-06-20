"""HAR (HTTP Archive) recorder for API + Integration lane evidence.

Provides a context manager that wraps outbound HTTP calls made via the
Python standard library (``urllib.request``) and via ``httpx`` (when
installed) and writes a ``.har`` file at the end.  Used by API /
Integration test runs to capture every request/response for human review.

Usage::

    from agents.evidence.http_recorder import record_http_to_har
    from pathlib import Path

    spec_dir = Path("/tmp/pfactory/specs/my-spec")

    with record_http_to_har(spec_dir, "ac2-api-test"):
        # Any urllib.request or httpx HTTP calls made here are recorded
        import urllib.request
        urllib.request.urlopen("https://httpbin.org/get")

    # /tmp/pfactory/specs/my-spec/findings/evidence/ac2-api-test/network.har
    # now contains a HAR 1.2 JSON file with one entry.

Implementation notes:

* Monkey-patches ``urllib.request.urlopen`` and (when available)
  ``httpx.Client.send`` to intercept req/resp pairs.
* Emits standard HAR 1.2 JSON on context exit.  If neither library is
  in use, the ``.har`` file has an empty ``entries`` array.
* Thread-safety: the recorder appends to a module-level list protected by
  a ``threading.Lock``; nested contexts are deliberately NOT supported
  (the innermost context owns the monkey-patch, which is fine for the
  single-test-per-process Executor model).
* The original functions are always restored on context exit, even if the
  body raises.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.evidence.layout import evidence_dir_for_test

# ─── Thread-local entry accumulator ─────────────────────────────────────────

_lock = threading.Lock()
_entries: list[dict[str, Any]] = []


def _reset_entries() -> None:
    with _lock:
        _entries.clear()


def _append_entry(entry: dict[str, Any]) -> None:
    with _lock:
        _entries.append(entry)


def _get_entries() -> list[dict[str, Any]]:
    with _lock:
        return list(_entries)


# ─── HAR entry builder ───────────────────────────────────────────────────────


def _build_har_request(url: str, method: str, headers: dict[str, str]) -> dict:
    return {
        "method": method.upper(),
        "url": url,
        "httpVersion": "HTTP/1.1",
        "headers": [{"name": k, "value": v} for k, v in headers.items()],
        "queryString": [],
        "cookies": [],
        "headersSize": -1,
        "bodySize": -1,
    }


def _build_har_response(
    status: int,
    status_text: str,
    body: bytes,
    headers: dict[str, str],
) -> dict:
    return {
        "status": status,
        "statusText": status_text,
        "httpVersion": "HTTP/1.1",
        "headers": [{"name": k, "value": v} for k, v in headers.items()],
        "cookies": [],
        "content": {
            "size": len(body),
            "mimeType": headers.get("Content-Type", "application/octet-stream"),
        },
        "redirectURL": "",
        "headersSize": -1,
        "bodySize": len(body),
    }


def _build_har_entry(
    url: str,
    method: str,
    req_headers: dict[str, str],
    status: int,
    status_text: str,
    resp_body: bytes,
    resp_headers: dict[str, str],
    started_at: datetime,
    elapsed_ms: float,
) -> dict:
    return {
        "startedDateTime": started_at.isoformat(),
        "time": elapsed_ms,
        "request": _build_har_request(url, method, req_headers),
        "response": _build_har_response(status, status_text, resp_body, resp_headers),
        "timings": {"send": 0, "wait": elapsed_ms, "receive": 0},
    }


# ─── urllib patcher ──────────────────────────────────────────────────────────


def _make_urllib_patch(original_urlopen):  # type: ignore[no-untyped-def]
    """Return a patched ``urlopen`` that records req/resp into ``_entries``."""

    def _patched_urlopen(url, data=None, timeout=None, **kwargs):  # type: ignore[no-untyped-def]
        started = datetime.now(UTC)
        t0 = time.perf_counter()

        # Normalise URL
        if hasattr(url, "full_url"):
            raw_url = url.full_url
            method = getattr(url, "method", "GET") or "GET"
            req_headers = dict(url.headers)
        else:
            raw_url = str(url)
            method = "POST" if data is not None else "GET"
            req_headers = {}

        try:
            resp = original_urlopen(url, data=data, timeout=timeout, **kwargs)
        except Exception:
            raise

        elapsed = (time.perf_counter() - t0) * 1000
        resp_body = b""
        try:
            resp_body = resp.read()
        except Exception:
            pass

        resp_headers = {}
        try:
            for k, v in resp.headers.items():
                resp_headers[k] = v
        except Exception:
            pass

        _append_entry(
            _build_har_entry(
                url=raw_url,
                method=method,
                req_headers=req_headers,
                status=resp.status,
                status_text=resp.reason or "",
                resp_body=resp_body,
                resp_headers=resp_headers,
                started_at=started,
                elapsed_ms=elapsed,
            )
        )

        # Wrap response to allow re-reading the body
        class _RewindableResp:
            def __init__(self, inner, body):  # type: ignore[no-untyped-def]
                self._inner = inner
                self._body = body
                self.status = inner.status
                self.reason = inner.reason
                self.headers = inner.headers
                self.url = getattr(inner, "url", raw_url)

            def read(self, amt=None):  # type: ignore[no-untyped-def]
                if amt is None:
                    return self._body
                return self._body[:amt]

            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *args):  # type: ignore[no-untyped-def]
                pass

        return _RewindableResp(resp, resp_body)

    return _patched_urlopen


# ─── httpx patcher ───────────────────────────────────────────────────────────


def _make_httpx_patch(original_send):  # type: ignore[no-untyped-def]
    """Return a patched ``httpx.Client.send`` that records req/resp."""

    def _patched_send(self_client, request, **kwargs):  # type: ignore[no-untyped-def]
        started = datetime.now(UTC)
        t0 = time.perf_counter()

        req_headers = dict(request.headers)
        raw_url = str(request.url)
        method = request.method

        try:
            response = original_send(self_client, request, **kwargs)
        except Exception:
            raise

        elapsed = (time.perf_counter() - t0) * 1000
        resp_headers = dict(response.headers)
        resp_body = response.content  # bytes, already read by httpx

        _append_entry(
            _build_har_entry(
                url=raw_url,
                method=method,
                req_headers=req_headers,
                status=response.status_code,
                status_text=getattr(response, "reason_phrase", ""),
                resp_body=resp_body,
                resp_headers=resp_headers,
                started_at=started,
                elapsed_ms=elapsed,
            )
        )
        return response

    return _patched_send


# ─── HAR document builder ────────────────────────────────────────────────────


def _build_har_document(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "log": {
            "version": "1.2",
            "creator": {
                "name": "PFactory HTTP Recorder",
                "version": "0.2.0",
                "comment": "Auto-generated by agents.evidence.http_recorder",
            },
            "entries": entries,
        }
    }


# ─── Public context manager ──────────────────────────────────────────────────


@contextlib.contextmanager
def record_http_to_har(spec_dir: Path, test_id: str):
    """Record all outbound HTTP traffic during the context to a ``.har`` file.

    Writes ``<spec_dir>/findings/evidence/<test_id>/network.har`` on exit.
    The directory is created if it does not exist.

    Intercepted transports:
    * ``urllib.request.urlopen`` — always patched
    * ``httpx.Client.send`` — patched when ``httpx`` is importable

    If neither transport is used the ``.har`` file has an empty ``entries``
    array (still a valid HAR 1.2 document).

    Args:
        spec_dir: PFactory workspace spec directory.
        test_id: Unique test identifier (used to build the output path).

    Yields:
        Nothing — the context manager is used purely for its side effects.

    Example::

        with record_http_to_har(spec_dir, "ac2-health-check"):
            urllib.request.urlopen("http://localhost:8080/health")
        # network.har written
    """
    _reset_entries()

    # ── Patch urllib.request.urlopen ────────────────────────────────────
    _orig_urlopen = urllib.request.urlopen
    urllib.request.urlopen = _make_urllib_patch(_orig_urlopen)  # type: ignore[assignment]

    # ── Optionally patch httpx ───────────────────────────────────────────
    _orig_httpx_send = None
    _httpx_client_cls = None
    try:
        import httpx

        _httpx_client_cls = httpx.Client
        _orig_httpx_send = httpx.Client.send
        httpx.Client.send = _make_httpx_patch(_orig_httpx_send)  # type: ignore[method-assign]
    except ImportError:
        pass  # httpx not installed — fine, only urllib is patched

    try:
        yield
    finally:
        # ── Restore originals ────────────────────────────────────────────
        urllib.request.urlopen = _orig_urlopen  # type: ignore[assignment]
        if _orig_httpx_send is not None and _httpx_client_cls is not None:
            _httpx_client_cls.send = _orig_httpx_send  # type: ignore[method-assign]

        # ── Write .har file ──────────────────────────────────────────────
        entries = _get_entries()
        har_doc = _build_har_document(entries)

        ev_dir = evidence_dir_for_test(spec_dir, test_id)
        ev_dir.mkdir(parents=True, exist_ok=True)
        har_path = ev_dir / "network.har"
        har_path.write_text(
            json.dumps(har_doc, indent=2, default=str),
            encoding="utf-8",
        )
