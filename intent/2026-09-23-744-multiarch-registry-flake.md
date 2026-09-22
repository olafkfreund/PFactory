---
status: approved
issue: 744
author: Olaf Krasicki-Freund
---

# Intent: A slow registry fails a required CI gate

## Problem

`tests/docker/test_p0_multi_arch.py::test_multi_arch_buildable` asks
`cgr.dev` / `docker.io` about each pinned base image
(`docker buildx imagetools inspect --raw <ref>`, `timeout=30`). When the
registry is slow, `subprocess.TimeoutExpired` propagates and the test ERRORs.

Measured on `dev`:

- The required `backend (ruff + pytest)` job runs
  `pytest tests/ apps/web-server/tests/ -m "not slow"`, which includes
  `docker`-marked tests. Of the 7 that run there, 6 are static file checks
  (Dockerfile digests pinned, release workflow signs, docs exist) — **this is
  the only one that touches the network**. The heavier image-building tests are
  `slow`-marked and do not run in that job.
- `docker (P0 acceptance)` is *also* a required check and runs the same test
  (`pytest tests/docker/ -m docker`), so the same hiccup can fail that gate too.

The test already skips when docker or buildx is absent, so "environment cannot
answer" is an accepted skip condition — but an unreachable/slow registry is
reported as a code failure instead. #743 hit this on a PR that changed a YAML
comment and a pin.

## Proposed outcome

- A registry timeout / transport failure no longer fails either gate: the test
  reports a skip whose reason names the registry and the error.
- A registry that answers and says an image is single-arch still FAILS, as now.
  (The check must not become a pass-without-checking.)
- Repeated flakiness stays visible: the skip reason is explicit, not silent.

## Affected users and systems

- Every PR: `backend (ruff + pytest)` and `docker (P0 acceptance)` are both
  required checks.
- `tests/docker/test_p0_multi_arch.py` only.

## Constraints

- Never skip on a *substantive* failure (manifest present, arch missing) — that
  is the assertion the test exists for.
- No unbounded retry loops that turn a 30 s failure into minutes of CI time.
- No `continue-on-error` on the job (repo rule: a control that passes without
  running is worse than one that fails).

## Open questions

1. Retry once before skipping (one extra inspect after a short backoff), or
   skip on the first timeout? Recommendation: retry once — a single hiccup is
   the common case, and it keeps real coverage rather than skipping eagerly.
2. Also drop `docker`-marked tests from the unit job (the issue's second
   option), since `docker (P0 acceptance)` already runs them as a required
   check? Recommendation: no — it does not fix the flake (it still fails the
   acceptance gate), and it removes the 6 cheap static checks from the fast
   job for no gain.
