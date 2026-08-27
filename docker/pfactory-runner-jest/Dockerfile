# syntax=docker/dockerfile:1.7
# pfactory-runner-jest — Task 7 (#23), sub-task 7.2
#
# Node 22 + Jest 29 (LTS series) + ts-jest + @types/jest + nyc (coverage) +
# ESLint 9 + TypeScript 5.6 pre-installed globally.
#
# Pairs with the framework registry entry:
#   frameworks/jest/descriptor.yaml → runtime.image: pfactory-runner-jest:latest
#
# Security posture:
#   - Non-root user (pfactory, uid 1000)
#   - --network=none at runtime (no egress)
#   - --read-only rootfs at runtime (/scratch bind-mount is the only rw surface)
#   - --tmpfs /tmp so Jest's temp files don't hit the ro rootfs
#
# Build (from repo root):
#   docker build -f docker/pfactory-runner-jest/Dockerfile \
#                -t pfactory-runner-jest:latest \
#                docker/pfactory-runner-jest
#
# Run (DockerRunner pattern):
#   docker run --rm \
#       --network=none \
#       --read-only \
#       --tmpfs /tmp:rw,size=64m \
#       -v <repo>:/repo:ro \
#       -v <scratch>:/scratch:rw \
#       --cpus=2 --memory=2g --pids-limit=512 \
#       pfactory-runner-jest:latest \
#       jest --ci --forceExit ...

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
# git: transitive dep for some Jest plugins and test utilities.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=test CI=true

# ── Node.js deps (global) ─────────────────────────────────────────────────
# All pinned to major versions with minor-version float.
# jest@29: LTS series at v0.2 release time; descriptor allows through 31.x.
# ts-jest@29: TypeScript transformer for Jest; must match jest major.
# @types/jest@29: Type definitions; must match jest major.
# nyc@17: Istanbul CLI for coverage instrumentation and LCOV output.
# eslint@9: Flat-config ESLint (Decision 11: flake-lint analog for TypeScript).
# typescript@5.6: Compiler; aligns with ts-jest requirements.
# @typescript-eslint/parser@8 + @typescript-eslint/eslint-plugin@8:
#   TypeScript-aware linting rules for the Gen-Functional guardrail.
RUN npm install -g \
    jest@29 \
    @types/jest@29 \
    ts-jest@29 \
    nyc@17 \
    eslint@9 \
    typescript@5.6 \
    @typescript-eslint/parser@8 \
    @typescript-eslint/eslint-plugin@8 \
    && npm cache clean --force

# ── Filesystem layout ─────────────────────────────────────────────────────
# /repo    — bind-mounted ro at runtime (target project code)
# /scratch — bind-mounted rw at runtime (jest-junit output, lcov reports)
# node:22-slim already ships a "node" user at UID 1000, so create pfactory at
# 1002 (guarded) rather than colliding with it.
RUN (id pfactory >/dev/null 2>&1 || useradd -m -u 1002 pfactory) \
    && mkdir -p /repo /scratch \
    && chown pfactory:pfactory /repo /scratch

USER pfactory
WORKDIR /repo

CMD ["jest", "--help"]

LABEL org.opencontainers.image.source="https://github.com/olafkfreund/PFactory"
LABEL org.opencontainers.image.description="PFactory Jest runner — Unit lane (TypeScript)"
LABEL org.pfactory.runner.framework="jest"
LABEL org.pfactory.runner.language="typescript"
LABEL org.pfactory.runner.lanes="unit"
