"""`inspect_raw_manifest` separates "no answer" from "answered no" (#744).

A slow registry used to fail two required gates (`backend (ruff + pytest)` and
`docker (P0 acceptance)`) on PRs that changed a YAML comment. Skipping is only
right when the registry never answered; a registry that answers `manifest
unknown` has found a real defect — a pinned digest that no longer exists — and
must still fail.

Offline by design: the runner and sleep are injected, so these run in the fast
job with no docker and no network.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from _pytest.outcomes import Skipped

from tests.docker import test_p0_multi_arch as multi_arch
from tests.docker.helpers import (
    ManifestInspectError,
    RegistryUnavailableError,
    inspect_raw_manifest,
)

_REF = "cgr.dev/chainguard/python:latest-dev@sha256:abc"
_MANIFEST = '{"manifests": [{"platform": {"architecture": "amd64"}}]}'


class _Runner:
    """Replays a scripted list of outcomes; records how often it was called."""

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, _argv, **_kwargs):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _timeout():
    return subprocess.TimeoutExpired(cmd="docker", timeout=30)


@pytest.fixture
def slept():
    calls: list[float] = []
    return calls, calls.append


def test_two_timeouts_report_the_registry_unavailable(slept):
    calls, sleep = slept
    runner = _Runner(_timeout(), _timeout())

    with pytest.raises(RegistryUnavailableError) as exc:
        inspect_raw_manifest(_REF, runner=runner, sleep=sleep, backoff=3.0)

    assert _REF in str(exc.value)
    assert "timed out" in str(exc.value)
    assert runner.calls == 2  # retried once
    assert calls == [3.0]  # ... with the backoff, once


@pytest.mark.parametrize(
    "stderr",
    [
        "dial tcp 1.2.3.4:443: i/o timeout",
        "failed to do request: context deadline exceeded",
        "net/http: TLS handshake timeout",
        "connection refused",
        "unexpected status from HEAD request: 503 Service Unavailable",
        "toomanyrequests: 429",
    ],
)
def test_transport_failures_report_the_registry_unavailable(stderr, slept):
    _calls, sleep = slept
    runner = _Runner(_completed(1, stderr=stderr), _completed(1, stderr=stderr))

    with pytest.raises(RegistryUnavailableError):
        inspect_raw_manifest(_REF, runner=runner, sleep=sleep)


@pytest.mark.parametrize(
    "stderr",
    [
        "manifest unknown",
        "not found: manifest unknown: manifest tagged by X is not found",
        "unauthorized: authentication required",
        # A refusal echoes the ref, and a sha256 digest carries 429/502/503/504
        # ~6% of the time. Substring matching read this as "never answered" and
        # skipped the gate for a vanished digest (#749).
        "ERROR: docker.io/library/alpine@sha256:"
        "503edd782bcd1b68d8a7d1ed2577b5f820eba820871323f605292651ff11e3c6: not found",
    ],
)
def test_a_refusal_fails_and_is_not_retried(stderr, slept):
    """The registry answered: a vanished digest is a defect, not a hiccup."""
    calls, sleep = slept
    runner = _Runner(_completed(1, stderr=stderr))

    with pytest.raises(ManifestInspectError) as exc:
        inspect_raw_manifest(_REF, runner=runner, sleep=sleep)

    assert stderr in str(exc.value)
    assert runner.calls == 1  # no retry
    assert calls == []


def test_a_retry_after_one_timeout_returns_the_manifest(slept):
    calls, sleep = slept
    runner = _Runner(_timeout(), _completed(0, stdout=_MANIFEST))

    assert inspect_raw_manifest(_REF, runner=runner, sleep=sleep) == _MANIFEST
    assert runner.calls == 2
    assert len(calls) == 1


def test_a_clean_answer_costs_no_retry_and_no_sleep(slept):
    calls, sleep = slept
    runner = _Runner(_completed(0, stdout=_MANIFEST))

    assert inspect_raw_manifest(_REF, runner=runner, sleep=sleep) == _MANIFEST
    assert runner.calls == 1
    assert calls == []


# ── the gate itself skips rather than failing (#744) ──────────────────────


def test_multi_arch_test_skips_when_the_registry_is_unavailable(monkeypatch):
    """The required gate must report skipped, not red, on someone else's outage."""

    def _buildx_ok(*_args, **_kwargs):
        return _completed(0, stdout="buildx v0")

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(multi_arch.subprocess, "run", _buildx_ok)

    def _unavailable(ref, **_kwargs):
        raise RegistryUnavailableError(f"{ref}: timed out after 30s")

    monkeypatch.setattr(multi_arch, "inspect_raw_manifest", _unavailable)

    with pytest.raises(Skipped) as exc:
        multi_arch.test_multi_arch_buildable()

    assert "registry unavailable" in str(exc.value)
    assert "timed out" in str(exc.value)
