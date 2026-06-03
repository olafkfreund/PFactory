# pfactory-runner-python — Task 4 (#5)
#
# A locked-down image that runs LLM-generated pytest tests against a
# bind-mounted read-only repo. Per the design plan:
#
#   docker run --rm \
#       --network=none \
#       -v <repo>:/work:ro \
#       -v <scratch>:/scratch:rw \
#       --read-only \
#       --cpus=2 --memory=2g --pids-limit=512 \
#       pfactory-runner-python:latest \
#       bash -c "..."
#
# Build (from the repo root):
#   docker build -f docker/runners/python.Dockerfile -t pfactory-runner-python:latest .
#   # or podman build -f docker/runners/python.Dockerfile -t pfactory-runner-python:latest .

FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="pfactory-runner-python" \
      org.opencontainers.image.description="Sandbox image for executing LLM-generated pytest tests in PFactory's functional lane." \
      org.opencontainers.image.source="https://github.com/olafkfreund/PFactory" \
      org.opencontainers.image.licenses="MIT"

# ──────────────────────────────────────────────────────────────────────────
# OS-level deps
# ──────────────────────────────────────────────────────────────────────────
# - tini gives us PID 1 with proper signal forwarding so timeouts cleanly
#   kill the test process tree
# - ca-certificates is needed for any HTTPS the project's test deps reach
#   (we run with --network=none by default, but pip-installs at image-build
#   time still need it)
# - git is a common transitive test dep (vcs-info plugins, etc.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tini \
        ca-certificates \
        git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ──────────────────────────────────────────────────────────────────────────
# Python deps
# ──────────────────────────────────────────────────────────────────────────
# pytest/pytest-cov are required for the functional lane.
# pip-audit is pre-installed so Task 3 (SAST phase 3) doesn't have to
# rebuild the image just to bring it in.
# Pin to known-good majors; minors can roll.
RUN pip install --no-cache-dir \
        "pytest>=8,<9" \
        "pytest-cov>=5,<7" \
        "coverage>=7,<8" \
        "pip-audit>=2,<3"

# ──────────────────────────────────────────────────────────────────────────
# Filesystem layout
# ──────────────────────────────────────────────────────────────────────────
#   /work     — bind-mounted ro, the target project
#   /scratch  — bind-mounted rw, the only writable spot (junit.xml, etc.)
RUN mkdir -p /work /scratch \
    && chmod 0755 /work \
    && chmod 0777 /scratch

# Non-root execution. UID 1000 collides with the typical desktop user
# but rootless podman remaps it via subuid so that's fine; doc'd for ops.
RUN groupadd --system --gid 1000 pfactory \
    && useradd --system --uid 1000 --gid 1000 --shell /usr/sbin/nologin pfactory \
    && chown -R pfactory:pfactory /scratch

USER pfactory
WORKDIR /scratch

# Tini reaps zombies + forwards SIGTERM so docker-stop kills pytest cleanly
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default to dropping into bash so the runner can pass `bash -c "..."`.
# The Python DockerRunner builds the actual command list at invocation time.
CMD ["bash"]
