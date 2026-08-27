# syntax=docker/dockerfile:1.7
# pfactory-runner-pytest — Task 7 (#23), sub-task 7.1
#
# Python 3.13 + pytest 8.x + coverage.py + httpx.
# Renamed / rebuilt from v0.1's docker/runners/python.Dockerfile to match the
# framework registry's runtime.image convention:
#   frameworks/pytest/descriptor.yaml → runtime.image: pfactory-runner-pytest:latest
#
# Security posture:
#   - Non-root user (pfactory, uid 1000)
#   - --network=none at runtime (no egress)
#   - --read-only rootfs at runtime (/scratch bind-mount is the only rw surface)
#   - --tmpfs /tmp so pytest plugins that need a temp dir don't break
#
# Build (from repo root):
#   docker build -f docker/pfactory-runner-pytest/Dockerfile \
#                -t pfactory-runner-pytest:latest \
#                docker/pfactory-runner-pytest
#
# Run (docstring pattern from DockerRunner):
#   docker run --rm \
#       --network=none \
#       --read-only \
#       --tmpfs /tmp:rw,size=64m \
#       -v <repo>:/repo:ro \
#       -v <scratch>:/scratch:rw \
#       --cpus=2 --memory=2g --pids-limit=512 \
#       pfactory-runner-pytest:latest \
#       bash -c "..."

FROM python:3.13-slim AS runner

# Runner images shipped NO security-upgrade layer, so they carried whatever the
# pinned base tag was built with, indefinitely. SECURITY_REFRESH changes daily
# so the layer is not served from the build cache forever -- without it the
# upgrade would run once and freeze (CFactory#440).
#
# `upgrade` only replaces already-installed packages; it never adds new ones,
# so a pinned toolchain image keeps its pinned tools.
ARG SECURITY_REFRESH=0
RUN echo "security refresh: ${SECURITY_REFRESH}" \
    && apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/*


# ── OS-level deps ─────────────────────────────────────────────────────────
# tini: PID 1 with proper SIGTERM forwarding so pytest's process tree is
#       cleanly reaped on timeout / docker stop.
# ca-certificates: needed at image-build time (pip install) even though
#       tests run with --network=none at container runtime.
# git: transitive dep for several pytest plugins and coverage tools.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tini \
        ca-certificates \
        git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps ───────────────────────────────────────────────────────────
# Pinned to known-good minor versions; patch versions float.
# pytest-asyncio: needed for async test support (FastAPI / httpx tests).
# pytest-mock: cleaner mock lifetime management via mocker fixture.
# pytest-timeout: hard per-test timeout so a rogue test can't stall the lane.
# httpx: async HTTP client; needed by many FastAPI integration tests.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir \
    "pytest==8.3.*" \
    "pytest-asyncio==0.24.*" \
    "pytest-cov==5.0.*" \
    "pytest-mock==3.14.*" \
    "pytest-timeout==2.3.*" \
    "httpx==0.28.*" \
    "coverage==7.6.*"

# ── Filesystem layout ─────────────────────────────────────────────────────
# /repo    — bind-mounted ro at runtime (target project code)
# /scratch — bind-mounted rw at runtime (junit.xml, coverage.xml, temp files)
RUN useradd -m -u 1000 pfactory \
    && mkdir -p /repo /scratch \
    && chown pfactory:pfactory /repo /scratch

USER pfactory
WORKDIR /repo

# tini reaps zombies + forwards SIGTERM so docker-stop kills pytest cleanly
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["pytest", "--help"]

LABEL org.opencontainers.image.source="https://github.com/olafkfreund/PFactory"
LABEL org.opencontainers.image.description="PFactory pytest runner — Unit lane (Python)"
LABEL org.pfactory.runner.framework="pytest"
LABEL org.pfactory.runner.language="python"
LABEL org.pfactory.runner.lanes="unit"
