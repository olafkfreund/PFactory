/**
 * PFactory Playwright auth setup template — #107 task 5 (storageState).
 *
 * Rendered by ``agents.evidence.layout.render_auth_setup`` into the generated
 * test workspace when a target's ``.pfactory.yml`` declares ``auth: { type: ref }``
 * and a subtask sets ``requires_auth``. It logs in ONCE through the form and
 * saves the authenticated session to ``@@STORAGE_STATE_PATH@@``; every test that
 * declares ``dependencies: ["setup"]`` + ``storageState`` then reuses that
 * session — so there's no second login in the HAR and protected tests don't each
 * re-authenticate.
 *
 * Credentials never appear in this file: the username / secret are read at run
 * time from the env vars the credential vault injects (@@USERNAME_ENV@@ /
 * @@SECRET_ENV@@), which the Executor populates only on egress lanes.
 *
 * Substitution variables (replaced at render time):
 *   @@LOGIN_URL@@            — the login page URL
 *   @@USERNAME_SELECTOR@@    — selector for the username/email field
 *   @@PASSWORD_SELECTOR@@    — selector for the password field
 *   @@SUBMIT_SELECTOR@@      — selector for the submit button
 *   @@SUCCESS_URL_PATTERN@@  — substring/glob the post-login URL must match
 *   @@USERNAME_ENV@@         — env var holding the username
 *   @@SECRET_ENV@@           — env var holding the secret
 *   @@STORAGE_STATE_PATH@@   — where to write the saved session
 */

import { test as setup, expect } from "@playwright/test";

const STORAGE_STATE = "@@STORAGE_STATE_PATH@@";

setup("authenticate", async ({ page }) => {
  await page.goto("@@LOGIN_URL@@");

  // Credentials come from the injected env vars — never hard-coded.
  await page.locator("@@USERNAME_SELECTOR@@").fill(process.env["@@USERNAME_ENV@@"] ?? "");
  await page.locator("@@PASSWORD_SELECTOR@@").fill(process.env["@@SECRET_ENV@@"] ?? "");
  await page.locator("@@SUBMIT_SELECTOR@@").click();

  // Wait for the post-login landing so the session cookie/token is established
  // before we snapshot it (auto-waited; never a fixed timeout).
  await page.waitForURL("**/@@SUCCESS_URL_PATTERN@@**");

  await page.context().storageState({ path: STORAGE_STATE });
  expect(STORAGE_STATE.length).toBeGreaterThan(0);
});
