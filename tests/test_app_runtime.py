"""Tests for AppRuntime — Task 8 (#24).

All tests are hermetic: no real docker process, no real HTTP requests,
no real time.sleep delays.  Injectable seams:

  runner_fn  — replaces subprocess.run
  clock      — replaces time.monotonic so health-poll advances instantly

Covers (15 cases):
  1.  start() passes correct argv including services list
  2.  start() raises AppRuntimeError on non-zero compose exit
  3.  stop() calls docker compose down --volumes
  4.  stop() is a no-op when start() was never called
  5.  context manager: start() + stop() called on clean exit
  6.  context manager: stop() still called when body raises
  7.  wait_for_healthy: returns HealthCheckResult list when URL responds immediately
  8.  wait_for_healthy: polls until status matches (3 attempts before success)
  9.  wait_for_healthy: handles URLError on early polls, succeeds later
  10. wait_for_healthy: raises AppRuntimeError("app_not_healthy: ...") on timeout
  11. wait_for_healthy: error message includes last_status code
  12. wait_for_healthy: multiple URLs — one times out — error mentions it
  13. HealthCheckResult dataclass round-trip
  14. dispatch_browser_lane: PFACTORY_TARGET_URL injected via extra_env
  15. poll_interval: clock advances by poll_interval between iterations
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError, URLError

import pytest
from tools.runners.app_runtime import (
    AppRuntime,
    AppRuntimeError,
    AppRuntimeResult,
    HealthCheckResult,
)
from tools.runners.lane_dispatch import DispatchResult, dispatch_browser_lane

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeWaitFor:
    """Minimal stand-in for pfactory_yml.schema.WaitFor."""

    url: str
    timeout_seconds: int = 60
    expect_status: int = 200


@dataclass
class _FakeTarget:
    """Minimal stand-in for pfactory_yml.schema.DockerComposeTarget."""

    type: str = "docker_compose"
    name: str = "web"
    compose_file: str = "docker-compose.test.yml"
    services: list = None  # type: ignore[assignment]
    wait_for: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.services is None:
            self.services = ["app", "db"]
        if self.wait_for is None:
            self.wait_for = [_FakeWaitFor(url="http://localhost:3000/ready")]


def _fake_completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def _make_runtime(
    target=None,
    repo_root: Path | None = None,
    runner_fn=None,
    clock=None,
    poll_interval: float = 0.0,  # no real sleeps in tests
):
    if target is None:
        target = _FakeTarget()
    if repo_root is None:
        repo_root = Path("/tmp/repo")
    kwargs: dict[str, Any] = {"poll_interval_seconds": poll_interval}
    if runner_fn is not None:
        kwargs["runner_fn"] = runner_fn
    if clock is not None:
        kwargs["clock"] = clock
    return AppRuntime(target, repo_root, **kwargs)


# ---------------------------------------------------------------------------
# 1. start() passes correct argv
# ---------------------------------------------------------------------------


def test_start_calls_docker_compose_up_with_services():
    """start() argv must include -f, up, -d, and each service name."""
    calls: list[list[str]] = []

    def _capture(argv, **kw):
        calls.append(argv)
        return _fake_completed(0)

    target = _FakeTarget(services=["app", "db", "redis"])
    runtime = _make_runtime(target=target, runner_fn=_capture)
    runtime.start()

    assert len(calls) == 1
    argv = calls[0]
    assert "up" in argv
    assert "-d" in argv
    assert "app" in argv
    assert "db" in argv
    assert "redis" in argv
    # compose file must be -f <path>
    f_idx = argv.index("-f")
    assert "docker-compose.test.yml" in argv[f_idx + 1]


def test_start_includes_compose_file_path():
    """The -f flag must point to repo_root / compose_file."""
    calls: list[list[str]] = []

    def _capture(argv, **kw):
        calls.append(argv)
        return _fake_completed(0)

    repo_root = Path("/projects/myapp")
    target = _FakeTarget(compose_file="infra/docker-compose.test.yml")
    runtime = _make_runtime(target=target, repo_root=repo_root, runner_fn=_capture)
    runtime.start()

    argv = calls[0]
    f_idx = argv.index("-f")
    assert argv[f_idx + 1] == str(repo_root / "infra/docker-compose.test.yml")


# ---------------------------------------------------------------------------
# 2. start() raises on non-zero exit
# ---------------------------------------------------------------------------


def test_start_raises_on_non_zero_exit():
    """Non-zero compose exit code must raise AppRuntimeError."""
    runtime = _make_runtime(
        runner_fn=lambda *a, **kw: _fake_completed(1, stderr="compose error"),
    )
    with pytest.raises(AppRuntimeError, match="docker compose up failed"):
        runtime.start()


def test_start_raises_includes_stderr():
    """AppRuntimeError message must include compose stderr."""
    runtime = _make_runtime(
        runner_fn=lambda *a, **kw: _fake_completed(1, stderr="no such file"),
    )
    with pytest.raises(AppRuntimeError, match="no such file"):
        runtime.start()


# ---------------------------------------------------------------------------
# 3. stop() calls docker compose down --volumes
# ---------------------------------------------------------------------------


def test_stop_runs_docker_compose_down_volumes():
    """stop() must call docker compose down --volumes after a successful start."""
    calls: list[list[str]] = []

    def _capture(argv, **kw):
        calls.append(argv)
        return _fake_completed(0)

    runtime = _make_runtime(runner_fn=_capture)
    runtime.start()
    runtime.stop()

    # calls[0] is the start(); calls[1] is the stop()
    assert len(calls) == 2
    stop_argv = calls[1]
    assert "down" in stop_argv
    assert "--volumes" in stop_argv


# ---------------------------------------------------------------------------
# 4. stop() is a no-op when not started
# ---------------------------------------------------------------------------


def test_stop_no_op_when_not_started():
    """stop() must not call subprocess when _started is False."""
    calls: list[list[str]] = []

    def _capture(argv, **kw):
        calls.append(argv)
        return _fake_completed(0)

    runtime = _make_runtime(runner_fn=_capture)
    runtime.stop()  # _started is False — never called start()

    assert calls == []


# ---------------------------------------------------------------------------
# 5. Context manager: start + stop on clean exit
# ---------------------------------------------------------------------------


def test_context_manager_starts_and_stops():
    """__enter__ must call start(); __exit__ must call stop()."""
    calls: list[list[str]] = []

    def _capture(argv, **kw):
        calls.append(argv)
        return _fake_completed(0)

    target = _FakeTarget(wait_for=[])  # no health checks
    runtime = _make_runtime(target=target, runner_fn=_capture)

    with runtime:
        assert runtime._started is True
    assert runtime._started is False

    assert len(calls) == 2  # start + stop
    assert "up" in calls[0]
    assert "down" in calls[1]


# ---------------------------------------------------------------------------
# 6. Context manager: stop called even when body raises
# ---------------------------------------------------------------------------


def test_context_manager_stops_on_exception():
    """stop() must be called even when the body raises an exception."""
    calls: list[list[str]] = []

    def _capture(argv, **kw):
        calls.append(argv)
        return _fake_completed(0)

    target = _FakeTarget(wait_for=[])
    runtime = _make_runtime(target=target, runner_fn=_capture)

    with pytest.raises(RuntimeError, match="body error"):
        with runtime:
            raise RuntimeError("body error")

    # stop must still have been called
    assert any("down" in argv for argv in calls)


# ---------------------------------------------------------------------------
# 7. wait_for_healthy: returns on immediate success
# ---------------------------------------------------------------------------


def test_wait_for_healthy_returns_when_url_returns_expect_status():
    """URL responds immediately with 200 → single poll, healthy=True."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    def _urlopen(req, timeout=None):
        return mock_resp

    target = _FakeTarget(
        wait_for=[_FakeWaitFor(url="http://localhost:3000/ready", expect_status=200)]
    )
    # Clock is always before deadline
    clock_value = [0.0]

    def _clock():
        return clock_value[0]

    runtime = _make_runtime(target=target, clock=_clock, poll_interval=0.0)
    with patch("tools.runners.app_runtime.urlrequest.urlopen", _urlopen):
        results = runtime.wait_for_healthy()

    assert len(results) == 1
    assert results[0].healthy is True
    assert results[0].last_status == 200
    assert results[0].url == "http://localhost:3000/ready"


