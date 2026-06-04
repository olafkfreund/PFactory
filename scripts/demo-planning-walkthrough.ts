/**
 * PFactory planning-portal demo walkthrough.
 *
 * Drives the single-origin portal (SPA + API on one port, default
 * http://127.0.0.1:3198, override with PFACTORY_PORTAL_URL) via Playwright:
 *   1. seeds 3 demo sessions through the API so the List/Board/detail are populated,
 *   2. records a video while walking the UI and taking named per-screen PNGs.
 *
 * Output:
 *   docs/static/img/screenshots/NN-*.png   (per-screen stills)
 *   docs/static/recordings/tmp/*.webm      (raw video; make-demo.sh -> mp4/gif)
 *
 * Run: tsx scripts/demo-planning-walkthrough.ts   (server must be up; see make-demo.sh)
 */

import { chromium, type Browser, type BrowserContext, type Page } from 'playwright';
import { readFileSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const PORTAL = process.env.PFACTORY_PORTAL_URL ?? 'http://127.0.0.1:3198';
const ROOT = join(__dirname, '..');
const SHOTS = join(ROOT, 'docs', 'static', 'img', 'screenshots');
const VIDEO_DIR = join(ROOT, 'docs', 'static', 'recordings', 'tmp');
const TOKEN = readFileSync(join(homedir(), '.pfactory', '.token'), 'utf8').trim();

const VIEWPORT = { width: 1440, height: 900 };
// Settle pauses let CSS transitions finish before a screenshot. Named so the
// intent (not just the number) is clear, and paced so the recording reads well.
const SETTLE = { quick: 400, normal: 900, slow: 1200, load: 1500 } as const;

mkdirSync(SHOTS, { recursive: true });
mkdirSync(VIDEO_DIR, { recursive: true });

// ── seed content ─────────────────────────────────────────────────────────
const INFRA_SOW = `# Statement of Work — Orders Platform: Multi-Region Microservices on EKS

A multi-region, highly-available orders platform on AWS EKS with disaster recovery.

## Acceptance Criteria
- AC#1: order-api, inventory, payments and fulfilment-worker each deploy to EKS with a Horizontal Pod Autoscaler and a default-deny NetworkPolicy.
- AC#2: The primary region runs RDS PostgreSQL Multi-AZ; a cross-region read replica supports fail-over (DR runbook documented).
- AC#3: ElastiCache Redis backs sessions and the scoreboard; traffic enters via a public ALB on 443 only.
- AC#4: Pods authenticate to AWS via IRSA; mTLS between services; secrets in AWS Secrets Manager; least-privilege IAM.
`;

// Deliberately free of cloud keywords (no deploy/microservice/redis/eks/…) so the
// AWS probe is skipped for this one — it then passes gates and can be approved,
// which surfaces the Emit tab for the taxonomy money-shot.
const FEATURE_PLAN = `# Refund flow — orders web app feature

Add a refund flow to the Orders web application.

## Acceptance Criteria
- AC#1: A "Refund" button on the order detail screen opens a confirmation dialog.
- AC#2: Only users with the finance role may issue a refund (role-based authorization).
- AC#3: Submitting a refund updates the order status to "refunded" and shows a toast.
- AC#4: Unit and integration tests cover the happy path and a double-refund guard.
`;

const SIMPLE_PLAN = `# Tic-tac-toe web game

A browser tic-tac-toe game.

## Acceptance Criteria
- AC#1: Two players alternate X and O on a 3x3 grid in the browser.
- AC#2: The game detects a win or a draw and shows the result.
`;

// ── API seeding ────────────────────────────────────────────────────────────
async function api(path: string, method = 'GET', body?: unknown): Promise<any> {
  const res = await fetch(PORTAL + path, {
    method,
    headers: body ? { 'content-type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status} ${await res.text()}`);
  return res.json();
}

async function ingest(text: string, title: string, category = '', template = ''): Promise<string> {
  const s = await api('/api/plan/sessions/ingest-text', 'POST', { text, title, category, template, channel: 'portal' });
  return s.session_id;
}

/** Seed 3 sessions so the List, Board, and a fully-populated detail all render. */
async function seedSessions(): Promise<{ infra: string; feature: string }> {
  console.log('seeding demo sessions…');

  // A — infra SOW: live AWS feasibility (~20-40s) → lands in Human Review.
  const infra = await ingest(INFRA_SOW, 'Orders Platform — Multi-Region EKS SOW', 'infrastructure', 'infra-change');
  await api(`/api/plan/sessions/${infra}/process`, 'POST');
  console.log('  · A (infra SOW) processed');

  // B — clean feature: passes gates → approve → surfaces the Emit tab + Done column.
  const feature = await ingest(FEATURE_PLAN, 'Refund API feature', 'software');
  const processed = await api(`/api/plan/sessions/${feature}/process`, 'POST');
  if ((processed.session ?? processed)?.review?.gates_passed) {
    await api(`/api/plan/sessions/${feature}/approve`, 'POST', { approver: 'olaf' });
    console.log('  · B (feature) processed + approved');
  } else {
    console.warn('  · B gates did not pass — Emit tab may be unavailable');
  }

  // C — fresh, unprocessed → Plans ready column.
  await ingest(SIMPLE_PLAN, 'Tic-tac-toe web game', 'software');
  console.log('  · C (fresh) ingested');
  return { infra, feature };
}

// ── Playwright helpers ───────────────────────────────────────────────────────
const settle = (page: Page, ms: number = SETTLE.normal) => page.waitForTimeout(ms);

async function shot(page: Page, name: string): Promise<void> {
  await page.screenshot({ path: join(SHOTS, `${name}.png`) });
  console.log(`  📸 ${name}`);
}

/** Run a capture step; a flaky/missing element is logged, never fatal. */
async function safely(label: string, fn: () => Promise<void>): Promise<void> {
  try {
    await fn();
  } catch (e) {
    console.warn(`  ! ${label} skipped: ${(e as Error).message}`);
  }
}

/** Click a SessionDetail tab by its visible name, then screenshot its panel. */
async function captureTab(page: Page, tab: string, panelTestId: string, file: string): Promise<void> {
  await safely(`tab ${tab}`, async () => {
    await page.getByRole('tab', { name: tab }).first().click();
    await page.waitForSelector(`[data-testid="${panelTestId}"]`, { timeout: 8000 });
    await settle(page);
    await shot(page, file);
  });
}

async function openSession(page: Page, id: string): Promise<void> {
  await safely(`open session ${id}`, async () => {
    await page.getByTestId(`session-row-${id}`).click();
    await page.waitForSelector('[data-testid="session-detail"]', { timeout: 10000 });
    await settle(page, SETTLE.normal);
  });
}

async function backToSessions(page: Page): Promise<void> {
  await safely('back', () => page.getByLabel('Back to sessions').click().then(() => settle(page, SETTLE.quick)));
}

// ── capture steps (read top-to-bottom as the walkthrough) ────────────────────
async function launchPortal(): Promise<{ browser: Browser; ctx: BrowserContext; page: Page }> {
  // On NixOS the npm-downloaded chromium can't load system libs (libnspr4 …); use
  // a working browser via executablePath (PFACTORY_CHROME), e.g. the Nix-built
  // chrome-headless-shell or google-chrome-stable.
  const executablePath = process.env.PFACTORY_CHROME || undefined;
  const browser = await chromium.launch(executablePath ? { executablePath } : {});
  const ctx = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 2,
    recordVideo: { dir: VIDEO_DIR, size: VIEWPORT },
  });
  // Open straight into a dark, authenticated portal.
  await ctx.addInitScript((t: string) => {
    localStorage.setItem('pfactory-token', t);
    localStorage.setItem('pfactory-theme', 'dark');
    localStorage.setItem('pfactory-color-theme', 'gruvbox');
  }, TOKEN);
  const page = await ctx.newPage();
  await page.goto(PORTAL, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-testid="planning-view"]', { timeout: 30000 });
  await settle(page, SETTLE.load);
  return { browser, ctx, page };
}

/** Empty-state portal + the Registry/Templates/Providers config tabs. */
async function captureEmptyState(page: Page): Promise<void> {
  await shot(page, '01-portal-list');
  const configTabs: [string, string][] = [['Registry', '02-registry'], ['Templates', '03-templates'], ['Providers', '04-providers']];
  for (const [tab, file] of configTabs) {
    await safely(`config ${tab}`, async () => {
      await page.getByRole('tab', { name: tab }).first().click();
      await settle(page);
      await shot(page, file);
    });
  }
}

/** New-plan dialog: file mode, then text mode (shows the category/template picker). */
async function captureIntakeDialog(page: Page): Promise<void> {
  await safely('new-plan dialog', async () => {
    await page.getByTestId('new-plan-btn').click();
    await page.waitForSelector('[data-testid="plan-upload-form"]', { timeout: 8000 });
    await settle(page);
    await shot(page, '05-new-plan-file');
    await page.getByRole('button', { name: 'Paste text' }).click();
    await settle(page);
    await shot(page, '06-new-plan-text');
    await page.keyboard.press('Escape');
    await settle(page, SETTLE.quick);
  });
}

/** The kanban board (renamed columns) with the seeds bucketed by state. */
async function captureBoard(page: Page): Promise<void> {
  await safely('board', async () => {
    await page.getByRole('button', { name: 'Board' }).click();
    await page.waitForSelector('[data-testid="plan-board"]', { timeout: 8000 });
    await settle(page, SETTLE.slow);
    await shot(page, '07-board');
    await page.getByRole('button', { name: 'List' }).click();
    await settle(page, SETTLE.quick);
  });
}

/** The fully-populated session detail — every tab (★ = money shots). */
async function captureSessionDetail(page: Page, id: string): Promise<void> {
  await openSession(page, id);
  await captureTab(page, 'Edit', 'plan-editor', '08-edit');
  await captureTab(page, 'Pipeline', 'pipeline-panel', '09-pipeline');
  await captureTab(page, 'Feasibility', 'feasibility-panel', '10-feasibility');
  await captureTab(page, 'AI Context', 'enrichment-panel', '11-ai-context');
  await captureTab(page, 'Review', 'review-panel', '12-review');
  await captureTab(page, 'Suggestions', 'annotation-panel', '13-suggestions');
  await captureTab(page, 'Approval', 'approval-panel', '14-approval');
  await backToSessions(page);
}

/** The Emit dry-run preview: tagged epic + children (taxonomy chips + pfactory:meta). */
async function captureEmitPreview(page: Page, id: string): Promise<void> {
  await openSession(page, id);
  await safely('emit', async () => {
    await page.getByRole('tab', { name: 'Emit' }).first().click();
    await page.waitForSelector('[data-testid="emit-panel"]', { timeout: 8000 });
    await page.getByTestId('emit-repo-input').fill('olafkfreund/demo-pfactory');
    await settle(page, SETTLE.quick);
    await page.getByTestId('emit-btn').click(); // aria-label is "Emit plan", so use testid
    await page.waitForSelector('text=/pfactory/', { timeout: 8000 }).catch(() => {});
    await settle(page, SETTLE.slow);
    await shot(page, '15-emit');
  });
  await backToSessions(page);
}

/** The Tasks kanban (renamed columns) — best-effort, it's project-scoped. */
async function captureTasksBoard(page: Page): Promise<void> {
  await safely('tasks board', async () => {
    await page.getByText('Tasks', { exact: true }).first().click();
    await settle(page, SETTLE.load);
    await shot(page, '16-tasks-board');
  });
}

// ── orchestration ────────────────────────────────────────────────────────────
async function main(): Promise<void> {
  const ids = await seedSessions();
  const { browser, ctx, page } = await launchPortal();

  await captureEmptyState(page);
  await captureIntakeDialog(page);
  await captureBoard(page);
  await captureSessionDetail(page, ids.infra);
  await captureEmitPreview(page, ids.feature);
  await captureTasksBoard(page);

  await ctx.close(); // flush video
  await browser.close();
  console.log('walkthrough complete.');
}

main().catch((e) => { console.error(e); process.exit(1); });
