"""AppRuntime — docker-compose lifecycle for Browser-lane tests.

Wraps `docker compose up -d` + health-poll + `docker compose down --volumes`
so the Executor can run a Playwright test against a live app. Health-poll
is HTTP HEAD against every `wait_for.url` declared in the
DockerComposeTarget, every 2s for up to 120s.

Typical usage from the Executor::

    from pfactory_yml.schema import DockerComposeTarget, WaitFor
    from tools.runners.app_runtime import AppRuntime

    target = DockerComposeTarget(
        type="docker_compose",
        name="web",
        compose_file="docker-compose.test.yml",
        services=["app", "db"],
        wait_for=[WaitFor(url="http://localhost:3000/ready", timeout_seconds=60)],
    )

    with AppRuntime(target, repo_root=Path("/path/to/project")) as runtime:
        results = runtime.wait_for_healthy()
        target_url = target.wait_for[0].url
        # run Playwright test against target_url ...

Architecture notes:
  - `compose_cmd` is injectable for tests (default: ``("docker", "compose")``).
  - `runner_fn` replaces ``subprocess.run`` in tests (avoids real docker calls).
  - `clock` replaces ``time.monotonic`` in tests (avoids real time.sleep waits).
  - The health-poll uses ``urllib.request`` (stdlib only — zero extra deps).
    HTTPError is caught so a 200-vs-503 check still captures the status code.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AppRuntimeError(Exception):
    """Raised when start, health-poll, or stop fails."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class HealthCheckResult:
    """Outcome of polling a single wait_for URL."""

    url: str
    last_status: int | None  # None = connection refused / DNS fail / timeout
    last_error: str | None
    healthy: bool


@dataclass
class AppRuntimeResult:
    """Aggregate result from AppRuntime.start()."""

    started: bool
    health_results: list[HealthCheckResult] = field(default_factory=list)
    compose_stdout: str = ""
    compose_stderr: str = ""


# ---------------------------------------------------------------------------
# AppRuntime
# ---------------------------------------------------------------------------


