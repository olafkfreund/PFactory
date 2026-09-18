---
status: draft
issue: 674
intent: intent/2026-09-18-674-pin-runtime-node.md
---

# Spec: Runtime node floats against a frozen glibc and breaks the image build

## Facts established (on the current base, `python:latest-dev@sha256:30cd0d99…`)

- `nodejs` resolves to `nodejs-26`, `npm` to `npm-12`; today
  `nodejs-26 26.9.0-r0` and `npm-12 12.0.2-r3`.
- The Wolfi index retains old releases: every `nodejs-26` from `26.0.0-r0` to
  `26.9.0-r0` is still listed (as are `glibc-2.44` r0-r6). An exact pin stays
  installable after newer releases land.
- `nodejs-26=26.9.0-r0` + `npm-12=12.0.2-r3` install and run on this base:
  `npm --version` → `12.0.2`, node loads libuv 1.52.1. node's highest required
  symbol is `GLIBC_2.44`; the base ships `glibc-2.44-2.44-r1`.

## Design

`Dockerfile`, runtime stage `apk add` line:

- Replace the bare `nodejs` / `npm` with exact pins
  `"nodejs-26=26.9.0-r0"` and `"npm-12=12.0.2-r3"` (the concrete package
  names, so a node major change is also an explicit edit).
- Append `&& node --version && npm --version` to the same `RUN`, so a node
  whose glibc the base does not have fails *at the install layer* with the
  loader error, not two layers later at `npm config set prefix`.
- Comment above the list: why these two are exact pins (unlike the CVE floors
  beside them): the base pins glibc per series in `/etc/apk/world`, glibc does
  not follow `apk add`, so a floating node breaks the build the day Wolfi
  rebuilds it against a newer glibc (#674). How to bump: pick a version from the
  index, confirm it runs on the current base, bump both together; a Trivy HIGH
  on node is the usual trigger.

**Decided (intent Q1):** exact pins + the check. **(intent Q2):** the no-op
`apk upgrade` layer is tracked separately in #733 and not touched here.

## Alternatives rejected

- **Check only, node floating**: fails loudly but still fails with no commit —
  the property the issue is about.
- **Copy node from the `node:26-bookworm-slim` stage**: already rejected in the
  Dockerfile's own comment (libuv and other linker deps don't resolve).
- **Pull glibc forward with node** (`apk add glibc …`): glibc is packaged per
  series (`glibc-2.44`, next `glibc-2.45`), so naming it does not move the
  series, and the bare `glibc` name resolves to an older 2.43 build.
- **Floors (`nodejs>=…`)**: a floor still floats upward — the failure mode.

## Risks

- Node security fixes now arrive only by bumping the pin. Mitigated by the P0
  Trivy gate (`test_trivy_no_high_critical`), which fails on a HIGH/CRITICAL
  node CVE; the comment says to bump then. No automation watches apk pins (the
  hub CLI-freshness job watches the npm CLIs, Dependabot watches `FROM` only).
- A Dependabot base-digest bump that ships an *older* glibc than node needs is
  not a real scenario (glibc only moves forward), and would be caught by the
  new check.

## Verification

- Full local `docker build .` of the image (default args) succeeds; the apk
  layer prints the node and npm versions; the built image runs `node --version`
  and `claude --version` as `nonroot`.
- Negative control (not committed): temporarily set the runtime `FROM` to the
  old digest `@sha256:534fb1a1…` (glibc 2.43) — the build fails in the **apk
  layer** on `node --version` with the `GLIBC_2.44` loader error, not at
  `npm config`.
- CI "Build root Dockerfile" and the P0/P4 acceptance jobs green on the PR.
