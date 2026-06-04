/**
 * PFactory screenshot capture for the docs site.
 *
 * Drives the running portal at http://localhost:3115 via Playwright
 * (headless Chromium) and saves ~14 PNGs to docs/static/img/screenshots/.
 *
 * Run with:
 *   1. Start the portal stack: web-server on :3114, vite dev on :3115
 *   2. Run the demo: ./scripts/demo.sh --yolo  (seeds 3 tasks)
 *   3. npm -w apps/frontend-web run capture-screenshots
 *
 * The script is intentionally tolerant — if a view 404s or a selector
 * misses, it logs a warning and continues, capturing what it can.
 */

import {chromium, type Browser, type Page} from '@playwright/test';
import * as path from 'node:path';
import * as fs from 'node:fs';

const PORTAL_URL = process.env.PFACTORY_PORTAL_URL ?? 'http://localhost:3115';
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

interface Shot {
  name: string;
  description: string;
  capture: (page: Page) => Promise<void>;
}

// ---------- helpers ----------

function loadToken(): string {
  if (!fs.existsSync(TOKEN_FILE)) {
    throw new Error(
      `Token not found at ${TOKEN_FILE}. Start the portal once to create it.`
    );
  }
  return fs.readFileSync(TOKEN_FILE, 'utf-8').trim();
}

async function shoot(page: Page, name: string): Promise<void> {
  const outPath = path.join(OUT_DIR, name);
  await page.screenshot({path: outPath, fullPage: false});
  // eslint-disable-next-line no-console
  console.log(`  ✓ ${name}`);
}

async function withFallback(
  name: string,
  fn: () => Promise<void>
): Promise<void> {
  try {
    await fn();
  } catch (e) {
    console.warn(`  ⚠ ${name} failed: ${(e as Error).message}`);
  }
}

// ---------- shot definitions ----------

