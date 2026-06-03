/**
 * PFactory-tuned Playwright configuration template — Task 16 / #32.
 *
 * This file is a **template** that Gen-Functional (or the Executor) renders
 * into the generated test workspace.  Two substitution variables are
 * replaced at render time:
 *
 *   @@OUTPUT_DIR@@   — absolute path to <spec_dir>/findings/evidence/<test_id>
 *   @@BASE_URL@@     — the target ``base_url`` from .pfactory.yml
 *
 * Evidence settings:
 *   screenshot  "@@SCREENSHOT_POLICY@@"   default: "only-on-failure"
 *   video       "@@VIDEO_POLICY@@"        default: "retain-on-failure"
 *   trace       "@@TRACE_POLICY@@"        default: "on-first-retry"
 *
 * The config deliberately keeps the project / reporter / workers setup
 * minimal so PFactory's docker sandbox can run without network egress
 * or complex process trees.  Each test file is run in a single worker
 * to avoid port conflicts.
 *
 * Callers that want to render the template in Python should call
 * ``agents.evidence.layout.render_playwright_config()``.
 */

import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: ".",          // tests are co-located with this config in the workspace
  timeout: 30_000,       // 30s per test — sane default for browser tests
  retries: 1,            // one retry so trace: "on-first-retry" triggers
  workers: 1,            // single worker prevents port clashes in sandbox

  outputDir: "@@OUTPUT_DIR@@",

  use: {
    baseURL: "@@BASE_URL@@",
    screenshot: "@@SCREENSHOT_POLICY@@",
    video: "@@VIDEO_POLICY@@",
    trace: "@@TRACE_POLICY@@",

    // Headless by default; the Docker container has no display server
    headless: true,@@STORAGE_STATE_USE@@
  },

  reporter: [
    ["list"],
    ["junit", { outputFile: "@@OUTPUT_DIR@@/junit.xml" }],
  ],

  projects: [@@SETUP_PROJECT@@
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },@@CHROMIUM_DEPS@@
    },
  ],
});
