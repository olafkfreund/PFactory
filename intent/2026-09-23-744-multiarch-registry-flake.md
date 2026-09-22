---
status: draft
issue: 744
author: Olaf Krasicki-Freund
---

# Intent: A slow registry fails the required unit gate

## Problem

`tests/docker/test_p0_multi_arch.py::test_multi_arch_buildable` shells out to
`docker buildx imagetools inspect --raw <base image digest>` with
`timeout=30`. When cgr.dev is slow the call raises `subprocess.TimeoutExpired`,
which is an error, not an assertion — so the test fails and takes the required
`backend (ruff + pytest)` check with it. That is what happened on #743, a PR
that changed a YAML comment and a pin.

The test guards against a missing docker/buildx with `pytest.skip`, but not
against an unreachable or slow registry, so a network hiccup is reported as a
code failure on a PR that cannot have caused it.

Measured on `dev` (CI run 35408605952):

- the unit job runs `pytest tests/ apps/web-server/tests/ -m "not slow"`
  (`ci.yml:98`), which does **not** exclude `-m docker`; its log shows
  `tests/docker/test_p0_multi_arch.py`, `test_p0_runtime.py` and
  `test_p0_supply_chain.py` all running there;
- the dedicated `docker (P0 acceptance)` job runs
  `pytest tests/docker/ -m docker -v` (`ci.yml:749`) with buildx set up and the
  image built — and it is itself a required check on `dev`.

So the docker suite runs twice, and the copy in the required unit job is the
one with no buildx setup step, no built image, and a hard dependency on a
third-party registry being fast.

`tests/pytest.ini` documents the marker as "run with `-m docker`", which is
what the dedicated job does and the unit job does not.

## Proposed outcome

- A slow or unreachable registry no longer fails the required unit gate on an
  unrelated PR.
- Base-image multi-arch coverage is not lost: it still runs, and still blocks
  merge, in `docker (P0 acceptance)`.
- A registry that is genuinely unreachable is visible as a skip with a reason
  naming the registry, never as a silent pass.

## Affected users and systems

- Everyone opening a PR (the required `backend (ruff + pytest)` check).
- `.github/workflows/ci.yml` (unit job), `tests/docker/test_p0_multi_arch.py`.

## Constraints

- Must not weaken the multi-arch contract: a base image that really lacks
  amd64/arm64 must still fail `docker (P0 acceptance)`.
- Must not turn a real failure into a skip: only a timeout/connection error
  may skip, never a manifest that parses and lacks an architecture.
- No `continue-on-error` on a gate (the repo's rule 4.10: a control that passes
  without checking looks identical to one that checked).

## Open questions

1. Scope: (a) exclude `-m docker` from the unit job only; (b) also make the
   test skip on `TimeoutExpired`/connection errors; or (c) only (b).
   Recommendation: (a) **and** (b) — (a) removes the duplicate run from the
   required gate, (b) keeps the dedicated job from flaking on the same
   third-party outage. (c) alone leaves the docker suite running twice.
