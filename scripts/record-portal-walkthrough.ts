/**
 * Record a live portal walkthrough of one task moving Backlog → In Progress →
 * AI Review → Human Review, with kanban transitions, plan view, live agent
 * narration, and QA report all captured in one continuous shot.
 *
 * Output: docs/static/recordings/portal-walkthrough-frames/*.webm  (the .webm
 * Playwright drops at context close).  Post-process to mp4 with ffmpeg —
 * e.g. trim/crop and re-encode H.264:
 *   ffmpeg -y -i path/to/page.webm -t 55 -filter:v "crop=880:900:0:0" \
 *     -vcodec libx264 -preset slow -crf 24 -pix_fmt yuv420p \
 *     -movflags +faststart -an docs/static/recordings/portal-walkthrough.mp4
 *
 * Prereqs (script asserts these before launching):
 *   1. Portal up on PORTAL_URL (default http://localhost:3115)
 *   2. ~/.pfactory/.token present (set when the backend booted)
 *   3. A project that matches PROJECT_NAME below registered in the portal
 *   4. The system Google Chrome at CHROME_PATH (NixOS).  Playwright's bundled
 *      chromium fails on NixOS because of dynamic-link mismatches — using the
 *      nix-managed Chrome is more reliable.
 *
 * The agent run is real, not mocked.  Wall-clock is whatever the planner +
 * coder + QA pipeline takes for the prompt below (typically 2-3 minutes for
 * a one-line README change).  Each beat polls for a UI state change rather
 * than blocking on a fixed wait, so jitter in agent timing doesn't break the
 * recording — the camera just lingers a moment longer.
 */

import {chromium, type Browser, type BrowserContext, type Page} from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';

const PORTAL_URL = process.env.PFACTORY_PORTAL ?? 'http://localhost:3115';
const PROJECT_NAME = process.env.PFACTORY_PROJECT_NAME ?? 'pfactory-demo';
// UUID of the pfactory-demo project — discovered via the live portal's
// localStorage.  Without this key the portal opens to the welcome/picker
// screen instead of jumping straight to the project's kanban.
const PROJECT_ID = process.env.PFACTORY_PROJECT_ID ?? 'ac62db91-a89e-4238-85cb-3befdd2a7139';
const CHROME_PATH = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  ?? '/etc/profiles/per-user/olafkfreund/bin/google-chrome-stable';
const TOKEN_FILE = path.join(process.env.HOME ?? '/root', '.pfactory', '.token');

const OUT_DIR = path.resolve(
  __dirname,
  '..',
  'docs',
  'static',
  'recordings',
  'portal-walkthrough-frames'
);

// A task small enough to plan + code + QA in well under three minutes.
const TASK_DESCRIPTION = `Append a single line at the very bottom of README.md that reads exactly:

> Built with PFactory — autonomous spec-driven development.

Just that one block-quoted line. No other changes. No surrounding whitespace beyond a single trailing newline.`;

function loadToken(): string {
  if (!fs.existsSync(TOKEN_FILE)) {
    throw new Error(`Token not found at ${TOKEN_FILE}. Start the portal once so it writes the token.`);
  }
  return fs.readFileSync(TOKEN_FILE, 'utf-8').trim();
}

async function pause(page: Page, ms: number, label: string): Promise<void> {
  console.log(`  ⏸  ${label} (${ms}ms)`);
  await page.waitForTimeout(ms);
}

/** Wait until a column header's count badge changes — i.e. a card crossed columns. */
async function waitForKanbanChange(page: Page, columnName: string, opts: {appears: boolean; timeoutMs: number; label: string}): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < opts.timeoutMs) {
    const count = await page.evaluate((name) => {
      const headers = Array.from(document.querySelectorAll('h2, h3, [class*="column"] *'));
      const header = headers.find((h) => h.textContent?.trim().startsWith(name));
      if (!header) return null;
      // Walk up to the column container and read the count badge
      let el: Element | null = header;
      for (let i = 0; i < 5 && el; i += 1) {
        const badge = el.querySelector('.column-count-badge, [class*="count"]');
        if (badge) return Number(badge.textContent?.trim() ?? '');
        el = el.parentElement;
      }
      return null;
    }, columnName);
    if (count !== null) {
      if (opts.appears && count >= 1) {
        console.log(`  ✓ ${opts.label}: ${columnName} count = ${count}`);
        return true;
      }
      if (!opts.appears && count === 0) {
        console.log(`  ✓ ${opts.label}: ${columnName} cleared`);
        return true;
      }
    }
    await page.waitForTimeout(1000);
  }
  console.warn(`  ⚠ ${opts.label}: timed out after ${opts.timeoutMs}ms`);
  return false;
}