# ---------------------------------------------------------------------------
# 8. wait_for_healthy: polls until status matches (3 attempts)
# ---------------------------------------------------------------------------


def test_wait_for_healthy_polls_until_status_matches():
    """First 2 polls return 503; third returns 200 — polls 3 times, healthy."""
    call_count = [0]

    def _urlopen(req, timeout=None):
        call_count[0] += 1
        if call_count[0] < 3:
            raise HTTPError(url="", code=503, msg="Service Unavailable", hdrs=None, fp=None)  # type: ignore[arg-type]
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    # Clock advances slowly — never passes deadline (10 polls worth)
    clock_value = [0.0]

    def _clock():
        v = clock_value[0]
        clock_value[0] += 1.0  # each call to clock() advances by 1s
        return v

    target = _FakeTarget(
        wait_for=[_FakeWaitFor(url="http://localhost:3000/", timeout_seconds=120)]
    )
    runtime = _make_runtime(target=target, clock=_clock, poll_interval=0.0)
    with patch("tools.runners.app_runtime.urlrequest.urlopen", _urlopen):
        with patch("tools.runners.app_runtime.time.sleep"):
            results = runtime.wait_for_healthy()

    assert results[0].healthy is True
    assert call_count[0] == 3


# ---------------------------------------------------------------------------
# 9. wait_for_healthy: URLError on early polls, success later
# ---------------------------------------------------------------------------


