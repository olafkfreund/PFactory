---
status: draft
issue: 674
spec: spec/2026-09-18-674-pin-runtime-node.md
---

# Plan: Runtime node floats against a frozen glibc and breaks the image build

Approved decisions (from the spec):

- Runtime `apk add`: bare `nodejs` / `npm` → exact pins
  `"nodejs-26=26.9.0-r0"` and `"npm-12=12.0.2-r3"` (verified to install and run
  on base `@sha256:30cd0d99…`, glibc-2.44; node needs `GLIBC_2.44`; the Wolfi
  index keeps old releases so the pins stay installable).
- Same `RUN` ends with `&& node --version && npm --version`.
- Comment explains: exact pins unlike the CVE floors, because the base pins
  glibc per series in `/etc/apk/world` and glibc does not follow `apk add`; how
  to bump (pick from the index, confirm on the current base, bump both; a Trivy
  HIGH on node is the usual trigger).
- The no-op `apk upgrade` layer is #733, untouched here. No base-digest or
  frontend-stage change.

## Steps

1. `Dockerfile`: update the package-list comment (the `nodejs, npm` entry) with
   the pin rationale and bump procedure.
2. `Dockerfile`: replace `nodejs \` / `npm \` with the two quoted pins, and
   append `&& node --version && npm --version` after the package list.
   → verify by `git diff` (one comment block, one `RUN`).
3. Local build: `docker build -t pfactory:674 .` (default build args).
   → verify the apk layer prints `v26.9.0` and `12.0.2`, and the build
   completes.
4. Runtime check on the built image:
   `docker run --rm --entrypoint sh pfactory:674 -c 'id -un; node --version; npm --version; claude --version'`
   → verify `nonroot`, `v26.9.0`, `12.0.2`, and a claude version string.
5. Negative control (not committed): set the runtime `FROM` to
   `cgr.dev/chainguard/python:latest-dev@sha256:534fb1a1b9ad4d9d149ab669ca4218be76c84990e2f3379c7f703d224647666b`,
   build with `--target runtime` → verify it fails in the **apk-add layer** on
   `node --version` with the `GLIBC_2.44` loader error. Restore `FROM`.
6. `git grep -n "nodejs-26=\|npm-12=" Dockerfile` shows exactly the two pins.

## Tests

    docker build -t pfactory:674 .
    docker run --rm --entrypoint sh pfactory:674 -c 'node --version; npm --version; claude --version'

Expected: build succeeds; versions print. On the PR: "Build root Dockerfile"
and the docker (P0) / helm (P4) acceptance jobs green.

## Rollback

Revert the commit; `nodejs`/`npm` float again (the pre-#674 behaviour). No
state involved.