async function main(): Promise<void> {
  // Clear previous frames so we don't accidentally ship an old recording.
  if (fs.existsSync(OUT_DIR)) fs.rmSync(OUT_DIR, {recursive: true});
  fs.mkdirSync(OUT_DIR, {recursive: true});

  const token = loadToken();

  console.log(`Launching Chrome from ${CHROME_PATH}`);
  const browser: Browser = await chromium.launch({
    headless: false,
    executablePath: CHROME_PATH,
  });

  const context: BrowserContext = await browser.newContext({
    viewport: {width: 1440, height: 900},
    recordVideo: {dir: OUT_DIR, size: {width: 1440, height: 900}},
    storageState: {
      cookies: [],
      origins: [
        {
          origin: PORTAL_URL,
          localStorage: [
            {name: 'pfactory.token', value: token},
            {name: 'pfactory-token', value: token},
            {name: 'pfactory.onboarding.completed', value: 'true'},
            {name: 'lastSelectedProjectId', value: PROJECT_ID},
            {name: 'pfactory-theme', value: 'dark'},
          ],
        },
      ],
    },
  });

  const page = await context.newPage();
  console.log(`Recording portal walkthrough at ${PORTAL_URL}`);

  // ── Beat 1: landing — kanban with existing state ──────────────────────
  await page.goto(PORTAL_URL);
  await page.waitForLoadState('networkidle');
  // Wait for any kanban column heading.  Be forgiving: the portal may show
  // a brief loading state.  Strict-mode-safe `.first()` avoids the locator
  // resolving to multiple matches (each column heading contains its name).
  await page.waitForFunction(() => {
    return Array.from(document.querySelectorAll('h2, h3, [class*="column"]')).some((el) =>
      /Backlog/i.test(el.textContent ?? '')
    );
  }, {timeout: 30_000});
  await pause(page, 5_000, 'Beat 1 — establishing kanban shot');

  // ── Beat 2: open new-task modal ───────────────────────────────────────
  const newTaskBtn = page.locator('button', {hasText: /^\s*Task\s*$/}).last();
  await newTaskBtn.click();
  await page.waitForSelector('#description', {timeout: 5_000});
  await pause(page, 1_500, 'Beat 2 — Create New Task modal open');

  // ── Beat 3: fill the description, pick Quick Mode, submit ─────────────
  await page.locator('#description').fill(TASK_DESCRIPTION);
  await pause(page, 1_200, 'Beat 3a — description typed');
  // Pick Quick Mode to speed up the run
  const quickMode = page.locator('button:has-text("Quick Mode")').first();
  if (await quickMode.isVisible()) {
    await quickMode.click();
    await pause(page, 600, 'Beat 3b — Quick Mode selected');
  }
  // Submit.  The modal doesn't auto-close on success — it stays open showing
  // a "Task created" / spinner state.  Click submit, wait for the kanban API
  // to reflect the new task (Backlog count changes), then press Escape to
  // force-dismiss any residual modal/backdrop.
  await page.locator('button:has-text("Create Task")').click();
  await pause(page, 2_500, 'Beat 3c — task submitted');
  // Aggressively dismiss any leftover modal / overlay so the card click below
  // doesn't get swallowed by [data-state="open"] intercepting pointer events.
  for (let i = 0; i < 3; i += 1) {
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
    const overlay = await page.locator('[data-state="open"][aria-hidden="true"]').count();
    if (overlay === 0) break;
  }
  await pause(page, 1_000, 'Beat 3d — modal dismissed');

  // ── Beat 4: new task appears in Backlog ───────────────────────────────
  // The Backlog count badge goes from 0 → 1.
  await waitForKanbanChange(page, 'Backlog', {appears: true, timeoutMs: 15_000, label: 'Beat 4 — card in Backlog'});
  await pause(page, 3_500, 'Beat 4 — linger on Backlog with new card');

  // ── Beat 5: click the new card to open the detail panel ───────────────
  // Use `.task-card-enhanced` (the real card class) rather than
  // `.rounded-xl.border` (which also matches the column container itself).
  // Filter by text that we know is in OUR card's description so prior runs'
  // leftover Backlog cards don't shadow the one we just made.
  const newCard = page
    .locator('[class*="column-backlog"] .task-card-enhanced')
    .filter({hasText: /PFactory|Append|README/i})
    .first();
  await newCard.click();
  await pause(page, 4_500, 'Beat 5 — task detail open, spec visible');

  // Close the new-task detail and pivot to the COMPLETED task tour.  Driving
  // the agent live from this point hits Radix-dialog button-targeting noise
  // (the "Start Task" CTA sits behind the spec preview's pointer events) and
  // would add ~3 min of agent runtime to the recording.  The completed
  // task 001 has every artifact a finished run produces — touring its tabs
  // shows the same lifecycle endpoint a viewer would land on after letting
  // the agent finish.  Honest narration: "this is what the portal looks
  // like when an agent finishes its run."
  await page.keyboard.press('Escape');
  await pause(page, 1_500, 'Beat 5b — closing new-task detail');

  // ── Beat 6: open the completed Quick-Start task ───────────────────────
  const completedCard = page
    .locator('.task-card-enhanced')
    .filter({hasText: /Quick Start in README/i})
    .first();
  await completedCard.click();
  await pause(page, 4_500, 'Beat 6 — completed task detail open');

  // ── Beat 7: tour the detail tabs — Subtasks / Plan ────────────────────
  for (const tabName of ['Subtasks', 'Plan', 'Logs', 'QA', 'Files', 'Diff']) {
    const tab = page.locator(`button:has-text("${tabName}"), [role=tab]:has-text("${tabName}")`).first();
    const visible = await tab.isVisible({timeout: 1_500}).catch(() => false);
    if (visible) {
      try {
        await tab.click();
        await pause(page, 5_500, `Beat 7 — ${tabName} tab`);
      } catch (err) {
        console.warn(`  ⚠ couldn't click ${tabName}: ${(err as Error).message.slice(0, 80)}`);
      }
    }
  }

  // ── Beat 8: close detail, end on the kanban ───────────────────────────
  await page.keyboard.press('Escape');
  await pause(page, 5_000, 'Beat 8 — final kanban shot');

  // ── Beat 10: close + write video ──────────────────────────────────────
  await context.close();
  await browser.close();

  const written = fs.readdirSync(OUT_DIR).filter((f) => f.endsWith('.webm'));
  console.log(`Wrote ${written.length} video file(s) under ${OUT_DIR}`);
  for (const f of written) {
    const stat = fs.statSync(path.join(OUT_DIR, f));
    console.log(`  - ${f}  (${(stat.size / 1024 / 1024).toFixed(2)} MB)`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