class AppRuntime:
    """Lifecycle wrapper around docker-compose for a single Browser-lane test.

    Manages:
      1. ``start()``            — ``docker compose -f <file> up -d <services>``
      2. ``wait_for_healthy()`` — HTTP HEAD poll on each ``wait_for`` URL
      3. ``stop()``             — ``docker compose -f <file> down --volumes``

    Designed as a context manager so ``stop()`` is always called even if the
    test raises an exception::

        with AppRuntime(target, repo_root) as runtime:
            runtime.wait_for_healthy()
            # run test …

    Args:
        target: A ``DockerComposeTarget`` instance from ``pfactory_yml.schema``.
        repo_root: Absolute path to the AIFactory project root; ``compose_file``
            is resolved relative to this directory.
        poll_interval_seconds: Seconds between health-poll attempts (default 2.0).
        compose_cmd: Override the compose binary tuple, e.g.
            ``("podman", "compose")`` or ``("docker-compose",)`` (default
            ``("docker", "compose")``).
        runner_fn: Replaces ``subprocess.run`` — injectable in tests so no real
            docker process is spawned.
        clock: Replaces ``time.monotonic`` — injectable in tests to skip waits.
    """

    def __init__(
        self,
        target,  # DockerComposeTarget — not type-annotated to avoid circular import
        repo_root: Path,
        *,
        poll_interval_seconds: float = 2.0,
        compose_cmd: tuple[str, ...] | None = None,
        runner_fn: Callable | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.target = target
        self.repo_root = repo_root
        self.poll_interval = poll_interval_seconds
        self.compose_cmd = compose_cmd or ("docker", "compose")
        self._runner_fn = runner_fn or subprocess.run
        self._clock = clock or time.monotonic
        self._started = False

    # ── start / stop ─────────────────────────────────────────────────────

    def start(self) -> AppRuntimeResult:
        """Bring up the compose services in detached mode.

        Runs ``docker compose -f <compose_file> up -d <services>``.
        The ``--wait-timeout 0`` flag tells compose not to wait on its own
        (we drive the readiness poll ourselves via ``wait_for_healthy``).

        Returns:
            ``AppRuntimeResult`` with ``started=True``.

        Raises:
            AppRuntimeError: if compose exits non-zero.
        """
        compose_file_abs = self.repo_root / self.target.compose_file
        argv = (
            list(self.compose_cmd)
            + [
                "-f",
                str(compose_file_abs),
                "up",
                "-d",
                "--wait-timeout",
                "0",  # we drive the readiness poll ourselves
            ]
            + list(self.target.services)
        )

        cp = self._runner_fn(argv, capture_output=True, text=True)
        result = AppRuntimeResult(
            started=cp.returncode == 0,
            compose_stdout=cp.stdout or "",
            compose_stderr=cp.stderr or "",
        )
        if cp.returncode != 0:
            raise AppRuntimeError(
                f"docker compose up failed (exit {cp.returncode}): {cp.stderr}"
            )
        self._started = True
        return result

    def stop(self) -> None:
        """Tear down the compose services and remove their volumes.

        Runs ``docker compose -f <compose_file> down --volumes`` so the
        next test starts from a clean state. No-op if ``start()`` was never
        called successfully (guards against double-stop in the context manager
        ``__exit__`` path when ``start()`` itself raised).
        """
        if not self._started:
            return
        compose_file_abs = self.repo_root / self.target.compose_file
        argv = list(self.compose_cmd) + [
            "-f",
            str(compose_file_abs),
            "down",
            "--volumes",
        ]
        self._runner_fn(argv, capture_output=True, text=True)
        self._started = False

    # ── context manager ───────────────────────────────────────────────────

    def __enter__(self) -> AppRuntime:
        """Start compose services and return self."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Always stop compose services, even if the test body raised."""
        self.stop()

    # ── health polling ────────────────────────────────────────────────────

    def wait_for_healthy(self) -> list[HealthCheckResult]:
        """Poll every ``wait_for`` URL until each returns its expected status.

        Each URL is polled independently with its own ``timeout_seconds``
        budget.  All URLs must pass before this method returns.

        Returns:
            List of ``HealthCheckResult`` — one per ``wait_for`` entry — all
            with ``healthy=True``.

        Raises:
            AppRuntimeError: if any URL does not reach its expected status
                before its timeout elapses.  The error message includes each
                unhealthy URL's last observed status code (or ``None`` on
                connection failure) so operators can diagnose the problem.
        """
        results: list[HealthCheckResult] = []
        for wf in self.target.wait_for:
            results.append(self._poll_one(wf))

        unhealthy = [r for r in results if not r.healthy]
        if unhealthy:
            details = ", ".join(
                f"{r.url} (last_status={r.last_status}, err={r.last_error!r})"
                for r in unhealthy
            )
            raise AppRuntimeError(f"app_not_healthy: {details}")
        return results

    def _poll_one(self, wait_for) -> HealthCheckResult:
        """Poll a single URL until healthy or timeout.

        Args:
            wait_for: A ``WaitFor`` instance with ``url``, ``timeout_seconds``,
                and ``expect_status``.

        Returns:
            ``HealthCheckResult`` — ``healthy=True`` if the URL responded with
            ``expect_status`` within the budget; ``healthy=False`` + populated
            ``last_status`` / ``last_error`` on timeout.
        """
        deadline = self._clock() + wait_for.timeout_seconds
        last_status: int | None = None
        last_error: str | None = None

        while self._clock() < deadline:
            try:
                req = urlrequest.Request(wait_for.url, method="HEAD")
                with urlrequest.urlopen(req, timeout=5) as resp:
                    last_status = resp.status
                    if last_status == wait_for.expect_status:
                        return HealthCheckResult(
                            url=wait_for.url,
                            last_status=last_status,
                            last_error=None,
                            healthy=True,
                        )
            except HTTPError as exc:
                last_status = exc.code
                if last_status == wait_for.expect_status:
                    return HealthCheckResult(
                        url=wait_for.url,
                        last_status=last_status,
                        last_error=None,
                        healthy=True,
                    )
                last_error = str(exc)
            except (URLError, TimeoutError, OSError) as exc:
                last_status = None
                last_error = str(exc)

            time.sleep(self.poll_interval)

        return HealthCheckResult(
            url=wait_for.url,
            last_status=last_status,
            last_error=last_error or "timeout",
            healthy=False,
        )
