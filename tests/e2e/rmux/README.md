# rmux E2E tests (Epic #44 R4)

Playwright scenarios for the Live Agent Console. Three specs:

| File | Scenario |
|---|---|
| `session-lifecycle.spec.ts` | API: rmux session resolves while task runs; 404 on unknown spec |
| `readonly-stream.spec.ts` | WS open → connected envelope + binary frames; UI badge flips to Connected (read-only) |
| `attach-roundtrip.spec.ts` | Attach modal → POST /attach → audit row → badge "Attached" → Detach → audit row |

## Prerequisites

- Web-server running at `http://localhost:3102` with `AIFACTORY_RMUX_ENABLED=true`
- Frontend dev server at `http://localhost:3100`
- `rmux v0.3.x` installed on PATH (the web-server's startup smoke test gates on it)
- At least one task in the `backlog` column of the `aifactory-test` project (the fixture imports the next one and runs it)

## Local run

```bash
# 1. Start the stack (each in its own terminal)
AIFACTORY_RMUX_ENABLED=true \
  python -m server.main                                # apps/web-server/

npm -w apps/frontend-web run dev                       # repo root

# 2. Run the suite
AIFACTORY_E2E_TOKEN=$(cat ~/.aifactory/.token) \
  AIFACTORY_E2E_PROJECT_ID=<your-project-id> \
  npm -w apps/frontend-web run test:e2e
```

## Flake budget

**Zero.** If a scenario flakes, the underlying race must be fixed — `retries: 0` is set in `playwright.config.ts` so flakes fail the build. Always use:

- `data-testid` selectors (not text — i18n changes break tests)
- `expect.poll` for eventual-consistency checks (not `setTimeout`)
- Playwright auto-wait via `expect(locator).toBeVisible()` (not `.waitFor`)

## CI

The `e2e-rmux` job in `.github/workflows/ci.yml` installs the rmux binary, the Playwright browser bundle, boots the web-server + Vite, and runs the suite headless. HTML report uploaded as a build artifact on failure.

## Future scope (v1.x)

- **Input round-trip latency** (≤100 ms) — needs reading the agent's pane after sending keystrokes; timing-sensitive and risks the zero-flake budget. Deferred to a follow-up PR with a more robust harness (probably a synthetic agent command that echoes the keystroke verbatim into the FIFO).
- **Race test** (1000 concurrent `POST /attach` → exactly 1 × 200) — already covered server-side in `tests/rmux/test_bridge.py::TestAttachRace::test_one_200_999_409`. Adding it here would duplicate.
- **kind-cluster variant** — the issue describes running against a kind cluster with the `aifactory:vX-rmux` Helm chart. v1 ships against the local boot; v1.x adds the deploy-then-test variant.
