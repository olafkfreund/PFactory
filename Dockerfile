# =============================================================================
# PFactory — Chainguard distroless build
# =============================================================================
# Epic #26 (issue #27): port from the legacy Ubuntu Dockerfile to a
# Chainguard base. Each P0 chunk turns one or more tests in tests/docker/
# from skipped → passing.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Build the React frontend
# ---------------------------------------------------------------------------
# Digest is the OCI image-index (manifest-list) sha256 so multi-arch buildx
# (P0.6) can still resolve the right platform manifest. The version tag is
# kept alongside the digest as a human hint and is ignored by docker when a
# digest is present.
#
# Why not cgr.dev/chainguard/node (AIFactory#1091): this stage was pinned to
# `node:latest-dev@sha256:...`, which cannot work. Chainguard rebuilds
# `latest-*` continuously and garbage-collects superseded digests, so the pin
# 404s within days and `load metadata` fails before the build starts. This
# repo carried the same dead digest as AIFactory. The usual answer -- pin a
# versioned tag by digest -- is not available: the public Chainguard catalog
# publishes only `latest`, `latest-dev`, `latest-slim`, `next` and `next-dev`
# for node; `node:24-dev` and `node:24` both 404, because versioned tags are a
# paid Production-tier feature under a customer-specific org path. Docker
# Official Images do retain superseded digests for versioned tags
# (node:24.0.0-bookworm-slim from May 2025 still resolves), so a pin here
# survives while staying reproducible.
#
# node >= 24.0.0 and npm >= 10 are required by the root package.json
# `engines`. Keep a glibc variant: an Alpine/musl image would need the
# `-musl` Rollup native binaries rather than `-gnu`.
# Nothing from this stage ships -- only apps/web-server/static is copied into
# the runtime stage -- so the base's CVE posture is not part of the attack
# surface. The runtime stage stays on Chainguard, where it does matter.
# Digest bumps land via Dependabot PRs (.github/dependabot.yml).
FROM docker.io/node:26-bookworm-slim@sha256:cd565714d4da3e84bfd341e31448f81d47c6362198f152345297c9c1154e6341 AS frontend-build

USER root
WORKDIR /build

# Workspace-aware install via root package + the frontend's package.json
COPY package.json package-lock.json ./
COPY apps/frontend-web/package.json apps/frontend-web/

RUN npm ci --workspace=apps/frontend-web

COPY apps/frontend-web/ apps/frontend-web/

# vite.config.ts: build.outDir = '../web-server/static'
RUN mkdir -p apps/web-server/static \
 && cd apps/frontend-web \
 && npm run build

# ---------------------------------------------------------------------------
# Stage 2: Runtime (Chainguard Python, dev variant for now — minimal split
# happens in P0.5 once we know what the runtime *actually* needs)
# ---------------------------------------------------------------------------
FROM cgr.dev/chainguard/python:latest-dev@sha256:534fb1a1b9ad4d9d149ab669ca4218be76c84990e2f3379c7f703d224647666b AS runtime

USER root

# Pull all available Wolfi security patches at build time. The base is pinned by
# digest for reproducibility, but a pinned digest lags behind freshly-disclosed
# CVEs. `apk upgrade` clears fixable HIGH/CRITICAL findings between digest bumps;
# when the snapshot itself lags, bump the digest above to a Chainguard rebuild
# that ships the fix (what cleared CVE-2026-45447, libcrypto3/libssl3 → 3.6.3-r1).
RUN apk upgrade --no-cache

