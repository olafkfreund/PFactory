# syntax=docker/dockerfile:1.7
# pfactory-runner-cypress — #110
#
# Official Cypress base (cypress/included) bundles Cypress + Electron/Chromium
# + every OS-level browser dependency, so no multi-GB install step needs
# network access inside the CI runner. Mirrors pfactory-runner-playwright
# (Browser lane, TypeScript).
#
# Pairs with the framework registry entry:
#   frameworks/cypress/descriptor.yaml → runtime.image: pfactory-runner-cypress:latest
#
# Why cypress/included base?
#   - Ships Cypress + the bundled browser + all system deps pre-satisfied.
#   - ENTRYPOINT defaults to ["cypress", "run"]; we reset it to [] so the
#     DockerRunner can invoke arbitrary commands (mirrors the other runners).
#
# Security posture:
#   - Non-root user (pfactory, uid 1002). The pre-installed Cypress binary
#     cache is relocated to a world-readable CYPRESS_CACHE_FOLDER so the
#     non-root user can find it.
#   - --network=none at runtime is the DEFAULT; browser tests that exercise a
#     served app use --network=bridge (DockerRunner flips this).
#   - --read-only rootfs at runtime (/scratch bind-mount is the only rw surface).
#   - --tmpfs /tmp for Cypress's temp files.
#
# Build (from repo root):
#   docker build -f docker/pfactory-runner-cypress/Dockerfile \
#                -t pfactory-runner-cypress:latest \
#                docker/pfactory-runner-cypress
#
# Run (DockerRunner pattern; browser tests need --network=bridge):
#   docker run --rm \
#       --network=bridge \
#       --read-only \
#       --tmpfs /tmp:rw,size=256m \
#       -v <repo>:/repo:ro \
#       -v <scratch>:/scratch:rw \
#       --cpus=2 --memory=4g --pids-limit=512 \
#       pfactory-runner-cypress:latest \
#       cypress run ...

FROM cypress/included:13.15.0 AS runner

# This image shipped NO security-upgrade layer, so it carried whatever the
# pinned base tag was built with, indefinitely. The base IS Debian/Ubuntu --
# it simply never called apt -- so an upgrade is both possible and needed.
# SECURITY_REFRESH changes daily so the layer is not served from the build
# cache forever (CFactory#440).
#
# `upgrade` only replaces already-installed packages; it never adds new ones,
# so the vendored browsers and their pinned versions are untouched.
ARG SECURITY_REFRESH=0
# The cypress base ships an UNSIGNED Google Chrome apt source, so a plain
# `apt-get update` fails outright:
#   E: The repository 'https://dl.google.com/linux/chrome/deb stable InRelease'
#      is not signed.
# Those third-party lists are dropped first. Nothing later in this image
# installs from them, the browser is already baked in and deliberately pinned
# (upgrading it is exactly what we do NOT want), and an unsigned repo is a
# supply-chain risk in its own right. The base distro lists are untouched, so
# the security upgrade still does its job.
RUN echo "security refresh: ${SECURITY_REFRESH}" \
    && rm -f /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /var/lib/apt/lists/*


ENV NODE_ENV=test CI=true

# Reset the base ENTRYPOINT (["cypress","run"]) so the DockerRunner controls
# the full command, like the other runner images.
ENTRYPOINT []

# ── Node.js deps (global) ─────────────────────────────────────────────────
# typescript@5.6: compiler for .cy.ts spec files. Cypress + browser come from
# the base image.
RUN npm install -g typescript@5.6 && npm cache clean --force

# ── Non-root + binary cache ───────────────────────────────────────────────
# cypress/included installs the binary under /root/.cache/Cypress. Relocate it
# to a world-readable folder and point CYPRESS_CACHE_FOLDER there so the
# non-root pfactory user can run Cypress.
ENV CYPRESS_CACHE_FOLDER=/opt/cypress-cache
RUN mkdir -p /opt/cypress-cache \
    && cp -a /root/.cache/Cypress/. /opt/cypress-cache/ \
    && chmod -R a+rX /opt/cypress-cache

RUN (id pfactory >/dev/null 2>&1 || useradd -m -u 1002 pfactory) \
    && mkdir -p /repo /scratch \
    && chown pfactory:pfactory /repo /scratch

USER pfactory
WORKDIR /repo

CMD ["cypress", "--version"]

LABEL org.opencontainers.image.source="https://github.com/olafkfreund/PFactory"
LABEL org.opencontainers.image.description="PFactory Cypress runner — Browser lane (TypeScript)"
LABEL org.pfactory.runner.framework="cypress"
LABEL org.pfactory.runner.language="typescript"
LABEL org.pfactory.runner.lanes="browser"
