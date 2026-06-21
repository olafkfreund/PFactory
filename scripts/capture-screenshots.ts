/**
 * PFactory screenshot capture for the docs gallery.
 *
 * Drives the LIVE portal (or a local dev portal) via Playwright + the system
 * Chrome and saves a curated gallery of PNGs to docs/static/img/screenshots/.
 *
 * Auth: localStorage pre-seed does NOT authenticate the SPA — the token must be
 * validated through the real login form. This script therefore performs a FORM
 * LOGIN (navigate "/", fill #token, submit) and then navigates ONLY in-SPA via
 * the left sidebar buttons (Planning / Files / MCP / Skills / Index & Memory /
 * Plans / GitHub PRs). Deep-linking to "/login" or "/files" 404s on the static
 * host, so we never deep-goto an in-app route.
 *
 * Environment:
 *   PFACTORY_PORTAL_URL   portal origin (default https://pfactory.freundcloud.org.uk)
 *   PFACTORY_TOKEN        API token (overrides ~/.pfactory/.token)
 *   PLAYWRIGHT_CHROMIUM_EXECUTABLE
 *                         path to a working Chrome/Chromium (required on NixOS —
 *                         the bundled Playwright chromium won't run there)
 *
 * Run with:
 *   PLAYWRIGHT_CHROMIUM_EXECUTABLE=/etc/profiles/per-user/$USER/bin/google-chrome-stable \
 *   PFACTORY_PORTAL_URL=https://pfactory.freundcloud.org.uk \
 *     node_modules/.bin/tsx scripts/capture-screenshots.ts
 *
 * The script is intentionally tolerant — if a view 404s or a selector misses,
 * it logs a warning and continues, capturing what it can.
 */

import {chromium, type Browser, type Page} from '@playwright/test';
import * as path from 'node:path';
import * as fs from 'node:fs';

const PORTAL_URL = (
  process.env.PFACTORY_PORTAL_URL ?? 'https://pfactory.freundcloud.org.uk'
).replace(/\/$/, '');

const TOKEN_FILE = path.join(
  process.env.HOME ?? '/root',
  '.pfactory',
  '.token'
);

const OUT_DIR = path.resolve(
  __dirname,
  '..',
  'docs',
  'static',
  'img',
  'screenshots'
);

const VIEWPORT = {width: 1600, height: 1000};

interface Shot {
  name: string;
  description: string;
  capture: (page: Page) => Promise<void>;
}

// ---------- helpers ----------

function loadToken(): string {
  if (process.env.PFACTORY_TOKEN) return process.env.PFACTORY_TOKEN.trim();
  if (!fs.existsSync(TOKEN_FILE)) {
    throw new Error(
      `Token not found. Set PFACTORY_TOKEN or write ${TOKEN_FILE}.\n` +
        `Mint a fresh one with:\n` +
        `  kubectl get secret factory-secrets -n factory ` +
        `-o jsonpath='{.data.APP_API_TOKEN}' | base64 -d > ${TOKEN_FILE}`
    );
  }
  return fs.readFileSync(TOKEN_FILE, 'utf-8').trim();
}

async function shoot(page: Page, name: string): Promise<void> {
  await page.screenshot({path: path.join(OUT_DIR, name), fullPage: false});
  // eslint-disable-next-line no-console
  console.log(`  captured ${name}`);
}

async function withFallback(name: string, fn: () => Promise<void>): Promise<void> {
  try {
    await fn();
  } catch (e) {
    console.warn(`  WARN ${name} failed: ${(e as Error).message}`);
  }
}

/** Settle: wait for network idle (best-effort) plus a short paint delay. */
async function settle(page: Page, ms = 700): Promise<void> {
  await page.waitForLoadState('networkidle', {timeout: 8000}).catch(() => {});
  await page.waitForTimeout(ms);
}

