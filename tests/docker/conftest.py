"""Pytest fixtures for P0 docker acceptance tests.

Tests are marked `@pytest.mark.docker` and `@pytest.mark.slow`. Default
CI (`-m "not slow"`) excludes them. The new `docker-acceptance` CI job
opts in with `-m docker`.

The `built_image` session fixture builds the Chainguard Dockerfile once
per test session and yields the image tag. Tests skip cleanly while
P0.1 is pending (Dockerfile.chainguard doesn't exist yet).
"""

from __future__ import annotations

import pytest

from tests.docker.helpers import (
    DOCKERFILE_PATH,
    docker_available,
    docker_build,
    docker_kill,
)

IMAGE_TAG = "pfactory:p0-test"


@pytest.fixture(scope="session")
def built_image() -> str:
    """Build the P0 image once per session; return its tag.

    Skips when Docker isn't available. Dockerfile is expected to exist
    post-P0.1; if it doesn't, that's a real failure of the codebase, not
    a skip condition.
    """
    if not docker_available():
        pytest.skip("Docker not available on this host")
    if not DOCKERFILE_PATH.exists():
        pytest.fail(f"{DOCKERFILE_PATH} not found")

    result = docker_build(DOCKERFILE_PATH, IMAGE_TAG)
    if result.returncode != 0:
        pytest.fail(
            f"docker build failed (exit {result.returncode}):\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return IMAGE_TAG


@pytest.fixture
def container_name(request: pytest.FixtureRequest):
    """Per-test container name; cleaned up after the test."""
    name = f"pfactory-p0-test-{request.node.name}"
    yield name
    docker_kill(name)


@pytest.fixture
def free_port() -> int:
    """Pick an unused TCP port on the host.

    Avoids collisions with a developer's locally-running PFactory backend
    (port 3114). Tests that publish the container's 3114 to a host port
    MUST use this fixture and poll that host port — never hardcode 3114.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]
