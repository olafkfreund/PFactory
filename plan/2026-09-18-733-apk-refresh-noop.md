---
status: draft
issue: 733
spec: spec/2026-09-18-733-apk-refresh-noop.md
---

# Plan: The root image's `apk upgrade` security refresh does nothing

Approved decisions (from the spec, which corrects the intent):

- Root `Dockerfile`: delete the `apk upgrade` layer (measured no-op: the base
  pins every package in `/etc/apk/world`). Keep `ARG SECURITY_REFRESH=0`, moved
  above the `apk add` `RUN`, which now starts with
  `echo "security refresh: ${SECURITY_REFRESH}" &&`. That layer is the real
  refresh (non-base packages have no world entry; floors resolve upward).
- New comment states the real mechanism: digest bumps (auto-merged, #735),
  explicit floors, and the daily re-run of `apk add`.
- `image-build.yml` / `deploy.yml` keep passing the arg; only their
  "`apt-get upgrade` layer" comments change to "the package layer".
- `tests/test_security_refresh.py`: the "refreshes packages" rule also
  matches `apk add`, so the root build must keep the cache-bust.
- `tests/docker/test_p0_supply_chain.py`: Trivy docstring no longer credits
  `apk upgrade`.
- Runner images and `runner-images.yml` untouched.

## Steps

1. `Dockerfile`: replace the refresh comment + `ARG` + `RUN … apk upgrade`
   block with the new comment + `ARG SECURITY_REFRESH=0` placed directly above
   the `apk add` `RUN`; prefix that `RUN` with the echo.
   → verify `git grep -n "apk upgrade" -- Dockerfile` shows comment text only,
   and `${SECURITY_REFRESH}` appears once, in the `apk add` `RUN`.
2. `.github/workflows/image-build.yml:110` and `deploy.yml:65`: comment wording
   only. → verify `git diff` touches comment lines only.
3. `tests/test_security_refresh.py`: `_UPGRADE` → also match `apk\s+add`; update
   the module docstring to say a Chainguard `apk add` layer is the refresh.
   → verify the file's tests pass.
4. `tests/docker/test_p0_supply_chain.py`: docstring wording.
5. Local builds: `docker build --build-arg SECURITY_REFRESH=a -t pf733 .`, then
   `--build-arg SECURITY_REFRESH=b` → verify the second build's `apk add` layer
   is not `CACHED` and prints `security refresh: b`; both succeed.
6. Negative control (not committed): delete the `SECURITY_REFRESH=` line from
   the root build step in `image-build.yml` → verify
   `test_every_cached_upgrade_layer_can_be_rebuilt` fails; restore. Then, with
   the arg still removed, revert only the regex widening → verify the same test
   passes (proving the widening is what catches it); restore both.

## Tests

    apps/backend/.venv/bin/pytest tests/test_security_refresh.py tests/docker -m "not docker" -q
    docker build --build-arg SECURITY_REFRESH=a -t pf733 . && docker build --build-arg SECURITY_REFRESH=b -t pf733 .

Expected: tests pass; both builds succeed; second build re-runs `apk add`.

## Rollback

Revert the commit; the no-op layer and old comments return. No image content
changes either way.
