"""Utilities shared across P0 docker acceptance tests.

All helpers wrap subprocess calls to `docker` with sensible defaults
(timeouts, capture_output, text=True). Failure modes return the
`CompletedProcess` so individual tests can assert on stdout/stderr/exit.
"""

from __future__ import annotations

import json
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