const SHOTS: Shot[] = [
  {
    name: '01-welcome.png',
    description: 'Welcome screen with the project list',
    capture: async (page) => {
      await page.goto(PORTAL_URL);
      await page.waitForLoadState('networkidle');
      await shoot(page, '01-welcome.png');
    },
  },
  {
    name: '02-onboarding-providers.png',
    description: 'Onboarding wizard — provider choice step',
    capture: async (page) => {
      // Try opening Settings → Agent Profile as a proxy for the
      // provider-choice surface (the onboarding wizard runs once).
      await page.goto(PORTAL_URL);
      const settings = page.getByRole('button', {name: /settings/i}).first();
      if (await settings.isVisible({timeout: 3000})) {
        await settings.click();
        await page.waitForTimeout(500);
        await shoot(page, '02-onboarding-providers.png');
      }
    },
  },
  {
    name: '03-kanban.png',
    description: 'Kanban board with seeded demo tasks',
    capture: async (page) => {
      await page.goto(PORTAL_URL);
      await page.waitForLoadState('networkidle');
      // Click first project card if Welcome screen is showing
      const projectCard = page
        .locator('[data-testid^="project-card-"]')
        .first();
      if (await projectCard.isVisible({timeout: 3000})) {
        await projectCard.click();
        await page.waitForLoadState('networkidle');
      }
      await shoot(page, '03-kanban.png');
    },
  },
  {
    name: '04-task-create-wizard.png',
    description: 'Task creation wizard, step 1',
    capture: async (page) => {
      const newTaskBtn = page
        .getByRole('button', {name: /new task|create task/i})
        .first();
      if (await newTaskBtn.isVisible({timeout: 3000})) {
        await newTaskBtn.click();
        await page.waitForTimeout(500);
        await shoot(page, '04-task-create-wizard.png');
        // Close the modal so subsequent shots aren't affected
        const closeBtn = page.getByRole('button', {name: /cancel|close/i}).first();
        if (await closeBtn.isVisible({timeout: 1000})) {
          await closeBtn.click();
        }
      }
    },
  },
  {
    name: '05-task-detail-overview.png',
    description: 'Task detail modal — metadata tab',
    capture: async (page) => {
      const taskCard = page
        .locator('[data-testid^="task-card-"], .task-card')
        .first();
      if (await taskCard.isVisible({timeout: 3000})) {
        await taskCard.click();
        await page.waitForTimeout(500);
        await shoot(page, '05-task-detail-overview.png');
      }
    },
  },
  {
    name: '06-task-detail-plan.png',
    description: 'Plan review section',
    capture: async (page) => {
      const planTab = page.getByRole('tab', {name: /plan|review/i}).first();
      if (await planTab.isVisible({timeout: 3000})) {
        await planTab.click();
        await page.waitForTimeout(500);
        await shoot(page, '06-task-detail-plan.png');
      }
    },
  },
  {
    name: '07-task-detail-logs.png',
    description: 'Phase logs',
    capture: async (page) => {
      const logsTab = page.getByRole('tab', {name: /logs/i}).first();
      if (await logsTab.isVisible({timeout: 3000})) {
        await logsTab.click();
        await page.waitForTimeout(500);
        await shoot(page, '07-task-detail-logs.png');
      }
    },
  },
  {
    name: '08-task-detail-files.png',
    description: 'Files tab',
    capture: async (page) => {
      const filesTab = page.getByRole('tab', {name: /files/i}).first();
      if (await filesTab.isVisible({timeout: 3000})) {
        await filesTab.click();
        await page.waitForTimeout(500);
        await shoot(page, '08-task-detail-files.png');
      }
    },
  },
  {
    name: '09-live-agent-console.png',
    description: 'rmux Live Agent Console',
    capture: async (page) => {
      const consoleTab = page
        .getByRole('tab', {name: /agent console|live console/i})
        .first();
      if (await consoleTab.isVisible({timeout: 3000})) {
        await consoleTab.click();
        await page.waitForTimeout(2000); // give WS time to connect
        await shoot(page, '09-live-agent-console.png');
      } else {
        console.warn(
          '  ⚠ Live Console tab not visible — is PFACTORY_RMUX_ENABLED set on the web-server?'
        );
      }
    },
  },
  {
    name: '10-terminal-grid.png',
    description: 'Multi-pane terminal grid',
    capture: async (page) => {
      await page.goto(PORTAL_URL);
      const projectCard = page.locator('[data-testid^="project-card-"]').first();
      if (await projectCard.isVisible({timeout: 3000})) {
        await projectCard.click();
      }
      const terminalsNav = page.getByRole('link', {name: /terminal/i}).first();
      if (await terminalsNav.isVisible({timeout: 3000})) {
        await terminalsNav.click();
        await page.waitForTimeout(1000);
        await shoot(page, '10-terminal-grid.png');
      }
    },
  },
  {
    name: '11-github-issues-sync.png',
    description: 'GitHub Issues view',
    capture: async (page) => {
      const githubNav = page
        .getByRole('link', {name: /github.*issues/i})
        .first();
      if (await githubNav.isVisible({timeout: 3000})) {
        await githubNav.click();
        await page.waitForTimeout(1000);
        await shoot(page, '11-github-issues-sync.png');
      }
    },
  },
  {
    name: '12-settings-llm-providers.png',
    description: 'LLM Providers settings',
    capture: async (page) => {
      const settings = page.getByRole('button', {name: /settings/i}).first();
      if (await settings.isVisible({timeout: 3000})) {
        await settings.click();
        await page.waitForTimeout(500);
        const llmTab = page
          .getByRole('tab', {name: /llm|provider/i})
          .first();
        if (await llmTab.isVisible({timeout: 2000})) {
          await llmTab.click();
          await page.waitForTimeout(500);
        }
        await shoot(page, '12-settings-llm-providers.png');
      }
    },
  },
  {
    name: '13-settings-agent-profile.png',
    description: 'Agent profile editor',
    capture: async (page) => {
      const agentTab = page
        .getByRole('tab', {name: /agent profile/i})
        .first();
      if (await agentTab.isVisible({timeout: 2000})) {
        await agentTab.click();
        await page.waitForTimeout(500);
        await shoot(page, '13-settings-agent-profile.png');
      }
    },
  },
  {
    name: '14-insights-chat.png',
    description: 'Insights chat',
    capture: async (page) => {
      // Close settings if open
      await page.keyboard.press('Escape');
      const insightsNav = page.getByRole('link', {name: /insights/i}).first();
      if (await insightsNav.isVisible({timeout: 3000})) {
        await insightsNav.click();
        await page.waitForTimeout(1000);
        await shoot(page, '14-insights-chat.png');
      }
    },
  },
];

// ---------- main ----------

async function main(): Promise<void> {
  fs.mkdirSync(OUT_DIR, {recursive: true});

  const token = loadToken();

  const browser: Browser = await chromium.launch({headless: true});
  const context = await browser.newContext({
    viewport: {width: 1440, height: 900},
    // Inject token so the portal is pre-authenticated
    extraHTTPHeaders: {Authorization: `Bearer ${token}`},
    storageState: {
      cookies: [],
      origins: [
        {
          origin: PORTAL_URL,
          localStorage: [
            {name: 'pfactory.token', value: token},
            // Skip the onboarding wizard on subsequent shots
            {name: 'pfactory.onboarding.completed', value: 'true'},
          ],
        },
      ],
    },
  });

  const page = await context.newPage();

  console.log(`Capturing ${SHOTS.length} screenshots to ${OUT_DIR}`);

  for (const shot of SHOTS) {
    await withFallback(shot.name, () => shot.capture(page));
  }

  await browser.close();
  console.log('\nDone. Screenshots saved to:', OUT_DIR);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
