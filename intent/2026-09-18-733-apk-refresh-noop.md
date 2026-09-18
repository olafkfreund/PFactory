---
status: draft
issue: 733
author: Olaf Krasicki-Freund
---

# Intent: The root image's `apk upgrade` security refresh does nothing

## Problem

The root `Dockerfile`'s runtime stage runs

    ARG SECURITY_REFRESH=0
    RUN echo "security refresh: ${SECURITY_REFRESH}" && apk upgrade --no-cache

and its comment says it "clears fixable HIGH/CRITICAL findings between digest
bumps". `image-build.yml` and `deploy.yml` compute a daily `SECURITY_REFRESH`
to bust its cache, with comments about an "`apt-get upgrade` layer".

It upgrades nothing. The Chainguard base pins every installed package exactly
in `/etc/apk/world`, and `apk upgrade` respects those pins. Measured:

- failing build 2026-09-03 (run 33738609065): fresh layer, `OK: … 76 packages`,
  zero upgrades — while the next `apk add` layer upgraded four packages;
- current base (`@sha256:075c08ad…`), today: zero upgrades.

A security control that reports doing something it does not do misleads
reviewers and auditors: base-layer fixes actually arrive only through base
digest bumps and the explicit version floors in the `apk add` line.

## What "making it real" would do (measured)

Loosening the world pins and running `apk upgrade` on today's base moves only
the Python interpreter (`python-3.14*`, `3.14.7_git20260917-r3` →
`3.14.7_git20260918-r0`) — a daily git-snapshot build. So a working refresh
would mainly make the runtime Python change every day underneath the digest
pin, which is what the pin exists to prevent. And since #735, green Dependabot
base-digest bumps auto-merge, which closes most of the "between digest bumps"
window the layer was meant to cover.

## Proposed outcome

- The root image no longer carries a refresh layer that does nothing; its
  comment says truthfully how base-layer fixes arrive (digest bumps, now
  auto-merged via #735, plus the explicit floors).
- The root-image build steps in `image-build.yml` and `deploy.yml` stop
  computing and passing `SECURITY_REFRESH`.
- #733 closed.

## Affected users and systems

- Root `Dockerfile` (runtime stage), `.github/workflows/image-build.yml`,
  `.github/workflows/deploy.yml`.
- **Not** the six runner images under `docker/pfactory-runner-*`: all are
  Debian/Ubuntu-based and use `apt-get upgrade`, which does work; their layers
  and `runner-images.yml` stay as they are.

## Constraints

- No change to what the image contains (the layer is a verified no-op).
- Keep the explicit CVE floors in the `apk add` line — they are the part that
  works.
- The P0 Trivy gate stays the enforcement point.

## Open questions

1. Delete the layer (recommended — measured no-op, and "fixing" it means daily
   Python churn), or make it real by loosening the world pins?