# System packages from Wolfi APK index. Build tools come bundled in :latest-dev.
#   git           — worktree operations
#   curl, wget    — downloads (HEALTHCHECK uses curl)
#   gh            — GitHub CLI (Wolfi apk package name)
#   nodejs, npm   — runtime Node for `npm install -g @anthropic-ai/claude-code`
#                   spawned by the agent. Installed via apk instead of
#                   binary-copying from the frontend stage so dynamic linker
#                   deps (libuv etc.) resolve correctly.
#   ca-certificates — TLS roots
#   bash          — entrypoint script (will be removed in P0.3)
#   binutils      — the :latest-dev base bundles binutils 2.46-r1, which carries
#                   CVE-2026-6846 (HIGH, heap overflow in XCOFF linking; fixed in
#                   2.46-r2). Force a build-newer rev to clear the P0 Trivy gate
#                   (test_trivy_no_high_critical). Constraint, not =2.46-r2, so
#                   the build stays green when Wolfi revs the package further;
#                   drop this once the base digest ships the fix.
#   bubblewrap    — OS-level bash sandbox for agent commands. Without it the
#                   Claude Agent SDK logs "Sandbox disabled: ... bubblewrap
#                   (bwrap) not installed" and runs commands with NO filesystem
#                   /network enforcement — unacceptable for enterprise use. The
#                   cluster node allows unprivileged user namespaces, so bwrap
#                   can create the sandbox.
#   socat         — required alongside bwrap by the SDK sandbox network-proxy
#                   path; its absence triggers the same warning.
# Two version floors below are CVE remediation, not preference. `apk upgrade`
# earlier in this stage does not clear either, because both packages come from
# the base image layer and must be named explicitly to be pulled forward:
#   busybox 1.37.0-r61 — CVE-2026-38753 (DoS via crafted AWK in awk_sub) and
#                        CVE-2026-38754 (heap overflow in ifsbreakup, shell/ash.c),
#                        both HIGH, both fixed in 1.38.0-r0.
#   wget    1.25.0-r14 — CVE-2026-58471 and CVE-2026-58472, both HIGH, both
#                        fixed in 1.25.0-r15.
# Floors rather than == pins, so the build stays green as Wolfi revs further
# (1.38.0-r1 and later already exist); drop each once the base digest ships it.
# Verified present in the Wolfi x86_64 APKINDEX before pinning — a floor above
# the newest available version fails the build outright.
RUN apk add --no-cache \
        bash \
        "binutils>2.46-r1" \
        "busybox>=1.38.0-r0" \
        bubblewrap \
        ca-certificates \
        curl \
        git \
        gh \
        gnupg \
        nodejs \
        npm \
        socat \
        "wget>=1.25.0-r15"

# Epic #44 R3 — optionally bundle the rmux binary.
#
# Build args:
#   WITH_RMUX=false   (default — bank-pilot image; no rmux binary at all)
#   WITH_RMUX=true    (dev/demo image; pins rmux v0.3.1 by SHA-256)
#
# CI matrix builds both: ``pfactory:vX`` (default) and ``pfactory:vX-rmux``.
# Bank-pilot image's Trivy report + Syft SBOM contain no rmux components.
#
# Arch support: only ``x86_64-unknown-linux-gnu`` is available upstream as
# of v0.3.1 (no aarch64 Linux build yet — tracked in Helvesec/rmux roadmap).
# ARM64 builds fail-fast with a clear message rather than silently install
# the wrong binary.
ARG WITH_RMUX=false
ARG RMUX_VERSION=0.3.1
# SHA-256 of rmux-v0.3.1-x86_64-unknown-linux-gnu.tar.gz from upstream
# SHA256SUMS file.  Bump together with RMUX_VERSION on upgrades.
ARG RMUX_SHA256_AMD64=511d3caceea4fcbc1458877a192efffcde5ceb1455f040f1a79c63ab00804cf8
RUN if [ "$WITH_RMUX" = "true" ]; then \
      arch="$(uname -m)"; \
      case "$arch" in \
        x86_64) \
          target="x86_64-unknown-linux-gnu"; \
          sha="${RMUX_SHA256_AMD64}" \
          ;; \
        *) \
          echo "WITH_RMUX=true: unsupported arch '$arch' (rmux v${RMUX_VERSION} ships x86_64 Linux only)" >&2; \
          exit 1 \
          ;; \
      esac; \
      curl -fsSL "https://github.com/Helvesec/rmux/releases/download/v${RMUX_VERSION}/rmux-v${RMUX_VERSION}-${target}.tar.gz" \
           -o /tmp/rmux.tar.gz; \
      echo "${sha}  /tmp/rmux.tar.gz" | sha256sum -c -; \
      mkdir -p /tmp/rmux-extract; \
      tar -xzf /tmp/rmux.tar.gz -C /tmp/rmux-extract; \
      find /tmp/rmux-extract -name rmux -type f -executable -exec install -m 0755 {} /usr/local/bin/rmux \; ; \
      rm -rf /tmp/rmux.tar.gz /tmp/rmux-extract; \
      /usr/local/bin/rmux -V; \
    else \
      echo "rmux integration not bundled (WITH_RMUX=false — bank-pilot image)"; \
    fi

# Project layout (keeping the legacy path under /home/projects for minimum
# diff with the existing Dockerfile; P0.4 may relocate to /app under nonroot)
RUN mkdir -p /home/projects/MagesticAI \
 && chown -R nonroot:nonroot /home/projects

# Copy project sources (respects .dockerignore)
COPY --chown=nonroot:nonroot . /home/projects/MagesticAI/

# Copy built frontend assets from Stage 1
COPY --from=frontend-build --chown=nonroot:nonroot \
    /build/apps/web-server/static/ \
    /home/projects/MagesticAI/apps/web-server/static/

