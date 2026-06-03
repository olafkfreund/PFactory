# syntax=docker/dockerfile:1.7
# pfactory-runner-playwright — Task 7 (#23), sub-task 7.3
#
# Official Microsoft Playwright base (noble/Ubuntu 24.04) + @playwright/test 1.49 +
# TypeScript 5.6. Chromium is the baseline browser; Firefox and WebKit are
# present in the base image and selectable via PLAYWRIGHT_BROWSER env var.
#
# Pairs with the framework registry entry:
#   frameworks/playwright/descriptor.yaml → runtime.image: pfactory-runner-playwright:latest
#
# Why mcr.microsoft.com/playwright base?
#   - Ships with Chromium, Firefox, and WebKit pre-installed + all OS-level
#     browser dependencies (fonts, libglib, libnspr, etc.) pre-satisfied.
#   - Avoids the multi-GB 'npx playwright install --with-deps' step that would
#     otherwise require network access inside the CI runner.
#   - Based on Ubuntu 24.04 (noble) — LTS, aligns with our security baseline.
#
# Security posture:
#   - Non-root user (pfactory, uid 1000).
#   - --network=none at runtime is the DEFAULT; Browser lane tests that
#     exercise the app must use --network=bridge and set APP_URL.
#     DockerRunner flips this via the network= parameter; no change needed here.
#   - --read-only rootfs at runtime (/scratch bind-mount is the only rw surface).
#   - --tmpfs /tmp for Playwright's temp traces and screenshot files.
#
# Build (from repo root):
#   docker build -f docker/pfactory-runner-playwright/Dockerfile \
#                -t pfactory-runner-playwright:latest \
#                docker/pfactory-runner-playwright
#
# Run (DockerRunner pattern; note: browser tests need --network=bridge):
#   docker run --rm \
#       --network=bridge \
#       --read-only \
#       --tmpfs /tmp:rw,size=256m \
#       -v <repo>:/repo:ro \
#       -v <scratch>:/scratch:rw \
#       --cpus=2 --memory=4g --pids-limit=512 \
#       -e PLAYWRIGHT_BROWSER=chromium \
#       pfactory-runner-playwright:latest \
#       npx playwright test ...

# Official Playwright base bundles Chromium, Firefox, WebKit + all system deps.
# Pin to the same minor as @playwright/test below (1.49) for exact parity.
FROM mcr.microsoft.com/playwright:v1.49.0-noble AS runner

# ── Node.js deps (global) ─────────────────────────────────────────────────
# @playwright/test@1.49: test runner + fixtures API; must match base image version.
# typescript@5.6: TypeScript compiler for .spec.ts files.
# Note: @playwright/test already bundles the playwright package; no need to
# install it separately.
ENV NODE_ENV=test CI=true

RUN npm install -g \
    @playwright/test@1.49 \
    typescript@5.6 \
    && npm cache clean --force

# ── Filesystem layout ─────────────────────────────────────────────────────
# /repo    — bind-mounted ro at runtime (target project code)
# /scratch — bind-mounted rw at runtime (screenshots, videos, traces, junit.xml)
# The || true guard handles the case where 'playwright' user already exists
# in the base image (some versions do, some don't).
RUN (id pfactory >/dev/null 2>&1 || useradd -m -u 1002 pfactory) \
    && mkdir -p /repo /scratch \
    && chown pfactory:pfactory /repo /scratch

# Playwright base image already has chromium; expose env so DockerRunner
# (or the test suite) can flip to firefox/webkit by setting PLAYWRIGHT_BROWSER.
# Task 8 (app runtime + health-poll) will set this per test plan lane config.
ENV PLAYWRIGHT_BROWSER=chromium

USER pfactory
WORKDIR /repo

CMD ["npx", "playwright", "--help"]

LABEL org.opencontainers.image.source="https://github.com/olafkfreund/PFactory"
LABEL org.opencontainers.image.description="PFactory Playwright runner — Browser lane (TypeScript)"
LABEL org.pfactory.runner.framework="playwright"
LABEL org.pfactory.runner.language="typescript"
LABEL org.pfactory.runner.lanes="browser"
