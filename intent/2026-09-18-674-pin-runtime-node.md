---
status: draft
issue: 674
author: Olaf Krasicki-Freund
---

# Intent: Runtime node floats against a frozen glibc and breaks the image build

## Problem

On 2026-09-03 every image build failed at `npm config set prefix`
(`Dockerfile`, runtime stage) with
`node: /usr/lib/libm.so.6: version 'GLIBC_2.44' not found`. Nothing in the
repo had changed. Merging #666 (base digest bump) cleared it; builds have been
green since.

The mechanism, from the failing log (run 33738609065) and the current base
image — more precise than the issue's:

- The runtime base (`cgr.dev/chainguard/python:latest-dev@sha256:…`) pins every
  installed package to an exact version in `/etc/apk/world`. So
  `apk upgrade --no-cache` is a **no-op**: in the failing run it printed
  `OK: 666 MiB in 76 packages` and upgraded nothing; on today's base it does the
  same. Only packages named in `apk add` move.
- `apk add … nodejs npm` is unconstrained, so it installs whatever `nodejs-26`
  the Wolfi index has that day (26.8.1-r1 on 09-03, 26.9.0-r0 today). Wolfi
  builds it against the newest glibc.
- glibc does not follow. It is packaged per series (`glibc-2.43`, `glibc-2.44`,
  …) and the base pins its series; node's `so:libm.so.6` dependency is satisfied
  by the older glibc, so apk sees no conflict. The binary then fails at run
  time.
- Today the base ships `glibc-2.44-2.44-r1`, so it works. The next time Wolfi
  rebuilds `nodejs-26` against glibc 2.45 before a base digest ships 2.45, the
  build breaks the same way, with no commit.

The issue's "remove the class" option — copy node from the pinned
`node:26-bookworm-slim` stage — was already tried and rejected: the Dockerfile
comment says node is installed via apk "instead of binary-copying from the
frontend stage so dynamic linker deps (libuv etc.) resolve correctly".

## Proposed outcome

- The runtime stage installs a `nodejs`/`npm` that is known to run on the
  pinned base's glibc, and keeps doing so until someone changes it on purpose.
- If a node change ever needs a newer glibc than the base has, the build fails
  at the apk layer with a message that says so, not two layers later at
  `npm config`.
- #674 closed.

## Affected users and systems

- `Dockerfile` (runtime stage) only. CI "Build root Dockerfile" and the
  release image build.
- The `claude-code` CLI and other npm-installed agent CLIs that run on this node.

## Constraints

- Node security fixes must still reach the image: the P0 Trivy gate
  (`test_trivy_no_high_critical`) fails on a HIGH/CRITICAL node CVE, which is
  the signal to bump.
- No change to the base digest (Dependabot owns it) or the frontend stage.
- The pinned version must exist in the Wolfi x86_64 index and be verified to run
  on the pinned base before it is committed (same rule the file already states
  for its floors).

## Open questions

1. How to hold node still: (a) exact-pin `nodejs-26=<ver>` and `npm-12=<ver>`
   to versions verified on the current base, plus a `node --version` check in
   the same `RUN` so a bad bump fails at install; or (b) only the check, keeping
   node floating (fails loudly, but still fails with no commit). Recommendation:
   (a) — (b) makes the failure clearer but keeps the "breaks with no commit"
   property the issue is about.
2. Out of scope but found here: the `apk upgrade` "security refresh" layer is a
   no-op on this base, while its comment says it clears fixable CVEs between
   digest bumps. File it as a separate issue? Recommendation: yes — it is a
   control that reports doing something it does not do.