def test_wait_for_healthy_handles_url_error_during_startup():
    """URLError on first poll (connection refused); 200 on second — healthy."""
    call_count = [0]

    def _urlopen(req, timeout=None):
        call_count[0] += 1
        if call_count[0] == 1:
            raise URLError(reason="Connection refused")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    clock_value = [0.0]

    def _clock():
        v = clock_value[0]
        clock_value[0] += 1.0
        return v

    target = _FakeTarget(
        wait_for=[_FakeWaitFor(url="http://localhost:3000/", timeout_seconds=120)]
    )
    runtime = _make_runtime(target=target, clock=_clock, poll_interval=0.0)
    with patch("tools.runners.app_runtime.urlrequest.urlopen", _urlopen):
        with patch("tools.runners.app_runtime.time.sleep"):
            results = runtime.wait_for_healthy()

    assert results[0].healthy is True
    assert call_count[0] == 2


# ---------------------------------------------------------------------------
# 10. wait_for_healthy: raises on timeout
# ---------------------------------------------------------------------------


def test_wait_for_healthy_times_out_and_raises():
    """Always returns 503; clock advances past deadline → AppRuntimeError.

    Clock sequence:
      [0.0] = deadline calculation (deadline = 0 + 5 = 5.0)
      [0.5] = first loop check (0.5 < 5.0 → enter loop, call urlopen → 503)
      [6.0] = second loop check (6.0 >= 5.0 → exit loop → return unhealthy)
    """

    def _urlopen(req, timeout=None):
        raise HTTPError(url="", code=503, msg="Service Unavailable", hdrs=None, fp=None)  # type: ignore[arg-type]

    clock_iter = iter([0.0, 0.5, 6.0])

    def _clock():
        return next(clock_iter)

    target = _FakeTarget(
        wait_for=[_FakeWaitFor(url="http://localhost:3000/", timeout_seconds=5)]
    )
    runtime = _make_runtime(target=target, clock=_clock, poll_interval=0.0)
    with patch("tools.runners.app_runtime.urlrequest.urlopen", _urlopen):
        with patch("tools.runners.app_runtime.time.sleep"):
            with pytest.raises(AppRuntimeError, match="app_not_healthy"):
                runtime.wait_for_healthy()


# ---------------------------------------------------------------------------
# 11. wait_for_healthy: error message includes last_status
# ---------------------------------------------------------------------------


def test_wait_for_healthy_reports_last_status_in_error():
    """AppRuntimeError message must include the last observed status code.

    Clock sequence: [0.0 (deadline calc), 0.5 (first loop check, < deadline),
    6.0 (second loop check, > deadline)] — ensures urlopen is called once
    before the deadline expires so last_status=503 is recorded.
    """

    def _urlopen(req, timeout=None):
        raise HTTPError(url="", code=503, msg="Service Unavailable", hdrs=None, fp=None)  # type: ignore[arg-type]

    # [0.0] = deadline calculation (deadline=5.0)
    # [0.5] = first while-loop check (0.5 < 5.0 → enters loop, calls urlopen)
    # [6.0] = second while-loop check (6.0 >= 5.0 → exits loop)
    clock_iter = iter([0.0, 0.5, 6.0])

    def _clock():
        return next(clock_iter)

    target = _FakeTarget(
        wait_for=[_FakeWaitFor(url="http://localhost:3000/", timeout_seconds=5)]
    )
    runtime = _make_runtime(target=target, clock=_clock, poll_interval=0.0)
    with patch("tools.runners.app_runtime.urlrequest.urlopen", _urlopen):
        with patch("tools.runners.app_runtime.time.sleep"):
            with pytest.raises(AppRuntimeError, match="last_status=503"):
                runtime.wait_for_healthy()


