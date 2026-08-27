# syntax=docker/dockerfile:1.7
# pfactory-runner-vitest — #110
#
# Node 22 + Vitest 3 (LTS-ish) + @vitest/coverage-v8 + jsdom +
# @testing-library/react|dom + TypeScript 5.6, pre-installed globally.
# Mirrors pfactory-runner-jest (Unit lane, TypeScript) — Vitest is a near
# drop-in for Jest with the `vi` namespace.
#
# Pairs with the framework registry entry:
#   frameworks/vitest/descriptor.yaml → runtime.image: pfactory-runner-vitest:latest
#
# Security posture:
#   - Non-root user (pfactory, uid 1002)
#   - --network=none at runtime (no egress)
#   - --read-only rootfs at runtime (/scratch bind-mount is the only rw surface)
#   - --tmpfs /tmp so Vitest's temp files don't hit the ro rootfs
#
# Build (from repo root):
#   docker build -f docker/pfactory-runner-vitest/Dockerfile \
#                -t pfactory-runner-vitest:latest \
#                docker/pfactory-runner-vitest
#
# Run (DockerRunner pattern):
#   docker run --rm \
#       --network=none \
#       --read-only \
#       --tmpfs /tmp:rw,size=64m \
#       -v <repo>:/repo:ro \
#       -v <scratch>:/scratch:rw \
#       --cpus=2 --memory=2g --pids-limit=512 \
#       pfactory-runner-vitest:latest \
#       npx vitest run ...

FROM node:22-slim AS runner

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
# ca-certificates: needed at image-build time (npm install) even though
#       tests run with --network=none at container runtime.
# git: transitive dep for some test utilities.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=test CI=true

# ── Node.js deps (global) ─────────────────────────────────────────────────
# vitest@3: current stable major (descriptor allows >=1.0,<5.0).
# @vitest/coverage-v8@3: LCOV coverage provider; must match vitest major.
# jsdom@25: DOM environment for component tests.
# @testing-library/react@16 + @testing-library/dom@10: React component testing.
# typescript@5.6: compiler for .ts/.tsx test files.
RUN npm install -g \
    vitest@3 \
    @vitest/coverage-v8@3 \
    jsdom@25 \
    @testing-library/react@16 \
    @testing-library/dom@10 \
    typescript@5.6 \
    && npm cache clean --force

# ── Filesystem layout ─────────────────────────────────────────────────────
# /repo    — bind-mounted ro at runtime (target project code)
# /scratch — bind-mounted rw at runtime (junit output, lcov reports)
# node:22-slim already ships a "node" user at UID 1000, so create pfactory at
# 1002 (guarded) rather than colliding with it.
RUN (id pfactory >/dev/null 2>&1 || useradd -m -u 1002 pfactory) \
    && mkdir -p /repo /scratch \
    && chown pfactory:pfactory /repo /scratch

USER pfactory
WORKDIR /repo

CMD ["vitest", "--version"]

LABEL org.opencontainers.image.source="https://github.com/olafkfreund/PFactory"
LABEL org.opencontainers.image.description="PFactory Vitest runner — Unit lane (TypeScript)"
LABEL org.pfactory.runner.framework="vitest"
LABEL org.pfactory.runner.language="typescript"
LABEL org.pfactory.runner.lanes="unit"
