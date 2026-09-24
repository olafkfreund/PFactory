"""Utilities shared across P0 docker acceptance tests.

All helpers wrap subprocess calls to `docker` with sensible defaults
(timeouts, capture_output, text=True). Failure modes return the
`CompletedProcess` so individual tests can assert on stdout/stderr/exit.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Path to the project's Dockerfile. Single source of truth for the test
# fixtures so the P0.12 rename (Dockerfile.chainguard → Dockerfile) didn't
# require touching every test file.
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"


def docker_available() -> bool:
    """True iff the `docker` CLI is on PATH and the daemon answers."""
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        timeout=5,
    )
    return result.returncode == 0


class RegistryUnavailableError(RuntimeError):
    """The registry never answered: timeout, refused, DNS, TLS, 5xx, throttle.

    The environment could not answer the question, so callers skip — the same
    category as "docker is not installed" (#744). NOT for a registry that
    answered and said no; see :class:`ManifestInspectError`.
    """


class ManifestInspectError(RuntimeError):
    """The registry answered and refused: manifest unknown / unauthorized.

    A real defect (e.g. a pinned digest that no longer exists), so callers must
    fail rather than skip (#744).
    """


# Substrings that mark a TRANSPORT failure in `imagetools inspect` stderr —
# the registry never answered. Matched case-insensitively. Anything else
# (manifest unknown, not found, unauthorized) is the registry answering, which
# is a real failure and must not be skipped.
_TRANSPORT_MARKERS = (
    "i/o timeout",
    "context deadline exceeded",
    "dial tcp",
    "tls handshake timeout",
    "connection refused",
    "connection reset",
    "temporary failure in name resolution",
    "no such host",
    "unexpected eof",
    "too many requests",
    "server misbehaving",
)

# The HTTP status codes need word boundaries, not substring matching (#749).
# A refusal echoes the ref, and a sha256 digest is 64 hex characters that carry
# these digit sequences ~6% of the time -- so `…@sha256:503edd78…: not found`
# read as "the registry never answered" and SKIPPED the gate for a vanished
# digest, the one case the refusal branch exists to catch. Inside a digest the
# neighbouring hex characters are word characters, so \b does not hold; in
# `status: 503 Service Unavailable` or `toomanyrequests: 429` it does.
_TRANSPORT_CODE_RE = re.compile(r"\b(?:429|502|503|504)\b")


# Two tries: one retry absorbs the common single hiccup without turning a 30s
# failure into minutes of CI time (#744).
_INSPECT_ATTEMPTS = 2


def _is_transport_error(stderr: str) -> bool:
    """True when stderr reads as "the registry never answered"."""
    low = (stderr or "").lower()
    if any(marker in low for marker in _TRANSPORT_MARKERS):
        return True
    return bool(_TRANSPORT_CODE_RE.search(low))


def inspect_raw_manifest(
    ref: str,
    *,
    timeout: int = 30,
    backoff: float = 3.0,
    runner=subprocess.run,
    sleep=time.sleep,
) -> str:
    """Return the raw image-index manifest JSON for ``ref``.

    Raises :class:`RegistryUnavailableError` when the registry never answered
    (after :data:`_INSPECT_ATTEMPTS` tries, ``backoff`` seconds apart) and
    :class:`ManifestInspectError` when it answered with a refusal. ``runner``
    and ``sleep`` are injection seams so the behaviour is testable offline.
    """
    argv = ["docker", "buildx", "imagetools", "inspect", "--raw", ref]
    last = ""
    for attempt in range(_INSPECT_ATTEMPTS):
        try:
            result = runner(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last = f"timed out after {timeout}s"
        else:
            if result.returncode == 0:
                return result.stdout
            stderr = (result.stderr or "").strip()
            if not _is_transport_error(stderr):
                raise ManifestInspectError(
                    f"`docker buildx imagetools inspect --raw {ref}` was refused:\n"
                    f"--- stderr ---\n{stderr[-1000:]}"
                )
            last = stderr[-500:]
        if attempt < _INSPECT_ATTEMPTS - 1:
            sleep(backoff)
    raise RegistryUnavailableError(f"{ref}: {last}")


def docker_build(
    dockerfile: Path,
    tag: str,
    build_args: dict[str, str] | None = None,
    context: Path | None = None,
    timeout: int = 600,
) -> subprocess.CompletedProcess:
    """Build a docker image. Returns the CompletedProcess for assertion."""
    args = ["docker", "build", "-f", str(dockerfile), "-t", tag]
    for key, value in (build_args or {}).items():
        args += ["--build-arg", f"{key}={value}"]
    args.append(str(context or REPO_ROOT))
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def docker_run(
    image: str,
    *cmd: str,
    detach: bool = False,
    user: str | None = None,
    read_only: bool = False,
    cap_drop: list[str] | None = None,
    security_opt: list[str] | None = None,
    tmpfs: list[str] | None = None,
    publish: list[str] | None = None,
    name: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run a docker container with optional hardening flags."""
    args = ["docker", "run", "--rm"]
    if detach:
        args.append("-d")
    if user:
        args += ["--user", user]
    if read_only:
        args.append("--read-only")
    for cap in cap_drop or []:
        args += ["--cap-drop", cap]
    for opt in security_opt or []:
        args += ["--security-opt", opt]
    for mount in tmpfs or []:
        args += ["--tmpfs", mount]
    for p in publish or []:
        args += ["-p", p]
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    if name:
        args += ["--name", name]
    args.append(image)
    args.extend(cmd)
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def docker_logs(container_name: str, tail: int = 60) -> str:
    """Last `tail` lines of a container's combined stdout/stderr.

    Best effort by design: this is only ever called on a path that is already
    failing, so a docker error here must not replace the real assertion with a
    subprocess traceback.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["docker", "logs", "--tail", str(tail), container_name],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"<could not read container logs: {type(exc).__name__}>"
    out = (result.stdout or "") + (result.stderr or "")
    return out.strip() or "<container produced no output>"


def health_or_explain(url: str, container_name: str, timeout: int = 60) -> str:
    """Return "" when the endpoint answers, else a message carrying the logs.

    The four P0 runtime tests asserted on wait_for_health alone, so a red run
    said only "container did not become healthy" and gave the reader nothing to
    act on -- which is why this check stayed red across four merges with nobody
    able to diagnose it (PFactory#586). The container's own output is the first
    thing anyone would ask for, so the failure carries it.
    """
    if wait_for_health(url, timeout=timeout):
        return ""
    return (
        f"container did not answer {url} within {timeout}s\n"
        f"--- docker logs {container_name} (last 60 lines) ---\n"
        f"{docker_logs(container_name)}"
    )


def docker_kill(container_name: str) -> None:
    """Best-effort cleanup of a named container. Never raises."""
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True,
        timeout=10,
    )


def docker_inspect(image_or_container: str) -> list[dict]:
    """Return parsed `docker inspect` output (list of objects)."""
    result = subprocess.run(
        ["docker", "inspect", image_or_container],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return json.loads(result.stdout)


def wait_for_health(url: str, timeout: int = 30) -> bool:
    """Poll an HTTP endpoint until it returns 200 or `timeout` elapses."""
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.5)
    return False
