---
status: approved
issue: 733
intent: intent/2026-09-18-733-apk-refresh-noop.md
---

# Spec: The root image's `apk upgrade` security refresh does nothing

## Correction to the approved intent

The intent's second outcome — "stop computing and passing `SECURITY_REFRESH`"
for the root image — is **wrong, and this spec does not do it**. Found while
designing:

- The daily `SECURITY_REFRESH` busts the cache of the `apk upgrade` layer *and
  every layer after it*, including `apk add`.
- The `apk add` layer is a real refresh. Its non-base packages are not in
  `/etc/apk/world` at all (measured: `gh`, `curl`, `gnupg`, `socat`,
  `bubblewrap` → no entry), so each fresh run resolves them to the newest index
  version; the named floors (`libssl3`, `libcrypto3`, `busybox`, `wget`,
  `binutils`) replace their base pins and resolve upward too (09-03:
  `libcrypto3 3.6.4-r2` against a `>=3.6.3-r5` floor).
- Dropping the arg would let `cache-from` freeze that layer indefinitely — the
  exact CFactory#440 failure `tests/test_security_refresh.py` was written for.

So: delete the no-op `apk upgrade`, but keep the cache-bust and point it at the
layer that does the work. The first intent outcome (no layer that does nothing,
an honest comment) stands.

## Design

### `Dockerfile` (runtime stage)

- Delete `RUN echo "security refresh: …" && apk upgrade --no-cache`.
- Keep `ARG SECURITY_REFRESH=0`, moved to just above the `apk add` `RUN`, and
  start that `RUN` with `echo "security refresh: ${SECURITY_REFRESH}" &&` so the
  arg busts this layer (same idiom as today).
- Replace the old comment with the true mechanism:
  - the base pins every package in `/etc/apk/world`, so `apk upgrade` cannot
    move anything (measured no-op, #733);
  - base-layer fixes arrive by base-digest bumps (auto-merged when green, #735)
    and by the explicit floors in `apk add`;
  - `SECURITY_REFRESH` re-runs `apk add` daily so the packages it installs, and
    the floors, resolve to the newest index versions instead of a cached day.
- Workflows (`image-build.yml`, `deploy.yml`) keep computing and passing the
  arg; only their comments change ("the `apt-get upgrade` layer" → "the package
  layer"), since the root image never used apt.

### `tests/test_security_refresh.py`

Widen the rule that decides "this image refreshes packages, so its cached build
must carry the cache-bust" from `(apt-get|apk) upgrade` to also match
`apk add`. Otherwise deleting `apk upgrade` silently drops the root image out of
the check. `apk add` appears only in the root `Dockerfile`; the runner images
are apt-based and unaffected.

### `tests/docker/test_p0_supply_chain.py`

Docstring of `test_trivy_no_high_critical` says "the image's `apk upgrade` +
digest pin clear every fixable HIGH/CRITICAL" — change to "digest bumps + the
explicit floors in `apk add`". No logic change.

## Alternatives rejected

- **Make `apk upgrade` real by loosening world pins**: measured to move only
  the daily git-snapshot Python build — runtime churn under the digest pin.
- **Delete the layer and the arg** (the intent's wording): freezes `apk add`.
- **Leave the layer, fix only its comment**: keeps a step that does nothing.

## Risks

- None to image content: the deleted layer is a measured no-op, and `apk add`
  keeps the same inputs and the same daily re-run.

## Verification

- Local `docker build --build-arg SECURITY_REFRESH=a .` then again with `=b`:
  the second build re-runs the `apk add` layer (not `CACHED`) and prints
  `security refresh: b`; the build succeeds.
- `pytest tests/test_security_refresh.py tests/docker -m "not docker" -q` green.
- Negative control (not committed): remove `SECURITY_REFRESH` from the root
  build step in `image-build.yml` → `test_every_cached_upgrade_layer_can_be_rebuilt`
  fails; with the old regex (`upgrade` only) it would have passed.
- `git grep -n "apk upgrade" -- Dockerfile` → only the explanatory comment.