# ---------------------------------------------------------------------------
# 12. wait_for_healthy: multiple URLs — one healthy, one times out
# ---------------------------------------------------------------------------


def test_wait_for_healthy_multiple_urls_all_must_pass():
    """Two wait_for URLs; first is healthy, second times out → error mentions the unhealthy one."""
    healthy_url = "http://localhost:3000/health"
    unhealthy_url = "http://localhost:3001/health"

    def _urlopen(req, timeout=None):
        if "3000" in req.full_url:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp
        # port 3001 always returns 503
        raise HTTPError(url="", code=503, msg="Service Unavailable", hdrs=None, fp=None)  # type: ignore[arg-type]

    # Provide enough clock values for both polls: deadline calc + loop checks
    clock_values = [0.0, 0.0, 6.0, 6.0, 0.0, 6.0, 6.0]
    clock_iter = iter(clock_values)

    def _clock():
        try:
            return next(clock_iter)
        except StopIteration:
            return 999.0  # always past deadline after values exhausted

    target = _FakeTarget(
        wait_for=[
            _FakeWaitFor(url=healthy_url, timeout_seconds=5),
            _FakeWaitFor(url=unhealthy_url, timeout_seconds=5),
        ]
    )
    runtime = _make_runtime(target=target, clock=_clock, poll_interval=0.0)
    with patch("tools.runners.app_runtime.urlrequest.urlopen", _urlopen):
        with patch("tools.runners.app_runtime.time.sleep"):
            with pytest.raises(AppRuntimeError) as exc_info:
                runtime.wait_for_healthy()

    # Error must mention the unhealthy URL, not the healthy one
    assert unhealthy_url in str(exc_info.value)
    # Should not falsely claim the healthy URL is unhealthy
    assert healthy_url not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 13. HealthCheckResult dataclass round-trip
# ---------------------------------------------------------------------------


def test_health_check_result_dataclass_fields():
    """HealthCheckResult stores all four fields correctly."""
    r = HealthCheckResult(
        url="http://example.com/health",
        last_status=200,
        last_error=None,
        healthy=True,
    )
    assert r.url == "http://example.com/health"
    assert r.last_status == 200
    assert r.last_error is None
    assert r.healthy is True

    r2 = HealthCheckResult(
        url="http://example.com/health",
        last_status=None,
        last_error="Connection refused",
        healthy=False,
    )
    assert r2.last_status is None
    assert r2.last_error == "Connection refused"
    assert r2.healthy is False


# ---------------------------------------------------------------------------
# 14. dispatch_browser_lane: PFACTORY_TARGET_URL injected via extra_env
# ---------------------------------------------------------------------------


def test_dispatch_browser_lane_passes_target_url_via_env(tmp_path):
    """dispatch_browser_lane must set extra_env['PFACTORY_TARGET_URL'] to the
    first wait_for URL before calling docker_runner.run()."""
    from tools.runners.docker_runner import DockerRunner, DockerRunResult

    target_url = "http://localhost:3000/ready"
    target = _FakeTarget(
        wait_for=[_FakeWaitFor(url=target_url, timeout_seconds=60)]
    )

    # Stub AppRuntime that succeeds without any real docker calls
    class _StubRuntime:
        def __init__(self, t, r, **kw):
            self._started = False

        def start(self):
            self._started = True
            return AppRuntimeResult(started=True)

        def stop(self):
            self._started = False

        def wait_for_healthy(self):
            return [
                HealthCheckResult(
                    url=target_url, last_status=200, last_error=None, healthy=True
                )
            ]

        def __enter__(self):
            self.start()
            return self

        def __exit__(self, *a):
            self.stop()

    # Track what extra_env was passed to docker_runner.run()
    received_extra_env: dict = {}

    class _StubDockerRunner:
        def run(self, **kwargs):
            received_extra_env.update(kwargs.get("extra_env") or {})
            return DockerRunResult(returncode=0)

    result = dispatch_browser_lane(
        target=target,
        repo_root=tmp_path,
        docker_runner=_StubDockerRunner(),  # type: ignore[arg-type]
        docker_run_kwargs={
            "repo_path": tmp_path,
            "scratch_path": tmp_path,
            "command": ["npx", "playwright", "test"],
        },
        app_runtime_cls=_StubRuntime,
    )

    assert isinstance(result, DispatchResult)
    assert result.lane == "browser"
    assert received_extra_env.get("PFACTORY_TARGET_URL") == target_url


