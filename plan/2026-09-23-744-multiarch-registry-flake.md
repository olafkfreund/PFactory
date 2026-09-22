---
status: approved
issue: 744
spec: spec/2026-09-23-744-multiarch-registry-flake.md
---

# Plan: A slow registry fails a required CI gate

Approved decisions (from the spec):

- `tests/docker/helpers.py` gains `RegistryUnavailable`,
  `ManifestInspectFailed` and
  `inspect_raw_manifest(ref, *, timeout=30, attempts=2, backoff=3.0,
  runner=subprocess.run, sleep=time.sleep) -> str`.
- Transport failure (TimeoutExpired, or exit != 0 with a transport marker in
  stderr) → retry once after `backoff`, then `RegistryUnavailable`.
  Any other non-zero exit (`manifest unknown`, `not found`, `unauthorized`) →
  `ManifestInspectFailed` immediately, no retry. Exit 0 → stdout.
- Markers (case-insensitive): `i/o timeout`, `context deadline exceeded`,
  `dial tcp`, `tls handshake timeout`, `connection refused`,
  `connection reset`, `temporary failure in name resolution`, `no such host`,
  `unexpected eof`, `503`, `502`, `504`, `429`, `too many requests`,
  `server misbehaving`.
- `test_multi_arch_buildable` skips only on `RegistryUnavailable`, with a
  reason naming the ref and error; arch assertions unchanged.
- New `tests/test_registry_inspect_helper.py`, NOT `docker`-marked.
- CI job selection unchanged (intent Q2: no).

*Deviations (implementation, forced by the ruff ratchet — a changed file may
not gain findings):*

- The exception classes are `RegistryUnavailableError` / `ManifestInspectError`,
  not `RegistryUnavailable` / `ManifestInspectFailed` (ruff N818 requires an
  `Error` suffix). Behaviour and meaning unchanged.
- `attempts` is the module constant `_INSPECT_ATTEMPTS = 2` rather than a
  keyword argument (ruff PLR0913: 6 arguments exceeded the limit of 5). The
  retry-once behaviour is unchanged; `timeout`, `backoff`, `runner` and `sleep`
  remain injectable, which is what the tests need.

## Steps

1. `tests/docker/helpers.py`: add the two exception classes, the marker tuple,
   `_is_transport_error(stderr)` and `inspect_raw_manifest`. → verify by the
   unit tests in step 3.
2. `tests/docker/test_p0_multi_arch.py`: replace the inline `subprocess.run`
   with the helper inside try/except `RegistryUnavailable` → `pytest.skip`;
   drop the now-unused `subprocess`/`json` imports only if unused.
   → verify `pytest tests/docker/test_p0_multi_arch.py -m docker -q` green.
3. `tests/test_registry_inspect_helper.py` (new): fake-runner cases —
   a. two timeouts → `RegistryUnavailable`, `sleep` called once;
   b. transport stderr (`dial tcp ... i/o timeout`) → `RegistryUnavailable`;
   c. `manifest unknown` → `ManifestInspectFailed`, runner called once;
   d. timeout then success → returns the JSON, `sleep` called once;
   e. exit 0 first try → returns JSON, `sleep` not called.
   → verify green in the fast job selection (`-m "not slow"`).
4. `test_multi_arch_buildable` skip path: monkeypatch the helper to raise
   `RegistryUnavailable`; assert the test skips (via `pytest.raises(Skipped)`
   or an in-file `pytester`-free call of the test function).
5. Negative control (not committed): make `_is_transport_error` return True
   always → step 3c fails. Restore.
6. Real run against the live registry: `pytest tests/docker/test_p0_multi_arch.py -m docker -q`.

## Tests

    apps/backend/.venv/bin/pytest tests/test_registry_inspect_helper.py tests/docker/ -m "not slow" -q
    apps/backend/.venv/bin/pytest tests/docker/test_p0_multi_arch.py -m docker -q

Expected: all pass; full backend suite via the pre-commit hook.

## Rollback

Revert the commit; the test returns to failing on a slow registry. No state.