# Drop to nonroot for venv + npm config (writeable paths only)
USER nonroot

# Configure npm global install dir under the nonroot home
RUN mkdir -p /home/nonroot/.npm-global \
 && npm config set prefix /home/nonroot/.npm-global

# Bake the provider coder CLIs into the image so the control-plane boot never
# npm-installs them (mirrors TFactory #791: the install-clis init container hung
# 8+ min on a slow registry and stalled the rollout). .npm-global/bin is already
# on PATH.
#
# Versions are pinned here and watched by the hub `agent-CLI freshness` job
# (Factory/scripts/check_cli_freshness.py --open-bump-pr), which proposes bumps
# as `chore/agent-cli-pins` across all three service repos at once and never
# merges them, plus factory-gitops/.github/workflows/cli-canary.yml, which
# asserts every repo pins all three CLIs identically and that each pin installs
# and launches.
#
# NOT Dependabot, whatever an earlier version of this comment claimed
# (factory-gitops#206). Dependabot's Dockerfile parser reads `FROM` lines only —
# no package-ecosystem parses shell arguments inside a RUN layer, so it cannot
# see the `@version` on the npm install below and never could. It does cover the
# `FROM` lines in this file, and nothing else in it.
#
# `install.cjs` is NOT redundant with the npm postinstall (Factory#383). The
# postinstall downloads the 275 MB platform-native binary correctly, but leaves
# `bin/claude.exe` as the 11-line stub that ships in the package — a script whose
# whole body prints "native binary not installed" and exits 1. So `claude` on
# PATH was broken in every control-plane pod while the binary underneath it ran
# fine, and the stub's own error blames --ignore-scripts / --omit=optional,
# neither of which is used here. Re-running install.cjs completes the swap.
#
# `claude --version` is the point of the fix, not decoration: this shipped broken
# because nothing asserted the CLI works. Full path, since PATH is set for the
# runtime user rather than for RUN.
RUN npm install -g \
        @anthropic-ai/claude-code@2.1.224 \
        @openai/codex@0.147.0 \
        @google/gemini-cli@0.54.4 \
 && node /home/nonroot/.npm-global/lib/node_modules/@anthropic-ai/claude-code/install.cjs \
 && /home/nonroot/.npm-global/bin/claude --version \
 && npm cache clean --force \
 && ln -sf /home/nonroot/.npm-global/bin/gemini /home/nonroot/.npm-global/bin/antigravity

# Single Python venv shared by web-server and backend scripts (matches
# agent_service.py's sys.executable expectations)
RUN python3 -m venv /home/projects/MagesticAI/.venv

RUN /home/projects/MagesticAI/.venv/bin/pip install --no-cache-dir \
        -r /home/projects/MagesticAI/apps/web-server/requirements.txt \
        -r /home/projects/MagesticAI/apps/backend/requirements.txt

# Git identity for in-container worktree operations
RUN git config --global user.name "PFactory" \
 && git config --global user.email "pfactory@container"

# Persistent data directory
RUN mkdir -p /home/nonroot/.pfactory

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
ENV APP_HOST=0.0.0.0 \
    APP_PORT=3114 \
    APP_BACKEND_PATH=/home/projects/MagesticAI/apps/backend \
    APP_PROJECTS_DATA_DIR=/home/nonroot/.pfactory \
    APP_DEFAULT_SHELL=/bin/bash \
    PYTHONUNBUFFERED=1 \
    PATH="/home/nonroot/.npm-global/bin:/home/projects/MagesticAI/.venv/bin:$PATH"

EXPOSE 3114

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:3114/api/health || exit 1

# `apps/backend/client_errors.py` is imported as a top-level module by 14
# web-server modules (and 10 backend ones). WORKDIR below is apps/web-server,
# so without this the interpreter cannot see it and `server.main` dies at
# import with ModuleNotFoundError. See #586.
ENV PYTHONPATH=/home/projects/MagesticAI/apps/backend

WORKDIR /home/projects/MagesticAI/apps/web-server

# Direct CMD — no shell wrapper. Egress control belongs in K8s NetworkPolicy
# (P4 of Epic #26), not in an entrypoint script. Runs as `nonroot` (uid 65532)
# from this point onwards.
#
# Explicitly clear the entrypoint inherited from cgr.dev/chainguard/python
# (which is `/usr/bin/python`) so `docker run image <cmd>` works portably.
# Absolute path to the venv python so we never depend on PATH ordering.
ENTRYPOINT []
CMD ["/home/projects/MagesticAI/.venv/bin/python", "-m", "server.main"]
