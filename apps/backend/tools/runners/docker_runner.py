"""Docker-based test runner — Task 4 (#5).

Runs a command inside a locked-down container (defaults: ``--network=none``,
``--read-only`` rootfs, ro-mount of the target repo, rw-mount of a scratch
dir, CPU/memory/PID caps). Wraps ``subprocess.run`` rather than the docker
Python SDK so the dependency stays at zero — works the same way under
``podman`` rootless by swapping the binary name.

Typical usage from the Executor in the test pipeline (Task 8+)::

    runner = DockerRunner(image="pfactory-runner-python:latest")
    result = runner.run(
        repo_path=Path("/path/to/project"),
        scratch_path=Path("/path/to/workspace/scratch"),
        command=[
            "bash", "-c",
            "cd /work && cp -r /scratch/tests . "
            "&& pytest --cov=app --cov-report=xml:/scratch/coverage.xml "
            "--junitxml=/scratch/junit.xml tests/functional"
        ],
        timeout_sec=600,
    )

Tests stub ``subprocess.run`` and assert on the constructed argv. A real
Docker integration smoke is gated on ``docker --version`` succeeding and
skipped otherwise.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Image resolution
# ---------------------------------------------------------------------------

# Where runner-images.yml publishes (and signs) the per-framework sandbox
# images. Bare `pfactory-runner-*` tags are qualified with this so a lane runs
# the image CI built from a commit, not whatever happens to sit in the local
# daemon under the same name (#449).
#
# Before #449 the workflow built six images, smoke-tested them and threw them
# away, while frameworks/*/descriptor.yaml and DEFAULT_IMAGE below named bare
# tags and guides/shipping.md told operators to `docker build -t
# pfactory-runner-pytest:latest` by hand. Nothing tied the tag a lane executed
# to a commit -- the hollow-verify shape of TFactory#886.
DEFAULT_RUNNER_REGISTRY = "ghcr.io/olafkfreund"

#: Override the registry that bare runner tags resolve to.
#:
#: * unset      -> DEFAULT_RUNNER_REGISTRY, i.e. the images CI publishes.
#: * "" (empty) -> no qualification; bare tags resolve against the local daemon
#:                 exactly as before. This is the escape hatch for iterating on
#:                 a Dockerfile locally, where the point IS to run your build.
#: * any other  -> that registry/namespace, e.g. a mirror or a fork's GHCR.
#:
#: Already-qualified images (anything containing a "/") and images that are not
#: `pfactory-runner-*` are passed through untouched, so an explicitly requested
#: image always wins.
RUNNER_REGISTRY_ENV = "PFACTORY_RUNNER_REGISTRY"

_RUNNER_PREFIX = "pfactory-runner-"


def resolve_runner_image(image: str) -> str:
    """Qualify a bare ``pfactory-runner-*`` tag with the publishing registry.

    >>> resolve_runner_image("pfactory-runner-pytest:latest")
    'ghcr.io/olafkfreund/pfactory-runner-pytest:latest'
    >>> resolve_runner_image("ghcr.io/acme/pfactory-runner-pytest:latest")
    'ghcr.io/acme/pfactory-runner-pytest:latest'
    >>> resolve_runner_image("python:3.12-slim")
    'python:3.12-slim'
    """
    if "/" in image or not image.startswith(_RUNNER_PREFIX):
        return image
    registry = os.environ.get(RUNNER_REGISTRY_ENV, DEFAULT_RUNNER_REGISTRY).strip().strip("/")
    if not registry:
        return image
    return f"{registry}/{image}"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DockerRunnerError(Exception):
    """Base for runner-level failures (binary missing, build issues)."""


class DockerTimeoutError(DockerRunnerError):
    """Raised when the container exceeds ``timeout_sec``."""


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class DockerRunResult:
    """Outcome of one container run."""

    returncode: int
    stdout: str = ""
    stderr: str = ""

    # Artifacts collected from the scratch volume after the run finishes.
    junit_xml_path: Path | None = None
    coverage_xml_path: Path | None = None

    # Full argv that was passed to the container runtime (for debug/logging).
    argv: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class DockerRunner:
    """Build + execute the docker invocation for a sandboxed test run.

    Args:
        image: Container image tag (e.g. ``pfactory-runner-python:latest``).
        binary: ``docker`` or ``podman``. Defaults to env override
            ``PFACTORY_CONTAINER_BIN`` else ``docker``.
        cpus: CPU quota (Docker ``--cpus`` value).
        memory: Memory cap (Docker ``--memory`` value, e.g. ``2g``).
        pids_limit: Maximum PIDs in the container.
        network: Network mode. Default ``none`` (no egress). DAST lane
            (phase 5) will pass ``bridge``.
        read_only_rootfs: Make the container rootfs read-only. Default True.
            tini still works (it doesn't write); the writable surface is
            limited to the bind-mounted /scratch.
    """

    DEFAULT_IMAGE = "pfactory-runner-python:latest"
    REPO_MOUNT = "/work"
    SCRATCH_MOUNT = "/scratch"

    def __init__(
        self,
        image: str | None = None,
        binary: str | None = None,
        cpus: str = "2",
        memory: str = "2g",
        pids_limit: int = 512,
        network: str = "none",
        read_only_rootfs: bool = True,
    ) -> None:
        # One resolution point: descriptors, lane_dispatch and every agent that
        # names an image all route through here, so qualifying it once covers
        # them all (#449).
        self.image = resolve_runner_image(image or self.DEFAULT_IMAGE)
        self.binary = binary or os.environ.get("PFACTORY_CONTAINER_BIN", "docker")
        self.cpus = cpus
        self.memory = memory
        self.pids_limit = pids_limit
        self.network = network
        self.read_only_rootfs = read_only_rootfs

    # ── argv construction ───────────────────────────────────────────────

    def build_argv(
        self,
        *,
        repo_path: Path,
        scratch_path: Path,
        command: Sequence[str],
        env: dict[str, str] | None = None,
        extra_args: Sequence[str] | None = None,
        secret_files: Mapping[str, str] | None = None,
    ) -> list[str]:
        """Return the full container invocation as a list of strings.

        Pure function — no side effects — so tests can assert on the
        exact argv without a real container runtime present.

        ``secret_files`` maps absolute host paths → absolute container paths;
        each is bind-mounted **read-only** (e.g. a materialised kubeconfig for
        a network-enabled api/integration lane, issue #73). Mounting only —
        the host files' lifecycle (materialise + wipe) is the caller's job.
        """
        if not command:
            raise DockerRunnerError("command must not be empty")
        if not repo_path.is_absolute() or not scratch_path.is_absolute():
            raise DockerRunnerError("repo_path and scratch_path must be absolute")
        for host_path, container_path in (secret_files or {}).items():
            if not Path(host_path).is_absolute() or not Path(container_path).is_absolute():
                raise DockerRunnerError(
                    "secret_files keys (host) and values (container) must be absolute paths"
                )

        argv: list[str] = [
            self.binary,
            "run",
            "--rm",
            "--network",
            self.network,
            "--cpus",
            str(self.cpus),
            "--memory",
            str(self.memory),
            "--pids-limit",
            str(self.pids_limit),
            "-v",
            f"{repo_path}:{self.REPO_MOUNT}:ro",
            "-v",
            f"{scratch_path}:{self.SCRATCH_MOUNT}:rw",
            "-w",
            self.SCRATCH_MOUNT,
        ]
        if self.read_only_rootfs:
            argv.append("--read-only")
            # tmpfs for /tmp because read-only rootfs would block apt/pip
            # caches that pytest plugins like xdist sometimes scribble.
            argv.extend(["--tmpfs", "/tmp:rw,size=64m"])

        # Read-only bind-mounts for materialised secret files (issue #73).
        for host_path, container_path in (secret_files or {}).items():
            argv.extend(["-v", f"{host_path}:{container_path}:ro"])

        for key, val in (env or {}).items():
            argv.extend(["-e", f"{key}={val}"])

        if extra_args:
            argv.extend(extra_args)

        argv.append(self.image)
        argv.extend(command)
        return argv

    # ── execution ───────────────────────────────────────────────────────

    def run(
        self,
        *,
        repo_path: Path,
        scratch_path: Path,
        command: Sequence[str],
        timeout_sec: int = 600,
        env: dict[str, str] | None = None,
        extra_env: dict[str, str] | None = None,
        extra_args: Sequence[str] | None = None,
        secret_files: Mapping[str, str] | None = None,
    ) -> DockerRunResult:
        """Execute the container and return a DockerRunResult.

        Args:
            repo_path: Absolute path to the repo mounted at ``/work:ro``.
            scratch_path: Absolute path to the scratch volume (``/scratch:rw``).
            command: Command + args to run inside the container.
            timeout_sec: Hard wall-clock cap; raises ``DockerTimeoutError``
                if the container runs longer.
            env: Base environment variables forwarded as ``-e KEY=VAL``
                flags.  Use for caller-level configuration that spans
                many subtasks (e.g. the full test suite env).
            extra_env: Additional environment variables merged ON TOP of
                ``env`` (``extra_env`` values win on collision).  Intended
                for per-run injections such as ``PFACTORY_TARGET_URL``
                set by the Browser-lane AppRuntime wrapper.  Callers that
                don't need the split can ignore this and use ``env`` alone.
            extra_args: Extra ``docker run`` flags inserted before the
                image name (e.g. ``["--user", "1000"]``).

        Raises:
            DockerRunnerError: if the binary isn't on PATH.
            DockerTimeoutError: if the container exceeds ``timeout_sec``.
        """
        if not shutil.which(self.binary):
            raise DockerRunnerError(
                f"{self.binary!r} binary not found on PATH — "
                f"install Docker / Podman or set PFACTORY_CONTAINER_BIN"
            )

        # Merge env + extra_env: extra_env takes precedence (it's the
        # per-run injection layer supplied by AppRuntime for Browser lanes).
        merged_env: dict[str, str] | None = None
        if env or extra_env:
            merged_env = dict(env or {})
            if extra_env:
                merged_env.update(extra_env)

        argv = self.build_argv(
            repo_path=repo_path,
            scratch_path=scratch_path,
            command=command,
            env=merged_env,
            extra_args=extra_args,
            secret_files=secret_files,
        )
        logger.debug("docker invocation: %s", " ".join(argv))

        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DockerTimeoutError(
                f"container exceeded {timeout_sec}s — killed by harness"
            ) from exc

        result = DockerRunResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            argv=argv,
        )

        # Pick up scratch-side artifacts if present.
        junit = scratch_path / "junit.xml"
        cov = scratch_path / "coverage.xml"
        if junit.exists():
            result.junit_xml_path = junit
        if cov.exists():
            result.coverage_xml_path = cov

        return result

    # ── convenience wrappers ────────────────────────────────────────────

    def run_pytest(
        self,
        *,
        repo_path: Path,
        scratch_path: Path,
        tests_relpath: str,
        cov_package: str | None = None,
        timeout_sec: int = 600,
    ) -> DockerRunResult:
        """Default pytest invocation for the functional lane.

        Copies whatever the planner/generator wrote into
        ``<scratch>/tests/`` over to the read-only mount as a working
        copy, then runs pytest with coverage + junit emit into scratch.

        The cp dance is what lets us keep the repo bind-mounted ro
        while still extending it with generated test files.
        """
        cov_arg = f"--cov={cov_package}" if cov_package else ""
        cmd_str = (
            "set -e; "
            "cp -r /scratch/tests . && "
            f"pytest {tests_relpath} "
            "--junitxml=/scratch/junit.xml "
            f"{cov_arg} "
            "--cov-report=xml:/scratch/coverage.xml "
            "--cov-report=term"
        )
        return self.run(
            repo_path=repo_path,
            scratch_path=scratch_path,
            command=["bash", "-lc", cmd_str],
            timeout_sec=timeout_sec,
        )
