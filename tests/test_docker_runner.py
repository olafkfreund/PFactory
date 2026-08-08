"""Tests for DockerRunner — Task 4 (#5), sub-task 4.1.

Most tests stub ``subprocess.run`` so we never spin up a real container;
the integration smoke at the bottom of the file is skipped when ``docker``
isn't on PATH so the suite stays green on dev machines without Docker.

Covers:
  - argv has the lockdown flags: --network none, --read-only, ro repo, rw scratch, cpu/mem/pid limits
  - tmpfs /tmp is added when read_only_rootfs=True
  - extra env vars become -e args in order
  - extra_args are appended before the image
  - rejects empty command
  - rejects relative paths
  - binary is configurable (docker / podman / env override)
  - run() raises DockerRunnerError when binary missing
  - run() raises DockerTimeoutError on subprocess.TimeoutExpired
  - run() picks up junit + coverage from scratch when they exist
  - run_pytest convenience wrapper produces a sane bash -lc string
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from tools.runners.docker_runner import (
    DEFAULT_RUNNER_IMAGE,
    DEFAULT_RUNNER_REGISTRY,
    RUNNER_REGISTRY_ENV,
    DockerRunner,
    DockerRunnerError,
    DockerTimeoutError,
    resolve_runner_image,
)

# ── argv construction ────────────────────────────────────────────────────


def _basic_argv(**overrides):
    r = DockerRunner(**overrides)
    return r.build_argv(
        repo_path=Path("/tmp/repo"),
        scratch_path=Path("/tmp/scratch"),
        command=["pytest", "-v"],
    )


def test_argv_has_lockdown_flags():
    argv = _basic_argv()
    assert "--network" in argv and "none" in argv
    assert "--read-only" in argv
    assert "--cpus" in argv
    assert "--memory" in argv
    assert "--pids-limit" in argv


def test_argv_mounts():
    argv = _basic_argv()
    assert "/tmp/repo:/work:ro" in argv
    assert "/tmp/scratch:/scratch:rw" in argv


def test_argv_workdir_is_scratch():
    argv = _basic_argv()
    idx = argv.index("-w")
    assert argv[idx + 1] == "/scratch"


def test_argv_default_image_appears_before_command():
    argv = _basic_argv()
    image_idx = argv.index(resolve_runner_image(DockerRunner.DEFAULT_IMAGE))
    cmd_idx = argv.index("pytest")
    assert image_idx < cmd_idx


def test_argv_uses_default_image_constant():
    """The argv carries the RESOLVED default, i.e. the image CI publishes (#449).

    It used to carry the bare tag, which resolves against whatever sits in the
    local daemon under that name -- so the lane ran whatever was last built by
    hand rather than what CI built from a commit.
    """
    argv = _basic_argv()
    assert resolve_runner_image(DockerRunner.DEFAULT_IMAGE) in argv
    assert f"{DEFAULT_RUNNER_REGISTRY}/{DockerRunner.DEFAULT_IMAGE}" in argv


def test_tmpfs_tmp_added_when_read_only_rootfs():
    argv = _basic_argv()
    assert "--tmpfs" in argv
    idx = argv.index("--tmpfs")
    assert argv[idx + 1].startswith("/tmp:")


def test_no_tmpfs_when_read_only_rootfs_disabled():
    argv = _basic_argv(read_only_rootfs=False)
    assert "--read-only" not in argv
    assert "--tmpfs" not in argv


def test_env_vars_become_e_flags():
    r = DockerRunner()
    argv = r.build_argv(
        repo_path=Path("/tmp/r"),
        scratch_path=Path("/tmp/s"),
        command=["cmd"],
        env={"FOO": "1", "BAR": "two"},
    )
    assert "FOO=1" in argv
    assert "BAR=two" in argv


def test_secret_files_become_ro_mounts():
    # #73: materialised secret files are bind-mounted read-only.
    argv = DockerRunner().build_argv(
        repo_path=Path("/tmp/r"),
        scratch_path=Path("/tmp/s"),
        command=["cmd"],
        secret_files={"/host/kubeconfig": "/root/.kube/config"},
    )
    assert "/host/kubeconfig:/root/.kube/config:ro" in argv


def test_no_secret_mounts_by_default():
    # The default (unit-lane) path mounts no secrets.
    argv = _basic_argv()
    assert not any(":ro" in a and "kube" in a for a in argv)


def test_secret_files_must_be_absolute():
    with pytest.raises(DockerRunnerError):
        DockerRunner().build_argv(
            repo_path=Path("/tmp/r"),
            scratch_path=Path("/tmp/s"),
            command=["cmd"],
            secret_files={"relative/path": "/root/.kube/config"},
        )


def test_extra_args_appended_before_image():
    r = DockerRunner()
    argv = r.build_argv(
        repo_path=Path("/tmp/r"),
        scratch_path=Path("/tmp/s"),
        command=["cmd"],
        extra_args=["--user", "1000"],
    )
    image_idx = argv.index(resolve_runner_image(DockerRunner.DEFAULT_IMAGE))
    user_idx = argv.index("--user")
    assert user_idx < image_idx


def test_custom_image_honoured():
    argv = _basic_argv(image="myreg/custom:dev")
    assert "myreg/custom:dev" in argv
    assert resolve_runner_image(DockerRunner.DEFAULT_IMAGE) not in argv


# ── the default image must be one something builds (#493) ────────────────

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNER_IMAGES_WORKFLOW = _REPO_ROOT / ".github/workflows/runner-images.yml"


def _built_runners() -> set[str]:
    """The frameworks `runner-images.yml` actually builds, read from its matrix.

    Read rather than hand-listed: a second copy of the matrix in a test is the
    thing that drifts, and drift is the whole of #493.
    """
    matrix = re.search(
        r"^\s*runner:\s*\[([^\]]+)\]", _RUNNER_IMAGES_WORKFLOW.read_text(), re.M
    )
    assert matrix, "runner-images.yml no longer declares a `runner:` matrix list"
    return {name.strip() for name in matrix.group(1).split(",")}


def test_default_image_is_built_by_the_runner_images_workflow():
    """#493: `DEFAULT_IMAGE` named `pfactory-runner-python`, which nothing built.

    Not in `runner-images.yml`'s matrix, no `docker/pfactory-runner-python/`,
    no compose service, no script -- only a hand-typed `docker build` in a
    Dockerfile's own header comment. Every caller landing on the default asked
    for an image no pipeline produces: `pull access denied` before #449, a 404
    against ghcr.io after it.

    Asserted against the workflow rather than against a literal, so the default
    cannot drift off the matrix again without this going red.
    """
    framework = DockerRunner.DEFAULT_IMAGE.removeprefix("pfactory-runner-").split(":")[0]
    assert framework in _built_runners(), (
        f"DEFAULT_IMAGE={DockerRunner.DEFAULT_IMAGE!r} names a runner "
        f"runner-images.yml does not build (it builds {sorted(_built_runners())})"
    )
    dockerfile = _REPO_ROOT / f"docker/pfactory-runner-{framework}/Dockerfile"
    assert dockerfile.exists(), f"no Dockerfile at {dockerfile}"


def test_class_default_is_the_module_constant():
    """One definition, two names (#493).

    `gen_functional._resolve_runner_fn` is deprecated, not dead, and it carried
    its own `pfactory-runner-python:latest` literal. Two spellings of one
    default is how one of them stayed on the v0.1 name after the rename, and it
    is why #493 had to name two files.
    """
    assert DockerRunner.DEFAULT_IMAGE is DEFAULT_RUNNER_IMAGE


# ── runner image resolution (#449) ───────────────────────────────────────


def test_bare_runner_tag_is_qualified_with_the_publishing_registry():
    assert (
        resolve_runner_image("pfactory-runner-pytest:latest")
        == f"{DEFAULT_RUNNER_REGISTRY}/pfactory-runner-pytest:latest"
    )


def test_already_qualified_image_is_untouched():
    """An explicitly requested image always wins over the default registry."""
    for image in (
        "ghcr.io/acme/pfactory-runner-pytest:latest",
        "docker.io/library/python:3.12-slim",
        "localhost:5000/pfactory-runner-jest:dev",
    ):
        assert resolve_runner_image(image) == image


def test_non_runner_image_is_untouched():
    assert resolve_runner_image("python:3.12-slim") == "python:3.12-slim"


def test_registry_env_override(monkeypatch):
    monkeypatch.setenv(RUNNER_REGISTRY_ENV, "registry.example.com/mirror")
    assert (
        resolve_runner_image("pfactory-runner-cloud:latest")
        == "registry.example.com/mirror/pfactory-runner-cloud:latest"
    )


def test_empty_registry_env_restores_bare_local_tags(monkeypatch):
    """The escape hatch for iterating on a Dockerfile, where running YOUR build
    is the point. Setting it empty must not fall back to the default."""
    monkeypatch.setenv(RUNNER_REGISTRY_ENV, "")
    assert resolve_runner_image("pfactory-runner-pytest:latest") == "pfactory-runner-pytest:latest"
    assert DockerRunner(image="pfactory-runner-pytest:latest").image == (
        "pfactory-runner-pytest:latest"
    )


def test_custom_binary_honoured():
    argv = _basic_argv(binary="podman")
    assert argv[0] == "podman"


def test_binary_env_override(monkeypatch):
    monkeypatch.setenv("PFACTORY_CONTAINER_BIN", "podman")
    argv = _basic_argv()
    assert argv[0] == "podman"


# ── validation ───────────────────────────────────────────────────────────


def test_empty_command_rejected():
    r = DockerRunner()
    with pytest.raises(DockerRunnerError):
        r.build_argv(
            repo_path=Path("/tmp/r"),
            scratch_path=Path("/tmp/s"),
            command=[],
        )


def test_relative_repo_path_rejected():
    r = DockerRunner()
    with pytest.raises(DockerRunnerError):
        r.build_argv(
            repo_path=Path("relative/repo"),
            scratch_path=Path("/tmp/s"),
            command=["cmd"],
        )


def test_relative_scratch_path_rejected():
    r = DockerRunner()
    with pytest.raises(DockerRunnerError):
        r.build_argv(
            repo_path=Path("/tmp/r"),
            scratch_path=Path("relative/scratch"),
            command=["cmd"],
        )


# ── execution (mocked subprocess) ────────────────────────────────────────


def _fake_completed(returncode=0, stdout="ok", stderr=""):
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def test_run_raises_when_binary_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    r = DockerRunner()
    with pytest.raises(DockerRunnerError, match="not found on PATH"):
        r.run(
            repo_path=tmp_path,
            scratch_path=tmp_path,
            command=["cmd"],
        )


def test_run_returns_result_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _fake_completed(0, "hello", "")
    )
    r = DockerRunner()
    res = r.run(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        command=["echo", "hi"],
    )
    assert res.ok is True
    assert res.returncode == 0
    assert res.stdout == "hello"
    # argv recorded for debug
    assert res.argv and res.argv[0] == "docker"


def test_run_raises_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    def _boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=1)
    monkeypatch.setattr(subprocess, "run", _boom)
    r = DockerRunner()
    with pytest.raises(DockerTimeoutError):
        r.run(
            repo_path=tmp_path,
            scratch_path=tmp_path,
            command=["echo"],
            timeout_sec=1,
        )


def test_run_picks_up_junit_and_coverage(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _fake_completed(0))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "junit.xml").write_text("<testsuites/>")
    (scratch / "coverage.xml").write_text("<coverage/>")

    r = DockerRunner()
    res = r.run(
        repo_path=tmp_path,
        scratch_path=scratch,
        command=["pytest"],
    )
    assert res.junit_xml_path == scratch / "junit.xml"
    assert res.coverage_xml_path == scratch / "coverage.xml"


def test_run_omits_artifact_paths_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _fake_completed(0))
    r = DockerRunner()
    res = r.run(repo_path=tmp_path, scratch_path=tmp_path, command=["echo"])
    assert res.junit_xml_path is None
    assert res.coverage_xml_path is None


# ── run_pytest convenience wrapper ──────────────────────────────────────


def test_run_pytest_command_shape(monkeypatch, tmp_path):
    """The wrapper builds bash -lc with pytest + cov + junit emit."""
    captured = {}
    def _capture(*args, **kw):
        captured["argv"] = args[0]
        return _fake_completed(0)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", _capture)

    r = DockerRunner()
    r.run_pytest(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        tests_relpath="tests/functional",
        cov_package="app",
    )
    argv = captured["argv"]
    # The final command is in the last 3 entries: bash -lc "..."
    assert argv[-3:][:2] == ["bash", "-lc"]
    cmd_str = argv[-1]
    assert "pytest tests/functional" in cmd_str
    assert "--cov=app" in cmd_str
    assert "--junitxml=/scratch/junit.xml" in cmd_str
    assert "--cov-report=xml:/scratch/coverage.xml" in cmd_str


def test_run_pytest_omits_cov_arg_when_package_unset(monkeypatch, tmp_path):
    captured = {}
    def _capture(*args, **kw):
        captured["argv"] = args[0]
        return _fake_completed(0)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", _capture)

    r = DockerRunner()
    r.run_pytest(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        tests_relpath="tests/",
    )
    assert "--cov=" not in captured["argv"][-1]


# ── extra_env parameter (Task 8 / #24) ─────────────────────────────────


def test_extra_env_merged_into_argv(monkeypatch, tmp_path):
    """extra_env values must appear as -e flags in the docker argv."""
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    captured = {}

    def _capture(*args, **kw):
        captured["argv"] = args[0]
        return _fake_completed(0)

    monkeypatch.setattr(subprocess, "run", _capture)

    r = DockerRunner()
    r.run(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        command=["pytest"],
        extra_env={"PFACTORY_TARGET_URL": "http://localhost:3000"},
    )
    argv = captured["argv"]
    assert "PFACTORY_TARGET_URL=http://localhost:3000" in argv


def test_extra_env_overrides_base_env(monkeypatch, tmp_path):
    """When env and extra_env share a key, extra_env wins."""
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    captured = {}

    def _capture(*args, **kw):
        captured["argv"] = args[0]
        return _fake_completed(0)

    monkeypatch.setattr(subprocess, "run", _capture)

    r = DockerRunner()
    r.run(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        command=["pytest"],
        env={"MY_VAR": "original"},
        extra_env={"MY_VAR": "override"},
    )
    argv = captured["argv"]
    assert "MY_VAR=override" in argv
    assert "MY_VAR=original" not in argv


def test_extra_env_none_does_not_affect_argv(monkeypatch, tmp_path):
    """Passing extra_env=None must not add any extra -e flags."""
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    captured = {}

    def _capture(*args, **kw):
        captured["argv"] = args[0]
        return _fake_completed(0)

    monkeypatch.setattr(subprocess, "run", _capture)

    r = DockerRunner()
    r.run(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        command=["pytest"],
        env={"BASE_VAR": "1"},
        extra_env=None,
    )
    argv = captured["argv"]
    assert "BASE_VAR=1" in argv
    # no extra -e flags beyond BASE_VAR
    e_flag_values = [argv[i + 1] for i, v in enumerate(argv) if v == "-e"]
    assert all(v.startswith("BASE_VAR") for v in e_flag_values)


# ── real-docker integration smoke (skipped if docker absent) ────────────


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "--version"],
            capture_output=True, check=True, timeout=3,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _docker_available(), reason="docker not available")
def test_docker_image_runs_echo(tmp_path):
    """Smoke: the runner can fire a docker invocation and capture stdout.

    Uses busybox (3MB, ships everywhere) — does NOT require the
    pfactory-runner-pytest image to be built. Validates the wiring
    end-to-end without the build artifact.
    """
    r = DockerRunner(image="busybox:latest")
    res = r.run(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        command=["echo", "pfactory-smoke"],
        # Generous timeout — the combination of --network=none +
        # --read-only + --tmpfs is observed to take 1-2 minutes on
        # some hosts (notably first invocation after daemon idle).
        # Steady-state on warm daemons is sub-second; we tolerate
        # the slow path rather than skipping the integration smoke.
        timeout_sec=180,
    )
    assert res.ok, res.stderr
    assert "pfactory-smoke" in res.stdout


# ── per-image smoke tests (Task 7 / #23) ────────────────────────────────
#
# These tests require:
#   1. Docker daemon accessible (checked by _docker_available()).
#   2. The pfactory-runner-* images to have been built locally.
#      Build them with:
#        docker build -t pfactory-runner-pytest:latest docker/pfactory-runner-pytest
#        docker build -t pfactory-runner-jest:latest docker/pfactory-runner-jest
#        docker build -t pfactory-runner-playwright:latest docker/pfactory-runner-playwright
#
# All cases are marked @pytest.mark.slow so they are excluded from the fast
# pre-commit and PR-check runs (pytest -m "not slow").  The runner-images CI
# workflow builds the images and exercises the same checks inline.


def _image_available(image: str) -> bool:
    """Return True if the image tag exists in the local Docker daemon."""
    if not _docker_available():
        return False
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            check=False,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# --- pytest image -----------------------------------------------------------

@pytest.mark.slow
@pytest.mark.skipif(not _docker_available(), reason="docker not available")
@pytest.mark.skipif(
    not _image_available("pfactory-runner-pytest:latest"),
    reason="pfactory-runner-pytest:latest not built locally",
)
def test_pytest_image_runs_hello_world(tmp_path):
    """pfactory-runner-pytest can run a trivial Python print."""
    r = DockerRunner(image="pfactory-runner-pytest:latest")
    res = r.run(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        command=["python", "-c", "print('ok')"],
        timeout_sec=60,
    )
    assert res.returncode == 0, res.stderr
    assert "ok" in res.stdout


@pytest.mark.slow
@pytest.mark.skipif(not _docker_available(), reason="docker not available")
@pytest.mark.skipif(
    not _image_available("pfactory-runner-pytest:latest"),
    reason="pfactory-runner-pytest:latest not built locally",
)
def test_pytest_image_has_pytest_on_path(tmp_path):
    """pytest 8.x must be importable inside pfactory-runner-pytest."""
    r = DockerRunner(image="pfactory-runner-pytest:latest")
    res = r.run(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        command=["pytest", "--version"],
        timeout_sec=60,
    )
    assert res.returncode == 0, res.stderr
    # pytest --version prints e.g. "pytest 8.3.2"
    assert "pytest" in res.stdout or "pytest" in res.stderr
    version_output = res.stdout + res.stderr
    assert "8." in version_output, f"Expected pytest 8.x, got: {version_output!r}"


# --- jest image -------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.skipif(not _docker_available(), reason="docker not available")
@pytest.mark.skipif(
    not _image_available("pfactory-runner-jest:latest"),
    reason="pfactory-runner-jest:latest not built locally",
)
def test_jest_image_runs_hello_world(tmp_path):
    """pfactory-runner-jest can run a trivial Node console.log."""
    r = DockerRunner(image="pfactory-runner-jest:latest")
    res = r.run(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        command=["node", "-e", "console.log('ok')"],
        timeout_sec=60,
    )
    assert res.returncode == 0, res.stderr
    assert "ok" in res.stdout


@pytest.mark.slow
@pytest.mark.skipif(not _docker_available(), reason="docker not available")
@pytest.mark.skipif(
    not _image_available("pfactory-runner-jest:latest"),
    reason="pfactory-runner-jest:latest not built locally",
)
def test_jest_image_has_jest_on_path(tmp_path):
    """jest 29.x must be on PATH inside pfactory-runner-jest."""
    r = DockerRunner(image="pfactory-runner-jest:latest")
    res = r.run(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        command=["jest", "--version"],
        timeout_sec=60,
    )
    assert res.returncode == 0, res.stderr
    version_output = res.stdout + res.stderr
    assert "29." in version_output, f"Expected jest 29.x, got: {version_output!r}"


# --- playwright image -------------------------------------------------------

@pytest.mark.slow
@pytest.mark.skipif(not _docker_available(), reason="docker not available")
@pytest.mark.skipif(
    not _image_available("pfactory-runner-playwright:latest"),
    reason="pfactory-runner-playwright:latest not built locally",
)
def test_playwright_image_runs_hello_world(tmp_path):
    """pfactory-runner-playwright can run a trivial Node console.log."""
    r = DockerRunner(image="pfactory-runner-playwright:latest")
    res = r.run(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        command=["node", "-e", "console.log('ok')"],
        timeout_sec=60,
    )
    assert res.returncode == 0, res.stderr
    assert "ok" in res.stdout


@pytest.mark.slow
@pytest.mark.skipif(not _docker_available(), reason="docker not available")
@pytest.mark.skipif(
    not _image_available("pfactory-runner-playwright:latest"),
    reason="pfactory-runner-playwright:latest not built locally",
)
def test_playwright_image_has_playwright_on_path(tmp_path):
    """@playwright/test 1.4x must be accessible via npx inside pfactory-runner-playwright."""
    r = DockerRunner(image="pfactory-runner-playwright:latest")
    res = r.run(
        repo_path=tmp_path,
        scratch_path=tmp_path,
        command=["npx", "playwright", "--version"],
        timeout_sec=120,
    )
    assert res.returncode == 0, res.stderr
    version_output = res.stdout + res.stderr
    assert "1.4" in version_output, f"Expected Playwright 1.4x, got: {version_output!r}"