/** Click a left-sidebar nav button by its visible label. */
async function nav(page: Page, label: string | RegExp): Promise<boolean> {
  const btn = page.getByRole('button', {name: label}).first();
  if (await btn.isVisible({timeout: 4000}).catch(() => false)) {
    await btn.click();
    await settle(page);
    return true;
  }
  console.warn(`  WARN nav button "${label}" not visible`);
  return false;
}

async function closeOverlays(page: Page): Promise<void> {
  await page.keyboard.press('Escape').catch(() => {});
  await page.waitForTimeout(250);
}

// ---------- shot definitions ----------

const SHOTS: Shot[] = [
  {
    name: '20-board.png',
    description: 'Planning Portal — Kanban board (Plans ready / Human Review / Done)',
    capture: async (page) => {
      await nav(page, /^Planning$/);
      await shoot(page, '20-board.png');
    },
  },
  {
    name: '21-board-list.png',
    description: 'Planning Portal — list view of plans',
    capture: async (page) => {
      await nav(page, /^Planning$/);
      const listBtn = page.getByRole('button', {name: /^List$/}).first();
      if (await listBtn.isVisible({timeout: 3000}).catch(() => false)) {
        await listBtn.click();
        await settle(page);
        await shoot(page, '21-board-list.png');
        // restore board view for later shots
        const boardBtn = page.getByRole('button', {name: /^Board$/}).first();
        if (await boardBtn.isVisible({timeout: 2000}).catch(() => false)) {
          await boardBtn.click();
          await settle(page, 400);
        }
      }
    },
  },
  {
    name: '22-plan-needs-attention.png',
    description: 'A plan card flagged "needs attention" (an error / blocked state)',
    capture: async (page) => {
      await nav(page, /^Planning$/);
      // Open a plan that the board flags as needing attention.
      const card = page
        .locator('text=/needs attention/i')
        .first()
        .locator('xpath=ancestor::*[self::button or self::a or @role="button"][1]');
      const target = (await card.count())
        ? card
        : page.getByText(/needs attention/i).first();
      if (await target.isVisible({timeout: 3000}).catch(() => false)) {
        await target.click({timeout: 3000}).catch(() => {});
        await settle(page);
        await shoot(page, '22-plan-needs-attention.png');
        await closeOverlays(page);
      }
    },
  },
  {
    name: '23-plan-done.png',
    description: 'A successfully governed, emitted plan (the Done column / detail)',
    capture: async (page) => {
      await nav(page, /^Planning$/);
      // Pick a card from the Done column. Fall back to clicking the first
      // recognizable plan title that ships with the demo workspace.
      const done = page
        .getByText(/Task Board service|Go hello-world|FastAPI API gateway/i)
        .first();
      if (await done.isVisible({timeout: 3000}).catch(() => false)) {
        await done.click({timeout: 3000}).catch(() => {});
        await settle(page);
        await shoot(page, '23-plan-done.png');
        await closeOverlays(page);
      }
    },
  },
  {
    name: '24-new-plan.png',
    description: 'Create-new-plan dialog (ingest a plan: text / file / GitHub)',
    capture: async (page) => {
      await nav(page, /^Planning$/);
      const newPlan = page
        .getByRole('button', {name: /new plan|create new plan/i})
        .first();
      if (await newPlan.isVisible({timeout: 3000}).catch(() => false)) {
        await newPlan.click();
        await settle(page);
        await shoot(page, '24-new-plan.png');
        await closeOverlays(page);
      }
    },
  },
  {
    name: '25-task.png',
    description: 'New task dialog (drive a single planning task)',
    capture: async (page) => {
      const taskBtn = page.getByRole('button', {name: /^Task$/}).first();
      if (await taskBtn.isVisible({timeout: 3000}).catch(() => false)) {
        await taskBtn.click();
        await settle(page);
        await shoot(page, '25-task.png');
        await closeOverlays(page);
      }
    },
  },
  {
    name: '26-files.png',
    description: 'Files — the planning workspace file tree',
    capture: async (page) => {
      if (await nav(page, /^Files$/)) await shoot(page, '26-files.png');
    },
  },
  {
    name: '27-mcp.png',
    description: 'MCP — registered Model Context Protocol servers',
    capture: async (page) => {
      if (await nav(page, /^MCP$/)) await shoot(page, '27-mcp.png');
    },
  },
  {
    name: '28-skills.png',
    description: 'Skills — the extensible skill registry',
    capture: async (page) => {
      if (await nav(page, /^Skills$/)) await shoot(page, '28-skills.png');
    },
  },
  {
    name: '29-index-memory.png',
    description: 'Index & Memory — code-aware planning index and project memory',
    capture: async (page) => {
      if (await nav(page, /Index & Memory/)) await shoot(page, '29-index-memory.png');
    },
  },
  {
    name: '30-plans.png',
    description: 'Plans — the emitted plans / signed Task Contract view',
    capture: async (page) => {
      if (await nav(page, /^Plans$/)) await shoot(page, '30-plans.png');
    },
  },
  {
    name: '31-github-prs.png',
    description: 'GitHub PRs — emitted epics + child issues and PR status',
    capture: async (page) => {
      if (await nav(page, /GitHub PRs/)) {
        // Give the GitHub connection probe time to resolve so we capture the
        // settled state, not the transient "Connecting…" spinner.
        await page.waitForTimeout(4000);
        await shoot(page, '31-github-prs.png');
      }
    },
  },
  {
    name: '32-project-picker.png',
    description: 'Project picker — switch between planning workspaces',
    capture: async (page) => {
      await nav(page, /^Planning$/);
      const picker = page.getByRole('combobox').first();
      if (await picker.isVisible({timeout: 3000}).catch(() => false)) {
        await picker.click();
        await page.waitForTimeout(500);
        await shoot(page, '32-project-picker.png');
        await closeOverlays(page);
      }
    },
  },
  {
    name: '33-settings.png',
    description: 'Settings — provider / model / governance configuration',
    capture: async (page) => {
      const settings = page.getByRole('button', {name: /^Settings$/}).first();
      if (await settings.isVisible({timeout: 3000}).catch(() => false)) {
        await settings.click();
        await settle(page);
        await shoot(page, '33-settings.png');
        await closeOverlays(page);
      }
    },
  },
  {
    name: '34-chat.png',
    description: 'Insights chat — ask questions about the plan and project',
    capture: async (page) => {
      const chat = page.getByRole('button', {name: /^Chat$/}).first();
      if (await chat.isVisible({timeout: 3000}).catch(() => false)) {
        await chat.click();
        await settle(page);
        await shoot(page, '34-chat.png');
        await closeOverlays(page);
      }
    },
  },
];

