/**
 * Playwright config for Epic #44 R4 — rmux Live Console E2E.
 *
 * Three scenarios in this directory:
 *   - session-lifecycle.spec.ts   rmux session create/teardown around a task
 *   - readonly-stream.spec.ts     bytes from the agent reach xterm in <200ms
 *   - attach-roundtrip.spec.ts    Attach modal → POST /attach → keystrokes → audit row
 *
 * Local run:
 *   PFACTORY_E2E_TOKEN=$(cat ~/.pfactory/.token) \
 *   PFACTORY_E2E_PROJECT_ID=8aad9e7f-... \
 *   npm -w apps/frontend-web run test:e2e
 *
 * CI run: the e2e-rmux job in .github/workflows/ci.yml boots the
 * web-server + Vite + rmux daemon, then invokes ``playwright test``.
 *
 * "Zero flaky" budget: rely entirely on data-testid selectors and
 * Playwright's auto-wait (no sleep-based assertions).  Timing-sensitive
 * latency checks use ``expect.poll`` with a clear timeout, not setTimeout.
 */

import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: '**/*.spec.ts',

  // Full parallel execution within each scenario file, but scenarios
  // share the same browser instance / page across tests to keep the
  // run under 5 minutes in CI.
  fullyParallel: false,
  workers: 1,

  // Zero retries — design §4 says "flaky-test budget = zero".  If a
  // scenario flakes, fix the underlying race, don't paper over with
  // retries.
  retries: 0,

  reporter: process.env.CI
    ? [['github'], ['html', { open: 'never' }]]
    : 'list',

  use: {
    baseURL: process.env.PFACTORY_E2E_BASE_URL ?? 'http://localhost:3115',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    // Bearer token to drive the API and as the WS query param.  The
    // ``PFACTORY_E2E_TOKEN`` env var is set by the CI job from
    // ``~/.pfactory/.token`` (a file the web-server writes at boot).
    extraHTTPHeaders: process.env.PFACTORY_E2E_TOKEN
      ? { Authorization: `Bearer ${process.env.PFACTORY_E2E_TOKEN}` }
      : undefined,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  // Don't try to start a dev server from inside the test runner — CI
  // and the local dev shell already have one.  ``baseURL`` points at
  // wherever it lives.
});