def test_dispatch_browser_lane_merges_existing_extra_env(tmp_path):
    """Caller-supplied extra_env must be preserved; PFACTORY_TARGET_URL is added."""
    from tools.runners.docker_runner import DockerRunResult

    target_url = "http://localhost:8080/"
    target = _FakeTarget(
        wait_for=[_FakeWaitFor(url=target_url)]
    )

    class _StubRuntime:
        def __init__(self, t, r, **kw):
            pass

        def start(self):
            return AppRuntimeResult(started=True)

        def stop(self):
            pass

        def wait_for_healthy(self):
            return []

        def __enter__(self):
            self.start()
            return self

        def __exit__(self, *a):
            self.stop()

    received: dict = {}

    class _StubDockerRunner:
        def run(self, **kwargs):
            received.update(kwargs.get("extra_env") or {})
            return DockerRunResult(returncode=0)

    dispatch_browser_lane(
        target=target,
        repo_root=tmp_path,
        docker_runner=_StubDockerRunner(),  # type: ignore[arg-type]
        docker_run_kwargs={
            "repo_path": tmp_path,
            "scratch_path": tmp_path,
            "command": ["playwright", "test"],
            "extra_env": {"PLAYWRIGHT_HEADED": "0"},
        },
        app_runtime_cls=_StubRuntime,
    )

    assert received["PLAYWRIGHT_HEADED"] == "0"
    assert received["PFACTORY_TARGET_URL"] == target_url


# ---------------------------------------------------------------------------
# 15. poll_interval: clock advances by poll_interval per iteration
# ---------------------------------------------------------------------------


def test_poll_interval_respected():
    """time.sleep is called with poll_interval between each failed poll."""
    sleep_calls: list[float] = []
    call_count = [0]
    poll_interval = 2.0

    def _urlopen(req, timeout=None):
        call_count[0] += 1
        if call_count[0] < 3:
            raise URLError(reason="not ready")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    # Clock never advances past deadline (large budget)
    clock_calls = [0]

    def _clock():
        clock_calls[0] += 1
        return float(clock_calls[0])  # advances by 1 each call; budget = 120

    target = _FakeTarget(
        wait_for=[_FakeWaitFor(url="http://localhost/", timeout_seconds=120)]
    )
    runtime = _make_runtime(
        target=target,
        clock=_clock,
        poll_interval=poll_interval,
    )
    with patch("tools.runners.app_runtime.urlrequest.urlopen", _urlopen):
        with patch("tools.runners.app_runtime.time.sleep", side_effect=sleep_calls.append):
            runtime.wait_for_healthy()

    # First two failed polls must each be followed by sleep(poll_interval)
    assert all(s == poll_interval for s in sleep_calls)
    # At least 2 sleeps (one per failed poll before success)
    assert len(sleep_calls) >= 2


# ---------------------------------------------------------------------------
# Bonus: AppRuntimeResult dataclass
# ---------------------------------------------------------------------------


def test_app_runtime_result_defaults():
    """AppRuntimeResult defaults are correct."""
    r = AppRuntimeResult(started=True)
    assert r.started is True
    assert r.health_results == []
    assert r.compose_stdout == ""
    assert r.compose_stderr == ""


def test_app_runtime_result_with_health_results():
    """AppRuntimeResult correctly stores health_results."""
    hr = HealthCheckResult(url="http://x/", last_status=200, last_error=None, healthy=True)
    r = AppRuntimeResult(started=True, health_results=[hr])
    assert len(r.health_results) == 1
    assert r.health_results[0].url == "http://x/"
