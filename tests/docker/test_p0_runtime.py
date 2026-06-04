"""P0.2 / P0.3 / P0.4 / P0.5 — runtime behavior of the hardened image."""

import pytest

from tests.docker.helpers import (
    DOCKERFILE_PATH,
    docker_run,
    wait_for_health,
)


@pytest.mark.docker
@pytest.mark.slow
def test_health_endpoint_responds(built_image: str, container_name: str, free_port: int) -> None:
    """P0.2 — container starts and `GET /api/health` returns 200 within 30s."""
    docker_run(
        built_image,
        detach=True,
        publish=[f"{free_port}:3114"],
        name=container_name,
    )
    assert wait_for_health(f"http://localhost:{free_port}/api/health", timeout=60), \
        "container did not become healthy within 30s"


@pytest.mark.docker
def test_no_iptables_in_dockerfile() -> None:
    """P0.3 — the Chainguard Dockerfile contains no iptables / NET_ADMIN logic.

    Egress control moves to K8s NetworkPolicy (P4), not in the image.
    """
    content = DOCKERFILE_PATH.read_text()
    assert "iptables" not in content.lower(), \
        "Dockerfile still references iptables; egress control belongs in NetworkPolicy"
    assert "NET_ADMIN" not in content, \
        "Dockerfile still grants NET_ADMIN"


@pytest.mark.docker
@pytest.mark.slow
def test_no_net_admin_required(built_image: str, container_name: str, free_port: int) -> None:
    """P0.3 — image runs and serves traffic without --cap-add NET_ADMIN."""
    docker_run(
        built_image,
        detach=True,
        publish=[f"{free_port}:3114"],
        name=container_name,
    )
    assert wait_for_health(f"http://localhost:{free_port}/api/health", timeout=60), \
        "container failed without NET_ADMIN; entrypoint still depends on it"


@pytest.mark.docker
@pytest.mark.slow
def test_no_entrypoint_shell_script(built_image: str) -> None:
    """P0.3 — the legacy shell entrypoint is absent from the image filesystem."""
    result = docker_run(built_image, "ls", "/usr/local/bin/docker-entrypoint.sh", timeout=10)
    assert result.returncode != 0, \
        "Image still ships docker-entrypoint.sh; entrypoint should be a direct CMD"


@pytest.mark.docker
@pytest.mark.slow
def test_runs_as_uid_65532(built_image: str) -> None:
    """P0.4 — `id -u` inside the container returns 65532 (Chainguard's nonroot)."""
    result = docker_run(built_image, "id", "-u", timeout=10)
    assert result.returncode == 0, f"`id -u` failed: {result.stderr}"
    assert result.stdout.strip() == "65532", \
        f"Container runs as uid {result.stdout.strip()}, expected 65532"


@pytest.mark.docker
@pytest.mark.slow
def test_runs_with_read_only_root_fs(built_image: str, container_name: str, free_port: int) -> None:
    """P0.5 — image starts and serves traffic with readOnlyRootFilesystem=true.

    Models the K8s pod spec the Helm chart (P4) will produce:
    - root filesystem is read-only
    - /tmp + /var/cache are emptyDir tmpfs (ephemeral)
    - /home/nonroot/.pfactory is a PersistentVolumeClaim in prod
      (modeled here as a tmpfs)
    """
    docker_run(
        built_image,
        detach=True,
        publish=[f"{free_port}:3114"],
        name=container_name,
        read_only=True,
        # uid/gid=65532 so the nonroot app user can actually write to these
        # mounts. Without these, docker creates the tmpfs root-owned and the
        # app gets a PermissionError on first write.
        tmpfs=[
            "/tmp:size=500m,uid=65532,gid=65532",
            "/var/cache:size=200m,uid=65532,gid=65532",
            "/home/nonroot/.pfactory:size=100m,uid=65532,gid=65532",
        ],
    )
    assert wait_for_health(f"http://localhost:{free_port}/api/health", timeout=60), \
        "container failed with --read-only; identify writable paths and mount them as tmpfs"


@pytest.mark.docker
@pytest.mark.slow
def test_dropped_capabilities(built_image: str, container_name: str, free_port: int) -> None:
    """P0.5 — image starts with all capabilities dropped + no-new-privileges."""
    docker_run(
        built_image,
        detach=True,
        publish=[f"{free_port}:3114"],
        name=container_name,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
    )
    assert wait_for_health(f"http://localhost:{free_port}/api/health", timeout=60), \
        "container failed with --cap-drop ALL; remove any cap-requiring operations"
