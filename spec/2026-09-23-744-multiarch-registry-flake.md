---
status: draft
issue: 744
intent: intent/2026-09-23-744-multiarch-registry-flake.md
---

# Spec: A slow registry fails a required CI gate

## Design

### `tests/docker/helpers.py` — one helper, two failure kinds

    class RegistryUnavailable(RuntimeError):   # transport: skip
    class ManifestInspectFailed(RuntimeError): # registry answered "no": fail

    def inspect_raw_manifest(
        ref: str, *, timeout: int = 30, attempts: int = 2, backoff: float = 3.0,
        runner=subprocess.run, sleep=time.sleep,
    ) -> str:

Runs `docker buildx imagetools inspect --raw <ref>` and returns raw stdout.

- `subprocess.TimeoutExpired`, or exit != 0 whose stderr matches a transport
  marker → retry after `backoff` (intent Q1: retry once), then raise
  `RegistryUnavailable` carrying the ref and the last error text.
- exit != 0 with any other stderr (e.g. `manifest unknown`, `not found`,
  `unauthorized`) → raise `ManifestInspectFailed` immediately, no retry. This
  is a real defect: a pinned digest that does not exist.
- exit 0 → return stdout (the test still parses and asserts on arches).

Transport markers (matched case-insensitively against stderr):
`i/o timeout`, `context deadline exceeded`, `dial tcp`, `tls handshake timeout`,
`connection refused`, `connection reset`, `temporary failure in name
resolution`, `no such host`, `unexpected eof`, `503`, `502`, `504`, `429`,
`too many requests`, `server misbehaving`.

`runner`/`sleep` are injection seams so the behaviour is testable without a
network (the same pattern `stability_runner`/`mutate_probe` use).

### `tests/docker/test_p0_multi_arch.py`

    try:
        raw = inspect_raw_manifest(ref)
    except RegistryUnavailable as exc:
        pytest.skip(f"registry unavailable, multi-arch not verified: {exc}")

`ManifestInspectFailed` is left to propagate (fails, as today). The arch
assertions are unchanged: a manifest that answers and lacks arm64 still fails.

### New `tests/test_registry_inspect_helper.py` (NOT `docker`-marked)

Unit tests over the helper with a fake runner, so they run in the fast job and
need no docker/network.

## Alternatives rejected

- **Catch `Exception` / bare skip on any non-zero exit**: would skip on
  `manifest unknown` — a pinned digest that vanished — turning a real failure
  into a green-ish skip. That is the pass-without-checking shape the repo
  rules forbid.
- **Drop `docker`-marked tests from the unit job** (issue option 2, intent Q2):
  does not fix the flake — the same test is required via `docker (P0
  acceptance)` — and removes 6 cheap static checks from the fast job.
- **`continue-on-error` on the job**: forbidden by repo rule 4.10.
- **Bigger timeout only**: a slow registry gets slower; 60 s of CI time per ref
  buys nothing when the answer is "no answer".

## Risks

- A genuine "registry is down for everyone" run now reports skipped instead of
  red. Visible in the run log (skip reason names the ref and error); the image
  build jobs would fail anyway if the base were truly unfetchable.
- Marker list is heuristic: an unlisted transport phrasing still fails the gate
  (the current behaviour, i.e. no regression).

## Verification

- Helper unit tests (fake runner): two timeouts → `RegistryUnavailable` and
  `sleep` called once; transport stderr → `RegistryUnavailable`; `manifest
  unknown` → `ManifestInspectFailed` with no retry; timeout then success →
  returns JSON.
- `test_multi_arch_buildable` with a monkeypatched helper raising
  `RegistryUnavailable` → the test skips, reason names the ref.
- Negative control: make the helper treat every non-zero exit as transport →
  the `manifest unknown` test fails.
- Real run: `pytest tests/docker/test_p0_multi_arch.py -m docker -q` green
  locally against the live registry.