// ---------- login ----------

async function formLogin(page: Page, token: string): Promise<void> {
  // Always start at the SPA root; the router redirects to the login surface.
  await page.goto(PORTAL_URL + '/', {waitUntil: 'domcontentloaded'});
  await settle(page);

  // If already authenticated (sidebar present), nothing to do.
  const sidebar = page.getByRole('button', {name: /^Planning$/}).first();
  if (await sidebar.isVisible({timeout: 2000}).catch(() => false)) return;

  const tokenInput = page.locator('#token');
  await tokenInput.waitFor({state: 'visible', timeout: 8000});
  await tokenInput.fill(token);
  await page.getByRole('button', {name: /continue/i}).first().click();

  // Wait for the authenticated app to mount.
  await sidebar.waitFor({state: 'visible', timeout: 15000});
  await settle(page);
}

// ---------- main ----------

async function main(): Promise<void> {
  fs.mkdirSync(OUT_DIR, {recursive: true});
  const token = loadToken();

  const browser: Browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
  });
  const context = await browser.newContext({viewport: VIEWPORT});
  const page = await context.newPage();

  console.log(`Portal: ${PORTAL_URL}`);
  console.log(`Output: ${OUT_DIR}`);

  await formLogin(page, token);
  console.log(`Authenticated. Capturing ${SHOTS.length} screenshots.`);

  for (const shot of SHOTS) {
    await withFallback(shot.name, () => shot.capture(page));
  }

  await browser.close();
  console.log('\nDone.');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
