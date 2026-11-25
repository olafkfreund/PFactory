# PFactory runtime base: install Wolfi packages before building the application.
FROM cgr.dev/chainguard/python:latest-dev@sha256:768ee36ee9f4b8c882634c07d0b889425bc8e100886a3f742b83b1c647cc2d4b AS runtime
LABEL org.opencontainers.image.source="https://github.com/olafkfreund/PFactory"

USER root

# System packages from Wolfi APK index. Build tools come bundled in :latest-dev.
#   git           — worktree operations
#   curl, wget    — downloads (HEALTHCHECK uses curl)
#   gh            — GitHub CLI (Wolfi apk package name)
#   (Node is NOT from apk: the main Dockerfile copies it from node-runtime.)
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
# Two version floors below are CVE remediation, not preference. Both packages
# come from the base image layer, which pins them in /etc/apk/world; only naming
# them here moves them (see the SECURITY_REFRESH note below):
#   libssl3 / libcrypto3 3.6.3-r3 — CVE-2026-14456 (HIGH), fixed in 3.6.3-r5.
#                        Named together because they ship from the same openssl
#                        origin and apk will not move one without the other.
#   busybox 1.37.0-r61 — CVE-2026-38753 (DoS via crafted AWK in awk_sub) and
#                        CVE-2026-38754 (heap overflow in ifsbreakup, shell/ash.c),
#                        both HIGH, both fixed in 1.38.0-r0.
#   wget    1.25.0-r14 — CVE-2026-58471 and CVE-2026-58472, both HIGH, both
#                        fixed in 1.25.0-r15.
#   curl / libcurl-openssl4 8.22.0-r4 — CVE-2026-92392 (HIGH, HTTP/2 upload
#                        use-after-free), fixed in 8.23.0-r0. Named together:
#                        same origin, and the base pins libcurl-openssl4.
#   ca-certificates-bundle 20260909-r2 — CVE-2026-111943 (HIGH, distrusted
#                        intermediate CA), fixed in 20260909-r3.
#   uv 0.12.22-r0 — CVE-2026-111943 (HIGH, same Go x509 denylist), fixed in
#                        0.13.6-r1. Agents launch MCP servers with uvx.
#   libbrotlicommon1 / libbrotlidec1 / libbrotlienc1 1.2.0-r5 — CVE-2026-111497
#                        (HIGH, decoder out-of-bounds write), fixed in 1.2.0-r7.
#                        Named together: same brotli origin.
#   py3-pip-wheel 26.2.1-r2 — CVE-2026-114613 (HIGH), msgpack map-deserialization
#                        leak in pip's vendored msgpack 1.2.1; fixed in 26.2.2-r1
#                        (vendors 1.2.3). python-3.14 depends on it, so it cannot
#                        be apk-deleted with the other pip packages below.
#   libexpat1 2.8.5-r0 — CVE-2026-113351 and CVE-2026-115506, both HIGH, both
#                        fixed in 2.10.0-r0.
#   python-3.14 (+ -base, -base-dev, -dev) 3.14.8_git20261001-r1 — CVE-2026-119170,
#                        HIGH, fixed in 3.14.10_git20261111-r0.
# Floors rather than == pins, so the build stays green as Wolfi revs further
# (1.38.0-r1 and later already exist); drop each once the base digest ships it.
# Verified present in the Wolfi x86_64 APKINDEX before pinning — a floor above
# the newest available version fails the build outright. (brotli 1.2.0-r7:
# verified present 2026-10-24.)
# How base-layer security fixes reach the runtime image (#733). The chainguard base
# pins every installed package exactly in /etc/apk/world, so `apk upgrade`
# cannot move any of them -- it was measured to upgrade nothing and has been
# removed. Fixes arrive three ways:
#   1. base digest bumps (Dependabot; green ones auto-merge, #735);
#   2. the explicit floors below, which replace a package's base pin;
#   3. this layer re-running weekly: SECURITY_REFRESH busts its cache, so the
#      packages it installs (gh, curl, gnupg, ... have no base pin) and the
#      floors resolve to the newest index versions, not to the day the layer
#      was first cached. The base workflow passes the date.
ARG SECURITY_REFRESH=0
RUN echo "security refresh: ${SECURITY_REFRESH}" \
    && apk add --no-cache \
        bash \
        "binutils>2.46-r1" \
        "busybox>=1.38.0-r0" \
        "libcrypto3>=3.6.3-r5" \
        "libssl3>=3.6.3-r5" \
        bubblewrap \
        ca-certificates \
        "ca-certificates-bundle>=20260909-r3" \
        "uv>=0.13.6-r1" \
        "curl>=8.23.0-r0" \
        "libcurl-openssl4>=8.23.0-r0" \
        "libbrotlicommon1>=1.2.0-r7" \
        "libbrotlidec1>=1.2.0-r7" \
        "libbrotlienc1>=1.2.0-r7" \
        "py3-pip-wheel>=26.2.2-r1" \
        "libexpat1>=2.10.0-r0" \
        "python-3.14>=3.14.10_git20261111-r0" \
        "python-3.14-base>=3.14.10_git20261111-r0" \
        "python-3.14-base-dev>=3.14.10_git20261111-r0" \
        "python-3.14-dev>=3.14.10_git20261111-r0" \
        git \
        gh \
        gnupg \
        socat \
        "wget>=1.25.0-r15"

# Runtime Node must come from the main Dockerfile's node-runtime stage (#1710).
RUN if apk info 2>/dev/null | grep -q '^nodejs'; then \
      echo "apk nodejs is installed; runtime Node must come from node-runtime (Factory#1710)"; exit 1; \
    fi

# Runtime pip is supplied to virtualenvs by py3-pip-wheel, then removed there.
RUN apk del --no-cache py3.14-pip py3.14-pip-base \
 && ! apk info -e py3.14-pip && ! apk info -e py3.14-pip-base
