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
